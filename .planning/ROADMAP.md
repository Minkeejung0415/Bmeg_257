# Roadmap: Caffeine Estimation Algorithm (AX6 + MAX30102)

## Overview

The project builds a strictly-layered biosignal pipeline: Arduino firmware streams raw IMU and PPG data over USB serial, a PC ingestion layer captures and logs that stream to CSV (the decoupling point that enables all offline development), signal processing and a pharmacokinetic model run in parallel, calibration pins the per-session baseline, and a concentration estimation + integration layer wires all pieces into a live end-to-end system. Final controlled experiments validate the <50 mg accuracy target. Phase 5 (PK model) can be started in parallel with Phase 2 because it requires only scipy and synthetic data — this is the only phase that does not require hardware to proceed.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

- [ ] **Phase 1: Firmware + Hardware Validation** - Stable Arduino firmware streaming both sensors at 100 Hz; validated hardware bring-up and documented experimental protocol
- [ ] **Phase 2: PC Ingestion + Raw Data Logger** - Serial reader thread with drop detection and CSV logger; produces the session file that decouples hardware from algorithm development
- [ ] **Phase 3: Signal Processing** - Offline-first HR extraction via NeuroKit2 and tremor feature extraction via Welch PSD; verified against recorded session CSV
- [ ] **Phase 4: Calibration** - Per-session resting baseline capture and delta computation; personal HR-per-mg slope fitting workflow required for <50 mg accuracy
- [ ] **Phase 5: PK Model** - One-compartment forward and inverse PK model validated against published caffeine reference curves; can run in parallel with Phase 2
- [ ] **Phase 6: Concentration Estimation + Integration** - Signal-to-concentration mapping, dose event detection, and end-to-end main.py orchestrator running on a real session file
- [ ] **Phase 7: Validation** - Controlled known-dose experiments across 0–400 mg range; MAE computed against <50 mg target

## Phase Details

### Phase 1: Firmware + Hardware Validation
**Goal**: Both sensors stream valid, timestamped data over serial at 100 Hz with no I2C contention, and the experimental protocol (sensor placement, rest windows, food state, calibration session design) is documented and fixed
**Depends on**: Nothing (first phase)
**Requirements**: FIRM-01, FIRM-02, FIRM-03, FIRM-04
**Success Criteria** (what must be TRUE):
  1. Arduino transmits AX6 accelerometer and gyroscope data at 100 Hz as ASCII CSV packets over serial at 115200 baud, readable by a PC terminal
  2. Arduino transmits MAX30102 raw IR FIFO PPG data at 100 Hz in the same serial stream without I2C address conflicts
  3. Every packet includes a 2-byte sequence counter; a Python script reading the stream can detect and log dropped packets by inspecting counter gaps
  4. A written experimental protocol document specifies sensor placement, required rest posture window duration, food state requirement (fasted), and calibration session procedure
**Plans**: TBD

### Phase 2: PC Ingestion + Raw Data Logger
**Goal**: A running Python ingestion process reads the Arduino serial stream on a dedicated thread, detects dropped packets, and writes all raw sensor data to a timestamped CSV file — producing the session file that enables all downstream offline development
**Depends on**: Phase 1
**Requirements**: INGEST-01, INGEST-02, INGEST-03
**Success Criteria** (what must be TRUE):
  1. ingestion.py reads the serial stream on a dedicated reader thread and never blocks on computation; the main thread can run without starving the reader
  2. Dropped packets (sequence counter gaps) are detected and logged with their position in the stream during every session
  3. A complete session CSV file is produced containing all raw AX6 and MAX30102 data with PC-side timestamps, suitable for offline replay
**Plans**: TBD

### Phase 3: Signal Processing
**Goal**: HR in BPM and tremor features (RMS, dominant frequency, 8–12 Hz band power) are reliably extracted from raw sensor data using offline replay against a recorded session CSV, with PPG artifact rejection gating corrupted windows
**Depends on**: Phase 2
**Requirements**: SIG-01, SIG-02, SIG-03, SIG-04, SIG-05, VAL-02
**Success Criteria** (what must be TRUE):
  1. signal_processing.py produces rolling 30-second HR estimates in BPM from raw PPG using NeuroKit2 Elgendi peak detection, verifiable against a session CSV replay
  2. Accelerometer data is high-pass filtered at 1–2 Hz before tremor extraction; tremor RMS is computed in 5-second windows with 50% overlap
  3. Welch PSD produces dominant frequency and 8–12 Hz band power from accelerometer data in 5-second windows
  4. PPG windows flagged as low quality by the SQI are rejected and not forwarded to estimation; clean and rejected windows are distinguishable in output
  5. Signal processing pipeline runs end-to-end against a recorded session CSV without live hardware
**Plans**: TBD

### Phase 4: Calibration
**Goal**: The system captures a resting baseline at the start of each session, computes delta HR and delta tremor from it for all downstream processing, and supports the known-dose personal calibration workflow that fits the individual HR-per-mg slope — required to achieve <50 mg accuracy
**Depends on**: Phase 3
**Requirements**: CAL-01, CAL-02, CAL-03
**Success Criteria** (what must be TRUE):
  1. calibration.py captures a 3–5 minute resting window at session start and writes baseline_hr and baseline_tremor_rms to a per-session baseline.json file
  2. During a confirmed rest window, delta_hr and delta_tremor are both near zero (within noise floor), confirming correct subtraction from the per-session baseline
  3. A calibration session workflow accepts a known caffeine dose input, records the resulting delta_hr and delta_tremor curve, and fits a personal HR-per-mg slope coefficient that is saved for use in concentration estimation
**Plans**: TBD

### Phase 5: PK Model
**Goal**: A one-compartment oral absorption pharmacokinetic model with multi-dose superposition and inverse dose fitting is implemented, validated against Bonati 1982 and Blanchard & Sawers 1983 published reference curves, entirely on synthetic data — no live sensor data required
**Depends on**: Phase 2 (can start in parallel; synthetic data only)
**Requirements**: PK-01, PK-02, PK-03, PK-04, VAL-01
**Success Criteria** (what must be TRUE):
  1. pk_model.py simulates plasma caffeine concentration C(t) for a single dose using scipy.integrate.solve_ivp with the specified published parameters (ka_fasted = 3.0 hr⁻¹, ka_fed = 0.8 hr⁻¹, ke = 0.139 hr⁻¹, Vd = 0.6 L/kg)
  2. Simulated C(t) curves match published Bonati 1982 and Blanchard & Sawers 1983 reference curves to within acceptable tolerance on a comparison plot
  3. Multi-dose superposition correctly accumulates caffeine concentration across two or more doses spaced hours apart; total C(t) equals the sum of individual dose curves
  4. Inverse solver recovers a known synthetic dose magnitude (given correct timing) with error below 10 mg on synthetic data
**Plans**: TBD

### Phase 6: Concentration Estimation + Integration
**Goal**: The signal-to-concentration mapping function converts (delta_hr, delta_tremor) to C_est(t), dose event detection identifies new caffeine consumption events, and main.py wires all modules into an end-to-end pipeline that runs on a real session file and displays live estimates
**Depends on**: Phase 3, Phase 4, Phase 5
**Requirements**: CONC-01, CONC-02, CONC-03, INT-01, INT-02, INT-03
**Success Criteria** (what must be TRUE):
  1. concentration.py converts (delta_hr, delta_tremor) pairs to an estimated plasma concentration C_est(t) using literature-derived transfer functions, producing a time-series output from a session CSV replay
  2. A new dose event is detected when 15-minute averaged delta_hr rises more than 1.5 bpm above the running baseline; detected event times are visible in output
  3. Cumulative daily caffeine intake is updated in real time as each new dose event is detected and its magnitude estimated
  4. main.py orchestrates the full pipeline end-to-end against a live Arduino serial stream, displaying current HR (BPM), tremor RMS, C(t), and cumulative dose (mg) in the terminal
  5. Offline replay mode processes a previously recorded session CSV through the full pipeline without live hardware connected
**Plans**: TBD

### Phase 7: Validation
**Goal**: Controlled known-dose experiments across the 0–400 mg range confirm the system achieves mean absolute error (MAE) below 50 mg, and system limitations (accuracy floor without personal calibration, food-state sensitivity, low-dose SNR boundary) are explicitly documented
**Depends on**: Phase 6
**Requirements**: VAL-03
**Success Criteria** (what must be TRUE):
  1. Controlled experiments cover at least 3 dose levels across the 0–400 mg range in fasted state with enforced rest windows per the Phase 1 protocol
  2. Mean absolute error (MAE) computed across all test sessions is below 50 mg, confirming the hard accuracy requirement
  3. A results document reports MAE per dose level, identifies the low-dose SNR boundary, and documents conditions under which the <50 mg target holds vs. does not hold
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order with one parallelism note: Phase 5 can start alongside Phase 2 using synthetic data only. Phases 3, 4, and 5 must all complete before Phase 6 can begin.

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Firmware + Hardware Validation | 0/TBD | Not started | - |
| 2. PC Ingestion + Raw Data Logger | 0/TBD | Not started | - |
| 3. Signal Processing | 0/TBD | Not started | - |
| 4. Calibration | 0/TBD | Not started | - |
| 5. PK Model | 0/TBD | Not started | - |
| 6. Concentration Estimation + Integration | 0/TBD | Not started | - |
| 7. Validation | 0/TBD | Not started | - |
