# References

All sources that informed the design, signal processing, pharmacokinetic modelling, and hardware configuration of this codebase. Formatted in Vancouver style.

---

## Caffeine Pharmacokinetics

1. Bonati M, Latini R, Galletti F, Young JF, Tognoni G, Garattini S. Caffeine disposition after oral doses. Clin Pharmacokinet. 1982;7(4):339-48.
   > PK model parameters (ka, ke), fasted oral absorption rate, and validation dataset (162 mg oral dose, fasted). Encoded as `BONATI_1982` in `pk_model.py`.

2. Blanchard J, Sawers SJA. The absolute bioavailability of caffeine in man. Eur J Clin Pharmacol. 1983;24(1):93-8.
   > Validation dataset (250 mg fasted, gelatine capsule). Lag time tlag = 0.4 hr reflects capsule formulation. Encoded as `BLANCHARD_SAWERS_1983` in `pk_model.py`.

3. Lelo A, Miners JO, Robson RA, Birkett DJ. Quantitative assessment of caffeine partial clearances in man. Br J Clin Pharmacol. 1986;22(2):183-6.
   > Elimination rate constant ke = 0.139 hr⁻¹ (t½ ≈ 5 h) and volume of distribution Vd = 0.6 L/kg. Cited in `pk_model.py`.

4. Benowitz NL. Clinical pharmacology of caffeine. Annu Rev Med. 1990;41:277-88.
   > One-compartment oral absorption model; consensus PK parameters (ka, ke, Vd); oral bioavailability ~100%. Cited in research architecture and features documents.

5. Magkos F, Kavouras SA. Caffeine use in sports, pharmacokinetics in man, and cellular mechanisms of action. Crit Rev Food Sci Nutr. 2005;45(7-8):535-62.
   > Corroboration of one-compartment model suitability; Tmax 30–60 min post-ingestion.

6. Fredholm BB, Bättig K, Holmén J, Nehlig A, Zvartau EE. Actions of caffeine in the brain with special reference to factors that contribute to its widespread use. Pharmacol Rev. 1999;51(1):83-133.
   > CYP1A2 genetic polymorphism and inter-individual variability in elimination half-life (2.5–10 h). Cited in pitfalls documentation (P-12).

7. Kamimori GH, Karyekar CS, Otterstetter R, Cox DS, Balkin TJ, Belenky GL, et al. The rate of absorption and relative bioavailability of caffeine administered in chewing gum versus capsules to normal healthy volunteers. Int J Pharm. 2002;234(1-2):159-67.
   > Formulation-specific absorption kinetics and reference PK parameters.

---

## Caffeine Effect on Heart Rate

8. Graham TE, Spriet LL. Metabolic, catecholamine, and exercise performance responses to various doses of caffeine. J Appl Physiol. 1995;78(3):867-74.
   > Caffeine HR dose-response (~3–5 bpm per 100 mg in non-tolerant adults). Primary basis for HR-to-concentration transfer function. Cited in `concentration.py` and `calibration.py`.

9. Goldstein ER, Ziegenfuss T, Kalman D, Kreider R, Campbell B, Wilborn C, et al. International society of sports nutrition position stand: caffeine and performance. J Int Soc Sports Nutr. 2010;7(1):5.
   > Confirmation of caffeine-induced HR elevation; corroborates Graham & Spriet 1995.

10. Denaro CP, Brown CR, Jacob P 3rd, Benowitz NL. Effects of caffeine with repeated dosing. Eur J Clin Pharmacol. 1991;40(3):273-8.
    > Caffeine tolerance and blunted HR response (~30–50% reduction in habitual consumers). Informs tolerance flag in `calibration.py`.

---

## Caffeine Effect on Tremor

11. Hallett M. Overview of human tremor physiology. Mov Disord. 1998;13(Suppl 3):43-8.
    > Physiological tremor band 8–12 Hz; caffeine selectively amplifying this band. Directly cited in `main.py` plot label; defines the tremor feature band used throughout `signal_processing.py`.

12. Hicks RG. The effects of caffeine on physiological tremor. Ergonomics. 1972;15(4):333-9.
    > Caffeine tremor frequency band characterization. Cited in features research documentation.

13. Morgan MH, Hewer RL, Cooper R. Acute effect of caffeine on essential tremor. J Neurol Neurosurg Psychiatry. 1983;47(1):94.
    > Caffeine tremor amplitude increases. Cited in features research documentation.

14. Raethjen J, Lemke MR, Lindemann M, Wenzelburger R, Pfister G, Deuschl G. Amitriptyline enhances the central component of physiological tremor. J Neurol Neurosurg Psychiatry. 2000;68(3):320-5.
    > Band power specificity of 8–12 Hz physiological tremor vs. broadband RMS. Cited in features research documentation.

15. Nawrot P, Jordan S, Eastwood J, Rotstein J, Hugenholtz A, Feeley M. Effects of caffeine on human health. Food Addit Contam. 2003;20(1):1-30.
    > General caffeine physiological effect characterization; background for tremor characterization.

---

## Heart Rate Variability and Caffeine

16. Vlcek M, Rovensky J, Blazicek P, Hulejova H, Rovensky MJ. Caffeine attenuates cardiovascular responses in healthy volunteers. Neuro Endocrinol Lett. 2008;29(5):685-90.
    > Caffeine reducing RMSSD and parasympathetic tone. Basis for HRV as optional second signal in `concentration.py`.

17. Zimmermann-Viehoff F, Thayer J, Koenig J, Herrmann C, Weber CS, Deter HC. Short-term effects of espresso caffeine on cardiac autonomic nerve activity in habitual and non-habitual coffee consumers — a randomized crossover study. Nutr J. 2015;14:104.
    > Dose-dependent caffeine effect on HRV (RMSSD, pNN50). Cited in features research documentation.

---

## Signal Processing Methods

18. Makowski D, Pham T, Lau ZJ, Brammer JC, Lespinasse F, Pham H, et al. NeuroKit2: A Python toolbox for neurophysiological signal processing. Behav Res Methods. 2021;53(4):1689-96.
    > PPG cleaning pipeline and Elgendi peak detector used in `signal_processing.py`. NeuroKit2 v0.2.x is the primary HR extraction library.

19. Elgendi M, Norton I, Brearley M, Abbott D, Schuurmans D. Systolic peak detection in acceleration photoplethysmograms measured from emergency responders in calm and cold water. PLoS ONE. 2013;8(12):e76585.
    > Elgendi peak detection method (wrapped by NeuroKit2). Reduces false positives 15–30% over raw peak detection on noisy PPG.

20. Welch PD. The use of fast Fourier transform for the estimation of power spectra: a method based on time averaging over short, modified periodograms. IEEE Trans Audio Electroacoust. 1967;15(2):70-3.
    > Welch PSD method used for 8–12 Hz tremor band power extraction in `signal_processing.py` via `scipy.signal.welch()`.

---

## Hardware

21. Maxim Integrated. MAX30102 high-sensitivity pulse oximeter and heart-rate sensor for wearable health [datasheet]. Rev 2. San Jose (CA): Maxim Integrated; 2018.
    > FIFO behaviour, sample rate register (0x0A), mode config register (0x09), LED current configuration. Wrapped by SparkFun MAX3010x library v1.1.2.

22. TDK InvenSense. ICM-42688-P six-axis IMU [datasheet]. Rev 1.7. San Jose (CA): TDK InvenSense; 2021.
    > ODR configuration, FIFO behaviour, I2C address (0x6A/0x6B), accelerometer range options (±2 g–±16 g). Wrapped by SparkFun ICM-42688-P library v1.0.8.

---

## Software Libraries

23. SparkFun Electronics. SparkFun ICM-42688-P Arduino library [Internet]. Version 1.0.8. Niwot (CO): SparkFun Electronics; 2023 [cited 2026 Mar 12]. Available from: https://github.com/sparkfun/SparkFun_ICM-42688-P_ArduinoLibrary

24. SparkFun Electronics. SparkFun MAX3010x sensor library [Internet]. Version 1.1.2. Niwot (CO): SparkFun Electronics; 2022 [cited 2026 Mar 12]. Available from: https://github.com/sparkfun/SparkFun_MAX3010x_Sensor_Library

25. Liechti R. pyserial [Internet]. Version 3.5. 2020 [cited 2026 Mar 12]. Available from: https://github.com/pyserial/pyserial

26. Harris CR, Millman KJ, van der Walt SJ, Gommers R, Virtanen P, Cournapeau D, et al. Array programming with NumPy. Nature. 2020;585(7825):357-62.

27. Virtanen P, Gommers R, Oliphant TE, Haberland M, Reddy T, Cournapeau D, et al. SciPy 1.0: fundamental algorithms for scientific computing in Python. Nat Methods. 2020;17(3):261-72.

28. McKinney W. Data structures for statistical computing in Python. In: Proceedings of the 9th Python in Science Conference; 2010 Jun 28–Jul 3; Austin, TX. 2010. p. 51-6.

29. Newville M, Stensitzki T, Allen DB, Ingargiola A. LMFIT: non-linear least-squares minimization and curve-fitting for Python [Internet]. Zenodo; 2014 [cited 2026 Mar 12]. doi:10.5281/zenodo.11813

30. Hunter JD. Matplotlib: a 2D graphics environment. Comput Sci Eng. 2007;9(3):90-5.
