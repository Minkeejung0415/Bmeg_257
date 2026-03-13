# PITFALLS — Caffeine Estimation via Biosensors (AX6 + MAX30102)

**Project**: Caffeine Estimation Algorithm (AX6 + MAX30102)
**Research type**: Pitfalls dimension — domain-specific failure modes
**Date**: 2026-03-12
**Accuracy constraint**: Error < 50mg across 0–600mg range

---

## How to use this document

Each pitfall includes:
- **Warning signs** — how to detect the problem early
- **Prevention strategy** — concrete actions to avoid it
- **Phase** — when in the project lifecycle to address it

---

## Part 1: MAX30102 PPG Sensor Pitfalls

### P-01: Motion Artifacts Corrupt the PPG Signal

**Description**: The MAX30102 uses reflected photoplethysmography — ambient body motion shifts the LED-photodiode geometry relative to skin capillaries and creates low-frequency noise that completely masks the cardiac pulse. Even small hand movements (typing, shifting grip) produce artifact amplitudes that dwarf the cardiac signal.

**Warning signs**:
- Heart rate readout spikes or drops by >20 bpm in <5 seconds
- FFT of raw IR channel shows broadband noise rather than a clean peak near 1–2 Hz
- Signal looks clean during strict stillness, unreliable the moment the hand moves
- Standard deviation of beat-to-beat interval (RMSSD) is implausibly large (>100ms)

**Prevention strategy**:
- Collect baseline HR only during enforced rest windows (subject sitting still, arm relaxed on a surface) — never during active tremor measurement
- Implement a signal quality index (SQI): reject HR readings where peak-to-trough amplitude in the 0.8–3 Hz band drops below a threshold (e.g., top 5% of baseline AC amplitude)
- Apply a bandpass filter 0.5–4 Hz before peak detection — this is mandatory, not optional
- Record raw IR channel values; log when subject is instructed to hold still vs. moving, so artifact windows can be flagged in post-processing
- Do NOT run HR extraction and tremor measurement simultaneously on the same hand if the AX6 is also attached — decouple motion and PPG windows

**Phase**: Arduino firmware + PC signal processing (Phase 1 & 2)

---

### P-02: Incorrect LED Drive Current / Sensor Gain

**Description**: The MAX30102 has configurable LED pulse amplitude (0–51 mA in 200 µA steps) and sample averaging. Default example code commonly ships with very low LED current (e.g., 6.4 mA) which is insufficient for reliable perfusion sensing on fingertip or wrist. On dark skin or subjects with poor peripheral perfusion, the AC component of the IR channel becomes too small to resolve reliably.

**Warning signs**:
- Raw IR channel ADC values show very small AC amplitude relative to DC offset (AC/DC ratio < 0.5%)
- SparkFun/Adafruit example code is used without reviewing register defaults
- Heart rate detection only works on one tester and fails on others with different skin tones/finger sizes

**Prevention strategy**:
- Set IR LED current to 25–50 mA for fingertip placement; start at 25 mA and increase if AC amplitude is inadequate
- Verify during hardware bring-up: AC/DC ratio of the IR channel should be ~1–3% on a healthy fingertip with proper pressure; below 0.5% means insufficient LED power or poor contact
- Log raw ADC values at startup and assert a minimum AC amplitude threshold before declaring the sensor "ready"
- Never use SpO2-optimized register settings (which prioritize red LED current balance) when the goal is heart rate extraction from IR only

**Phase**: Arduino firmware bring-up (Phase 1)

---

### P-03: SpO2 Mode vs. Heart Rate Mode Confusion

**Description**: The MAX30102 can operate in HR-only mode (IR LED only) or SpO2 mode (red + IR LED alternating). SpO2 mode halves the effective sampling rate per channel, increases power consumption, and the red channel adds no value for HR extraction. Using SpO2 mode accidentally reduces the HR channel sample rate and introduces red-channel crosstalk artifacts.

**Warning signs**:
- Both red and IR channels are being logged but only IR is used downstream — SpO2 mode is on unnecessarily
- Apparent sample rate is half of configured rate (SpO2 mode interleaves two channels)
- Register 0x09 (MODE_CONFIG) is set to 0x03 (SpO2) rather than 0x02 (HR only)

**Prevention strategy**:
- Explicitly set MODE_CONFIG register to HR-only mode (0x02) in firmware
- Confirm by reading register back and asserting the expected value at startup
- If SpO2 is ever needed as a secondary signal, treat it as a separate mode with separate post-processing

**Phase**: Arduino firmware (Phase 1)

---

### P-04: Sensor Placement and Pressure Sensitivity

**Description**: PPG signal quality is highly dependent on consistent placement and contact pressure. Too light — air gap and ambient light leak. Too tight — venous occlusion flattens the AC waveform. The fingertip is best but requires a stable clip or housing; fingertip-on-flat-surface contact (held by the subject) introduces movement.

**Warning signs**:
- Saturated ADC values (IR channel at or near 0x3FFFF = 262143) — excessive ambient light or LED overdrive
- Very low DC offset (<50,000 counts) — insufficient contact
- Heart rate readings that differ by >10 bpm between trials with the same subject and caffeine dose

**Prevention strategy**:
- Use a fingertip clip enclosure or elastic wrap to ensure consistent contact and block ambient light
- At session start, run a 30-second "sensor check" and print a quality indicator before proceeding
- Establish a placement protocol in the experimental procedure: same finger (index or middle), same hand, same orientation every session
- Log ambient light interference: run MAX30102 with LEDs off for 1 second at startup to measure ambient noise floor; abort if above threshold

**Phase**: Hardware setup + experimental protocol (Phase 1)

---

### P-05: Insufficient Sampling Rate for Heart Rate Accuracy

**Description**: The MAX30102 supports sample rates from 50 to 3200 Hz. Using too low a rate (50 Hz) reduces peak-detection accuracy for HR calculation. Using too high a rate (>400 Hz) wastes Arduino serial bandwidth without benefit for HR. The typical mistake is leaving the default without considering the HR extraction algorithm's requirements.

**Warning signs**:
- Sample rate register (0x0A) left at power-on default (50 Hz)
- HR variability (HRV) derived metrics show quantization artifacts (HR values only jump in fixed increments)
- Serial baud rate is too low for the actual sample output rate, causing buffer backup

**Prevention strategy**:
- Set sample rate to 100 Hz for HR extraction — provides adequate temporal resolution with manageable data rate
- At 100 Hz with 18-bit samples, each sample is 3 bytes × 2 channels = 6 bytes/sample = 600 bytes/s, well within 115200 baud
- Use peak-detection with parabolic interpolation to improve sub-sample timing accuracy at 100 Hz

**Phase**: Arduino firmware (Phase 1)

---

## Part 2: AX6 / IMU Tremor Measurement Pitfalls

### P-06: Aliasing from Insufficient Sampling Rate

**Description**: Caffeine-induced physiological tremor occurs in the 8–12 Hz band. The Nyquist theorem requires sampling at >24 Hz minimum; in practice, ≥50 Hz is needed to reliably resolve the 8–12 Hz band. The AX6 supports rates up to 3200 Hz, but if firmware configures it too low (e.g., 12.5 Hz default in low-power mode), the tremor band aliases down to DC or low-frequency noise.

**Warning signs**:
- AX6 ODR register left at default low-power value (12.5 Hz)
- FFT of accelerometer data shows no energy above 6 Hz even during known tremor
- Tremor features don't change between caffeine and no-caffeine conditions

**Prevention strategy**:
- Set AX6 accelerometer ODR to 100 Hz minimum; 200 Hz is preferable for clean anti-aliasing margin above the 12 Hz tremor ceiling
- Verify ODR by checking the timestamp delta between consecutive samples
- Apply a digital low-pass anti-alias filter in firmware or as the first step in PC processing (cutoff = ODR/2 minus margin, e.g., 40 Hz at 100 Hz ODR)
- Do not use the AX6 activity/step-counter integrated functions — read raw accelerometer registers directly for the tremor pipeline

**Phase**: Arduino firmware (Phase 1)

---

### P-07: Orientation Dependence and Gravity Contamination

**Description**: Raw accelerometer data contains a 1g gravity component along whichever axis is aligned with vertical. If the hand tilts even slightly between baseline and post-caffeine measurements, the change in gravity projection onto the sensor axes looks like a DC shift and contaminates RMS amplitude. Gravity is approximately 9.8 m/s² — a 5-degree tilt change produces ~0.085g DC shift, which is comparable to tremor amplitudes (<0.05g for mild caffeine tremor).

**Warning signs**:
- Tremor RMS values differ significantly between sessions where the subject held their hand at slightly different angles
- DC component of accelerometer signal changes between rest and "tremor" conditions in a way that doesn't track actual tremor
- Mean of accelerometer signal over a 1-second window is not near the expected gravity projection (should be stable if hand orientation is fixed)

**Prevention strategy**:
- High-pass filter the accelerometer data before computing tremor RMS — cutoff at 1–2 Hz removes gravity and slow postural drift while preserving the 8–12 Hz tremor band
- Alternatively, use vector magnitude (sqrt(ax² + ay² + az²) − 1g) but high-pass filtering is simpler and more robust
- Standardize hand position in the experimental protocol: arm extended, hand flat, resting on surface during measurement windows (this also reduces voluntary motion)
- Use the gyroscope channel as a cross-check: if gyroscope shows large angular velocity, the hand is actively moving (voluntary motion), not tremoring

**Phase**: PC signal processing (Phase 2)

---

### P-08: Voluntary Motion vs. Physiological Tremor Confusion

**Description**: Physiological tremor from caffeine is at 8–12 Hz with amplitude <0.1g. Voluntary motion (reaching, typing, shifting grip) produces signals at 0–5 Hz with amplitudes of 0.5–5g. If voluntary motion epochs are included in the tremor feature extraction window, they completely dominate the tremor RMS and frequency estimates, producing false "high tremor" readings.

**Warning signs**:
- Tremor RMS is orders of magnitude higher (>0.5g) than published caffeine tremor amplitudes (<0.1g)
- Dominant frequency from FFT is below 5 Hz (voluntary movement range) rather than 8–12 Hz
- Tremor features correlate more with "was the subject active" than with caffeine dose

**Prevention strategy**:
- Enforce a measurement protocol: tremor is only measured during 30-second windows of controlled posture (arm outstretched, fingers spread, as still as possible)
- Flag motion events: any 1-second window where bandpass-filtered (1–5 Hz) RMS exceeds a threshold (e.g., 0.2g) is marked as a voluntary motion artifact and excluded from tremor feature calculation
- Extract tremor features only from the 6–15 Hz bandpass-filtered signal, not broadband RMS
- The gyroscope channel is valuable here: zero angular velocity confirms postural hold; high angular velocity flags active movement

**Phase**: Experimental protocol + PC signal processing (Phase 1 & 2)

---

### P-09: Accelerometer Range vs. Resolution Trade-off

**Description**: The AX6 accelerometer supports ±2g, ±4g, ±8g, ±16g ranges. At ±16g, the LSB resolution is 0.488 mg — caffeine tremor amplitudes (~10–50 mg peak) would be resolved by only 20–100 counts, which is marginal. At ±2g range, resolution is 0.061 mg — adequate for tremor but saturates if the subject makes any sharp movement.

**Warning signs**:
- Using ±16g range (common default in some AX6 libraries) for tremor measurement
- Tremor feature values show quantization steps rather than smooth variation
- Tremor cannot be distinguished from sensor noise floor

**Prevention strategy**:
- Set accelerometer range to ±4g as a compromise: 0.122 mg resolution, 40× above tremor floor, and only saturates at very strong voluntary movements
- Alternatively, use ±2g but implement saturation detection (clip detection) to flag and discard saturated windows
- Log the configured range and assert it at startup

**Phase**: Arduino firmware (Phase 1)

---

## Part 3: Pharmacokinetic Model Pitfalls

### P-10: Using Wrong Population-Average PK Parameters

**Description**: The one-compartment oral absorption model uses three parameters: absorption rate constant (ka), elimination rate constant (ke), and volume of distribution (Vd). Population averages from literature have wide ranges: ka = 1–5 hr⁻¹, half-life = 2.5–10 hr (ke = 0.07–0.28 hr⁻¹), Vd = 0.5–0.7 L/kg. Using the wrong end of these ranges shifts the predicted peak plasma concentration by 2–4×, and shifts the time-to-peak by 30–60 minutes.

**Warning signs**:
- Using a single published value without citing source or acknowledging range
- Model predicts peak effect earlier/later than the subject experiences it by >45 minutes
- Estimated dose diverges from actual dose systematically (always over or under)
- Half-life assumed to be exactly 5 hours without acknowledging the CYP1A2 variation

**Prevention strategy**:
- Use the mid-range consensus values as defaults: ka = 2 hr⁻¹, half-life = 5 hr (ke = 0.139 hr⁻¹), Vd = 0.6 L/kg × body weight
- Implement PK parameter uncertainty: run the inverse model with parameter ranges (±30%) and report estimated dose as a range, not a point estimate
- Add a session-specific ka calibration option: if the subject records exact dose and time for the first coffee, fit ka from the observed HR peak timing
- Document which literature sources the PK parameters come from (Fredholm et al. 1999, or Blanchard & Sawers 1983 are standard references)

**Phase**: PK model implementation (Phase 3)

---

### P-11: Ignoring Food Effects on Caffeine Absorption

**Description**: Food in the stomach significantly slows caffeine absorption. Fasted ka ≈ 3–5 hr⁻¹ (time-to-peak ~30–45 min); fed state ka ≈ 0.5–1 hr⁻¹ (time-to-peak 90–120 min). Using a single ka value when the subject sometimes consumes caffeine with food and sometimes without creates systematic timing errors that translate to large dose estimation errors.

**Warning signs**:
- HR peak timing relative to caffeine dose is inconsistent across sessions (±60 minutes variation)
- Model consistently overestimates dose when caffeine was taken with a meal (because observed HR rise is delayed relative to model prediction, making the model think a smaller dose was taken later)
- Experimental protocol does not record whether caffeine was consumed with food

**Prevention strategy**:
- Record "fasted vs. fed" as a required field in the experimental data log for every caffeine dose event
- Use two ka values: ka_fasted = 3.0 hr⁻¹, ka_fed = 0.8 hr⁻¹ — switch based on the logged meal state
- Simplify the experiment: run all sessions fasted (≥2 hours since last meal) to eliminate this variable for an academic proof-of-concept
- Document this as a known limitation if food state is not controlled

**Phase**: Experimental protocol + PK model (Phase 1 & 3)

---

### P-12: Ignoring CYP1A2 Individual Variability in Elimination

**Description**: Caffeine is metabolized almost entirely by CYP1A2. This enzyme has major genetic polymorphisms: "fast metabolizers" have half-lives of ~2.5–3 hours; "slow metabolizers" have half-lives of 8–10 hours. Additionally, smoking induces CYP1A2 (cutting half-life in half), while oral contraceptives and some SSRIs inhibit it (doubling half-life). Using a fixed population-average half-life of 5 hours on a slow metabolizer will cause the model to underestimate late-session cumulative caffeine by 50–100%.

**Warning signs**:
- Subject reports feeling caffeinated hours after the model predicts plasma concentration should be near zero
- Late-afternoon caffeine dose estimation consistently diverges from early-morning accuracy
- Subject is on medications or smokes — this was not recorded

**Prevention strategy**:
- Record subject demographics in every session: smoking status, hormonal contraceptive use, known CYP1A2 medications
- For single-subject academic validation, fit the elimination rate from multi-dose session data: if dose and timing are known exactly for session 1, fit ke to the HR decay curve
- Treat ke as a personal parameter once calibrated (store per-subject), rather than re-using population average indefinitely
- Acknowledge ±50% ke uncertainty in accuracy error budget analysis

**Phase**: PK model + experimental protocol (Phase 1 & 3)

---

### P-13: Incorrect Inverse PK Model — Ill-Posed Dose Estimation

**Description**: Going from observed physiological response → plasma concentration → dose is an inverse problem. The forward model C(t) = f(D, ka, ke, Vd, t_dose) has multiple solutions: a low dose taken 2 hours ago produces a similar plasma concentration profile to a moderate dose taken 3 hours ago. Without knowing the dose timing, the inverse problem is underdetermined. Additionally, multiple doses overlap in the plasma concentration curve, making it hard to deconvolve individual dose contributions.

**Warning signs**:
- Dose estimation is sensitive to small errors in HR measurement (1 bpm error → large dose error)
- Multi-dose sessions produce worse accuracy than single-dose sessions
- Estimated dose is very different depending on assumed t_dose (dose timing)

**Prevention strategy**:
- Require dose timing as a logged input (the subject records when they drank coffee, even if not how much) — reduces the inverse problem to dose magnitude estimation only
- For multi-dose days: use the temporal structure of the HR/tremor signal to detect dose events (step increase in HR above baseline) and assign timing automatically — but validate this detection separately
- Implement the inverse as a constrained optimization: minimize (observed_signal − predicted_signal)² subject to D ≥ 0, using scipy.optimize.minimize_scalar per dose window
- Validate the inverse model on synthetic data (generate C(t) with known D, add realistic noise, then recover D) before ever running on real sensor data

**Phase**: Algorithm development (Phase 3)

---

## Part 4: The <50mg Accuracy Wall

### P-14: The Signal-to-Noise Problem at Low Caffeine Doses

**Description**: At 50–100mg caffeine (half a cup of coffee), the expected HR increase is only 1.5–3 bpm above baseline. The MAX30102 measuring resting HR has a typical accuracy of ±2–3 bpm even under ideal conditions. This means the signal from a 50mg dose is within the sensor noise floor — the signal-to-noise ratio is ~1:1. Without averaging over many minutes, a 50mg dose cannot be reliably detected from HR alone.

**Warning signs**:
- Attempting to detect a single dose from a single 30-second HR window
- HR standard deviation across multiple baseline measurements is ≥3 bpm
- Accuracy is good at 200–400mg but fails at 50–100mg doses

**Prevention strategy**:
- Use 5–10 minute averaged HR windows, not single measurements — this reduces noise by sqrt(10), improving SNR to ~3:1 for a 50mg dose
- The 50mg accuracy target is most achievable in cumulative estimation (total daily intake) rather than single-dose precision — frame the requirement correctly
- Tremor is a necessary second signal for low-dose discrimination: tremor may show a detectable increase at 100mg even when HR does not
- Sensor fusion (HR delta + tremor RMS + tremor dominant frequency) combined via weighted estimate is the only viable path to <50mg accuracy — neither signal alone achieves it

**Phase**: Algorithm design — needs explicit acknowledgment before finalizing requirements (Phase 3)

---

### P-15: Individual Variation in HR and Tremor Response to Caffeine

**Description**: Population-average caffeine effect sizes (3–5 bpm per 100mg, 8–12 Hz tremor increase) have very large inter-individual standard deviations. Some individuals show 8 bpm per 100mg; others show 1 bpm per 100mg (particularly habitual high-dose consumers with tolerance). The slope of the physiological response function is the key parameter for dose estimation, and it varies 5–8× across individuals. A fixed population-average slope produces systematic dose errors proportional to the slope mismatch.

**Warning signs**:
- Using the same HR-to-dose slope for all subjects without personalization
- First real-world test shows consistent over- or underestimation (bias, not just noise)
- Accuracy is good for one subject and poor for another

**Prevention strategy**:
- Personal calibration is almost mandatory for <50mg accuracy: run at least one known-dose session per subject and fit the personal HR-per-mg slope
- Even a single calibration point dramatically reduces systematic error — the slope can be estimated from a single dose+response observation
- Design the experimental protocol to include one known-dose "calibration session" before the blind estimation sessions
- Document this as the core limitation of the no-training-data constraint: without personal calibration, the accuracy floor is approximately ±80–100mg; with calibration, ±30–50mg is achievable

**Phase**: Algorithm design + experimental protocol (Phase 1 & 3)

---

### P-16: Caffeine Tolerance and Baseline Drift

**Description**: Regular caffeine consumers have partial cardiovascular tolerance — their resting HR does not rise as much per unit of caffeine. Additionally, the resting HR baseline itself drifts over the course of a day due to circadian rhythm, hydration, posture, and stress. A baseline measured at 8am and used as a reference for a 3pm measurement will have accumulated 5–15 bpm of drift unrelated to caffeine.

**Warning signs**:
- Caffeine effect is underestimated in the afternoon relative to the morning
- Baseline HR measured once at session start diverges from subsequent rest-period HR by >5 bpm
- Subject is a habitual ≥4 cups/day consumer — tolerance means flat HR response

**Prevention strategy**:
- Re-measure baseline HR at each dose window (before the expected effect onset of each dose, not just once at session start)
- Include a "return-to-baseline" window in the experimental protocol — after caffeine has metabolized (>5 half-lives = 25+ hours), measure a clean baseline for the next day
- Screen for high habitual caffeine use in subject selection — heavy users may not show enough HR response to estimate dose accurately; document as inclusion/exclusion criteria
- Use HR trend (rate of change) in addition to absolute delta, as tolerance shifts the level but not necessarily the time-course slope

**Phase**: Experimental protocol + algorithm (Phase 1 & 3)

---

## Part 5: Arduino Serial Streaming Pitfalls

### P-17: Serial Buffer Overflow and Data Loss

**Description**: The Arduino hardware serial receive buffer is 64 bytes by default (some boards: 256 bytes). The transmit buffer is also 64 bytes. At high data rates (AX6 at 200 Hz + MAX30102 at 100 Hz = 300 samples/second), if the PC-side Python reader falls behind, the Arduino transmit buffer overflows silently — bytes are dropped without any error notification. This causes intermittent corrupt packets and timestamp gaps that are invisible without explicit sequence numbering.

**Warning signs**:
- Reconstructed signal has occasional jumps or freezes when plotted in real-time
- Lost samples only appear during periods of high PC CPU load (e.g., Python doing a blocking computation)
- No sequence number in the packet format — cannot detect lost packets

**Prevention strategy**:
- Add a 2-byte sequence counter to every packet — increment on the Arduino and check for gaps in Python; log any gaps explicitly
- Use binary protocol (not ASCII Serial.print) — a 6-axis 16-bit sample is 12 bytes binary vs. ~50+ bytes ASCII; binary reduces bandwidth 4× and dramatically reduces overflow risk
- On the Python side, use a dedicated serial reader thread that does nothing except read bytes into a queue; signal processing runs on the main thread from the queue — never do computation in the serial callback
- Set baud rate to 115200 or 230400 — do not use 9600 (common default) with high sample rates
- Use pyserial's `timeout` parameter — a blocking read without timeout hangs the reader thread if the Arduino resets

**Phase**: Arduino firmware + PC serial reader (Phase 1 & 2)

---

### P-18: Timing and Timestamping Errors

**Description**: Arduino millis() has a resolution of 1 ms but drifts relative to wall-clock time by up to 0.01–0.1% (several seconds per hour). If the PC assigns timestamps based on when it receives packets (not when they were sampled), buffering delays introduce jitter. For PK modelling, the timing of the HR peak after a dose matters to within 5–10 minutes; for tremor FFT, sample timing must be accurate to within 1/(2×ODR) = 5 ms at 100 Hz.

**Warning signs**:
- Inter-sample timestamps are irregular (variable instead of constant ODR period) when plotted
- Calculated sample rate from timestamps differs from configured ODR by >1%
- PK model timing errors — "dose taken at 10:00 but HR peak detected at 9:45" due to clock drift

**Prevention strategy**:
- Use Arduino timestamps (millis()) for relative inter-sample timing only; use the PC clock for absolute wall-clock time
- On the PC, record `(arduino_millis, pc_unix_timestamp)` pairs and use linear interpolation/regression to build an Arduino-to-PC clock mapping that corrects drift over the session
- Design the packet format to include a 4-byte Arduino timestamp (millis()) with every sample group
- Validate the clock mapping: at session start, send a known sync pulse and verify Arduino and PC timestamps align before collecting data

**Phase**: Arduino firmware + PC serial reader (Phase 1 & 2)

---

### P-19: I2C Bus Conflicts Between AX6 and MAX30102

**Description**: Both the AX6 and MAX30102 are I2C devices. I2C is a shared bus — if the firmware reads both sensors in a blocking sequential loop, the slower sensor (or the one that requires more bytes) creates latency that delays sampling of the other sensor. If the MAX30102 FIFO overflows while the firmware is reading AX6, samples are silently lost from the PPG buffer. This produces irregular effective sample rates and FIFO overflow flags that may not be checked.

**Warning signs**:
- MAX30102 FIFO overflow bit (OVF_COUNTER in register 0x04) is not zero during operation
- Effective sample rate of one sensor is lower than configured when computed from timestamps
- AX6 samples appear regular but MAX30102 HR signal has gaps

**Prevention strategy**:
- Check MAX30102 OVF_COUNTER register on every read; log any overflow events
- Configure MAX30102 sample averaging to reduce FIFO fill rate (average 4 samples internally = effective 25 Hz output at 100 Hz ODR) — reduces the urgency of reads
- Alternatively, use the MAX30102 interrupt pin (INT_n) to trigger reads only when the FIFO has new data, rather than polling blindly
- Profile the I2C loop timing: measure the total time of one full read cycle with a oscilloscope or by toggling a GPIO pin and measuring on a logic analyzer; ensure it completes within the ODR period (10 ms at 100 Hz)

**Phase**: Arduino firmware (Phase 1)

---

## Summary: Accuracy Error Budget

For the <50mg accuracy constraint, the total estimation error is the RSS (root-sum-of-squares) of:

| Error source | Typical contribution | Mitigatable? |
|---|---|---|
| HR measurement noise (±2 bpm → dose error) | ±40–80mg | Yes — averaging over 5–10 min |
| PK parameter (ke) uncertainty | ±30–50mg | Yes — personal ke calibration |
| PK parameter (ka / food state) uncertainty | ±20–60mg | Yes — control food state |
| Individual HR-per-mg slope variation | ±80–150mg | Yes — personal calibration session |
| Motion artifact in HR measurement | ±20–100mg | Yes — strict motion protocol |
| Serial data loss affecting signal quality | ±5–20mg | Yes — binary protocol + sequence IDs |
| Tremor measurement (adds independent signal) | reduces error | Yes — required for low-dose accuracy |

**Key conclusion**: Without a single personal calibration session (one known-dose measurement per subject), the <50mg accuracy target is not achievable. With personal calibration of the HR-per-mg slope and elimination rate, ±30–50mg is achievable for single doses above 100mg in a controlled setting.

---

## Pitfall Quick Reference

| ID | Pitfall | Phase | Severity |
|---|---|---|---|
| P-01 | PPG motion artifacts | 1+2 | Critical |
| P-02 | MAX30102 LED current too low | 1 | High |
| P-03 | SpO2 mode vs. HR mode | 1 | Medium |
| P-04 | Sensor placement inconsistency | 1 | High |
| P-05 | Insufficient PPG sample rate | 1 | Medium |
| P-06 | IMU ODR too low — aliasing | 1 | Critical |
| P-07 | Gravity contamination in tremor RMS | 2 | High |
| P-08 | Voluntary motion vs. tremor confusion | 1+2 | Critical |
| P-09 | Accelerometer range/resolution mismatch | 1 | Medium |
| P-10 | Wrong PK parameter values | 3 | High |
| P-11 | Ignoring food effects on ka | 1+3 | High |
| P-12 | CYP1A2 individual ke variability | 1+3 | High |
| P-13 | Ill-posed inverse PK problem | 3 | Critical |
| P-14 | SNR floor at low doses | 3 | Critical |
| P-15 | Individual HR/tremor response variation | 1+3 | Critical |
| P-16 | Tolerance and baseline drift | 1+3 | High |
| P-17 | Serial buffer overflow / data loss | 1+2 | High |
| P-18 | Timestamp drift and jitter | 1+2 | Medium |
| P-19 | I2C bus contention | 1 | Medium |

---

*Research basis: domain knowledge from biosignal processing literature, MAX30102 datasheet (Maxim Integrated), AX6/ADXL series IMU documentation, caffeine PK literature (Fredholm et al., Blanchard & Sawers), and Arduino serial communication engineering practice.*
