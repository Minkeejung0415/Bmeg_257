# Caffeine Estimation System

Real-time caffeine intake estimation using an IMU + PPG wrist sensor array, a
pharmacokinetic model, and physiological transfer functions from peer-reviewed
literature.

**Target accuracy:** MAE < 50 mg across 0–400 mg range (fasted, sedentary,
with personal-slope calibration).  Without a personal-slope session, expect
±80–150 mg.

---

## Hardware

| Component | Part | Library |
|-----------|------|---------|
| IMU | SparkFun ICM-42688-P (ICM-42688-P breakout) | SparkFun ICM-42688-P v1.0.8 |
| PPG | SparkFun MAX30102 breakout | SparkFun MAX3010x v1.1.2 |
| MCU | Any Arduino (Uno R4, Mega, Pro Micro, etc.) | — |

Both sensors share the I²C bus (SDA/SCL) at 400 kHz.  Connect to 3.3 V.

---

## Repository layout

```
Bmeg_257/
├── firmware/
│   └── caffeine_sensor/
│       └── caffeine_sensor.ino   # Arduino firmware
├── src/
│   ├── ingestion.py              # Module 1 — serial reader + CSV logger
│   ├── signal_processing.py      # Module 2 — PPG HR + tremor analysis
│   ├── pk_model.py               # Module 3 — pharmacokinetic model
│   ├── calibration.py            # Module 4 — baseline + personal slope
│   ├── concentration.py          # Module 5 — transfer functions + dose detection
│   └── main.py                   # Module 6 — orchestration (live + replay)
├── sessions/                     # Auto-created; holds session CSVs
├── requirements.txt
└── README.md
```

---

## Quick-start

### 1. Install Python dependencies

```powershell
cd C:\Users\justi\Documents\Bmeg_257
pip install -r requirements.txt
```

### 2. Flash the Arduino

Open `firmware/caffeine_sensor/caffeine_sensor.ino` in the Arduino IDE.
Install the two required libraries via the Library Manager, then upload.

Open the Serial Monitor at 115 200 baud and confirm you see lines like:
```
seq,ts_ms,ax,ay,az,gx,gy,gz,ppg
0,1234,0.0123,-0.0045,9.8123,0.012,0.003,-0.001,87432
1,1244,…
```

### 3. Validate the PK model (no hardware needed)

```powershell
cd src
python main.py validate
```
Expected output: validation tables for Bonati 1982 and Blanchard & Sawers 1983
with MAE < 0.5 mg/L, plus `pk_validation.png`.

### 4. Live mode

```powershell
python main.py live --port COM3 --food-state fasted --weight 72
```

**Workflow:**
1. Sit completely still, no caffeine for ≥4 h.
2. Press Enter when prompted to begin the 3-minute baseline capture.
3. After baseline completes, the monitoring loop starts.
4. Commands during monitoring:
   - `d` + Enter → manually log a dose (prompts for mg)
   - `s` + Enter → print current status
   - `q` + Enter → quit cleanly

### 5. Replay mode (no hardware)

```powershell
python main.py replay sessions/session_20260312_143000.csv
```

---

## Calibration guide

### Per-session baseline (Phase 1 — REQUIRED)

Collected automatically at startup.  Minimum 3 minutes seated, still,
zero caffeine.  Saved to `baseline.json`.

### Personal-slope calibration (Phase 2 — strongly recommended)

Individual variation in caffeine's cardiovascular effect is **5–8×**.
Without a personal slope the population average is used and errors reach
±80–150 mg.

**Protocol:**
1. Fast for 4 hours (no food, coffee, or tea).
2. Take a known dose of caffeine (e.g. one 200 mg tablet).
3. Record the full session (≥4 hours post-dose) in live mode.
4. In a Python shell, fit the personal slope:

```python
import sys; sys.path.insert(0, 'src')
import pandas as pd, numpy as np
from calibration import Calibration
from signal_processing import SignalProcessor

# Load the session from the known-dose day
df = pd.read_csv('sessions/session_known_dose.csv')
proc = SignalProcessor()
df_proc = SignalProcessor.process_dataframe(df)

# Keep only valid HR windows where delta_hr is from post-dose period
cal = Calibration()
# (assume baseline was captured at start of that session)
t_hr = df_proc['ts_ms'].values / 3_600_000        # convert ms → hr
t_dose_hr = 0.75                                   # dose at 45 min into session
t_since_dose = t_hr - t_dose_hr
delta_hr = df_proc['hr_bpm'].values - cal.baseline_hr

mask = (t_since_dose > 0.25) & df_proc['hr_valid']
cal.fit_personal_slope(
    known_dose_mg=200,
    t_hr=t_since_dose[mask],
    delta_hr_obs=delta_hr[mask],
    food_state='fasted',
)
```

The fitted slope is written to `baseline.json` and used automatically on
all subsequent sessions.

---

## Pharmacokinetic model

One-compartment oral-absorption model (Bonati 1982, Blanchard & Sawers 1983):

```
dA_gut/dt    = -ka · A_gut
dA_plasma/dt =  ka · A_gut  -  ke · A_plasma
C(t)         =  A_plasma(t) / (Vd · BW)
```

| Parameter | Fasted | Fed | Units |
|-----------|--------|-----|-------|
| ka | 3.0 | 0.8 | hr⁻¹ |
| ke | 0.139 | 0.139 | hr⁻¹ |
| t½ elim | — | — | ≈5 h |
| Vd | 0.6 | 0.6 | L/kg |
| F | 1.0 | 1.0 | — |

Multi-dose superposition: linear sum of individual closed-form curves.

---

## Transfer functions

| Signal | Literature | Relationship |
|--------|-----------|--------------|
| Delta HR | Graham & Spriet 1995 | ~3–5 BPM per 100 mg caffeine |
| 8–12 Hz tremor | Hallett 1998 | Band power increases with [caffeine] |

HR is the primary estimation channel.  Tremor band power is used as a
corroborating signal for dose-event detection only (not as a direct
concentration estimator).

---

## Accuracy targets and constraints

| Condition | Expected MAE |
|-----------|-------------|
| Fasted, sedentary, personal slope | **< 50 mg** |
| Fasted, sedentary, population slope | 80–150 mg |
| Fed state (without food-state flag) | 100–200 mg |
| Motion during measurement | Unreliable |

**Critical constraints:**
- PPG and tremor windows are **enforced rest periods** — motion artifacts
  corrupt both signals simultaneously.
- Per-session baseline MUST be captured BEFORE any caffeine.
- Food state MUST be declared (`--food-state fasted|fed`); food slows
  absorption 3–6×.
- The session CSV (Module 1 output) is the decoupling point — all other
  modules can be developed and tested from it offline.

---

## Packet format (Arduino → Python)

ASCII CSV at 115 200 baud, 100 Hz:

```
seq,ts_ms,ax,ay,az,gx,gy,gz,ppg
```

| Field | Type | Units | Notes |
|-------|------|-------|-------|
| seq | uint16 | — | Wraps 0–65535; gap = dropped packets |
| ts_ms | uint32 | ms | millis() since boot |
| ax/ay/az | float | m/s² | ICM-42688-P ±4 g |
| gx/gy/gz | float | deg/s | ICM-42688-P ±500 dps |
| ppg | uint32 | ADC counts | MAX30102 Red channel, HR mode (0x09=0x02) |

---

## References

- Bonati M. et al. (1982). Caffeine disposition after oral doses. *Clinical Pharmacology & Therapeutics*, 32(1), 98–106.
- Blanchard J. & Sawers S.J.A. (1983). The absolute bioavailability of caffeine in man. *European Journal of Clinical Pharmacology*, 24(1), 93–98.
- Graham T.E. & Spriet L.L. (1995). Metabolic, catecholamine, and exercise performance responses to varying doses of caffeine. *Journal of Applied Physiology*, 78(3), 867–874.
- Hallett M. (1998). Overview of human tremor physiology. *Movement Disorders*, 13(S3), 43–48.

---

## Split USB bridge mode (IMU and PPG on different boards)

If your IMU and PPG are connected to different USB serial devices, capture both
streams and merge them into one replay-compatible session CSV:

```powershell
python src/dual_usb_capture.py --imu-port COM5 --ppg-port COM3
```

Then run the estimator on the merged file:

```powershell
python src/main.py replay sessions/session_YYYYMMDD_HHMMSS.csv
```

Notes:
- The bridge emits the same session columns used by replay mode:
  `wall_time,seq,ts_ms,ax,ay,az,gx,gy,gz,ppg,dropped_before`
- If an IMU sample is too stale, the row is still written with zeroed IMU
  values so logging never blocks.
