"""
signal_processing.py – Module 2
================================
Two independent sub-processors operating on streaming sensor data:

  (a) PPG → Heart Rate
      • Neurokit2 Elgendi peak detection on rolling 30 s windows.
      • Signal Quality Index (SQI) derived from RR-interval regularity
        and signal amplitude.  Windows below SQI_THRESHOLD are rejected.
      • HR recomputed every HR_UPDATE_SAMPLES samples (every 5 s).

  (b) Accelerometer → Tremor
      • Causal 2nd-order Butterworth high-pass filter at 1.5 Hz removes
        gravity without phase lag accumulation.
      • Tremor RMS of the filtered 3-axis magnitude.
      • 8–12 Hz band power via scipy.signal.welch (Hann window,
        nperseg = 256, 50 % overlap).
      • Windows computed every TREMOR_UPDATE_SAMPLES samples (every 2.5 s,
        i.e. 50 % overlap over 5 s windows).

All buffers are fixed-length deques; no heap growth at runtime.

IMPORTANT – motion artifact policy
-----------------------------------
Both HR and tremor measurements require enforced rest periods.  Motion
corrupts PPG and inflates tremor simultaneously, making it impossible to
distinguish caffeine-induced tremor from movement artifact.  The
concentration estimator must discard any HR or tremor window for which
the raw tremor_rms exceeds a configured rest-motion threshold (set in
calibration.py).  SQI already penalises corrupted PPG windows, but the
caller should also apply an accel-magnitude gate before trusting HR.

Usage
-----
    proc = SignalProcessor(fs=100)

    # Call once per sample from the ingestion queue
    hr_result, tremor_result = proc.add_sample(
        ppg=raw_ppg, ax=ax_mss, ay=ay_mss, az=az_mss
    )
    if hr_result and hr_result.valid:
        print(f"HR = {hr_result.hr_bpm:.1f} bpm  SQI={hr_result.sqi:.2f}")
    if tremor_result:
        print(f"RMS={tremor_result.rms:.4f}  band_power={tremor_result.band_power_8_12:.6f}")
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
import scipy.signal as ss
import neurokit2 as nk
import pandas as pd

# ── Constants ─────────────────────────────────────────────────────────────────
FS: int = 100  # Hz — must match firmware ODR

# PPG
PPG_WINDOW_SAMPLES: int = 30 * FS      # 3 000 samples = 30 s
HR_UPDATE_SAMPLES:  int =  5 * FS      # recompute HR every 5 s
SQI_THRESHOLD:      float = 0.40       # windows below this are marked invalid
MIN_PPG_AMPLITUDE:  int   = 500        # raw ADC counts; reject contact-loss

# Accelerometer
TREMOR_WINDOW_SAMPLES:  int = 5 * FS       # 500 samples = 5 s
TREMOR_UPDATE_SAMPLES:  int = int(2.5 * FS)  # 50 % overlap → every 2.5 s
HP_CUTOFF_HZ:           float = 1.5       # gravity-removal high-pass cutoff

# Welch PSD
WELCH_NPERSEG: int = 256   # 2.56 s sub-window at 100 Hz


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class HRResult:
    """Heart-rate extraction result from a 30 s PPG window."""
    hr_bpm: float        # beats per minute
    sqi: float           # 0–1 signal quality index
    valid: bool          # True if sqi ≥ SQI_THRESHOLD and hr in physiological range
    n_peaks: int = 0     # number of peaks detected in the window


@dataclass
class TremorResult:
    """Tremor analysis result from a 5 s accelerometer window."""
    rms: float                # m/s² RMS of gravity-free accel magnitude
    band_power_8_12: float    # (m/s²)²/Hz, integral of Welch PSD 8–12 Hz
    valid: bool = True        # always True unless window was too short


# ── Main class ────────────────────────────────────────────────────────────────

class SignalProcessor:
    """
    Streaming signal processor.  Feed samples one at a time via add_sample();
    results are returned only when a full window fires.

    Parameters
    ----------
    fs : int
        Sampling frequency in Hz (must match the Arduino firmware).
    """

    def __init__(self, fs: int = FS) -> None:
        self.fs = fs

        # ── PPG ──────────────────────────────────────────────────────────────
        self._ppg_buf: deque[float] = deque(maxlen=PPG_WINDOW_SAMPLES)
        self._ppg_counter: int = 0

        # ── Accel (high-pass filtered) ────────────────────────────────────────
        self._ax_buf: deque[float] = deque(maxlen=TREMOR_WINDOW_SAMPLES)
        self._ay_buf: deque[float] = deque(maxlen=TREMOR_WINDOW_SAMPLES)
        self._az_buf: deque[float] = deque(maxlen=TREMOR_WINDOW_SAMPLES)
        self._accel_counter: int = 0

        # Causal 2nd-order Butterworth HP filter for gravity removal
        self._hp_sos = ss.butter(
            2, HP_CUTOFF_HZ / (fs / 2), btype='high', output='sos'
        )
        zi_proto = ss.sosfilt_zi(self._hp_sos)  # shape (n_sections, 2)
        self._hp_zi_x: np.ndarray = zi_proto.copy()
        self._hp_zi_y: np.ndarray = zi_proto.copy()
        self._hp_zi_z: np.ndarray = zi_proto.copy()
        self._filter_seeded: bool = False

        # ── Cached results ────────────────────────────────────────────────────
        self.latest_hr: Optional[HRResult] = None
        self.latest_tremor: Optional[TremorResult] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def add_sample(
        self,
        ppg: int,
        ax: float,
        ay: float,
        az: float,
    ) -> Tuple[Optional[HRResult], Optional[TremorResult]]:
        """
        Ingest one sensor sample.

        Parameters
        ----------
        ppg : int
            Raw MAX30102 ADC value.
        ax, ay, az : float
            Accelerometer readings in m/s² (gravity included; filter removes it).

        Returns
        -------
        (HRResult | None, TremorResult | None)
            Non-None values are produced only when the corresponding window fires.
        """
        # High-pass filter accel sample-by-sample (causal, no future samples)
        ax_f, ay_f, az_f = self._hp_filter(ax, ay, az)

        self._ppg_buf.append(float(ppg))
        self._ax_buf.append(ax_f)
        self._ay_buf.append(ay_f)
        self._az_buf.append(az_f)

        self._ppg_counter   += 1
        self._accel_counter += 1

        hr_result:     Optional[HRResult]     = None
        tremor_result: Optional[TremorResult] = None

        # Fire HR every HR_UPDATE_SAMPLES once we have a full window
        if (
            self._ppg_counter % HR_UPDATE_SAMPLES == 0
            and len(self._ppg_buf) == PPG_WINDOW_SAMPLES
        ):
            hr_result = self._compute_hr()
            self.latest_hr = hr_result

        # Fire tremor every TREMOR_UPDATE_SAMPLES once we have a full window
        if (
            self._accel_counter % TREMOR_UPDATE_SAMPLES == 0
            and len(self._ax_buf) == TREMOR_WINDOW_SAMPLES
        ):
            tremor_result = self._compute_tremor()
            self.latest_tremor = tremor_result

        return hr_result, tremor_result

    def reset_buffers(self) -> None:
        """Clear all sample buffers (call between calibration phases)."""
        self._ppg_buf.clear()
        self._ax_buf.clear()
        self._ay_buf.clear()
        self._az_buf.clear()
        self._ppg_counter   = 0
        self._accel_counter = 0
        # Reset filter state on next sample
        self._filter_seeded = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _hp_filter(
        self, ax: float, ay: float, az: float
    ) -> Tuple[float, float, float]:
        """
        Apply the causal high-pass filter to one accel sample.

        On the first call, the filter state is initialised with the DC value of
        the first sample so the transient ring-down doesn't corrupt the buffer.
        """
        if not self._filter_seeded:
            zi_proto = ss.sosfilt_zi(self._hp_sos)
            self._hp_zi_x = zi_proto * ax
            self._hp_zi_y = zi_proto * ay
            self._hp_zi_z = zi_proto * az
            self._filter_seeded = True

        ax_f, self._hp_zi_x = ss.sosfilt(self._hp_sos, [ax], zi=self._hp_zi_x)
        ay_f, self._hp_zi_y = ss.sosfilt(self._hp_sos, [ay], zi=self._hp_zi_y)
        az_f, self._hp_zi_z = ss.sosfilt(self._hp_sos, [az], zi=self._hp_zi_z)

        return float(ax_f[0]), float(ay_f[0]), float(az_f[0])

    # ── PPG / HR ──────────────────────────────────────────────────────────────

    def _compute_hr(self) -> HRResult:
        """
        Run the Elgendi PPG peak detector on the current 30 s window and
        return a heart-rate estimate with quality annotation.
        """
        ppg = np.array(self._ppg_buf, dtype=np.float64)

        # Amplitude gate — flat-line or contact-loss signal
        if np.ptp(ppg) < MIN_PPG_AMPLITUDE:
            return HRResult(hr_bpm=0.0, sqi=0.0, valid=False)

        try:
            ppg_signals, info = nk.ppg_process(
                ppg, sampling_rate=self.fs, method='elgendi'
            )
        except Exception:
            return HRResult(hr_bpm=0.0, sqi=0.0, valid=False)

        # Instantaneous heart rate (neurokit2 interpolates between peaks)
        hr_series: pd.Series = ppg_signals.get('PPG_Rate', pd.Series(dtype=float))
        if hr_series.empty:
            return HRResult(hr_bpm=0.0, sqi=0.0, valid=False)

        hr_bpm = float(hr_series.iloc[-1])

        # Physiological range gate
        if not (30 < hr_bpm < 220):
            return HRResult(hr_bpm=hr_bpm, sqi=0.0, valid=False)

        peaks: np.ndarray = info.get('PPG_Peaks', np.array([], dtype=int))
        sqi = self._compute_sqi(ppg, peaks)
        valid = sqi >= SQI_THRESHOLD

        return HRResult(hr_bpm=hr_bpm, sqi=sqi, valid=valid, n_peaks=len(peaks))

    def _compute_sqi(self, ppg: np.ndarray, peaks: np.ndarray) -> float:
        """
        Signal Quality Index in [0, 1].

        Combines:
        1. Inverse coefficient of variation of RR intervals (regularity).
        2. Amplitude normalised to MIN_PPG_AMPLITUDE (contact quality).

        Lower is worse.  Windows below SQI_THRESHOLD should be discarded.
        """
        if len(peaks) < 3:
            return 0.0

        rr_samples = np.diff(peaks)           # inter-peak intervals in samples
        if len(rr_samples) < 2:
            return 0.5

        cv = float(np.std(rr_samples) / (np.mean(rr_samples) + 1e-9))
        regularity_score = float(np.clip(1.0 - cv, 0.0, 1.0))

        # Amplitude score
        amp = float(np.ptp(ppg))
        amp_score = float(np.clip(amp / MIN_PPG_AMPLITUDE, 0.0, 1.0))

        # Harmonic mean gives zero if either component is zero
        sqi = 2 * regularity_score * amp_score / (regularity_score + amp_score + 1e-9)
        return float(np.clip(sqi, 0.0, 1.0))

    # ── Accelerometer / Tremor ────────────────────────────────────────────────

    def _compute_tremor(self) -> TremorResult:
        """
        Compute tremor metrics from the current 5 s high-pass filtered accel
        window.

        Returns
        -------
        TremorResult
            rms            : m/s² RMS of the 3-axis gravity-free magnitude.
            band_power_8_12: Welch PSD integral over 8–12 Hz in (m/s²)²/Hz.
        """
        ax = np.array(self._ax_buf, dtype=np.float64)
        ay = np.array(self._ay_buf, dtype=np.float64)
        az = np.array(self._az_buf, dtype=np.float64)

        # Euclidean magnitude of gravity-free acceleration
        mag = np.sqrt(ax ** 2 + ay ** 2 + az ** 2)

        # Total RMS
        rms = float(np.sqrt(np.mean(mag ** 2)))

        # Welch power spectral density
        n = len(mag)
        nperseg = min(WELCH_NPERSEG, n // 2)
        freqs, psd = ss.welch(
            mag,
            fs=self.fs,
            nperseg=nperseg,
            noverlap=nperseg // 2,
            window='hann',
            average='mean',
        )

        # Integrate PSD over 8–12 Hz band (np.trapz removed in NumPy 2.0)
        _trapz = getattr(np, 'trapezoid', getattr(np, 'trapz', None))
        mask = (freqs >= 8.0) & (freqs <= 12.0)
        if mask.any():
            band_power = float(_trapz(psd[mask], freqs[mask]))
        else:
            band_power = 0.0

        return TremorResult(rms=rms, band_power_8_12=band_power, valid=True)

    # ── Utility: batch processing (for replay / offline analysis) ─────────────

    @classmethod
    def process_dataframe(
        cls,
        df: 'pd.DataFrame',
        fs: int = FS,
    ) -> 'pd.DataFrame':
        """
        Process an entire session DataFrame offline and return a new DataFrame
        with HR and tremor columns appended at the sample level (forward-filled
        between window outputs).

        Expects columns: ppg, ax, ay, az.

        Returns a copy of *df* with additional columns:
            hr_bpm, hr_sqi, hr_valid, tremor_rms, band_power_8_12
        """
        proc = cls(fs=fs)
        hr_bpms, hr_sqis, hr_valids = [], [], []
        tremor_rms_vals, band_power_vals = [], []

        last_hr = HRResult(0.0, 0.0, False)
        last_tr = TremorResult(0.0, 0.0, False)

        for _, row in df.iterrows():
            hr_res, tr_res = proc.add_sample(
                ppg=int(row['ppg']),
                ax=float(row['ax']),
                ay=float(row['ay']),
                az=float(row['az']),
            )
            if hr_res is not None:
                last_hr = hr_res
            if tr_res is not None:
                last_tr = tr_res

            hr_bpms.append(last_hr.hr_bpm)
            hr_sqis.append(last_hr.sqi)
            hr_valids.append(last_hr.valid)
            tremor_rms_vals.append(last_tr.rms)
            band_power_vals.append(last_tr.band_power_8_12)

        out = df.copy()
        out['hr_bpm']          = hr_bpms
        out['hr_sqi']          = hr_sqis
        out['hr_valid']        = hr_valids
        out['tremor_rms']      = tremor_rms_vals
        out['band_power_8_12'] = band_power_vals
        return out
