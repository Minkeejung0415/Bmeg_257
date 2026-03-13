# Requirements: Caffeine Estimation Algorithm (AX6 + MAX30102)

**Defined:** 2026-03-12
**Core Value:** Accurately estimate cumulative caffeine intake from physiological signals alone — no manual logging required

## v1 Requirements

### Firmware

- [ ] **FIRM-01**: Arduino streams AX6 raw accelerometer and gyroscope data at 100 Hz over serial (115200 baud, ASCII CSV format)
- [ ] **FIRM-02**: Arduino streams MAX30102 raw IR FIFO PPG data at 100 Hz over serial (HR-only mode, not SpO2; LED current 25–50 mA)
- [ ] **FIRM-03**: Both AX6 and MAX30102 operate on the same I2C bus without contention (correct I2C addresses, no conflicts)
- [ ] **FIRM-04**: Each serial packet includes a 2-byte sequence counter to enable detection of dropped packets

### Ingestion

- [ ] **INGEST-01**: PC-side Python ingestion reads serial stream via a dedicated reader thread (never blocks on computation)
- [ ] **INGEST-02**: Ingestion detects and logs sequence counter gaps (dropped packets)
- [ ] **INGEST-03**: Ingestion logs all raw sensor data to a timestamped CSV file per session (enables offline development and replay)

### Signal Processing

- [ ] **SIG-01**: HR is extracted from raw PPG using NeuroKit2 Elgendi peak detection, producing BPM in rolling 30-second windows
- [ ] **SIG-02**: Accelerometer data is high-pass filtered at 1–2 Hz before tremor extraction (removes gravity component)
- [ ] **SIG-03**: Tremor RMS amplitude is computed from filtered accelerometer in 5-second windows (50% overlap)
- [ ] **SIG-04**: Tremor dominant frequency and 8–12 Hz band power are extracted via Welch PSD in 5-second windows
- [ ] **SIG-05**: PPG signal quality index (SQI) gates HR extraction — corrupted windows are rejected and not used in estimation

### Calibration

- [ ] **CAL-01**: System captures a 3–5 minute resting baseline before any caffeine is consumed, recording baseline HR (HR₀) and baseline tremor RMS (tremor₀) to a per-session baseline.json file
- [ ] **CAL-02**: Delta HR (ΔHR = current HR − HR₀) and delta tremor (Δtremor = current tremor RMS − tremor₀) are computed from the per-session baseline for all downstream processing
- [ ] **CAL-03**: System supports a known-dose personal calibration session workflow: user consumes a known caffeine dose, system records the resulting ΔHR and Δtremor curve, and fits the personal HR-per-mg slope (required for <50 mg accuracy)

### Pharmacokinetic Model

- [ ] **PK-01**: One-compartment oral absorption PK forward model is implemented using scipy.integrate.solve_ivp with published caffeine parameters (ka_fasted = 3.0 hr⁻¹, ka_fed = 0.8 hr⁻¹, ke = 0.139 hr⁻¹, Vd = 0.6 L/kg)
- [ ] **PK-02**: PK model supports multi-dose superposition — caffeine concentrations from multiple doses throughout the day accumulate correctly via linear superposition
- [ ] **PK-03**: Inverse PK solver estimates ingested dose magnitude from an observed concentration curve using scipy.optimize least-squares (dose timing provided by dose event detection)
- [ ] **PK-04**: PK model is validated against published caffeine reference curves (Bonati 1982, Blanchard & Sawers 1983) before integration with live sensor data

### Concentration Estimation

- [ ] **CONC-01**: Signal-to-concentration mapping function converts (ΔHR, Δtremor) to estimated plasma caffeine concentration C_est(t) using literature-derived transfer functions
- [ ] **CONC-02**: Dose event detection identifies new caffeine consumption events from a threshold rise in 15-minute averaged ΔHR (default threshold: >1.5 bpm above running baseline)
- [ ] **CONC-03**: Cumulative daily caffeine intake is tracked and updated in real time as new dose events are detected

### Integration & Output

- [ ] **INT-01**: End-to-end pipeline runs from live Arduino serial stream to real-time dose estimate via main.py orchestrator
- [ ] **INT-02**: System displays in real time: current HR (BPM), tremor RMS, estimated plasma concentration C(t), and cumulative dose (mg)
- [ ] **INT-03**: Pipeline supports offline replay mode — processes a previously recorded session CSV without live hardware

### Validation

- [ ] **VAL-01**: PK model is verified against synthetic caffeine PK reference curves before any real sensor data is used
- [ ] **VAL-02**: Signal processing pipeline is tested offline against recorded session CSV files
- [ ] **VAL-03**: Controlled known-dose validation experiments are conducted (fasted state, enforced rest windows, ≥3 dose levels across 0–400 mg range), and mean absolute error (MAE) is computed against the <50 mg target

## v2 Requirements

### Accuracy Enhancements

- **DIFF-01**: Per-user Vd scaling from body weight input (reduces systematic dose error ~10–15%)
- **DIFF-02**: Caffeine tolerance binary flag (tolerant vs. naive; shifts expected ΔHR magnitude ~30–50%)
- **DIFF-03**: HRV (RMSSD) from PPG inter-peak intervals as additional concentration mapping dimension

### Usability

- **UX-01**: C(t) time-series plot for session visualization
- **UX-02**: Confidence interval on dose estimate output
- **UX-03**: Activity-gated tremor windowing using gyroscope cross-validation

## Out of Scope

| Feature | Reason |
|---|---|
| On-Arduino HR calculation | Destroys raw PPG waveform needed for PC-side NeuroKit2 processing |
| ML/neural network models | No training dataset exists; physics-based PK approach is sufficient |
| Two-compartment PK model | Marginal accuracy gain over one-compartment; 2–3× implementation cost |
| Confounder modeling (exercise, stress) | Controlled environment assumed; exercise dwarfs caffeine HR signal 10–20× |
| Mobile/Bluetooth streaming | USB serial to PC is sufficient for academic proof-of-concept |
| HRV frequency domain (LF/HF ratio) | Requires stable 5-minute clean windows; impractical during daily monitoring |
| Bayesian adaptive PK updating | Over-engineered for v1; lmfit parameter fitting is sufficient |

## Traceability

| Requirement | Phase | Status |
|---|---|---|
| FIRM-01 | Phase 1 | Pending |
| FIRM-02 | Phase 1 | Pending |
| FIRM-03 | Phase 1 | Pending |
| FIRM-04 | Phase 1 | Pending |
| INGEST-01 | Phase 2 | Pending |
| INGEST-02 | Phase 2 | Pending |
| INGEST-03 | Phase 2 | Pending |
| SIG-01 | Phase 3 | Pending |
| SIG-02 | Phase 3 | Pending |
| SIG-03 | Phase 3 | Pending |
| SIG-04 | Phase 3 | Pending |
| SIG-05 | Phase 3 | Pending |
| CAL-01 | Phase 4 | Pending |
| CAL-02 | Phase 4 | Pending |
| CAL-03 | Phase 4 | Pending |
| PK-01 | Phase 5 | Pending |
| PK-02 | Phase 5 | Pending |
| PK-03 | Phase 5 | Pending |
| PK-04 | Phase 5 | Pending |
| CONC-01 | Phase 6 | Pending |
| CONC-02 | Phase 6 | Pending |
| CONC-03 | Phase 6 | Pending |
| INT-01 | Phase 6 | Pending |
| INT-02 | Phase 6 | Pending |
| INT-03 | Phase 6 | Pending |
| VAL-01 | Phase 5 | Pending |
| VAL-02 | Phase 3 | Pending |
| VAL-03 | Phase 7 | Pending |

**Coverage:**
- v1 requirements: 27 total
- Mapped to phases: 27
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-12*
*Last updated: 2026-03-12 after roadmap creation; traceability confirmed against ROADMAP.md*
