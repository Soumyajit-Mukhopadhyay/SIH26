"""Build the archive, train FrontCast, and score it against persistence.

Run it:

    python -m orca.ml.train_frontcast --days 120 --epochs 12

Two things about this script are deliberate.

**It fetches through the same code path the live pipeline uses.** The archive is
assembled with ``orca.jobs.ingest.fetch_grid``, the same function that produces
the rasters the map serves. A training set built by a second, parallel fetcher is
a training set that can silently diverge from what the model is handed at
inference — different stride, different fill value, different land mask — and the
symptom is a model that scores well offline and is useless live.

**It always scores a persistence baseline.** "Tomorrow's fronts are today's
fronts" is a genuinely strong forecast at one day, and a model that cannot beat
it has learned nothing. Reporting the model's F1 without the baseline beside it
would be a number chosen to look good.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np

from orca.ml import FRONTCAST_VERSION
from orca.ml.frontcast import (
    FRONT_DILATION,
    HISTORY_DAYS,
    LEAD_DAYS,
    TILE,
    TrainingReport,
    _build_torch_model,
    default_weights_path,
    label_from_sst,
    to_features,
)
from orca.science.grid import AOI, Grid

log = logging.getLogger(__name__)

#: Training grid. Coarser than the 0.05 deg AOI on purpose — SIED's 32x32 window
#: has to stay small enough in degrees for its bimodality test to fire, and at
#: 0.2 deg that window spans 6.4 deg and the detector returns an empty mask. At
#: 0.1 deg it finds fronts over about 2% of the field, which is the regime the
#: labels are meaningful in.
TRAIN_STEP = 0.1


def training_grid() -> Grid:
    return Grid(west=AOI.west, east=AOI.east, south=AOI.south, north=AOI.north, step=TRAIN_STEP)


# ------------------------------------------------------------------- archive


async def build_archive(days: int, cache: Path) -> list[tuple[datetime, np.ndarray]]:
    """Fetch ``days`` of SST over the training grid, newest last.

    Cached to .npz per day: MUR SST for one day is a fixed historical fact, so
    re-fetching it on every training run would be pure waste of somebody else's
    bandwidth.
    """
    from orca.jobs.ingest import fetch_grid

    cache.mkdir(parents=True, exist_ok=True)
    grid = training_grid()
    # MUR is a day behind; start two days back so the newest request is not a
    # coin flip on whether today's field has been published yet.
    end = datetime.now(UTC).replace(hour=9, minute=0, second=0, microsecond=0) - timedelta(days=2)

    archive: list[tuple[datetime, np.ndarray]] = []
    misses = 0
    for offset in range(days, -1, -1):
        day = end - timedelta(days=offset)
        path = cache / f"mur_sst_{day:%Y%m%d}.npz"
        if path.exists():
            archive.append((day, np.load(path)["sst"]))
            continue
        try:
            out = await fetch_grid("mur_sst", grid=grid, when=day)
        except Exception as exc:  # noqa: BLE001 — one bad day must not end the run
            log.warning("archive: %s failed: %s", day.date(), exc)
            misses += 1
            continue
        if out is None:
            misses += 1
            continue
        field = out[0]
        np.savez_compressed(path, sst=field.astype(np.float32))
        archive.append((day, field.astype(np.float32)))
        print(f"  fetched {day:%Y-%m-%d}  finite {100 * np.isfinite(field).mean():.0f}%")

    print(f"archive: {len(archive)} days, {misses} unavailable")
    return archive


# ------------------------------------------------------------------ samples


def build_samples(
    archive: list[tuple[datetime, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Turn the archive into ``(inputs, labels, persistence)``.

    * ``inputs``     (N, T, C, H, W) — the SST history
    * ``labels``     (N, L, H, W)    — SIED fronts at each lead time
    * ``persistence``(N, L, H, W)    — SIED fronts on the LAST INPUT DAY, repeated.
      This is the baseline forecast, carried alongside every sample so the split
      it is scored on is byte-identical to the model's.
    """
    by_day = {day.date(): field for day, field in archive}
    days = sorted(by_day)
    max_lead = max(LEAD_DAYS)

    # SIED is the expensive step, so every day is labelled exactly once.
    print(f"labelling {len(days)} days with SIED...")
    labels_by_day = {d: label_from_sst(by_day[d]) for d in days}

    inputs, targets, baselines = [], [], []
    for index in range(HISTORY_DAYS - 1, len(days) - max_lead):
        window = days[index - HISTORY_DAYS + 1 : index + 1]
        # Only contiguous windows. A gap in the archive means the day embedding
        # would be lying about how far apart the steps are.
        if (window[-1] - window[0]).days != HISTORY_DAYS - 1:
            continue
        leads = [days[index] + timedelta(days=lead) for lead in LEAD_DAYS]
        if any(d not in labels_by_day for d in leads):
            continue

        inputs.append(np.stack([to_features(by_day[d]) for d in window]))
        targets.append(np.stack([labels_by_day[d] for d in leads]))
        today = labels_by_day[days[index]]
        baselines.append(np.stack([today for _ in LEAD_DAYS]))

    if not inputs:
        raise SystemExit("no contiguous training windows — fetch a longer archive")
    return np.stack(inputs), np.stack(targets), np.stack(baselines)


def tile_samples(
    inputs: np.ndarray, targets: np.ndarray, baselines: np.ndarray, *, per_field: int, seed: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Cut random TILE-sized crops, keeping only tiles that contain a front.

    Fronts cover ~2% of the field. Training on uniformly sampled tiles would feed
    the network a large majority of empty ocean, and the cheapest way to a low
    loss would be to predict "no front" everywhere. Requiring a front in the crop
    is a sampling decision, not a metric one — the reported scores below are
    computed on whole fields, empty ocean included.
    """
    rng = np.random.default_rng(seed)
    _, _, _, height, width = inputs.shape
    xi, xt, xb = [], [], []
    for sample in range(inputs.shape[0]):
        kept = 0
        for _ in range(per_field * 12):
            if kept >= per_field:
                break
            top = int(rng.integers(0, height - TILE))
            left = int(rng.integers(0, width - TILE))
            label = targets[sample, :, top : top + TILE, left : left + TILE]
            if label.sum() < 20:
                continue
            xi.append(inputs[sample, :, :, top : top + TILE, left : left + TILE])
            xt.append(label)
            xb.append(baselines[sample, :, top : top + TILE, left : left + TILE])
            kept += 1
    return np.stack(xi), np.stack(xt), np.stack(xb)


# ------------------------------------------------------------------- metrics


def score(pred: np.ndarray, truth: np.ndarray, *, threshold: float = 0.5) -> dict[str, float]:
    """Per-lead-time IoU, F1, precision and recall over whole fields."""
    hard = pred >= threshold
    real = truth >= 0.5
    tp = float(np.logical_and(hard, real).sum())
    fp = float(np.logical_and(hard, ~real).sum())
    fn = float(np.logical_and(~hard, real).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    iou = tp / (tp + fp + fn) if tp + fp + fn else 0.0
    return {
        "iou": round(iou, 4),
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
    }


# -------------------------------------------------------------------- train


def train(
    inputs: np.ndarray,
    targets: np.ndarray,
    baselines: np.ndarray,
    *,
    epochs: int,
    batch: int,
    lr: float,
    seed: int,
) -> tuple[object, TrainingReport]:
    import torch
    from torch import nn

    torch.manual_seed(seed)
    rng = np.random.default_rng(seed)

    # Split by SAMPLE INDEX, which here means by time: the archive is ordered, so
    # the validation set is the most recent stretch. A random pixel-level split
    # would put tiles from the same day on both sides and score the model on
    # weather it had already seen.
    count = inputs.shape[0]
    cut = int(count * 0.8)
    order = np.arange(count)
    train_idx, val_idx = order[:cut], order[cut:]
    print(f"samples: {len(train_idx)} train / {len(val_idx)} val")

    model = _build_torch_model()
    params = sum(p.numel() for p in model.parameters())
    print(f"parameters: {params:,}")

    # Fronts are ~2% of pixels, so the positive class is weighted up. Without it
    # the model converges on "no front anywhere", which scores 98% pixel accuracy
    # and has zero recall — the exact failure a plain accuracy metric hides.
    positive_rate = float(targets.mean())
    pos_weight = torch.tensor(min((1 - positive_rate) / max(positive_rate, 1e-6), 40.0))
    print(f"positive rate {positive_rate:.4f} -> pos_weight {float(pos_weight):.1f}")

    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimiser = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    schedule = torch.optim.lr_scheduler.CosineAnnealingLR(optimiser, T_max=max(epochs, 1))

    xt = torch.from_numpy(inputs[train_idx])
    yt = torch.from_numpy(targets[train_idx])
    xv = torch.from_numpy(inputs[val_idx])
    yv = torch.from_numpy(targets[val_idx])

    for epoch in range(epochs):
        model.train()
        perm = rng.permutation(len(train_idx))
        total = 0.0
        for start in range(0, len(perm), batch):
            chunk = perm[start : start + batch]
            optimiser.zero_grad()
            logits = model(xt[chunk])
            loss = loss_fn(logits, yt[chunk])
            loss.backward()
            optimiser.step()
            total += float(loss) * len(chunk)
        schedule.step()

        model.eval()
        with torch.no_grad():
            val_logits = model(xv)
            val_loss = float(loss_fn(val_logits, yv))
            probs = torch.sigmoid(val_logits).numpy()
        summary = score(probs[:, 0], targets[val_idx][:, 0])
        print(
            f"  epoch {epoch + 1:2d}/{epochs}  train {total / len(perm):.4f}  "
            f"val {val_loss:.4f}  +1d IoU {summary['iou']:.3f} F1 {summary['f1']:.3f}"
        )

    # Final scoring, per lead time, model against persistence on the same split.
    model.eval()
    with torch.no_grad():
        probs = torch.sigmoid(model(xv)).numpy()

    metrics, baseline = {}, {}
    for index, lead in enumerate(LEAD_DAYS):
        key = f"+{lead}d"
        metrics[key] = score(probs[:, index], targets[val_idx][:, index])
        baseline[key] = score(baselines[val_idx][:, index], targets[val_idx][:, index])

    report = TrainingReport(
        version=FRONTCAST_VERSION,
        trained_at=datetime.now(UTC).isoformat(),
        samples_train=len(train_idx),
        samples_val=len(val_idx),
        epochs=epochs,
        history_days=HISTORY_DAYS,
        lead_days=list(LEAD_DAYS),
        metrics=metrics,
        baseline=baseline,
        notes=(
            f"{params:,} parameters. Labels are Cayula-Cornillon SIED on the observed field at "
            f"the target day, widened by {FRONT_DILATION} cell to a front ZONE, over the Indian EEZ "
            f"at {TRAIN_STEP} deg. Validation is the most "
            "recent 20% of the archive, split by time rather than at random, so the model is "
            "scored on weather it never saw. Baseline is persistence: the SIED mask on the last "
            "input day, carried forward unchanged."
        ),
    )
    return model, report


# --------------------------------------------------------------------- main


async def main() -> None:
    parser = argparse.ArgumentParser(description="Train FrontCast")
    parser.add_argument("--days", type=int, default=120, help="days of SST archive to use")
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--lr", type=float, default=2e-3)
    parser.add_argument("--tiles-per-field", type=int, default=10)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from orca.config import get_settings

    cache = get_settings().data_dir / "training" / "mur_sst"

    print(f"=== archive: up to {args.days} days at {TRAIN_STEP} deg ===")
    archive = await build_archive(args.days, cache)
    if len(archive) < HISTORY_DAYS + max(LEAD_DAYS) + 4:
        raise SystemExit(f"only {len(archive)} usable days — not enough to train")

    print("\n=== samples ===")
    inputs, targets, baselines = build_samples(archive)
    print(f"whole-field samples: {inputs.shape[0]}  field {inputs.shape[-2]}x{inputs.shape[-1]}")
    ti, tt, tb = tile_samples(
        inputs, targets, baselines, per_field=args.tiles_per_field, seed=args.seed
    )
    print(f"tiles: {ti.shape[0]} of {TILE}x{TILE}  front pixels {100 * tt.mean():.2f}%")

    print("\n=== training ===")
    model, report = train(
        ti, tt, tb, epochs=args.epochs, batch=args.batch, lr=args.lr, seed=args.seed
    )

    import torch

    out = args.out or default_weights_path()
    out.parent.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), out)
    out.with_suffix(".json").write_text(json.dumps(report.describe(), indent=2), encoding="utf-8")

    print(f"\n=== result ===\nweights -> {out}  ({out.stat().st_size / 1e6:.1f} MB)")
    for key in report.metrics:
        m, b = report.metrics[key], report.baseline[key]
        verdict = "BEATS persistence" if m["f1"] > b["f1"] else "loses to persistence"
        print(
            f"  {key}: model F1 {m['f1']:.3f} IoU {m['iou']:.3f} | "
            f"persistence F1 {b['f1']:.3f} IoU {b['iou']:.3f}  -> {verdict}"
        )


if __name__ == "__main__":
    asyncio.run(main())
