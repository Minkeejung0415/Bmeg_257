# Caffeine Estimation Algorithm (AX6 + MAX30102)

## What This Is

A software algorithm that estimates cumulative caffeine consumption in real-time using two wearable sensors: an AX6 6-axis IMU (to capture caffeine-induced hand tremor/jitter) and a MAX30102 optical heart rate sensor (to capture caffeine-induced heart rate elevation). An Arduino collects raw sensor data and streams it to a PC where the estimation algorithm runs. Target accuracy: error < 50mg across a 0–600mg+ range with multiple doses per day.

## Core Value

Accurately estimate the user's total caffeine intake from physiological signals alone — no manual logging required.

## Requirements

### Validated

(None yet — ship to validate)

### Active

- [ ] Arduino firmware reads AX6 (accelerometer/gyroscope) and MAX30102 (PPG/HR) continuously and streams timestamped data to PC
- [ ] PC-side signal processing extracts heart rate from MAX30102 PPG signal
- [ ] PC-side signal processing extracts tremor features (RMS amplitude, dominant frequency) from AX6 accelerometer data
- [ ] A pharmacokinetic (PK) model tracks caffeine plasma concentration over time for multiple doses
- [ ] Algorithm maps HR delta (vs. personal baseline) and tremor features to estimated caffeine plasma concentration
- [ ] Inverse PK model converts estimated plasma concentration back to ingested dose(s)
- [ ] System tracks multiple caffeine doses throughout the day with cumulative estimation
- [ ] Estimation error < 50mg across 0–600mg range

### Out of Scope

- Mobile app or wearable UI — data collected via Arduino serial/USB to PC
- Accounting for confounders (exercise, stress, anxiety) — controlled environment assumed
- ML/neural network models — ruled out due to no training dataset; algorithm is physics/PK-model driven
- Real-time onboard estimation on Arduino — algorithm runs on PC

## Context

- **Sensors**: AX6 (6-axis IMU, accelerometer + gyroscope) and MAX30102 (PPG heart rate sensor) wired to an Arduino
- **Architecture**: Arduino firmware (C/C++) for data collection → serial stream to PC → Python algorithm for processing and estimation
- **Caffeine pharmacokinetics**: One-compartment oral absorption model. Absorption rate constant ka ≈ 1–2 hr⁻¹, elimination half-life ≈ 5 hr (ke = 0.693/5 ≈ 0.139 hr⁻¹). For dose D: C(t) = (D·ka)/(Vd·(ka−ke)) · (e^(−ke·t) − e^(−ka·t))
- **Physiological signals**: Caffeine raises resting HR ~3–5 bpm per 100mg (population average from literature). Caffeine increases tremor amplitude and frequency (8–12 Hz band).
- **Baseline calibration**: Each session starts with a resting baseline measurement (HR, tremor RMS) before any caffeine is consumed — delta-based approach reduces individual variation
- **No training data**: Algorithm relies on published PK parameters and caffeine-HR/tremor relationships from literature, not from a trained model
- **Academic project**: Large-scale academic project requiring rigorous methodology and documentation

## Constraints

- **Platform**: Arduino for data acquisition (C/C++ firmware), Python for PC-side processing
- **Compute**: PC-side processing — no embedded compute constraint
- **Accuracy**: Error must be < 50mg (hard requirement)
- **Caffeine range**: Must cover 0–600mg+ (0 to ~5 cups of coffee or energy drinks)
- **Multi-dose**: Must handle multiple doses spaced hours apart in a single day
- **No ML**: No training dataset available — must use physics-based / PK-model approach

## Key Decisions

| Decision | Rationale | Outcome |
|---|---|---|
| Delta-based signal mapping (vs. absolute) | Individual baseline HR and tremor vary widely; delta from personal baseline is more reliable | — Pending |
| Pharmacokinetic inverse model for dose estimation | Only principled way to go from plasma concentration curve back to dose without ML | — Pending |
| Single-compartment PK model | Sufficient for caffeine kinetics; two-compartment adds complexity without meaningful gain at this scale | — Pending |
| Controlled environment assumption | Eliminates confounder complexity; acceptable for academic proof-of-concept | — Pending |

---
*Last updated: 2026-03-12 after initialization*
