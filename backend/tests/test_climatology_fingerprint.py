"""Determinism tests for ``Climatology.fingerprint``.

The fingerprint is the canonical reproducibility identifier for the
pre-computed Hobday climatology zarr. Reviewers verify it matches the
hex pinned in ``docs/reproducibility.md`` to detect dependency drift,
fixture mutation, or scientific-code regression.

The contract these tests pin down:

* **Idempotent build → identical fingerprint.** Calling ``from_arrays``
  twice with byte-identical inputs MUST yield the same hex.
* **Value sensitivity.** Mutating a single cell in either ``seas`` or
  ``thresh`` MUST change the fingerprint.
* **Attrs sensitivity (sans ``created_utc``).** Changing a recorded
  parameter (``pctile``, ``clim_start``, …) MUST change the fingerprint.
* **``created_utc`` is excluded** so two bootstrap runs of the same
  source data produce the same fingerprint. Without this, the hex would
  be useless as a long-term reference value.
"""

from __future__ import annotations

import numpy as np

from app.climatology import DOY_LEN, Climatology


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _build(seed: int = 0) -> Climatology:
    """Build a small deterministic Climatology from a fixed seed.

    A 4-lat × 5-lon grid keeps the fingerprint cost trivial while still
    exercising the multi-element ``tobytes()`` path that the digest relies on.
    """
    rng = np.random.default_rng(seed)
    seas = rng.uniform(15.0, 25.0, size=(DOY_LEN, 4, 5)).astype("float32")
    thresh = (seas + 2.0).astype("float32")
    lats = np.linspace(40.0, 41.0, 4, dtype="float32")
    lons = np.linspace(10.0, 11.0, 5, dtype="float32")
    attrs = {
        "clim_start": 1991,
        "clim_end": 2020,
        "pctile": 90.0,
        "window_half_width": 5,
        "smooth_width": 31,
        "source_dataset": "test-fixture",
        "grid_resolution": "0.25",
        "bbox": [10.0, 40.0, 11.0, 41.0],
    }
    return Climatology.from_arrays(seas, thresh, lats, lons, attrs=attrs)


# ---------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------
def test_fingerprint_is_deterministic_across_rebuilds() -> None:
    """Two ``from_arrays`` calls with identical inputs MUST hash equal."""
    a = _build()
    b = _build()
    fp_a = a.fingerprint()
    fp_b = b.fingerprint()
    assert fp_a == fp_b, (
        f"Idempotent build produced different fingerprints:\n  {fp_a}\n  {fp_b}"
    )
    # Sanity: the hex is the canonical SHA-256 length.
    assert len(fp_a) == 64
    assert all(c in "0123456789abcdef" for c in fp_a)


def test_fingerprint_excludes_created_utc() -> None:
    """``created_utc`` is wall-clock and MUST NOT influence the hash.

    This is what makes the fingerprint useful as a long-term reference: two
    bootstrap runs over the same source data, hours or weeks apart, must tie
    out at the digest level.
    """
    a = _build()
    b = _build()
    b.attrs["created_utc"] = "2099-01-01T00:00:00+00:00"
    assert a.fingerprint() == b.fingerprint(), (
        "fingerprint() must ignore the created_utc attr"
    )


# ---------------------------------------------------------------------
# Value sensitivity
# ---------------------------------------------------------------------
def test_fingerprint_changes_when_seas_mutates() -> None:
    """A single-cell mutation in ``seas`` MUST flip the fingerprint."""
    a = _build()
    fp_before = a.fingerprint()
    # Bump one cell — has to be done on a writable copy because xarray's
    # underlying ndarray is shared via view.
    seas_arr = a.seas.values.copy()
    seas_arr[100, 2, 3] += 0.5
    mutated = Climatology.from_arrays(
        seas_arr,
        a.thresh.values.copy(),
        a.seas["latitude"].values,
        a.seas["longitude"].values,
        attrs=dict(a.attrs),
    )
    fp_after = mutated.fingerprint()
    assert fp_before != fp_after, "mutating a seas cell must change the fingerprint"


def test_fingerprint_changes_when_thresh_mutates() -> None:
    """A single-cell mutation in ``thresh`` MUST also flip the fingerprint."""
    a = _build()
    fp_before = a.fingerprint()
    thresh_arr = a.thresh.values.copy()
    thresh_arr[42, 0, 0] -= 0.25
    mutated = Climatology.from_arrays(
        a.seas.values.copy(),
        thresh_arr,
        a.seas["latitude"].values,
        a.seas["longitude"].values,
        attrs=dict(a.attrs),
    )
    assert fp_before != mutated.fingerprint(), (
        "mutating a thresh cell must change the fingerprint"
    )


def test_fingerprint_changes_when_attrs_mutate() -> None:
    """Recorded build parameters are part of the contract — they MUST hash in."""
    a = _build()
    b = _build()
    b.attrs["pctile"] = 95.0  # different threshold percentile
    assert a.fingerprint() != b.fingerprint(), (
        "fingerprint() must include build parameters in the digest"
    )
