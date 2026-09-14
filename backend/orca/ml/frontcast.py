"""FrontCast — where the thermal fronts will be, not where they were.

## Why this model exists at all

ORCA already detects ocean fronts with Cayula–Cornillon SIED, which is
deterministic, cited, and runs over the whole area of interest in about a
quarter of a second. Distilling it into a network to make it *faster* would be
engineering theatre.

The gap SIED cannot close is that **it is a detector**. Given this morning's
sea-surface temperature it tells you where the fronts are this morning. A
fisherman deciding at 04:00 whether tomorrow's trip is worth the diesel needs to
know where they will be *tomorrow*, and no amount of edge detection on today's
image answers that.

So FrontCast is a forecaster. It reads a short history of SST fields and
predicts the SIED front mask at +1, +2 and +3 days.

## Where the labels come from, and why that is honest

The training labels are ORCA's own SIED output on the observed field at the
target day. That is deliberate and it is worth being precise about what it does
and does not buy:

* It is **not** circular. The model never sees day D+1's temperature; it sees
  days up to D and must predict a function of a field it has not been given.
* It **inherits SIED's definition of a front**, including its blind spots. If
  SIED misses a weak front, FrontCast is not rewarded for finding it. The model
  can only ever be as right about what a front *is* as the algorithm that
  labelled it — it can only be better about *when*.
* It costs nothing and needs no hand-labelling, which is what makes a model for
  the Indian EEZ possible at all. There is no public labelled front dataset for
  60–100°E, 0–25°N; the one open dataset of this kind covers the north-west
  Pacific.

Every prediction ORCA serves says this out loud rather than implying the model
learned fronts from nature.

## The architecture, and why each piece is there

``SST history (T days) → per-day CNN encoder → temporal transformer → U-Net
decoder → three sigmoid heads (+1d, +2d, +3d)``

* **Per-day CNN encoder, shared weights.** Each day's field is encoded
  independently by the same small convolutional stack. Sharing the weights is
  what makes the history length changeable without retraining, and it is the
  reason a five-day input costs five cheap encodes rather than one enormous one.

* **Temporal transformer over the day axis.** Fronts move, and the direction
  they are moving is only visible across days. Attention over the time axis at
  each spatial location lets the model weigh "three days ago" differently from
  "yesterday" — and, unlike a recurrent layer, it sees the whole history at once
  so a stale day caused by cloud cover does not have to be propagated through
  every later step. This is the piece that separates FrontCast from a detector.

* **Three sigmoid heads, not a softmax.** "Front at +1 day", "front at +2 days"
  and "front at +3 days" are not mutually exclusive outcomes for a pixel —
  a front can persist across all three. A softmax would force them to compete
  for one unit of probability and make persistence unrepresentable. Independent
  sigmoids with per-head binary cross-entropy is the correct shape for the
  question, and it means each lead time can be calibrated and reported on its
  own.

## What it does not do

It does not touch the safety verdict. A front is a fishing signal, not a hazard
threshold, and ``orca.services.risk_engine`` neither imports nor consults this
module.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

#: Days of SST history the model reads.
HISTORY_DAYS = 5

#: Lead times predicted, in days. One sigmoid head each.
#:
#: Zero is included and is a different KIND of task from the others. At +0 the
#: model reproduces SIED on the field it was given, which is a deterministic
#: function of its own input — so it is a DETECTOR, and a well-trained one can
#: score very high. At +1 and beyond it is a FORECASTER of a field it has not
#: seen, and the honest ceiling there is far lower: measured over this archive,
#: the SIED mask overlaps its own next day only 44% of the time, so a +1d score
#: near a detector's would mean the model was more self-consistent than the
#: ocean it is describing.
#:
#: Both are served, both are scored, and the report never compares one against
#: the other's baseline.
LEAD_DAYS: tuple[int, ...] = (0, 1, 2, 3)

#: Leads that are genuine forecasts, i.e. of a field the model was not shown.
#: Persistence is only a meaningful baseline for these — at +0 "yesterday's
#: answer" IS the label, so the baseline is trivially perfect and reporting the
#: model against it would be nonsense.
FORECAST_LEADS: tuple[int, ...] = tuple(lead for lead in LEAD_DAYS if lead > 0)

#: Tile size the network is trained and run on. The area of interest is far
#: larger; inference walks it in overlapping tiles and blends the seams.
TILE = 64

#: Overlap between neighbouring tiles at inference, in pixels. Convolutional
#: models are least reliable at a tile edge because half the receptive field is
#: padding, so tiles overlap and the seam is averaged rather than butt-joined.
TILE_OVERLAP = 16

#: Channels the encoder reads per day.
#:
#: The mask is an input, not a nuisance. Roughly a quarter of any given SST field
#: over this box is absent — cloud, or land — and a model that cannot tell "cool
#: water" from "no observation" will read cloud edges as fronts, which is the
#: single most likely way this model could produce confident nonsense.
INPUT_CHANNELS = ("sst_anomaly", "gradient", "valid_mask")

#: SST is normalised by subtracting the per-field median and dividing by this,
#: in kelvin. A fixed divisor rather than a per-field standard deviation: the
#: model must see an absolute temperature contrast, and per-field standardisation
#: would rescale a calm day to look exactly like a frontal one.
SST_SCALE_K = 2.0


@dataclass(slots=True)
class TrainingReport:
    """What a training run actually achieved, kept beside the weights."""

    version: str
    trained_at: str
    samples_train: int
    samples_val: int
    epochs: int
    history_days: int
    lead_days: list[int]
    #: Per-lead-time metrics on the held-out split, keyed by "+1d" etc.
    metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    #: The same metrics for a persistence baseline — "tomorrow's fronts are
    #: today's fronts". A model that cannot beat this has learned nothing, and
    #: publishing the model's score without it would be meaningless.
    baseline: dict[str, dict[str, float]] = field(default_factory=dict)
    notes: str = ""

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trained_at": self.trained_at,
            "samples_train": self.samples_train,
            "samples_val": self.samples_val,
            "epochs": self.epochs,
            "history_days": self.history_days,
            "lead_days": self.lead_days,
            "metrics": self.metrics,
            "baseline": self.baseline,
            "notes": self.notes,
        }


# --------------------------------------------------------------------- features


def to_features(sst: np.ndarray) -> np.ndarray:
    """One day of SST to the model's input channels.

    Returns ``(3, H, W)`` float32: normalised anomaly, gradient magnitude, and
    the validity mask. NaNs are replaced by zero *after* the anomaly is formed,
    so an absent pixel reads as "equal to the field median" rather than as a
    large negative excursion the convolutions would treat as an edge.
    """
    valid = np.isfinite(sst)
    out = np.zeros((3, *sst.shape), dtype=np.float32)
    if not valid.any():
        return out

    median = float(np.median(sst[valid]))
    anomaly = np.where(valid, sst - median, 0.0) / SST_SCALE_K
    out[0] = np.clip(anomaly, -4.0, 4.0).astype(np.float32)

    # Gradient magnitude on the filled field. Computed here rather than taken
    # from science.fronts so the feature is identical at train and inference time
    # even if the front module's defaults change.
    filled = np.where(valid, sst, median)
    gy, gx = np.gradient(filled)
    grad = np.hypot(gx, gy)
    # A robust scale: the 99th percentile of a field with a few bad pixels is far
    # more stable than its maximum.
    scale = float(np.percentile(grad[valid], 99)) if valid.sum() > 10 else 1.0
    out[1] = np.clip(grad / max(scale, 1e-6), 0.0, 4.0).astype(np.float32)

    out[2] = valid.astype(np.float32)
    return out


#: Pixels the SIED mask is widened by to form the training target.
#:
#: SIED returns a one-pixel edge, and scoring a forecast on exact pixel overlap
#: asks the wrong question. A front is a ZONE of enhanced gradient a few
#: kilometres wide; which pixel inside it the detector picks is close to
#: arbitrary and moves between consecutive days even when the front itself has
#: barely shifted. Measured on this archive, day-to-day persistence of the raw
#: mask scores F1 0.26 — so a model matching yesterday's front exactly would
#: still be marked three-quarters wrong.
#:
#: Widening by one cell at 0.1 deg makes the target a ~22 km band, which is the
#: scale a fisherman can actually steer to. Both the model AND the persistence
#: baseline are scored against the same widened target, so this makes the task
#: better posed without making the comparison flattering.
FRONT_DILATION = 1


def label_from_sst(sst: np.ndarray, *, dilate: int = FRONT_DILATION) -> np.ndarray:
    """The SIED front zone for one observed day — the supervision signal.

    Imported lazily so this module can be read (and its metadata served) on a
    machine where the science stack is present but torch is not.
    """
    from scipy import ndimage

    from orca.science import fronts

    mask = fronts.sied_fronts(sst).mask
    if dilate > 0:
        mask = ndimage.binary_dilation(mask, iterations=dilate)
    return mask.astype(np.float32)


# ----------------------------------------------------------------- the network


def _build_torch_model(channels: int = 32):
    """Construct the network. Imports torch lazily — ORCA runs without it."""
    import torch
    from torch import nn

    class DoubleConv(nn.Module):
        def __init__(self, cin: int, cout: int) -> None:
            super().__init__()
            self.block = nn.Sequential(
                nn.Conv2d(cin, cout, 3, padding=1),
                nn.GroupNorm(4, cout),
                nn.GELU(),
                nn.Conv2d(cout, cout, 3, padding=1),
                nn.GroupNorm(4, cout),
                nn.GELU(),
            )

        def forward(self, x):
            return self.block(x)

    class TemporalTransformer(nn.Module):
        """Attention across the day axis, applied per spatial location.

        The spatial dimensions are folded into the batch so each pixel's
        five-day history is one sequence. That keeps the attention matrix
        5x5 instead of (5*H*W)x(5*H*W), which is the difference between a model
        that trains on a laptop and one that does not — and full spatio-temporal
        attention is not needed here because the convolutional encoder has
        already mixed information spatially.
        """

        def __init__(self, dim: int, heads: int = 4, days: int = HISTORY_DAYS) -> None:
            super().__init__()
            self.attn = nn.MultiheadAttention(dim, heads, batch_first=True)
            self.norm1 = nn.LayerNorm(dim)
            self.norm2 = nn.LayerNorm(dim)
            self.ff = nn.Sequential(nn.Linear(dim, dim * 2), nn.GELU(), nn.Linear(dim * 2, dim))
            # Learned position per day. Recency is not a symmetric property, and
            # without this the attention cannot tell yesterday from last week.
            self.day_embedding = nn.Parameter(torch.zeros(days, dim))
            nn.init.normal_(self.day_embedding, std=0.02)

        def forward(self, x):
            # x: (B, T, C, H, W) -> (B*H*W, T, C)
            b, t, c, h, w = x.shape
            seq = x.permute(0, 3, 4, 1, 2).reshape(b * h * w, t, c)
            seq = seq + self.day_embedding[:t].unsqueeze(0)
            normed = self.norm1(seq)
            attended, _ = self.attn(normed, normed, normed, need_weights=False)
            seq = seq + attended
            seq = seq + self.ff(self.norm2(seq))
            # Collapse the day axis by taking the most recent step's
            # representation, which attention has now enriched with the whole
            # history. Mean-pooling here blurred moving fronts into a smear.
            out = seq[:, -1, :]
            return out.reshape(b, h, w, c).permute(0, 3, 1, 2)

    class FrontCastNet(nn.Module):
        def __init__(self, cin: int = len(INPUT_CHANNELS), base: int = channels) -> None:
            super().__init__()
            self.enc1 = DoubleConv(cin, base)
            self.pool = nn.MaxPool2d(2)
            self.enc2 = DoubleConv(base, base * 2)
            self.temporal = TemporalTransformer(base * 2)
            self.bottleneck = DoubleConv(base * 2, base * 2)
            self.up = nn.Upsample(scale_factor=2, mode="bilinear", align_corners=False)
            self.dec1 = DoubleConv(base * 2 + base, base)
            # One head per lead time. Independent sigmoids, because a pixel can
            # be a front on all three days at once.
            self.heads = nn.ModuleList([nn.Conv2d(base, 1, 1) for _ in LEAD_DAYS])

        def forward(self, x):
            # x: (B, T, C, H, W)
            b, t = x.shape[0], x.shape[1]
            flat = x.reshape(b * t, *x.shape[2:])
            s1 = self.enc1(flat)
            s2 = self.enc2(self.pool(s1))
            # Only the deep features go through time; the skip connection keeps
            # the most recent day's detail, which is what the decoder needs to
            # place a front precisely.
            s2 = s2.reshape(b, t, *s2.shape[1:])
            fused = self.temporal(s2)
            fused = self.bottleneck(fused)
            skip = s1.reshape(b, t, *s1.shape[1:])[:, -1]
            dec = self.dec1(torch.cat([self.up(fused), skip], dim=1))
            return torch.cat([head(dec) for head in self.heads], dim=1)

    return FrontCastNet()


# ------------------------------------------------------------------- inference


@dataclass(slots=True)
class FrontCastPrediction:
    """Front probability fields, one per lead time."""

    #: lead-day -> (H, W) probability in [0, 1]
    probability: dict[int, np.ndarray]
    version: str
    report: dict[str, Any] | None
    #: Fraction of the field that carried a real observation. A prediction over a
    #: mostly-cloudy field is not worth the same as one over a clear field, and
    #: the caller is told rather than left to assume.
    observed_fraction: float

    def describe(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "lead_days": sorted(self.probability),
            "observed_fraction": round(self.observed_fraction, 4),
            "training_report": self.report,
        }


class FrontCast:
    """Loads the weights once and predicts. Safe to construct when torch is absent."""

    def __init__(self, weights: Path | None = None) -> None:
        self.weights_path = weights or default_weights_path()
        self._model: Any = None
        self._report: dict[str, Any] | None = None
        self._load_error: str | None = None

    # -- availability -----------------------------------------------------

    @property
    def available(self) -> bool:
        return self.weights_path.exists() and _torch_available()

    def status(self) -> dict[str, Any]:
        """Why the model can or cannot run, in terms a UI can show verbatim."""
        report = self.report()
        return {
            "version": _version(),
            "torch_installed": _torch_available(),
            "weights_present": self.weights_path.exists(),
            "weights_path": str(self.weights_path),
            "ready": self.available,
            "load_error": self._load_error,
            "history_days": HISTORY_DAYS,
            "lead_days": list(LEAD_DAYS),
            "input_channels": list(INPUT_CHANNELS),
            "training_report": report,
            "label_source": (
                "Cayula-Cornillon SIED applied to the observed SST field at the target day. "
                "The model inherits SIED's definition of a front, including its blind spots — "
                "it can be better about WHEN a front will be, never about what one is."
            ),
            "not_a_verdict": (
                "A front is a fishing signal, not a hazard threshold. FrontCast cannot move a "
                "GO/NO-GO; the deterministic rule engine does that and does not consult it."
            ),
        }

    def report(self) -> dict[str, Any] | None:
        if self._report is None:
            path = self.weights_path.with_suffix(".json")
            if path.exists():
                try:
                    self._report = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    log.warning("frontcast: unreadable training report: %s", exc)
        return self._report

    # -- prediction -------------------------------------------------------

    def _ensure_loaded(self) -> Any:
        if self._model is not None:
            return self._model
        import torch

        model = _build_torch_model()
        state = torch.load(self.weights_path, map_location="cpu", weights_only=True)
        model.load_state_dict(state)
        model.eval()
        self._model = model
        return model

    def predict(self, history: list[np.ndarray]) -> FrontCastPrediction:
        """Predict front probability from the most recent ``HISTORY_DAYS`` SST fields.

        ``history`` is oldest-first. Shorter histories are left-padded by
        repeating the earliest field, so a source that is missing a day degrades
        instead of refusing — the day embedding still tells the transformer which
        step is which.
        """
        if not history:
            raise ValueError("frontcast needs at least one SST field")
        if not self.available:
            raise FrontCastUnavailable(
                self._load_error or "FrontCast weights are not present on this machine"
            )

        import torch

        frames = list(history)[-HISTORY_DAYS:]
        while len(frames) < HISTORY_DAYS:
            frames.insert(0, frames[0])

        features = np.stack([to_features(f) for f in frames])  # (T, C, H, W)
        observed = float(np.isfinite(frames[-1]).mean())
        model = self._ensure_loaded()

        _, _, height, width = features.shape
        accum = np.zeros((len(LEAD_DAYS), height, width), dtype=np.float32)
        weight = np.zeros((height, width), dtype=np.float32)
        window = _hann2d(TILE)
        step = TILE - TILE_OVERLAP

        with torch.no_grad():
            for top in _tile_starts(height, TILE, step):
                for left in _tile_starts(width, TILE, step):
                    patch = features[:, :, top : top + TILE, left : left + TILE]
                    tensor = torch.from_numpy(patch).unsqueeze(0)
                    logits = model(tensor)[0].numpy()
                    probs = 1.0 / (1.0 + np.exp(-logits))
                    accum[:, top : top + TILE, left : left + TILE] += probs * window
                    weight[top : top + TILE, left : left + TILE] += window

        accum /= np.maximum(weight, 1e-6)
        # A pixel with no observation on the most recent day gets no prediction.
        # Interpolating one would be the model inventing a front over cloud.
        latest_valid = np.isfinite(frames[-1])
        probability = {}
        for index, lead in enumerate(LEAD_DAYS):
            layer = accum[index]
            layer[~latest_valid] = np.nan
            probability[lead] = layer

        return FrontCastPrediction(
            probability=probability,
            version=_version(),
            report=self.report(),
            observed_fraction=observed,
        )


class FrontCastUnavailable(RuntimeError):
    """The model cannot run here. Carries the reason, for display."""


# ------------------------------------------------------------------- helpers


def _version() -> str:
    from orca.ml import FRONTCAST_VERSION

    return FRONTCAST_VERSION


def _torch_available() -> bool:
    try:
        import torch  # noqa: F401
    except ImportError:
        return False
    return True


def default_weights_path() -> Path:
    from orca.config import get_settings

    return get_settings().data_dir / "models" / "frontcast.pt"


def _tile_starts(extent: int, tile: int, step: int) -> list[int]:
    """Tile origins covering ``extent``, with the last tile flush to the edge."""
    if extent <= tile:
        return [0]
    starts = list(range(0, extent - tile + 1, step))
    if starts[-1] != extent - tile:
        starts.append(extent - tile)
    return starts


def _hann2d(size: int) -> np.ndarray:
    """A separable Hann window, for blending overlapping tiles.

    Weighting the seam rather than butt-joining it: a convolutional model is
    least reliable at a tile edge, where half its receptive field is padding, and
    an unweighted join leaves a visible grid of discontinuities across the map.
    """
    w = np.hanning(size + 2)[1:-1]
    w = np.maximum(w, 1e-3)
    return np.outer(w, w).astype(np.float32)


#: Process-wide instance. Loading the weights is the expensive part and they are
#: read-only, so one instance is shared rather than reloaded per request.
frontcast = FrontCast()
