"""FrontCast — the feature engineering, and the claims the model makes about itself.

The network's weights are not asserted here; a trained model's skill lives in its
training report, beside the persistence baseline it must beat. What IS pinned is
everything a wrong answer could hide behind: that a missing observation does not
read as a cold anomaly, that the label definition is the one documented, and that
the model cannot reach the safety verdict.
"""

from __future__ import annotations

import numpy as np
import pytest

from orca.ml import frontcast


def synthetic_front(height: int = 64, width: int = 64, jump: float = 2.0) -> np.ndarray:
    """A field with one sharp north-south temperature step — a textbook front."""
    field = np.full((height, width), 28.0, dtype=np.float32)
    field[:, width // 2 :] += jump
    return field


class TestFeatures:
    def test_shape_and_channels(self) -> None:
        features = frontcast.to_features(synthetic_front())
        assert features.shape == (len(frontcast.INPUT_CHANNELS), 64, 64)
        assert features.dtype == np.float32

    def test_an_absent_pixel_reads_as_the_median_not_as_cold(self) -> None:
        """The single most likely way this model could produce confident
        nonsense is by treating a cloud edge as a temperature edge. A NaN must
        become 'equal to the field median', which is zero anomaly — not a large
        negative excursion the convolutions would see as a front."""
        field = synthetic_front()
        field[10:20, 10:20] = np.nan
        features = frontcast.to_features(field)
        anomaly, _, mask = features
        assert np.allclose(anomaly[10:20, 10:20], 0.0)
        assert np.allclose(mask[10:20, 10:20], 0.0), "the mask must mark it absent"
        assert mask.mean() < 1.0 and mask.max() == 1.0

    def test_the_mask_channel_distinguishes_no_data_from_flat_water(self) -> None:
        """Uniform water and absent water both have zero anomaly. Only the mask
        separates them, which is why it is an input rather than a nuisance."""
        flat = frontcast.to_features(np.full((32, 32), 29.0, dtype=np.float32))
        absent = frontcast.to_features(np.full((32, 32), np.nan, dtype=np.float32))
        assert np.allclose(flat[0], absent[0]), "both are zero anomaly"
        assert not np.allclose(flat[2], absent[2]), "the mask must tell them apart"

    def test_a_sharp_step_produces_a_gradient_ridge(self) -> None:
        features = frontcast.to_features(synthetic_front(jump=3.0))
        gradient = features[1]
        column = gradient.shape[1] // 2
        assert gradient[:, column - 1 : column + 1].max() > gradient[:, :5].max()

    def test_an_entirely_absent_field_does_not_raise(self) -> None:
        """A fully clouded day must degrade, not crash the whole history."""
        features = frontcast.to_features(np.full((16, 16), np.nan, dtype=np.float32))
        assert features.shape[0] == 3
        assert np.allclose(features, 0.0)

    def test_normalisation_is_absolute_not_per_field(self) -> None:
        """Per-field standardisation would rescale a calm day to look exactly
        like a frontal one, which is precisely the distinction the model needs."""
        calm = frontcast.to_features(synthetic_front(jump=0.2))
        sharp = frontcast.to_features(synthetic_front(jump=4.0))
        assert np.abs(sharp[0]).max() > np.abs(calm[0]).max() * 4


class TestLabels:
    def test_the_label_is_the_widened_sied_mask(self) -> None:
        """A front is a ZONE. Which pixel inside it SIED picks is close to
        arbitrary and moves between consecutive days, so the target is widened —
        identically for the persistence baseline, so the comparison stays fair."""
        field = _noisy_front()
        raw = frontcast.label_from_sst(field, dilate=0)
        widened = frontcast.label_from_sst(field, dilate=1)
        assert widened.sum() >= raw.sum()
        assert set(np.unique(widened)) <= {0.0, 1.0}

    def test_dilation_default_matches_the_documented_constant(self) -> None:
        field = _noisy_front()
        assert np.array_equal(
            frontcast.label_from_sst(field),
            frontcast.label_from_sst(field, dilate=frontcast.FRONT_DILATION),
        )


class TestTiling:
    def test_the_last_tile_is_flush_with_the_edge(self) -> None:
        """Otherwise a strip along the bottom and right of every prediction gets
        no coverage at all."""
        starts = frontcast._tile_starts(250, 64, 48)
        assert starts[0] == 0
        assert starts[-1] == 250 - 64

    def test_a_field_smaller_than_one_tile_still_yields_a_tile(self) -> None:
        assert frontcast._tile_starts(40, 64, 48) == [0]

    def test_the_blend_window_is_never_zero(self) -> None:
        """A Hann window is zero at both ends; left as-is, the outermost row of
        every tile would be divided by a zero weight."""
        window = frontcast._hann2d(64)
        assert window.min() > 0.0
        assert window.max() <= 1.0
        assert window[32, 32] > window[0, 0]


class TestSelfDescription:
    def test_status_is_readable_without_torch_or_weights(self) -> None:
        """The researcher panel must be able to explain why a model is missing.
        A status call that itself requires the model would leave an empty box."""
        status = frontcast.FrontCast(weights=frontcast.Path("nope.pt")).status()
        assert status["ready"] is False
        assert status["weights_present"] is False
        assert isinstance(status["torch_installed"], bool)
        assert status["lead_days"] == list(frontcast.LEAD_DAYS)

    def test_status_states_where_the_labels_came_from(self) -> None:
        """The model inherits SIED's definition of a front. Serving a prediction
        without saying so implies it learned fronts from nature."""
        status = frontcast.FrontCast(weights=frontcast.Path("nope.pt")).status()
        assert "SIED" in status["label_source"]
        assert "blind spots" in status["label_source"]

    def test_status_states_that_it_cannot_move_a_verdict(self) -> None:
        status = frontcast.FrontCast(weights=frontcast.Path("nope.pt")).status()
        assert "GO/NO-GO" in status["not_a_verdict"]

    def test_predicting_without_weights_raises_a_readable_error(self) -> None:
        model = frontcast.FrontCast(weights=frontcast.Path("nope.pt"))
        with pytest.raises(frontcast.FrontCastUnavailable):
            model.predict([synthetic_front()])

    def test_predicting_with_no_history_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            frontcast.frontcast.predict([])


class TestHeadsAreIndependent:
    def test_there_is_one_head_per_lead_time(self) -> None:
        """Independent sigmoids, not a softmax: a pixel can be a front on all
        three days at once, and a softmax would force the lead times to compete
        for one unit of probability — making persistence unrepresentable."""
        torch = pytest.importorskip("torch")
        model = frontcast._build_torch_model()
        assert len(model.heads) == len(frontcast.LEAD_DAYS)
        with torch.no_grad():
            out = model(torch.zeros(1, frontcast.HISTORY_DAYS, 3, 64, 64))
        assert out.shape[1] == len(frontcast.LEAD_DAYS)

    def test_the_temporal_block_distinguishes_day_order(self) -> None:
        """Recency is not symmetric. Without a per-day embedding the attention
        cannot tell yesterday from last week, and the model degenerates into a
        detector over a bag of days."""
        torch = pytest.importorskip("torch")
        model = frontcast._build_torch_model()
        assert model.temporal.day_embedding.shape[0] == frontcast.HISTORY_DAYS
        history = torch.randn(1, frontcast.HISTORY_DAYS, 3, 64, 64)
        with torch.no_grad():
            forward = model(history)
            reversed_ = model(history.flip(dims=[1]))
        assert not torch.allclose(forward, reversed_), (
            "reversing the history must change the forecast"
        )


def _noisy_front() -> np.ndarray:
    """A front with enough texture for SIED's bimodality test to engage."""
    rng = np.random.default_rng(3)
    field = np.full((80, 80), 28.0, dtype=np.float32)
    field[:, 40:] += 1.5
    return field + rng.normal(0, 0.05, field.shape).astype(np.float32)
