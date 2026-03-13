# Project Research Summary

**Project:** Caffeine Estimation via Biosensors (AX6 IMU + MAX30102)
**Domain:** Biosignal processing / pharmacokinetic modelling / embedded systems
**Researched:** 2026-03-12
**Confidence:** HIGH (all four research dimensions returned well-grounded findings)

---

## Executive Summary

This project is a two-tier biosignal pipeline: Arduino firmware continuously streams raw IMU and PPG data over USB serial, and a Python PC pipeline ingests that stream, extracts physiological features (heart rate delta and tremor band power), maps them to plasma caffeine concentration via a one-compartment pharmacokinetic model, and inverts the model to infer dose and cumulative intake. The architecture is strictly layered — firmware must be stable before PC signal processing can be validated, and per-session baseline calibration must precede any meaningful estimation. There is no published open-source precedent for this specific sensor combination; this is a novel implementation assembled from established components (SparkFun first-party libraries, NeuroKit2, scipy, lmfit).

The recommended implementation path is deliberate and sequential. The raw data logger (Layer 2b in the architecture) is a high-leverage early deliverable: once a CSV of real sensor data exists, the entire signal processing and PK model pipeline can be developed and tested offline without hardware. The PK model itself requires no live sensor data and can be implemented and validated against synthetic curves in parallel with firmware work. This decoupling is critical to managing the project timeline.

The hardest constraint in the project is the <50 mg accuracy target. Research is unambiguous: without at least one known-dose personal calibration session, this target is not achievable. Inter-individual variation in the HR-per-mg response slope spans 5–8×, which alone introduces ±80–150 mg error with population-average parameters. The required mitigation is a single controlled calibration session per subject, after which ±30–50 mg accuracy is achievable for doses above 100 mg in a controlled (low-motion, food-state-logged) environment. The accuracy target should be framed as cumulative daily intake rather than per-dose precision.

---

## Key Findings

### Recommended Stack

The stack splits cleanly across two domains with no speculative dependencies. On the Arduino side, SparkFun's first-party libraries for the ICM-42688-P (AX6) and MAX3010x (MAX30102) are the only viable choices — both are maintained, MIT-licensed, and match the exact hardware. ASCII CSV serial at 115200 baud handles the 100 Hz dual-sensor stream at ~5 KB/s, well within capacity. On the PC side, the scientific Python stack (numpy, scipy, pandas) handles all signal processing and ODE solving. NeuroKit2 0.2.9 is the 2025 standard for research-grade PPG processing and must be used in preference to raw `scipy.find_peaks()` or the unmaintained `heartpy` — the Elgendi peak detector it wraps reduces false positives by 15–30% on noisy PPG. The PK model is three lines of ODEs solved by `scipy.integrate.solve_ivp`; no external PK library is needed or recommended.

**Core technologies:**
- SparkFun ICM-42688-P Arduino lib (1.0.8): AX6 raw accel/gyro at 100 Hz — first-party, only maintained option
- SparkFun MAX3010x Arduino lib (1.1.2): MAX30102 raw IR FIFO streaming — de-facto standard
- pyserial 3.5: Arduino-to-Python serial bridge — no credible alternative
- numpy 1.26/2.0 + scipy 1.13 + pandas 2.2: core numerics, signal filtering, Welch PSD, ODE solving
- NeuroKit2 0.2.9: PPG cleaning and Elgendi-method peak detection — 2025 research standard
- lmfit 1.3.2: PK parameter fitting with confidence intervals (conditional — only if literature params need tuning)
- matplotlib 3.9: development visualization and debug plots

**What to avoid:** Arduino-side HR calculation (destroys raw waveform), `heartpy` (unmaintained), raw `scipy.find_peaks()` on unprocessed PPG, any ML framework (no training data exists), two-compartment PK models (marginal accuracy gain, 2–3× implementation cost).

### Expected Features

The feature set has a hard dependency chain: baseline calibration must precede delta computation, which must precede concentration mapping, which must precede PK inversion. There is no shortcut to this ordering.

**Must have (table stakes):**
- Resting HR + tremor baseline measurement (3–5 min pre-caffeine window) — all estimation is delta-based; without this, nothing works
- HR extraction from raw PPG (BPM via NeuroKit2 Elgendi peaks) — primary signal
- Delta HR from baseline — collapses inter-subject absolute HR variance
- Tremor RMS + 8–12 Hz band power from accelerometer (Welch PSD, 5 s windows at 50% overlap) — required second signal for low-dose discrimination
- Delta tremor from baseline — same rationale as delta HR
- PPG artifact rejection (signal quality gating) — motion artifacts otherwise dominate
- One-compartment oral absorption PK forward model (scipy solve_ivp) — literature-validated for caffeine
- Multi-dose superposition — caffeine accumulates across a day; single-dose model is insufficient
- Signal-to-concentration mapping function (delta HR + delta tremor → C_est) — bridges sensors and PK model
- Inverse PK solver (least-squares D given C_est(t)) — core inference step
- Real-time HR/tremor/C(t)/cumulative dose display — minimum output
- Dose event detection — required for multi-dose tracking

**Should have (v1 differentiators):**
- Per-user Vd scaling from body weight input — reduces systematic dose error ~10–15%
- Caffeine tolerance binary flag — tolerant users show blunted HR; ignoring this introduces ~30–50% error
- Activity-gated tremor windowing — prevents voluntary motion contaminating tremor features
- HRV (RMSSD) from PPG inter-peak intervals — independent second dimension for concentration mapping
- Session CSV export — essential for academic validation
- C(t) time-series plot — debugging and validation artifact

**Defer to v2+:**
- Known-dose calibration session workflow (high complexity, but note: personal HR-per-mg slope calibration is mandatory for <50 mg accuracy — this is a v1 requirement disguised as a differentiator; see Gaps section)
- HRV frequency domain (LF/HF ratio) — requires stable 5-minute windows; impractical in daily use
- Confidence intervals on dose estimate — scientifically rigorous, complex for v1
- Mobile/Bluetooth streaming — out of scope

### Architecture Approach

The system is a strictly sequential seven-layer pipeline. Each layer has a single responsibility and a well-defined input/output contract. The firmware layer (Layer 1) produces a timestamped ASCII CSV stream. The ingestion layer (Layer 2) parses that stream into a rolling in-memory deque and, critically, logs raw data to CSV — this log file is the decoupling point that allows all downstream layers to be developed offline. Signal processing (Layer 3), calibration (Layer 4), concentration estimation (Layer 5), and the PK model (Layer 6) operate on buffered data only. The PK model layer can be built and tested fully offline from the start of the project.

**Major components:**
1. `ingestion.py` — serial port reader, ASCII frame parser, raw data logger (deque buffer)
2. `calibration.py` — consumes pre-caffeine window, calls signal_processing, writes `baseline.json` per session
3. `signal_processing.py` — NeuroKit2 PPG pipeline, scipy Welch PSD tremor features
4. `features.py` — subtracts per-session baseline; outputs delta_hr and delta_tremor
5. `concentration.py` — maps (delta_hr, delta_tremor) to C_est(t) using literature transfer functions
6. `pk_model.py` — PKState dataclass, scipy solve_ivp one-compartment forward model, superposition, dose fitting via scipy.optimize
7. `main.py` — orchestrator; wires all modules; handles session lifecycle

### Critical Pitfalls

1. **Personal calibration is mandatory for the accuracy target** (P-15) — Inter-individual HR-per-mg slope variation is 5–8×. Without a known-dose calibration session per subject, error floor is ±80–150 mg. With personal calibration, ±30–50 mg is achievable. This must be designed into the experimental protocol from the start, not retrofitted. Prevention: build calibration session workflow into Phase 1 experimental design.

2. **PPG motion artifacts and the measurement protocol are inseparable** (P-01, P-08) — Motion artifacts from the MAX30102 and voluntary motion contaminating tremor measurement are both eliminated the same way: enforced rest windows with standardized posture. HR and tremor measurements cannot be taken simultaneously during active use; a 30-second quiet posture window is required per measurement epoch. This is a protocol constraint that must be communicated in every phase.

3. **The inverse PK problem is ill-posed without dose timing** (P-13) — Estimating dose magnitude from a concentration curve is underdetermined without knowing when the dose was taken. Dose event detection from the HR rise signal reduces the problem to magnitude estimation only. Prevention: implement temporal derivative threshold detection for dose events; validate inverse model on synthetic data before running on real sensor data.

4. **Food state must be logged or controlled; it changes ka by 3–6×** (P-11) — Fed vs. fasted state shifts time-to-peak from 30–45 min to 90–120 min. Using a single ka value on mixed sessions introduces systematic timing errors that translate directly to dose errors. Prevention for academic proof-of-concept: run all sessions in a fasted state (2+ hours since last meal) and document this as a protocol requirement.

5. **Serial buffer overflow is silent and will corrupt the dataset** (P-17) — The Arduino transmit buffer is 64–256 bytes; at 100 Hz with ASCII packets, any PC-side computation blocking the serial reader thread for >50 ms causes dropped packets. Prevention: dedicate a reader thread exclusively to serial ingestion (writes to a queue); never do computation in the reader callback. Add a 2-byte sequence counter to every packet.

---

## Implications for Roadmap

Research identifies seven functional layers with strict dependencies. The roadmap should follow this layering. The raw data logger at the end of Phase 2 is the project's most important unlock — it decouples hardware from algorithm development.

### Phase 1: Firmware + Hardware Validation
**Rationale:** Nothing downstream can be tested without a working serial stream. This phase must complete first. It also encompasses the experimental protocol decisions (sensor placement, rest windows, food state control, calibration session design) that cannot be changed later without invalidating collected data.
**Delivers:** Stable Arduino firmware streaming both sensors at 100 Hz over serial; validated sensor bring-up; documented experimental protocol.
**Addresses:** Table-stakes sensor acquisition; all hardware-layer features.
**Avoids:** P-02 (LED current), P-03 (SpO2 mode), P-04 (sensor placement), P-05 (PPG sample rate), P-06 (IMU ODR/aliasing), P-09 (accel range), P-19 (I2C contention), P-11 and P-15 (protocol-level food state and calibration session design).

### Phase 2: PC Ingestion + Raw Data Logger
**Rationale:** The logger is the decoupling point. Once a real CSV exists, Phases 3–6 can proceed without hardware. This phase is thin but has an outsized impact on development velocity.
**Delivers:** `ingestion.py` with serial reader thread, ASCII frame parser, sequence gap detection, and CSV logger. A real recorded session file.
**Uses:** pyserial 3.5, pandas 2.2 (CSV write).
**Avoids:** P-17 (serial overflow — dedicated reader thread + sequence counter), P-18 (timestamp drift — dual-timestamp per packet, clock mapping).

### Phase 3: Signal Processing (offline-first)
**Rationale:** HR extraction and tremor feature extraction are independent sub-tasks that can be developed against the Phase 2 CSV before live sensor data is available. NeuroKit2 PPG pipeline and scipy Welch PSD are well-characterized; this phase has the most available prior art.
**Delivers:** `signal_processing.py` with validated HR BPM extraction (NeuroKit2 Elgendi), tremor RMS, and 8–12 Hz band power. Verified against synthetic PPG and known-motion accelerometer data.
**Implements:** Signal processing layer (Architecture Layer 3).
**Avoids:** P-01 (motion artifact gating via SQI), P-07 (1–2 Hz highpass before tremor RMS), P-08 (6–15 Hz bandpass for tremor features only).

### Phase 4: Calibration
**Rationale:** Short implementation, but required before any delta feature is meaningful. Depends on Phase 3 functions. Calibration design locks in the session protocol.
**Delivers:** `calibration.py` writing per-session `baseline.json`; confirmed delta_hr and delta_tremor near zero during rest windows; calibration session workflow for personal slope fitting (required for <50 mg accuracy).
**Avoids:** P-16 (baseline drift — per-dose re-baseline windowing), P-15 (personal calibration session captures individual HR-per-mg slope).

### Phase 5: PK Model (can start in parallel with Phase 2)
**Rationale:** The one-compartment ODE and superposition model require only scipy and can be developed, validated against published caffeine PK curves, and tested on synthetic data before any real sensor data exists. This is the only phase that can genuinely start before Phase 3 completes.
**Delivers:** `pk_model.py` with PKState dataclass, `concentration_at(t)` via scipy solve_ivp, multi-dose superposition, and inverse dose fitting via scipy.optimize. Validated against Bonati/Blanchard & Sawers reference curves.
**Avoids:** P-10 (use consensus mid-range PK params with ±30% uncertainty range), P-11 (two ka values: fasted 3.0, fed 0.8), P-12 (treat ke as personal parameter after calibration), P-13 (validate inverse on synthetic data first).

### Phase 6: Concentration Estimation + Integration
**Rationale:** `concentration.py` (the signal-to-C_est mapping function) depends on both Phase 3 (calibrated delta features) and Phase 5 (PK model). Integration wires all six modules together for end-to-end pipeline testing.
**Delivers:** `concentration.py` (literature-derived delta_hr and delta_tremor → C_est transfer functions); `main.py` orchestrator; end-to-end pipeline running on a real session file.
**Implements:** Architecture Layer 5 + Layer 7a.

### Phase 7: Validation
**Rationale:** Accuracy testing against known doses is the proof-of-concept gate. Requires controlled experiments (fasted, known-dose, enforced rest protocol). Edge cases (zero caffeine, closely spaced doses, high-dose session) must be tested explicitly.
**Delivers:** Accuracy metrics against the <50 mg target; documented limitations (accuracy floor without calibration; food state sensitivity; low-dose SNR boundary).
**Avoids:** P-14 (frame the accuracy target as cumulative daily intake, not per-dose point estimate; use 5–10 minute averaged windows for low-dose discrimination).

### Phase Ordering Rationale

- Phase 1 (firmware) is an absolute prerequisite for Phase 2; both precede all PC work on live data.
- Phase 2 (logger) gates hardware dependency — its CSV output enables Phases 3–6 to proceed offline.
- Phase 5 (PK model) is the only phase that can start before Phase 3, using only synthetic data. Parallelize this with Phase 2.
- Phase 4 (calibration) is short but depends on Phase 3 signal processing functions.
- Phase 6 (integration) cannot start until Phases 3, 4, and 5 all deliver.
- Phase 7 (validation) requires controlled experiments with real hardware; cannot be fully automated.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 6 (concentration mapping):** The signal-to-concentration transfer function coefficients (delta_hr per ng/mL, tremor RMS per ng/mL) are derived from literature but the exact fusion weighting between the two signals is not empirically established for this sensor combination. Needs a calibration-derived fit in practice.
- **Phase 7 (validation):** Controlled experiment design (dose ranges, session spacing, subject inclusion criteria for habitual caffeine use) needs explicit experimental protocol documentation before data collection begins.

Phases with well-documented patterns (skip additional research):
- **Phase 1 (firmware):** SparkFun library examples plus this STACK.md are sufficient. No novel integration challenges.
- **Phase 2 (ingestion):** pyserial readline + threaded reader is a standard pattern. No new research needed.
- **Phase 3 (signal processing):** NeuroKit2 and scipy Welch PSD are mature, well-documented. Follow STACK.md guidance directly.
- **Phase 5 (PK model):** One-compartment model equations are fully specified in STACK.md and ARCHITECTURE.md. scipy solve_ivp is standard.

---

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | First-party Arduino libraries; NeuroKit2 is the 2025 research standard; scipy/pandas are canonical. All versions verified as of mid-2025. |
| Features | HIGH | Feature set grounded in published caffeine physiology literature (Graham & Spriet 1995, Hallett 1998, Benowitz 1990). Dependency chain is well-established. |
| Architecture | HIGH | Layered pipeline architecture is a direct consequence of hardware constraints and signal dependencies. No architectural controversy. Build order is mathematically determined. |
| Pitfalls | HIGH | 19 specific pitfalls identified, all from datasheet-level or peer-reviewed biosignal processing literature. Error budget analysis is quantitative. |

**Overall confidence:** HIGH

### Gaps to Address

- **Personal calibration session design:** The research is clear that a known-dose calibration session is required to hit <50 mg accuracy, but it is listed in FEATURES.md as a "differentiator (v2)." This is a misclassification — for a <50 mg accuracy target, personal slope calibration is v1 mandatory. The roadmapper should treat Phase 4 as including a calibration session protocol, not just resting baseline.

- **Signal fusion weighting (delta_hr vs. delta_tremor):** The relative weighting of HR and tremor signals in the C_est fusion step is not specified by literature for this hardware configuration. The practical approach is to implement both signals and fit the weighting from the first known-dose session. Flag this as an implementation decision to be resolved during Phase 6.

- **Dose event detection sensitivity threshold:** The derivative threshold for detecting a new dose event from the C_est(t) curve is not derivable from first principles alone — it depends on actual sensor noise floor. Should be set empirically after Phase 3 signal processing is validated. Default starting point: a rise of >1.5 bpm in 15-minute averaged HR above current running baseline.

- **Accelerometer range recommendation inconsistency:** STACK.md recommends ±2g; PITFALLS.md (P-09) recommends ±4g as a compromise to avoid saturation from voluntary motion. Use ±4g per PITFALLS.md.

---

## Sources

### Primary (HIGH confidence)

- SparkFun ICM-42688-P Arduino Library GitHub (sparkfun/SparkFun_ICM-42688-P_ArduinoLibrary) — library API and version
- SparkFun MAX3010x Arduino Library GitHub (sparkfun/SparkFun_MAX3010x_Sensor_Library) — library API and version
- NeuroKit2 GitHub (neuropsychology/NeuroKit) — PPG pipeline, Elgendi peak detector
- MAX30102 datasheet (Maxim Integrated) — register map, FIFO behavior, sample rate options
- TDK InvenSense ICM-42688-P datasheet — ODR options, FIFO, I2C address

### Secondary (MEDIUM confidence)

- Benowitz NL (1990) — caffeine PK one-compartment parameters
- Blanchard J, Sawers SJA (1983) — caffeine PK reference curves
- Bonati M et al. (1982) — caffeine PK parameters
- Graham TE, Spriet LL (1995) — caffeine HR dose-response (3–5 bpm per 100 mg)
- Goldstein ER et al. (2010) ISSN position stand — caffeine HR effect confirmation
- Hallett M (1998) review — physiological tremor 8–12 Hz band and caffeine effect
- Morgan MH et al. (1983) — caffeine tremor amplitude
- Hicks RG et al. (1972) — caffeine tremor frequency band
- Vlcek M et al. (2008) — caffeine and HRV/RMSSD reduction
- Denaro CP et al. (1991) — caffeine tolerance and blunted HR response
- Fredholm BB et al. (1999) — CYP1A2 individual variability in caffeine elimination

### Tertiary (LOW confidence — absence verified, not positive claim)

- No published open-source Python implementation of caffeine estimation from wearable biosignals (IMU + PPG) found as of mid-2025. This project is novel in this combination. (Verified absence.)

---

*Research completed: 2026-03-12*
*Ready for roadmap: yes*
