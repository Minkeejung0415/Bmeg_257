"""
concentration.py – Module 5
============================
Maps physiological signals (delta_hr, delta_tremor) to estimated plasma
caffeine concentration C_est(t) and detects new dose events.

Transfer functions from literature
------------------------------------
Graham & Spriet (1995):
    ~3–5 BPM per 100 mg caffeine at rest.  Combined with the PK model
    (Vd = 0.6 L/kg, 70 kg) this gives:
        hr_per_mg_L ≈ 1.47 BPM / (mg/L)   [population average]
    Individual variation is 5–8× — use the personal slope from calibration.py.

Hallett (1998):
    Caffeine significantly increases 8–12 Hz physiological tremor power,
    particularly after 200–400 mg doses.  Used here as a corroborating
    signal to increase confidence in dose-event detection but NOT used
    as a direct concentration estimator (too noisy).

Algorithm
---------
1. Each time a new HR and tremor window arrive, the estimator computes
   delta_hr and delta_tremor from the calibrated baseline.

2. A 15-minute sliding window of delta_hr is maintained.  When the
   smoothed delta_hr rises > DOSE_DETECT_THRESHOLD_BPM above a running
   pre-event baseline AND at least MIN_DOSE_SEPARATION_HR hours have
   elapsed since the last detected dose, a new dose event is triggered.

3. The dose magnitude is estimated by the PK model inverse solver, using
   the peak delta_hr seen in the detection window as the observable.

4. After a dose is registered in the PK model, the PK-model concentration
   C_pk(t) is used as the primary output.  Before any dose is registered,
   the raw transfer-function estimate C_hr = delta_hr / hr_per_mg_L is
   returned.

5. Cumulative daily intake is tracked and reported.

Motion artefact policy
-----------------------
All HR and tremor values passed to update() MUST have already passed the
calibration.is_resting_window() gate.  This module trusts its inputs.
Caller is responsible for gating.

Usage
-----
    estimator = ConcentrationEstimator(calibration, pk_model)

    # Each time signal_processing fires a valid HR window:
    result = estimator.update(
        hr_bpm=72.3, tremor_rms=0.012, band_power_8_12=0.0004,
        wall_time=row.wall_time
    )
    print(result)
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── Constants ──────────────────────────────────────────────────────────────────

# Dose-detection hyper-parameters
DOSE_DETECT_WINDOW_S:       float = 15 * 60   # 15-minute smoothing window
DOSE_DETECT_THRESHOLD_BPM:  float = 1.5       # delta-HR rise above pre-event mean
MIN_DOSE_SEPARATION_HR:     float = 1.0       # minimum hours between detected doses
MIN_DOSE_DETECT_SAMPLES:    int   = 6         # need ≥6 HR observations (~30 min)

# Tremor corroboration: if delta_band_power rises > this times resting, flag it
TREMOR_CORROBORATION_FACTOR: float = 2.0

# Daily intake tracking — warn at this level
DAILY_INTAKE_WARNING_MG: float = 400.0        # FDA daily limit

# History kept for plot/export (1 sample per HR-window ≈ every 5 s)
MAX_HISTORY: int = 10_800   # 15 hours


@dataclass
class ConcentrationResult:
    """Single-timepoint output from the concentration estimator."""
    wall_time:       float          # Unix timestamp
    t_hr:            float          # hours since session start
    delta_hr:        float          # BPM above baseline
    delta_tremor:    float          # m/s² above baseline
    band_power_8_12: float          # 8–12 Hz band power (m/s²)²/Hz
    C_hr_mg_L:       float          # transfer-function estimate
    C_pk_mg_L:       float          # PK-model prediction
    C_est_mg_L:      float          # best estimate (PK if doses known, else HR)
    dose_detected:   bool           # True if a new dose was detected this update
    estimated_dose_mg: float        # mg of the newly detected dose (0 if none)
    doses:           List[Tuple[float, float]]  # all (t_hr, dose_mg) so far
    daily_dose_mg:   float          # cumulative daily caffeine estimate


class ConcentrationEstimator:
    """
    Estimates plasma caffeine concentration from physiological signals.

    Parameters
    ----------
    calibration : Calibration
        Fully initialised Calibration object (baseline must be set).
    pk_model : PKModel
        PK model instance (will have doses added to it in-place).
    session_start_time : float, optional
        Unix timestamp of session start.  Defaults to now.
    """

    def __init__(
        self,
        calibration,    # Calibration instance
        pk_model,       # PKModel instance
        session_start_time: Optional[float] = None,
    ) -> None:
        self._cal = calibration
        self._pk  = pk_model
        self._t0  = session_start_time if session_start_time is not None else time.time()

        # ── Dose-detection state ──────────────────────────────────────────────
        # Rolling 15-min buffer of (wall_time, delta_hr) pairs
        self._dhr_buf: deque[Tuple[float, float]] = deque()

        # Smoothed delta_hr just before the last dose (pre-event baseline)
        self._pre_event_dhr_mean: float = 0.0

        # Timestamp of last detected dose
        self._last_dose_time: float = -np.inf

        # Known dose events: list of (t_hr, dose_mg)
        self._doses: List[Tuple[float, float]] = []
        self.daily_dose_mg: float = 0.0

        # ── History for plotting / export ─────────────────────────────────────
        self.history: deque[ConcentrationResult] = deque(maxlen=MAX_HISTORY)

        # ── Tremor baseline (band power) for corroboration ────────────────────
        self._baseline_band_power: Optional[float] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def update(
        self,
        hr_bpm: float,
        tremor_rms: float,
        band_power_8_12: float,
        wall_time: Optional[float] = None,
    ) -> ConcentrationResult:
        """
        Ingest one processed window and update concentration estimate.

        Parameters
        ----------
        hr_bpm : float
            Heart rate in BPM (already SQI-validated, motion-gated).
        tremor_rms : float
            Tremor RMS in m/s² (high-pass filtered).
        band_power_8_12 : float
            8–12 Hz PSD integral in (m/s²)²/Hz.
        wall_time : float, optional
            Unix timestamp of this observation.  Defaults to now.

        Returns
        -------
        ConcentrationResult
        """
        if wall_time is None:
            wall_time = time.time()

        t_hr = (wall_time - self._t0) / 3600.0

        # ── Compute deltas ────────────────────────────────────────────────────
        d_hr    = self._cal.delta_hr(hr_bpm)
        d_tremor = self._cal.delta_tremor(tremor_rms)

        # ── Seed tremor band-power baseline from first observation ────────────
        if self._baseline_band_power is None:
            self._baseline_band_power = band_power_8_12

        # ── Update dose-detection rolling buffer ──────────────────────────────
        self._dhr_buf.append((wall_time, d_hr))
        # Trim entries older than DOSE_DETECT_WINDOW_S
        cutoff = wall_time - DOSE_DETECT_WINDOW_S
        while self._dhr_buf and self._dhr_buf[0][0] < cutoff:
            self._dhr_buf.popleft()

        # ── Detect new dose ───────────────────────────────────────────────────
        dose_detected    = False
        estimated_dose   = 0.0
        if len(self._dhr_buf) >= MIN_DOSE_DETECT_SAMPLES:
            dose_detected, estimated_dose = self._detect_dose(
                t_hr, d_hr, band_power_8_12, wall_time
            )

        # ── Estimate concentration ────────────────────────────────────────────
        C_hr = max(d_hr / self._cal.effective_hr_per_mg_L, 0.0)
        C_pk = self._pk.concentration_at(t_hr)

        # Use PK model once we have at least one dose registered
        C_est = C_pk if self._doses else C_hr

        # ── Build result ──────────────────────────────────────────────────────
        result = ConcentrationResult(
            wall_time        = wall_time,
            t_hr             = t_hr,
            delta_hr         = d_hr,
            delta_tremor     = d_tremor,
            band_power_8_12  = band_power_8_12,
            C_hr_mg_L        = C_hr,
            C_pk_mg_L        = C_pk,
            C_est_mg_L       = C_est,
            dose_detected    = dose_detected,
            estimated_dose_mg= estimated_dose,
            doses            = list(self._doses),
            daily_dose_mg    = self.daily_dose_mg,
        )
        self.history.append(result)

        if self.daily_dose_mg >= DAILY_INTAKE_WARNING_MG:
            print(
                f"[WARNING] Cumulative daily caffeine ≥ {DAILY_INTAKE_WARNING_MG:.0f} mg "
                f"(current estimate: {self.daily_dose_mg:.0f} mg)",
                flush=True,
            )

        return result

    def add_manual_dose(self, dose_mg: float, t_hr: Optional[float] = None) -> None:
        """
        Manually register a known dose (e.g. when the user presses a key).

        This bypasses automatic detection and sets the PK model running.
        """
        if t_hr is None:
            t_hr = (time.time() - self._t0) / 3600.0
        self._doses.append((t_hr, dose_mg))
        self._pk.add_dose(t_hr, dose_mg)
        self.daily_dose_mg += dose_mg
        self._last_dose_time = self._t0 + t_hr * 3600
        print(
            f"[MANUAL DOSE] +{dose_mg:.0f} mg at t={t_hr:.3f} h  "
            f"(daily total: {self.daily_dose_mg:.0f} mg)",
            flush=True,
        )

    def reset_daily_tracking(self) -> None:
        """Call at midnight to reset daily cumulative dose."""
        self.daily_dose_mg = 0.0
        # Keep PK model doses — they affect current concentration
        print("[TRACKER] Daily dose counter reset to 0.", flush=True)

    # ── Dose detection ────────────────────────────────────────────────────────

    def _detect_dose(
        self,
        t_hr: float,
        delta_hr_current: float,
        band_power: float,
        wall_time: float,
    ) -> Tuple[bool, float]:
        """
        Evaluate whether the current delta_hr trajectory indicates a new dose.

        Returns (dose_detected, estimated_dose_mg).
        """
        # Time since last detection
        elapsed_since_last_hr = (wall_time - self._last_dose_time) / 3600.0
        if elapsed_since_last_hr < MIN_DOSE_SEPARATION_HR:
            return False, 0.0

        dhr_values = np.array([v for _, v in self._dhr_buf])
        current_mean = float(np.mean(dhr_values))

        # Rising delta_hr relative to pre-event level
        rise = current_mean - self._pre_event_dhr_mean

        if rise < DOSE_DETECT_THRESHOLD_BPM:
            # No dose yet — update pre-event mean slowly
            self._pre_event_dhr_mean = 0.9 * self._pre_event_dhr_mean + 0.1 * current_mean
            return False, 0.0

        # Corroborate with tremor band power if available
        tremor_corroborated = (
            self._baseline_band_power is not None
            and band_power > TREMOR_CORROBORATION_FACTOR * self._baseline_band_power
        )

        # Estimate dose magnitude using PK inverse solver
        # Use peak delta_hr in the buffer as the "observed peak concentration"
        peak_dhr = float(np.max(dhr_values))
        peak_C_est = peak_dhr / self._cal.effective_hr_per_mg_L

        # t_peak from PK model: time-to-peak ≈ ln(ka/ke)/(ka-ke)
        tp = self._pk.t_peak()

        # Dose estimate: C_peak = F·dose·ka / (Vd·(ka-ke)) · (e^{-ke·tp} - e^{-ka·tp})
        # Invert to get dose
        ka, ke = self._pk.ka, self._pk.ke
        vd = self._pk.vd_total
        if abs(ka - ke) > 1e-6:
            peak_ratio = (
                ka / (ka - ke)
                * (np.exp(-ke * tp) - np.exp(-ka * tp))
            )
        else:
            peak_ratio = ke * tp * np.exp(-ke * tp)

        if peak_ratio > 1e-6:
            dose_est = float(np.clip(
                peak_C_est * vd / (self._pk.F * peak_ratio),
                25.0, 600.0
            ))
        else:
            dose_est = 100.0   # fallback

        # Register the dose — assume it occurred t_peak hours ago
        t_dose_hr = max(0.0, t_hr - tp)
        self._doses.append((t_dose_hr, dose_est))
        self._pk.add_dose(t_dose_hr, dose_est)
        self.daily_dose_mg += dose_est
        self._last_dose_time = wall_time

        # Reset pre-event baseline
        self._pre_event_dhr_mean = current_mean

        print(
            f"[DOSE DETECTED] t_dose={t_dose_hr:.2f} h  "
            f"estimated={dose_est:.0f} mg  "
            f"tremor_corroborated={tremor_corroborated}  "
            f"daily_total={self.daily_dose_mg:.0f} mg",
            flush=True,
        )
        return True, dose_est

    # ── Export helpers ────────────────────────────────────────────────────────

    def export_history_as_arrays(
        self,
    ) -> Dict[str, np.ndarray]:
        """
        Return all history fields as a dict of numpy arrays for plotting.
        """
        if not self.history:
            return {}
        fields = [
            'wall_time', 't_hr', 'delta_hr', 'delta_tremor',
            'band_power_8_12', 'C_hr_mg_L', 'C_pk_mg_L', 'C_est_mg_L',
            'daily_dose_mg',
        ]
        return {
            f: np.array([getattr(r, f) for r in self.history])
            for f in fields
        }

    def latest_summary(self) -> str:
        """Return a one-line human-readable status string."""
        if not self.history:
            return "No data yet."
        r = self.history[-1]
        n_doses = len(r.doses)
        return (
            f"t={r.t_hr:.2f}h  HR+{r.delta_hr:+.1f}BPM  "
            f"Trm+{r.delta_tremor:+.4f}m/s²  "
            f"C_est={r.C_est_mg_L:.2f}mg/L  "
            f"Doses={n_doses}  Daily≈{r.daily_dose_mg:.0f}mg"
        )
