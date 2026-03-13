# Features: Caffeine Estimation System (AX6 + MAX30102)

**Research type:** Features dimension
**Date:** 2026-03-12
**Project:** Caffeine Estimation Algorithm — AX6 6-axis IMU + MAX30102 on Arduino, streamed to PC

---

## Summary

A caffeine biosignal estimation system needs three functional layers: (1) signal feature extraction from PPG/HR and accelerometer data, (2) per-user baseline calibration to convert absolute signals into caffeine-attributable deltas, and (3) a pharmacokinetic (PK) model that maps signal deltas to plasma concentration and inverts that to inferred dose. The features below are grounded in published caffeine physiology and signal processing literature.

**Key dependency chain:**
`Baseline calibration → Signal delta computation → Concentration mapping → PK inverse model → Dose/cumulative estimate`

---

## Table Stakes
*Essential for the algorithm to produce any estimate at all.*

### HR / PPG Features

| Feature | Description | Complexity | Source / Rationale |
|---|---|---|---|
| Heart rate (BPM) extraction | Pan-Tompkins or peak-detection on MAX30102 raw PPG; yields instantaneous HR | Low | Standard peak-detection on IR photodiode signal. Caffeine raises resting HR ~3–5 bpm per 100 mg (Graham & Spriet 1995; Goldstein et al. 2010). |
| HR delta (ΔHR) from baseline | Subtract resting baseline HR from current HR | Low | Individual resting HRs vary 50–90 bpm; delta collapses inter-subject variance. This is the primary HR feature used in the PK mapping. |
| Signal quality / artifact rejection | Discard PPG windows with motion artifacts (high-frequency noise, clipping, excessive variance) | Low–Medium | Motion corrupts PPG; must gate estimation to clean windows only. |

### Accelerometer / IMU Features

| Feature | Description | Complexity | Source / Rationale |
|---|---|---|---|
| RMS amplitude (full band) | RMS of accelerometer magnitude in resting hand window (e.g., 1–2 s epochs) | Low | First-order tremor intensity measure. Caffeine increases physiological tremor amplitude (Hicks et al. 1972; Morgan et al. 1983). |
| Power spectral density (PSD) in 8–12 Hz band | FFT or Welch PSD of accelerometer signal; extract band power in 8–12 Hz physiological tremor band | Medium | Caffeine selectively amplifies physiological tremor in 8–12 Hz band. Band power is more specific than broadband RMS (Hallett 1998; Raethjen et al. 2000). |
| Dominant tremor frequency | Peak frequency of PSD in 6–14 Hz range | Medium | Caffeine shifts dominant frequency slightly upward; secondary feature but helps discriminate caffeine tremor from other sources. |
| Tremor delta (Δtremor RMS, Δband power) from baseline | Subtract resting baseline tremor from current tremor | Low | Same rationale as ΔHR — individual resting tremor varies widely; delta is the usable signal. |

### Baseline Calibration

| Feature | Description | Complexity | Dependencies |
|---|---|---|---|
| Resting HR baseline measurement | 2–5 minute resting window at session start before any caffeine; average HR recorded | Low | Must precede all estimation. Sets HR₀ for ΔHR computation. |
| Resting tremor baseline measurement | Same resting window; compute baseline tremor RMS and band power | Low | Must precede all estimation. Sets tremor₀ for Δtremor computation. |
| Baseline storage per session | Store HR₀ and tremor₀ for the duration of a session | Low | Required for all delta computations throughout the day. |

### Pharmacokinetic Model

| Feature | Description | Complexity | Source / Rationale |
|---|---|---|---|
| One-compartment oral absorption model | C(t) = (D·ka)/(Vd·(ka−ke)) · (e^(−ke·t) − e^(−ka·t)). Parameters: ka ≈ 1.5 hr⁻¹, ke ≈ 0.139 hr⁻¹ (t½ ≈ 5 hr), Vd ≈ 0.6 L/kg | Low–Medium | Well-validated for caffeine (Benowitz 1990; Magkos & Kavouras 2005). Single compartment is sufficient for oral caffeine; two-compartment adds negligible accuracy gain. |
| Multi-dose superposition | Sum of concentration curves for each estimated dose; C_total(t) = Σ C_i(t − t_i) | Medium | Caffeine accumulates across a day; must handle 2–5 doses spaced hours apart to meet the 0–600 mg range requirement. |
| Inverse PK solver | Given observed concentration curve C_obs(t), infer dose D and timing t_dose. Minimum viable: least-squares fit of D to observed ΔHR/tremor-derived signal | Medium | This is the core inference step. Without it, no dose estimate is possible. |
| Concentration → signal mapping function | Maps plasma concentration C(t) to expected ΔHR and Δtremor using linear or piecewise-linear transfer functions derived from literature | Medium | Bridges the PK model and the sensor signals. ΔHR ≈ (3–5 bpm) / (100 mg / Vd·BW) × C(t). Tremor response is more nonlinear; may need a simple saturating function. |

### Output / Reporting (Minimum)

| Feature | Description | Complexity |
|---|---|---|
| Real-time HR and tremor display | Numeric or simple plot of current HR, ΔHR, tremor RMS updated each window | Low |
| Current estimated plasma concentration C(t) | Display current inferred caffeine concentration (mg/L or µg/mL) | Low |
| Cumulative estimated dose (mg) | Running total of estimated caffeine intake; updated as new dose events are detected | Low |
| Dose event detection | Detect when a new caffeine dose has been ingested (rise in ΔHR / tremor above threshold) | Medium |

---

## Differentiators
*Improve accuracy or usability beyond basic function. Optional for v1.*

### HR / PPG

| Feature | Description | Complexity | Value |
|---|---|---|---|
| Heart rate variability (HRV) — RMSSD | Compute RMSSD (root mean square of successive RR differences) from PPG peak intervals | Medium | Caffeine reduces parasympathetic tone, decreasing HRV. RMSSD adds a second independent signal dimension for concentration mapping (Vlcek et al. 2008). Requires clean PPG with reliable R-R detection. |
| HRV frequency domain (LF/HF ratio) | FFT of RR interval series; LF/HF ratio as sympathovagal balance index | High | Caffeine increases LF/HF. Adds signal richness but requires 5-minute stable windows and careful artifact removal — likely impractical in a dynamic daily-use scenario. |
| SpO₂ monitoring | MAX30102 supports SpO₂; compute from red/IR ratio | Low–Medium | Caffeine does not reliably alter SpO₂ at normal doses. Low direct value for caffeine estimation but could flag motion artifact or sensor detachment. |

### Accelerometer / IMU

| Feature | Description | Complexity | Value |
|---|---|---|---|
| Gyroscope features | Use AX6 gyroscope channels in addition to accelerometer for tremor characterization | Low–Medium | Gyroscope captures rotational tremor; may improve signal-to-noise in the 8–12 Hz band. The AX6 provides this for free — worth including if accelerometer alone is insufficient. |
| Per-axis tremor analysis | Analyze X, Y, Z axes separately rather than vector magnitude | Low | Tremor is not isotropic; dominant axis may vary by hand orientation. Per-axis analysis could improve sensitivity at low doses. |
| Activity-gated windowing | Only use windows where the user is at rest (low broadband acceleration) | Medium | Caffeine tremor is masked by voluntary movement. Automatic rest detection prevents corrupted estimates. Dependent on reliable motion classifier. |

### Calibration

| Feature | Description | Complexity | Value |
|---|---|---|---|
| Per-user PK parameter adjustment | Allow weight input to scale Vd (volume of distribution). Vd = 0.6 L/kg × body_weight_kg | Low | Reduces systematic dose error by ~10–15% compared to using a fixed population Vd. Weight is easy to collect once. |
| Known-dose calibration session | User consumes a known caffeine dose (e.g., 100 mg tablet); observed ΔHR and Δtremor used to fit individual sensitivity coefficients | High | Most accurate approach — personalizes the concentration→signal transfer function. However, requires a controlled calibration session, which may not be practical. Revisit if accuracy target is not met. |
| Caffeine tolerance flag | Simple binary flag: regular drinker vs. naive. Shifts expected ΔHR and Δtremor magnitudes (tolerant users show blunted HR response) | Low | Tolerance reduces caffeine-induced HR elevation by ~30–50% in heavy users (Denaro et al. 1991). Even a binary flag improves mapping function accuracy. |

### Output / Reporting

| Feature | Description | Complexity | Value |
|---|---|---|---|
| Time-series plot of C(t) | Plot estimated plasma concentration curve over the session | Low–Medium | Useful for debugging and validation; shows expected concentration profile vs. time. |
| Confidence interval on dose estimate | Propagate parameter uncertainty (ka, ke, individual variability) to report ±range on dose estimate | High | Scientifically rigorous but complex for v1. |
| Dose timing reconstruction | Report estimated time of each dose ingestion, not just magnitude | Medium | Useful for multi-dose tracking; requires reliable dose event detection. |
| Session summary export | Export session data (raw signals, features, estimates) to CSV | Low | Essential for academic validation and debugging. |

---

## Anti-Features
*Things that would hurt: over-engineering, unnecessary complexity, scope creep.*

| Anti-Feature | Why It Hurts |
|---|---|
| Machine learning / neural network model | No training dataset exists. A model trained on population data would need individual fine-tuning anyway. Adds massive complexity with no accuracy benefit over the physics-based PK approach. Explicitly out of scope. |
| Two-compartment PK model | Marginal improvement in caffeine kinetics accuracy at 2–3× implementation complexity. One-compartment is well-validated for caffeine. |
| Real-time onboard Arduino estimation | Arduino lacks the floating-point compute and memory for PK model computation. All processing on PC via serial stream is the right architecture. |
| Mobile app or Bluetooth streaming | Out of scope. USB serial to PC is simpler, more reliable for academic prototype, and avoids wireless latency. |
| Confounder modeling (exercise, stress, anxiety) | Exercise raises HR by 20–100 bpm — orders of magnitude larger than caffeine's 3–5 bpm effect. Attempting to model confounders without a controlled environment will introduce more error than it removes. Controlled environment assumption is the right call for proof-of-concept. |
| Adaptive PK parameter estimation (Bayesian updating) | Elegant but high complexity. Requires a prior distribution over PK parameters and real-time posterior updating. Not justified when the project has no training data and a hard 50 mg accuracy target. |
| HRV frequency-domain analysis (LF/HF) | Requires stable 5-minute windows with low noise. In a daily-use scenario with natural activity, this is rarely available. RMSSD (if HRV is desired) is far more practical. |
| Absolute concentration calibration without known dose | Cannot calibrate the concentration→signal transfer function from signals alone; need at least one reference point (known dose or literature values). Attempting to bootstrap this from signals only is circular. |
| Per-minute dose updates | The PK model has a time resolution limited by the absorption curve (meaningful changes occur over 15–30 minute windows). Updating dose estimate every minute adds noise without information. Use 10–15 minute sliding windows for stable estimates. |

---

## Feature Dependency Map

```
SESSION START
    │
    ▼
[Baseline measurement: HR₀, tremor RMS₀, tremor band power₀]   ← MUST RUN FIRST
    │
    ▼
[Continuous signal acquisition: PPG → HR; AX6 → tremor features]
    │
    ▼
[Delta computation: ΔHR = HR − HR₀,  Δtremor = tremor − tremor₀]
    │
    ▼
[Signal-to-concentration mapping: ΔHR + Δtremor → C_obs(t)]
    │          │
    │          └── [Caffeine tolerance flag] (optional, scales mapping)
    │          └── [Per-user Vd scaling from weight] (optional)
    ▼
[One-compartment PK model: C(t) = f(D, ka, ke, Vd, t)]
    │
    ▼
[Inverse PK solver: C_obs(t) → D̂, t̂_dose]
    │
    ▼
[Multi-dose superposition: accumulate D̂_1, D̂_2, ... → cumulative estimate]
    │
    ▼
[Output: real-time C(t) display, cumulative dose display, dose event log]
    │
    └── [Session export: CSV of raw signals + features + estimates]
```

**Critical path:** Baseline → Deltas → Mapping → PK forward model → Inverse solver → Multi-dose accumulation

**Optional enhancements that can be added without re-architecting:**
- HRV (RMSSD) feeds into signal-to-concentration mapping as an additional input channel
- Gyroscope features feed into tremor feature vector
- Activity-gating gates the delta computation step
- Per-user calibration session replaces literature-derived mapping coefficients with individual ones

---

## Complexity Summary

| Layer | Feature | Complexity | Priority |
|---|---|---|---|
| PPG/HR | HR extraction (BPM) | Low | Table stakes |
| PPG/HR | ΔHR from baseline | Low | Table stakes |
| PPG/HR | Artifact rejection | Low–Medium | Table stakes |
| PPG/HR | HRV (RMSSD) | Medium | Differentiator |
| IMU | Tremor RMS | Low | Table stakes |
| IMU | 8–12 Hz band power (PSD) | Medium | Table stakes |
| IMU | Dominant tremor frequency | Medium | Table stakes |
| IMU | Δtremor from baseline | Low | Table stakes |
| IMU | Gyroscope features | Low–Medium | Differentiator |
| IMU | Activity-gated windowing | Medium | Differentiator |
| Calibration | Resting baseline (HR + tremor) | Low | Table stakes |
| Calibration | Per-user Vd (weight input) | Low | Differentiator |
| Calibration | Tolerance flag | Low | Differentiator |
| Calibration | Known-dose calibration session | High | Differentiator (v2) |
| PK model | One-compartment forward model | Low–Medium | Table stakes |
| PK model | Inverse solver (least-squares D̂) | Medium | Table stakes |
| PK model | Multi-dose superposition | Medium | Table stakes |
| PK model | Signal→concentration mapping fn | Medium | Table stakes |
| Output | Real-time HR/tremor display | Low | Table stakes |
| Output | Current C(t) display | Low | Table stakes |
| Output | Cumulative dose display | Low | Table stakes |
| Output | Dose event detection | Medium | Table stakes |
| Output | Session CSV export | Low | Differentiator |
| Output | C(t) time-series plot | Low–Medium | Differentiator |

---

## Key Literature Grounding

- **Caffeine HR effect**: ~3–5 bpm increase per 100 mg in non-tolerant adults (Graham & Spriet 1995; Goldstein et al. 2010 ISSN position stand). Effect is blunted ~30–50% in habitual consumers (Denaro et al. 1991).
- **Caffeine tremor effect**: Caffeine increases physiological tremor amplitude and power in 8–12 Hz band at doses ≥ 200 mg (Hicks et al. 1972; Morgan et al. 1983; Hallett 1998 review). Tremor response is more variable inter-individually than HR response.
- **HRV and caffeine**: Caffeine reduces HRV (RMSSD, pNN50) by reducing parasympathetic modulation (Vlcek et al. 2008; Zimmermann-Viehoff et al. 2015). Effect is dose-dependent.
- **Caffeine PK**: One-compartment model with ka ≈ 1.0–2.0 hr⁻¹ (typical 1.5), ke ≈ 0.139 hr⁻¹ (t½ = 5 hr), Vd ≈ 0.6 L/kg. Peak plasma concentration at Tmax ≈ 30–60 min post-ingestion (Benowitz 1990; Magkos & Kavouras 2005). Oral bioavailability ~100%.
- **Delta-based estimation**: Using individual deltas from resting baseline substantially reduces inter-subject variability in physiological response studies (standard approach in exercise physiology and pharmacology research).

---

*Generated by gsd-project-researcher | 2026-03-12*
