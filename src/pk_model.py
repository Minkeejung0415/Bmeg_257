"""
pk_model.py – Module 3
=======================
One-compartment oral absorption pharmacokinetic (PK) model for caffeine.

Model equations
---------------
Two-state ODE (gut → plasma):

    dA_gut/dt    = -ka · A_gut
    dA_plasma/dt =  ka · A_gut  -  ke · A_plasma

    C(t)         = A_plasma(t) / Vd_total          [mg / L]

Analytical closed-form solution for a single dose at t=0:

    C(t) = F · dose · ka / (Vd_total · (ka - ke)) · (e^{-ke·t} - e^{-ka·t})

Parameters (population defaults)
---------------------------------
    ka_fasted = 3.0   hr⁻¹   (Bonati 1982; half-life of absorption ~14 min)
    ka_fed    = 0.8   hr⁻¹   (Blanchard & Sawers 1983; slowed by food ~3–6×)
    ke        = 0.139 hr⁻¹   (t½ ≈ 5 h; Bonati 1982)
    Vd        = 0.6   L/kg   (Lelo et al. 1986)
    F         = 1.0          (bioavailability ≈ 100%)
    BW        = 70    kg     (configurable)

Multi-dose superposition
------------------------
Caffeine is a linear drug at therapeutic doses, so plasma concentration
under multiple doses is the sum of the individual dose curves (principle
of superposition).  add_dose() registers a dose event; simulate() returns
the total C(t) curve.

Inverse solver
--------------
estimate_dose() uses scipy.optimize.least_squares to find the dose
magnitude that best explains an observed (C_est, t) trajectory.

Validation data (reference curves)
------------------------------------
Bonati (1982) — single 162 mg oral dose, fasted:
    t_hr   = [0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    C_mg_L = [0.85, 1.75, 2.45, 3.00, 3.35, 3.15, 2.70, 2.30, 1.60, 1.10]

Blanchard & Sawers (1983) — 250 mg oral dose, fasted:
    t_hr   = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0]
    C_mg_L = [2.0, 4.5, 5.5, 5.7, 5.1, 4.4, 3.1, 2.2]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

# ── Reference validation data ─────────────────────────────────────────────────

BONATI_1982 = {
    'dose_mg': 162,
    'food_state': 'fasted',
    't_hr':   np.array([0.25, 0.50, 0.75, 1.00, 1.50, 2.00, 3.00, 4.00, 6.00, 8.00]),
    'C_mg_L': np.array([0.85, 1.75, 2.45, 3.00, 3.35, 3.15, 2.70, 2.30, 1.60, 1.10]),
}

BLANCHARD_SAWERS_1983 = {
    'dose_mg': 250,
    'food_state': 'fasted',
    't_hr':   np.array([0.50, 1.00, 1.50, 2.00, 3.00, 4.00, 6.00, 8.00]),
    'C_mg_L': np.array([2.00, 4.50, 5.50, 5.70, 5.10, 4.40, 3.10, 2.20]),
}

# ── Dose event ────────────────────────────────────────────────────────────────

@dataclass
class DoseEvent:
    t_hr: float    # time of dose (hours from session start)
    dose_mg: float # caffeine amount in mg


# ── Main PK model class ───────────────────────────────────────────────────────

class PKModel:
    """
    One-compartment oral-absorption PK model for caffeine.

    Parameters
    ----------
    body_weight_kg : float
        Subject body weight in kg; scales Vd to get total volume.
    food_state : {'fasted', 'fed'}
        Controls ka; fasted = 3.0 hr⁻¹, fed = 0.8 hr⁻¹.
    """

    # Population PK parameters (see module docstring for citations)
    KA_FASTED: float = 3.0    # hr⁻¹
    KA_FED:    float = 0.8    # hr⁻¹
    KE:        float = 0.139  # hr⁻¹  (elimination rate constant)
    VD_PER_KG: float = 0.6    # L/kg  (apparent volume of distribution)
    F:         float = 1.0    # bioavailability

    def __init__(
        self,
        body_weight_kg: float = 70.0,
        food_state: str = 'fasted',
    ) -> None:
        self.bw = body_weight_kg
        self.food_state = food_state
        self.ka = self.KA_FASTED if food_state == 'fasted' else self.KA_FED
        self.ke = self.KE
        self.vd_total = self.VD_PER_KG * body_weight_kg  # litres

        self._doses: List[DoseEvent] = []

    # ── Dose history management ───────────────────────────────────────────────

    def add_dose(self, t_hr: float, dose_mg: float) -> None:
        """Register a dose event (can be called at any time)."""
        self._doses.append(DoseEvent(t_hr=t_hr, dose_mg=dose_mg))
        self._doses.sort(key=lambda d: d.t_hr)

    def clear_doses(self) -> None:
        """Remove all dose history (e.g., when starting a new session day)."""
        self._doses.clear()

    @property
    def doses(self) -> List[DoseEvent]:
        return list(self._doses)

    # ── Analytical single-dose curve ──────────────────────────────────────────

    def single_dose_curve(
        self,
        t_hr: np.ndarray,
        dose_mg: float,
        t0_hr: float = 0.0,
        ka: Optional[float] = None,
        ke: Optional[float] = None,
    ) -> np.ndarray:
        """
        Plasma concentration C(t) for a single oral dose using the analytical
        solution.

        Parameters
        ----------
        t_hr : array-like
            Time points in hours.
        dose_mg : float
            Dose in mg.
        t0_hr : float
            Dose administration time in hours.
        ka, ke : float, optional
            Override model rate constants (e.g. for fitting).

        Returns
        -------
        np.ndarray  (same shape as t_hr)
            Plasma concentration in mg/L.  Zero before t0_hr.
        """
        ka = ka if ka is not None else self.ka
        ke = ke if ke is not None else self.ke
        t_hr = np.asarray(t_hr, dtype=float)
        dt = np.maximum(t_hr - t0_hr, 0.0)

        if abs(ka - ke) < 1e-6:
            # Degenerate case (ka ≈ ke): L'Hôpital limit
            C = (self.F * dose_mg / self.vd_total) * ke * dt * np.exp(-ke * dt)
        else:
            C = (
                (self.F * dose_mg * ka)
                / (self.vd_total * (ka - ke))
                * (np.exp(-ke * dt) - np.exp(-ka * dt))
            )

        return np.maximum(C, 0.0)

    def t_peak(
        self,
        ka: Optional[float] = None,
        ke: Optional[float] = None,
    ) -> float:
        """Time of peak concentration (hours after dose)."""
        ka = ka if ka is not None else self.ka
        ke = ke if ke is not None else self.ke
        return float(np.log(ka / ke) / (ka - ke))

    def c_peak(self, dose_mg: float) -> float:
        """Peak plasma concentration (mg/L) for a single dose."""
        tp = self.t_peak()
        return float(self.single_dose_curve(np.array([tp]), dose_mg)[0])

    # ── Multi-dose superposition ──────────────────────────────────────────────

    def concentration_at(self, t_hr: float) -> float:
        """
        Total plasma concentration at a single time point via superposition.
        """
        if not self._doses:
            return 0.0
        c = 0.0
        for dose in self._doses:
            c += float(
                self.single_dose_curve(np.array([t_hr]), dose.dose_mg, dose.t_hr)[0]
            )
        return float(c)

    def simulate(self, t_hr: np.ndarray) -> np.ndarray:
        """
        Simulate total plasma concentration over a time array using analytical
        superposition.

        Parameters
        ----------
        t_hr : array-like
            Time vector in hours.

        Returns
        -------
        np.ndarray  shape (len(t_hr),)
            Total caffeine plasma concentration in mg/L.
        """
        t_hr = np.asarray(t_hr, dtype=float)
        C = np.zeros_like(t_hr)
        for dose in self._doses:
            C += self.single_dose_curve(t_hr, dose.dose_mg, dose.t_hr)
        return C

    # ── ODE-based simulation (more accurate for complex dose schedules) ────────

    def simulate_ode(
        self,
        t_span_hr: Tuple[float, float],
        n_points: int = 1000,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Full ODE integration of the two-compartment model via solve_ivp.

        Dose events are applied as instantaneous discontinuities using
        scipy.integrate.solve_ivp's 'events' or sequential integration
        (sequential approach is more robust).

        Returns
        -------
        t_hr : np.ndarray  (n_points,)
        C_mg_L : np.ndarray  (n_points,)
        """
        def ode_rhs(t, y):
            a_gut, a_plasma = y
            return [-self.ka * a_gut,
                    self.ka * a_gut - self.ke * a_plasma]

        t_start, t_end = t_span_hr
        doses_sorted = sorted(self._doses, key=lambda d: d.t_hr)

        # Build list of integration segments separated by dose events
        breakpoints = [d.t_hr for d in doses_sorted if t_start < d.t_hr < t_end]
        segments = sorted(set([t_start] + breakpoints + [t_end]))

        # State at entry
        y_current = np.array([0.0, 0.0])

        # Apply any doses that occurred before t_start (carry-in concentrations)
        for dose in doses_sorted:
            if dose.t_hr <= t_start:
                y_current[0] += self.F * dose.dose_mg

        t_all: List[np.ndarray] = []
        C_all: List[np.ndarray] = []

        dose_index = {d.t_hr: d for d in doses_sorted}

        for i in range(len(segments) - 1):
            seg_start = segments[i]
            seg_end   = segments[i + 1]

            n_seg = max(3, int((seg_end - seg_start) * n_points / (t_end - t_start + 1e-9)))
            t_eval_seg = np.linspace(seg_start, seg_end, n_seg)

            sol = solve_ivp(
                ode_rhs,
                [seg_start, seg_end],
                y_current.copy(),
                t_eval=t_eval_seg,
                method='RK45',
                rtol=1e-6,
                atol=1e-9,
            )

            # Store (excluding last point to avoid duplication at boundaries)
            t_all.append(sol.t[:-1])
            C_all.append(sol.y[1][:-1] / self.vd_total)

            y_current = sol.y[:, -1].copy()

            # Apply dose at segment boundary (if any)
            if seg_end in dose_index:
                y_current[0] += self.F * dose_index[seg_end].dose_mg

        # Append final point
        t_all.append(np.array([segments[-1]]))
        C_all.append(np.array([y_current[1] / self.vd_total]))

        t_out = np.concatenate(t_all)
        C_out = np.maximum(np.concatenate(C_all), 0.0)
        return t_out, C_out

    # ── Inverse solver ────────────────────────────────────────────────────────

    def estimate_dose(
        self,
        t_obs_hr: np.ndarray,
        C_obs: np.ndarray,
        t_dose_hr: float,
        dose_bounds: Tuple[float, float] = (10.0, 800.0),
    ) -> Tuple[float, float]:
        """
        Estimate dose magnitude given observed concentration–time data.

        Uses scipy.optimize.least_squares (Levenberg–Marquardt variant via
        'trf' with bounds) to minimise the sum of squared residuals between
        predicted and observed C(t).

        Parameters
        ----------
        t_obs_hr : array-like
            Observation times in hours.
        C_obs : array-like
            Observed plasma concentration estimates in mg/L.
        t_dose_hr : float
            Assumed dose administration time (hours).
        dose_bounds : (float, float)
            (min_dose_mg, max_dose_mg) — physical plausibility bounds.

        Returns
        -------
        (dose_mg_estimated, residual_rmse)
        """
        t_obs_hr = np.asarray(t_obs_hr, dtype=float)
        C_obs    = np.asarray(C_obs,    dtype=float)

        def residuals(params: np.ndarray) -> np.ndarray:
            dose_mg = float(params[0])
            C_pred = self.single_dose_curve(t_obs_hr, dose_mg, t0_hr=t_dose_hr)
            return C_pred - C_obs

        x0 = [100.0]   # initial guess: 100 mg
        result = least_squares(
            residuals,
            x0,
            bounds=([dose_bounds[0]], [dose_bounds[1]]),
            method='trf',
            loss='soft_l1',  # robust to outliers
            f_scale=0.5,
        )

        dose_est = float(result.x[0])
        rmse = float(np.sqrt(np.mean(result.fun ** 2)))
        return dose_est, rmse

    def estimate_dose_and_time(
        self,
        t_obs_hr: np.ndarray,
        C_obs: np.ndarray,
        t_dose_guess_hr: float = 0.0,
    ) -> Tuple[float, float, float]:
        """
        Jointly estimate dose magnitude AND dose time using least_squares.

        Returns (dose_mg, t_dose_hr, rmse).
        """
        t_obs_hr = np.asarray(t_obs_hr, dtype=float)
        C_obs    = np.asarray(C_obs,    dtype=float)

        def residuals(params: np.ndarray) -> np.ndarray:
            dose_mg   = float(params[0])
            t_dose_hr = float(params[1])
            C_pred = self.single_dose_curve(t_obs_hr, dose_mg, t0_hr=t_dose_hr)
            return C_pred - C_obs

        x0 = [100.0, t_dose_guess_hr]
        result = least_squares(
            residuals,
            x0,
            bounds=([10.0, max(0.0, t_dose_guess_hr - 2)],
                    [800.0, t_dose_guess_hr + 0.5]),
            method='trf',
            loss='soft_l1',
            f_scale=0.5,
        )
        dose_est   = float(result.x[0])
        t_dose_est = float(result.x[1])
        rmse = float(np.sqrt(np.mean(result.fun ** 2)))
        return dose_est, t_dose_est, rmse

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_against_reference(
        self,
        reference: dict,
        verbose: bool = True,
    ) -> dict:
        """
        Validate simulated C(t) against a reference dataset.

        Parameters
        ----------
        reference : dict
            One of BONATI_1982 or BLANCHARD_SAWERS_1983 defined at module top.
        verbose : bool
            Print a summary table.

        Returns
        -------
        dict
            Keys: 'mae', 'rmse', 'max_error', 'C_pred', 'C_ref'
        """
        dose_mg    = reference['dose_mg']
        food_state = reference.get('food_state', 'fasted')
        t_hr       = reference['t_hr']
        C_ref      = reference['C_mg_L']

        ka = self.KA_FASTED if food_state == 'fasted' else self.KA_FED
        C_pred = self.single_dose_curve(t_hr, dose_mg, t0_hr=0.0, ka=ka)

        errors = np.abs(C_pred - C_ref)
        mae    = float(np.mean(errors))
        rmse   = float(np.sqrt(np.mean((C_pred - C_ref) ** 2)))
        max_e  = float(np.max(errors))

        if verbose:
            print(f"\nValidation — {dose_mg} mg oral dose ({food_state}):")
            print(f"  {'t (hr)':>8}  {'C_ref':>8}  {'C_pred':>8}  {'|err|':>8}")
            print("  " + "-" * 38)
            for t, cr, cp, e in zip(t_hr, C_ref, C_pred, errors):
                print(f"  {t:8.2f}  {cr:8.3f}  {cp:8.3f}  {e:8.3f}")
            print(f"\n  MAE={mae:.3f} mg/L   RMSE={rmse:.3f} mg/L   "
                  f"MaxErr={max_e:.3f} mg/L")

        return {
            'mae': mae, 'rmse': rmse, 'max_error': max_e,
            'C_pred': C_pred, 'C_ref': C_ref,
        }


# ── Convenience function for quick simulations ────────────────────────────────

def simulate_caffeine(
    dose_mg: float,
    food_state: str = 'fasted',
    body_weight_kg: float = 70.0,
    t_max_hr: float = 12.0,
    n_points: int = 500,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Simulate a single-dose caffeine plasma concentration curve.

    Returns (t_hr, C_mg_L) numpy arrays.
    """
    pk = PKModel(body_weight_kg=body_weight_kg, food_state=food_state)
    pk.add_dose(0.0, dose_mg)
    t = np.linspace(0, t_max_hr, n_points)
    C = pk.simulate(t)
    return t, C


# ── CLI validation ────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import matplotlib.pyplot as plt

    pk70 = PKModel(body_weight_kg=70, food_state='fasted')

    print("=" * 60)
    print("Validating PK model against published reference data")
    print("=" * 60)

    res1 = pk70.validate_against_reference(BONATI_1982)
    res2 = pk70.validate_against_reference(BLANCHARD_SAWERS_1983)

    # Plot validation
    t_sim = np.linspace(0, 10, 500)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    for ax, ref, res, title in zip(
        axes,
        [BONATI_1982, BLANCHARD_SAWERS_1983],
        [res1, res2],
        ['Bonati 1982 — 162 mg fasted', 'Blanchard & Sawers 1983 — 250 mg fasted'],
    ):
        ka = PKModel.KA_FASTED
        C_sim = pk70.single_dose_curve(t_sim, ref['dose_mg'], t0_hr=0.0, ka=ka)
        ax.plot(t_sim, C_sim, '-', label='Model prediction', lw=2)
        ax.scatter(ref['t_hr'], ref['C_mg_L'], s=60, zorder=5,
                   label=f'Reference data (MAE={res["mae"]:.3f} mg/L)')
        ax.set_xlabel('Time (hr)')
        ax.set_ylabel('Plasma concentration (mg/L)')
        ax.set_title(title)
        ax.legend()
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    plt.savefig('pk_validation.png', dpi=120)
    plt.show()
    print("\nSaved pk_validation.png")
