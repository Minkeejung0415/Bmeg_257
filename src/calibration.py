"""
calibration.py – Module 4
==========================
Two-phase calibration workflow:

Phase 1 — Per-session resting baseline (REQUIRED before any caffeine)
----------------------------------------------------------------------
Run for 3–5 minutes while the subject sits still.  Computes:
    baseline_hr          : median resting heart rate (BPM)
    baseline_tremor_rms  : median resting tremor RMS (m/s²)

Saved to baseline.json alongside the session file.

Phase 2 — Known-dose calibration (strongly RECOMMENDED)
--------------------------------------------------------
Without a personal slope the population average is used:
    hr_per_mg_L ≈ 1.47 BPM / (mg/L)

Individual variation in caffeine pharmacodynamics is 5–8×, making the
population value produce ±80–150 mg errors.  A single known-dose session
(e.g. 200 mg tablet in fasted state) fits a personal slope that brings
expected MAE below 50 mg.

The personal slope is saved in baseline.json and loaded automatically on
future sessions.  Re-run fit_personal_slope() whenever body composition
or caffeine tolerance changes significantly (e.g. after prolonged abstinence).

Motion artefact gating
-----------------------
The calibration module exposes is_resting_window() which should be called
before any HR or tremor value is forwarded to the concentration estimator.
Any window in which raw (unfiltered) tremor_rms exceeds
rest_motion_threshold_ms2 is silently discarded.

Usage
-----
    cal = Calibration()

    # Phase 1 – collect 3–5 min of resting data
    cal.start_baseline_capture()
    while not cal.baseline_complete:
        hr_result, tremor_result = processor.add_sample(...)
        if hr_result and hr_result.valid:
            cal.add_baseline_sample(hr_result.hr_bpm, tremor_result.rms)
    cal.finalise_baseline(body_weight_kg=72)

    # Phase 2 – optional personal slope from a known-dose session
    cal.fit_personal_slope(known_dose_mg=200, t_hr=times, delta_hr_obs=dHR)

    # Runtime usage
    d_hr  = cal.delta_hr(current_hr)
    d_trm = cal.delta_tremor(current_tremor_rms)
    ok    = cal.is_resting_window(raw_tremor_rms)
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
from scipy.optimize import minimize_scalar

# ── Constants ──────────────────────────────────────────────────────────────────

# Minimum resting-window duration before baseline is considered valid
MIN_BASELINE_SECONDS: float = 20.0   # 30 s

# Expected HR update interval used to convert seconds → sample count
HR_UPDATE_INTERVAL_S: float = 1.0     # signal_processing fires HR every 5 s

# Motion-artefact gate: reject windows with raw tremor RMS above this
DEFAULT_REST_MOTION_THRESHOLD_MS2: float = 0.15   # m/s² — tune per subject

# Population-average transfer function (Graham & Spriet 1995)
# ~3–5 BPM per 100 mg caffeine.  At 70 kg, Vd=0.6 L/kg → Vd_total=42 L
# 100 mg / 42 L ≈ 2.38 mg/L peak → (3.5 BPM) / (2.38 mg/L) ≈ 1.47
POPULATION_HR_PER_MG_L: float = 1.47   # BPM / (mg/L)


# ── Calibration class ─────────────────────────────────────────────────────────

class Calibration:
    """
    Session calibration manager.

    Parameters
    ----------
    baseline_file : str | Path
        JSON file where baseline and personal slope are persisted.
    rest_motion_threshold_ms2 : float
        Raw tremor RMS above which a window is rejected as motion artefact.
    """

    def __init__(
        self,
        baseline_file: str | Path = 'baseline.json',
        rest_motion_threshold_ms2: float = DEFAULT_REST_MOTION_THRESHOLD_MS2,
    ) -> None:
        self._file = Path(baseline_file)
        self.rest_motion_threshold = rest_motion_threshold_ms2

        # ── Persisted calibration state ───────────────────────────────────────
        self.baseline_hr:            Optional[float] = None
        self.baseline_tremor_rms:    Optional[float] = None
        self.hr_per_mg_L:            Optional[float] = None   # personal slope
        self.body_weight_kg:         float           = 70.0
        self.calibration_timestamp:  Optional[str]   = None

        # ── Accumulation buffers for Phase 1 ─────────────────────────────────
        self._hr_samples:     List[float] = []
        self._tremor_samples: List[float] = []
        self._capture_start:  Optional[float] = None
        self._capturing:      bool = False

        self._load()

    # ── Persistence ───────────────────────────────────────────────────────────

    def _load(self) -> None:
        if self._file.exists():
            try:
                with open(self._file) as f:
                    data = json.load(f)
                self.baseline_hr           = data.get('baseline_hr')
                self.baseline_tremor_rms   = data.get('baseline_tremor_rms')
                self.hr_per_mg_L           = data.get('hr_per_mg_L')
                self.body_weight_kg        = data.get('body_weight_kg', 70.0)
                self.calibration_timestamp = data.get('calibration_timestamp')
            except (json.JSONDecodeError, KeyError):
                pass   # Corrupt file — start fresh

    def _save(self) -> None:
        payload = {
            'baseline_hr':          self.baseline_hr,
            'baseline_tremor_rms':  self.baseline_tremor_rms,
            'hr_per_mg_L':          self.hr_per_mg_L,
            'body_weight_kg':       self.body_weight_kg,
            'calibration_timestamp': self.calibration_timestamp,
        }
        with open(self._file, 'w') as f:
            json.dump(payload, f, indent=2)

    # ── Phase 1: baseline capture ─────────────────────────────────────────────

    def start_baseline_capture(self) -> None:
        """
        Begin accumulating resting-state HR and tremor samples.
        Call this once the subject is sitting still.
        """
        self._hr_samples.clear()
        self._tremor_samples.clear()
        self._capture_start = time.monotonic()
        self._capturing = True

    def add_baseline_sample(
        self,
        hr_bpm: float,
        tremor_rms: float,
        raw_tremor_rms: Optional[float] = None,
    ) -> None:
        """
        Add one HR and tremor observation to the baseline accumulator.

        Parameters
        ----------
        hr_bpm : float
            Validated heart-rate estimate from signal_processing.
        tremor_rms : float
            Tremor RMS (high-pass filtered) from signal_processing.
        raw_tremor_rms : float, optional
            If provided, samples are only accepted when raw_tremor_rms is
            below rest_motion_threshold (motion gate).
        """
        if not self._capturing:
            return
        if raw_tremor_rms is not None and raw_tremor_rms > self.rest_motion_threshold:
            return   # motion artefact — discard
        if not (30 < hr_bpm < 120):
            return   # physiologically implausible at rest
        self._hr_samples.append(hr_bpm)
        self._tremor_samples.append(tremor_rms)

    @property
    def baseline_elapsed_s(self) -> float:
        """Seconds elapsed since start_baseline_capture()."""
        if self._capture_start is None:
            return 0.0
        return time.monotonic() - self._capture_start

    @property
    def baseline_complete(self) -> bool:
        """True if enough data has been collected and the baseline is valid."""
        return (
            len(self._hr_samples) >= 6              # at least 6 HR windows
            and self.baseline_elapsed_s >= MIN_BASELINE_SECONDS
        )

    def finalise_baseline(
        self,
        body_weight_kg: Optional[float] = None,
        verbose: bool = True,
    ) -> Tuple[float, float]:
        """
        Compute and persist the baseline from accumulated samples.

        Outlier rejection via 1.5×IQR before computing median.

        Returns
        -------
        (baseline_hr_bpm, baseline_tremor_rms)
        """
        if len(self._hr_samples) < 2:
            raise RuntimeError(
                "Insufficient baseline samples — run start_baseline_capture() "
                "and add_baseline_sample() for at least 3 minutes."
            )

        self._capturing = False

        if body_weight_kg is not None:
            self.body_weight_kg = body_weight_kg

        self.baseline_hr        = _robust_median(self._hr_samples)
        self.baseline_tremor_rms = _robust_median(self._tremor_samples)
        self.calibration_timestamp = datetime.now().isoformat()

        self._save()

        if verbose:
            print(
                f"\n[CALIBRATION] Baseline finalised from "
                f"{len(self._hr_samples)} HR windows and "
                f"{len(self._tremor_samples)} tremor windows."
            )
            print(f"  Resting HR          : {self.baseline_hr:.1f} BPM")
            print(f"  Resting tremor RMS  : {self.baseline_tremor_rms:.5f} m/s²")
            if self.hr_per_mg_L is not None:
                print(f"  Personal slope      : {self.hr_per_mg_L:.4f} BPM/(mg/L)  "
                      "(from previous known-dose session)")
            else:
                print(f"  Personal slope      : NOT SET — using population default "
                      f"({POPULATION_HR_PER_MG_L:.4f} BPM/(mg/L)); "
                      f"expected MAE ±80–150 mg")

        return self.baseline_hr, self.baseline_tremor_rms

    # ── Phase 2: personal slope fitting ──────────────────────────────────────

    def fit_personal_slope(
        self,
        known_dose_mg: float,
        t_hr: np.ndarray,
        delta_hr_obs: np.ndarray,
        food_state: str = 'fasted',
        verbose: bool = True,
    ) -> float:
        """
        Fit a personal BPM-per-(mg/L) transfer function coefficient from a
        controlled known-dose session.

        The model assumes:
            delta_HR(t) = hr_per_mg_L · C_caffeine(t)

        where C_caffeine(t) is predicted from the PK model using the known dose.

        Parameters
        ----------
        known_dose_mg : float
            Actual caffeine dose administered (mg).
        t_hr : array-like
            Time since dose (hours) for each delta-HR observation.
        delta_hr_obs : array-like
            Observed delta HR (HR - baseline_hr) at each time point (BPM).
        food_state : {'fasted', 'fed'}
            Controls PK absorption rate.
        verbose : bool

        Returns
        -------
        float
            Fitted hr_per_mg_L coefficient (BPM per mg/L).
        """
        # Import here to avoid circular dependency
        from pk_model import PKModel

        t_hr         = np.asarray(t_hr,       dtype=float)
        delta_hr_obs = np.asarray(delta_hr_obs, dtype=float)

        pk = PKModel(body_weight_kg=self.body_weight_kg, food_state=food_state)
        C_pred = pk.single_dose_curve(t_hr, known_dose_mg, t0_hr=0.0)

        # Avoid numerical issues when C_pred is near-zero at very early times
        valid = C_pred > 0.05
        if valid.sum() < 3:
            raise ValueError(
                "Not enough post-absorption observations to fit a slope. "
                "Ensure observations start >15 min after dose."
            )

        def mse(slope: float) -> float:
            delta_hr_pred = slope * C_pred[valid]
            return float(np.mean((delta_hr_pred - delta_hr_obs[valid]) ** 2))

        result = minimize_scalar(mse, bounds=(0.01, 30.0), method='bounded')
        self.hr_per_mg_L = float(result.x)
        self._save()

        residuals   = delta_hr_obs[valid] - self.hr_per_mg_L * C_pred[valid]
        rmse        = float(np.sqrt(np.mean(residuals ** 2)))
        r2          = float(1 - np.var(residuals) / (np.var(delta_hr_obs[valid]) + 1e-9))

        if verbose:
            print(f"\n[CALIBRATION] Personal slope fitted:")
            print(f"  Known dose          : {known_dose_mg:.0f} mg ({food_state})")
            print(f"  hr_per_mg_L         : {self.hr_per_mg_L:.4f} BPM/(mg/L)")
            print(f"  Population default  : {POPULATION_HR_PER_MG_L:.4f} BPM/(mg/L)")
            print(f"  Fit RMSE            : {rmse:.3f} BPM")
            print(f"  R²                  : {r2:.3f}")
            print(f"  Saved to            : {self._file}")

        return self.hr_per_mg_L

    # ── Runtime helper methods ────────────────────────────────────────────────

    def delta_hr(self, hr_bpm: float) -> float:
        """
        Compute HR change from resting baseline.

        Raises RuntimeError if baseline has not been captured.
        """
        if self.baseline_hr is None:
            raise RuntimeError(
                "Baseline not set.  Run start_baseline_capture() → "
                "add_baseline_sample() → finalise_baseline() first."
            )
        return hr_bpm - self.baseline_hr

    def delta_tremor(self, tremor_rms: float) -> float:
        """Compute tremor change from resting baseline."""
        if self.baseline_tremor_rms is None:
            raise RuntimeError(
                "Baseline not set.  Run start_baseline_capture() → "
                "add_baseline_sample() → finalise_baseline() first."
            )
        return tremor_rms - self.baseline_tremor_rms

    def is_resting_window(self, raw_tremor_rms: float) -> bool:
        """
        Return True if the current window is motion-free enough to be trusted.

        Use this gate before forwarding HR or tremor values to the estimator.
        """
        return raw_tremor_rms <= self.rest_motion_threshold

    @property
    def effective_hr_per_mg_L(self) -> float:
        """
        Return the personal slope if available, otherwise the population default.
        """
        return self.hr_per_mg_L if self.hr_per_mg_L is not None else POPULATION_HR_PER_MG_L

    @property
    def is_calibrated(self) -> bool:
        """True if a resting baseline has been finalised."""
        return self.baseline_hr is not None and self.baseline_tremor_rms is not None

    @property
    def has_personal_slope(self) -> bool:
        """True if a personal hr_per_mg_L has been fitted."""
        return self.hr_per_mg_L is not None

    def summary(self) -> str:
        """Return a human-readable calibration summary string."""
        lines = ["Calibration summary:"]
        if self.is_calibrated:
            lines.append(f"  Baseline HR         : {self.baseline_hr:.1f} BPM")
            lines.append(f"  Baseline tremor RMS : {self.baseline_tremor_rms:.5f} m/s²")
            lines.append(f"  Captured at         : {self.calibration_timestamp}")
        else:
            lines.append("  *** NO BASELINE — run baseline capture first ***")
        if self.has_personal_slope:
            lines.append(f"  Personal slope      : {self.hr_per_mg_L:.4f} BPM/(mg/L)")
        else:
            lines.append(
                f"  Personal slope      : NOT SET — using population "
                f"default {POPULATION_HR_PER_MG_L:.4f} BPM/(mg/L)"
            )
        return "\n".join(lines)


# ── Utility ───────────────────────────────────────────────────────────────────

def _robust_median(values: List[float]) -> float:
    """
    Compute the median after removing values outside the 1.5×IQR fence
    (Tukey's outer fence).
    """
    arr = np.array(values, dtype=float)
    q1, q3 = np.percentile(arr, [25, 75])
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    clean = arr[(arr >= lower) & (arr <= upper)]
    if len(clean) == 0:
        clean = arr   # fall back to all values if everything is an outlier
    return float(np.median(clean))
