# Architecture Research: Caffeine Estimation System

**Research type:** Project Research — Architecture dimension
**Date:** 2026-03-12
**Question:** How should the caffeine estimation system be structured architecturally?

---

## Summary

The system is a two-tier pipeline: an Arduino firmware layer that continuously samples two sensors and streams structured data over serial USB, and a PC-side Python pipeline that ingests that stream, processes signals, extracts physiological features, runs a pharmacokinetic model, and produces a dose estimate. The architecture is strictly sequential — firmware must work before the PC pipeline can be built, and baseline calibration must exist before estimation is meaningful.

---

## 1. Arduino Firmware Architecture

### Sensor Polling Rates

**AX6 IMU (accelerometer + gyroscope)**
- Tremor of interest is in the 8–12 Hz band. Nyquist requires sampling at ≥ 24 Hz; practical minimum is 50 Hz to leave margin for filter design.
- Recommended: 50–100 Hz. 100 Hz is achievable on Arduino Uno/Mega over I2C with the AX6 at its standard ODR settings and leaves enough headroom for the MAX30102 read interleaved.
- At 100 Hz, 6 axes × 2 bytes = 12 bytes/sample → 1200 bytes/s for IMU alone. Well within 115200 baud serial capacity.

**MAX30102 (PPG / heart rate)**
- Heart rate changes on the order of seconds; the PPG waveform itself runs at ~1 Hz (one cardiac cycle). To resolve individual pulse peaks reliably, sample at ≥ 25 Hz (25 samples/cardiac cycle at 60 bpm).
- Recommended: 100 Hz (matches the MAX30102's native FIFO rate modes; simplifies firmware by using a single loop tick for both sensors).
- Red + IR channels, 18-bit resolution → 4 bytes/sample/channel → 800 bytes/s for PPG.

**Combined throughput:** ~2000 bytes/s at 100 Hz, comfortably within 115200 baud (≈11500 bytes/s).

### Data Framing and Serial Protocol

Use a lightweight binary or ASCII framed protocol. ASCII CSV is easier to debug during development; binary is more compact for long sessions. Recommendation: start with ASCII, migrate to binary if throughput becomes a bottleneck.

**Recommended ASCII frame:**
```
T:<timestamp_ms>,AX:<ax>,AY:<ay>,AZ:<az>,GX:<gx>,GY:<gy>,GZ:<gz>,IR:<ir>,RED:<red>\n
```

Key requirements:
- **Timestamp**: Arduino `millis()` in milliseconds, included in every frame. The PC clock should not be trusted for sample timing — use the Arduino timestamp to reconstruct sample intervals on the PC side.
- **Framing delimiter**: newline `\n` terminates each frame so the PC can use `readline()` without a length prefix.
- **Sync/start marker**: optional but useful — a `START\n` message when the Arduino boots lets the PC know the stream is fresh.
- **No partial frames**: assemble the full frame in a buffer before writing to Serial in a single `Serial.println()` call to minimise the risk of interleaved bytes.

### Firmware Loop Structure

```
setup():
  init AX6 via I2C (set ODR, range)
  init MAX30102 via I2C (set sample rate, LED current)
  Serial.begin(115200)
  Serial.println("START")

loop():
  if (millis() - last_sample_time >= SAMPLE_INTERVAL_MS):
    read AX6 (ax, ay, az, gx, gy, gz)
    read MAX30102 (ir, red) — drain FIFO, take latest sample
    Serial.println(frame)
    last_sample_time = millis()
```

- `SAMPLE_INTERVAL_MS = 10` for 100 Hz.
- Avoid `delay()` — use non-blocking timing with `millis()` so I2C reads do not compound into drift.
- Both sensors share the I2C bus; AX6 address is 0x6A or 0x6B, MAX30102 is 0x57. No address conflict.

---

## 2. PC-Side Pipeline Architecture

The PC pipeline is a linear chain of stages. Each stage has a single responsibility and a well-defined input/output contract.

```
Serial Port
    |
    v
[1. Serial Ingestion]
    - Reads lines from COM port at 115200 baud
    - Parses ASCII frames into (timestamp, ax, ay, az, gx, gy, gz, ir, red) tuples
    - Buffers samples into a rolling window (e.g., collections.deque)
    - Output: structured sample stream
    |
    v
[2. Signal Processing]
    - HR extraction: bandpass filter PPG (0.5–4 Hz), peak detection → instantaneous HR
    - Tremor extraction: bandpass filter accel (8–12 Hz), compute RMS amplitude
    - Both computed over a sliding window (e.g., 10–30 s)
    - Output: (timestamp, hr_bpm, tremor_rms) time series
    |
    v
[3. Feature Extraction]
    - Compute delta features vs. personal baseline:
        delta_hr   = hr_bpm    - baseline_hr
        delta_tremor = tremor_rms - baseline_tremor
    - Apply smoothing (moving average or exponential smoothing) to reduce noise
    - Output: (timestamp, delta_hr, delta_tremor) smoothed series
    |
    v
[4. Plasma Concentration Estimator]
    - Maps (delta_hr, delta_tremor) → estimated plasma caffeine concentration C_est(t)
    - Uses literature relationships:
        delta_hr ≈ 0.03–0.05 bpm per ng/mL plasma caffeine
        tremor RMS increases monotonically with concentration
    - Weights and combines both signals into a single C_est estimate
    - Output: C_est(t) time series
    |
    v
[5. PK Model + Dose Estimator]
    - Runs a one-compartment oral absorption model forward
    - Fits/updates model parameters to match C_est(t)
    - Detects dose events (rises in C_est) and estimates dose magnitude
    - Tracks cumulative dose across the session
    - Output: dose_events[], cumulative_dose_mg
    |
    v
[6. Output Layer]
    - Prints/logs current estimates to console or file
    - Optionally plots real-time traces (matplotlib animated or periodic saves)
```

### Module Boundaries

| Module | File | Inputs | Outputs |
|---|---|---|---|
| Serial ingestion | `ingestion.py` | COM port string, baud rate | sample stream |
| Signal processing | `signal_processing.py` | raw sample stream | hr_bpm, tremor_rms series |
| Feature extraction | `features.py` | hr/tremor series, baseline dict | delta_hr, delta_tremor |
| Concentration estimator | `concentration.py` | delta features | C_est(t) |
| PK model | `pk_model.py` | C_est(t) | dose_events, cumulative_dose |
| Calibration | `calibration.py` | raw sample stream (pre-caffeine) | baseline dict |
| Main runner | `main.py` | config, COM port | orchestrates all above |

---

## 3. Pharmacokinetic Model Architecture

### One-Compartment Oral Absorption Model

For a single dose D (mg) administered at time t=0:

```
C(t) = (D · F · ka) / (Vd · (ka − ke)) · (exp(−ke · t) − exp(−ka · t))
```

Parameters (literature values for caffeine):
- `ka` = 1.0–2.0 hr⁻¹ (absorption rate constant; ~30–45 min to peak)
- `ke` = 0.139 hr⁻¹ (elimination rate constant; half-life ≈ 5 hr)
- `Vd` = 0.6 L/kg (volume of distribution; ~42 L for 70 kg adult)
- `F` = 1.0 (oral bioavailability of caffeine is essentially 100%)

### Handling Multiple Doses

Caffeine PK is linear at typical doses — the principle of superposition applies. For N doses with magnitudes D_i administered at times t_i:

```
C_total(t) = Σ C_i(t − t_i)   for all i where t ≥ t_i
```

Implementation: maintain a list of `(dose_time, dose_amount)` tuples. At each time step, sum contributions from all prior doses. This is O(N) per time step; with at most ~10 doses per day this is trivial.

### Dose Detection and Estimation

Two approaches, to be used together:

1. **Event detection from C_est(t)**: detect a rapid rise in estimated concentration (derivative threshold) to flag a new dose event. The rise onset time is the dose time.
2. **Curve fitting**: given C_est(t) after a detected event, fit D_i to minimise least-squares error between modelled C(t) and observed C_est(t) over the absorption window (~0–2 hr post-dose). Use `scipy.optimize.minimize_scalar` or `curve_fit`.

The model also enables **prediction**: project the concentration curve forward in time given known dose history.

### State Representation

```python
@dataclass
class PKState:
    doses: list[tuple[float, float]]  # (time_hr, dose_mg) pairs
    ka: float = 1.5      # hr^-1
    ke: float = 0.139    # hr^-1
    Vd: float = 42.0     # litres
    F:  float = 1.0

    def concentration_at(self, t_hr: float) -> float:
        """Sum contributions from all prior doses."""
        ...
```

---

## 4. Calibration Architecture

### Purpose

Absolute HR and tremor values vary substantially between individuals and even between sessions for the same individual (resting HR differs by time of day, hydration, sleep). Calibration establishes a personal within-session baseline so that delta signals are used everywhere downstream.

### Calibration Protocol

1. User sits at rest for 3–5 minutes before consuming any caffeine.
2. The system collects this pre-caffeine window (configurable, default 3 min = 18000 samples at 100 Hz).
3. Compute and store:
   - `baseline_hr` = mean HR over the calibration window (after HR extraction)
   - `baseline_tremor_rms` = mean tremor RMS over the calibration window
   - `baseline_timestamp` = wall-clock time at end of calibration

### Baseline Storage

Store as a JSON file per session:

```json
{
  "session_id": "2026-03-12T09:00:00",
  "baseline_hr_bpm": 62.4,
  "baseline_tremor_rms": 0.012,
  "calibration_duration_s": 180,
  "notes": ""
}
```

File location: `data/baselines/<session_id>.json`

This enables retrospective analysis and cross-session comparison without re-running calibration.

### Calibration Module Boundary

`calibration.py` is responsible only for:
- Consuming the raw sample stream for the calibration window duration
- Running signal processing (HR extraction, tremor RMS) on that window
- Writing the baseline JSON
- Returning the baseline dict to the main runner

It does NOT own the signal processing logic — it calls `signal_processing.py` functions. This keeps calibration as a thin orchestrator.

---

## 5. Build Order and Dependency Chain

The dependency graph is strictly layered. Nothing in a higher layer can be tested until the layer below it works.

```
Layer 1 — Hardware + Firmware
  [1a] AX6 I2C read — verify raw accelerometer/gyro values
  [1b] MAX30102 I2C read — verify raw IR/Red PPG values
  [1c] Combined firmware loop — both sensors, correct timing, serial output
  [1d] Data framing — validate frame format on PC with a simple logger

Layer 2 — PC Ingestion
  [2a] Serial reader — parse frames, reconstruct sample stream
  [2b] Raw data logger — save CSV for offline development of later layers
  → GATE: can develop Layers 3+ offline using logged data

Layer 3 — Signal Processing
  [3a] HR extraction from PPG — bandpass + peak detection, validate against reference
  [3b] Tremor RMS extraction from accelerometer — bandpass + RMS, validate on known motion

Layer 4 — Calibration
  [4a] Baseline capture — run calibration protocol, save baseline JSON
  [4b] Delta computation — confirm delta_hr and delta_tremor are near zero at rest

Layer 5 — Concentration Estimation
  [5a] Literature mapping — implement HR-to-concentration and tremor-to-concentration functions
  [5b] Fused estimate — combine both signals into C_est(t)

Layer 6 — PK Model
  [6a] Forward model — implement C(t) for a single dose, validate against published curves
  [6b] Superposition — extend to multiple doses
  [6c] Dose fitting — curve-fit D given observed C_est(t)
  [6d] Cumulative dose tracking — session-level accumulation

Layer 7 — Integration + Validation
  [7a] End-to-end pipeline — all layers wired together
  [7b] Accuracy testing — compare estimated vs. known dose
  [7c] Edge cases — zero caffeine session, closely spaced doses, high-dose session
```

### Critical Path

```
1c (firmware) → 2a (ingestion) → 2b (logger) → 3a/3b (signal proc)
→ 4a (calibration) → 5b (concentration) → 6c (dose fitting) → 7b (validation)
```

Layer 2b (the raw data logger) is a high-leverage early deliverable: once it exists, all signal processing and modelling work can proceed offline without needing live hardware.

---

## 6. Component Communication Map

```
[Arduino Hardware]
  AX6 (I2C 0x6A) ──┐
                    ├── [Arduino Firmware Loop] ──(Serial USB 115200 baud)──►
  MAX30102 (I2C 0x57)┘

[PC]
  Serial port ──► [ingestion.py] ──► raw sample stream (in-memory deque)
                                          │
                          ┌───────────────┘
                          │
                          ▼
               [calibration.py] ──(during baseline window)──► baseline.json
                          │
                          ▼
               [signal_processing.py] ──► hr_bpm, tremor_rms
                          │
                          ▼
               [features.py] ──(subtract baseline)──► delta_hr, delta_tremor
                          │
                          ▼
               [concentration.py] ──► C_est(t)
                          │
                          ▼
               [pk_model.py] ──► dose_events[], cumulative_dose_mg
                          │
                          ▼
               [output / display]
```

---

## 7. Key Architectural Decisions

| Decision | Rationale |
|---|---|
| 100 Hz unified sample rate for both sensors | Simplifies firmware loop; meets Nyquist for 12 Hz tremor; MAX30102 FIFO supports it; serial bandwidth is sufficient |
| ASCII framing over binary | Easier to debug during development; upgrade to binary only if throughput becomes an issue |
| Arduino timestamp in every frame | PC clock drift and serial buffering make PC-side timestamping unreliable; source-of-truth timing stays at the sensor |
| Delta-based features (not absolute) | Individual baseline variation dominates absolute signal; deltas are more robust across sessions and subjects |
| Superposition for multi-dose PK | Caffeine kinetics are linear at therapeutic doses; superposition is exact, not an approximation |
| Baseline stored as JSON per session | Enables offline reprocessing, debugging, and future cross-session analysis without re-running hardware |
| Layer 2b raw data logger as early deliverable | Decouples hardware availability from algorithm development; allows signal processing and PK model to be developed and tested offline |

---

## Build Order Implications for Roadmap

1. **Phase 1 (Firmware)**: Must ship before any PC work can use live data. Deliverable: stable serial stream with correct framing and timing.
2. **Phase 2 (Ingestion + Logging)**: Thin PC layer that gates all downstream. Once logs exist, hardware is no longer on the critical path.
3. **Phase 3 (Signal Processing)**: Parallelisable with Phase 2 if offline test data (synthetic or logged) is available. HR extraction and tremor extraction are independent sub-tasks.
4. **Phase 4 (Calibration)**: Depends on Phase 3 (uses HR/tremor functions). Short implementation but required before any delta feature can be computed.
5. **Phase 5 (PK Model)**: Can be developed fully offline using synthetic C(t) curves. Does not require live sensor data. Can start as early as Phase 2.
6. **Phase 6 (Integration)**: Wires all prior phases. Depends on all of the above.
7. **Phase 7 (Validation)**: Requires known-dose experiments. Accuracy target: error < 50mg.

---

*Research completed: 2026-03-12*
*Sources: PROJECT.md project context, caffeine PK literature (Bonati et al. 1982, Blanchard & Sawers 1983), AX6/MAX30102 datasheets (domain knowledge), Arduino serial throughput specifications*
