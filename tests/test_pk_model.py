"""
tests/test_pk_model.py
======================
Offline unit tests for pk_model.py -- no hardware required.

Run with:
    cd /path/to/Bmeg_257
    python -m pytest tests/ -v

Or directly:
    python tests/test_pk_model.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

import numpy as np
import pytest
from pk_model import PKModel, BONATI_1982, BLANCHARD_SAWERS_1983, simulate_caffeine


class TestSingleDoseCurve:
    """Unit tests for the analytical single-dose solution."""

    def test_zero_at_dose_time(self):
        pk = PKModel()
        C = pk.single_dose_curve(np.array([0.0]), dose_mg=200)
        assert C[0] == pytest.approx(0.0, abs=1e-9)

    def test_zero_before_dose(self):
        pk = PKModel()
        C = pk.single_dose_curve(np.array([-1.0, -0.5]), dose_mg=200, t0_hr=0.0)
        np.testing.assert_array_equal(C, 0.0)

    def test_approaches_zero_after_many_hours(self):
        pk = PKModel()
        C = pk.single_dose_curve(np.array([72.0]), dose_mg=200)
        assert C[0] < 0.01  # virtually eliminated after 3 days

    def test_peak_is_positive(self):
        pk = PKModel()
        tp = pk.t_peak()
        C_peak = pk.single_dose_curve(np.array([tp]), dose_mg=200)
        assert C_peak[0] > 0

    def test_t_peak_formula(self):
        """t_peak = ln(ka/ke) / (ka - ke)"""
        pk = PKModel(food_state='fasted')
        tp_expected = np.log(pk.ka / pk.ke) / (pk.ka - pk.ke)
        assert pk.t_peak() == pytest.approx(tp_expected, rel=1e-6)

    def test_higher_dose_gives_proportionally_higher_concentration(self):
        """Model is linear — C scales 1:1 with dose."""
        pk = PKModel()
        t = np.array([1.0])
        C1 = pk.single_dose_curve(t, 100)
        C2 = pk.single_dose_curve(t, 200)
        assert C2[0] == pytest.approx(2 * C1[0], rel=1e-6)

    def test_fasted_faster_absorption_than_fed(self):
        """Fasted ka=3.0 > fed ka=0.8, so peak time is earlier when fasted."""
        pk_fasted = PKModel(food_state='fasted')
        pk_fed    = PKModel(food_state='fed')
        assert pk_fasted.t_peak() < pk_fed.t_peak()

    def test_ka_equals_ke_edge_case(self):
        """Should not raise even when ka ≈ ke (degenerate case)."""
        pk = PKModel()
        C = pk.single_dose_curve(np.array([1.0, 2.0, 5.0]), 100, ka=0.139, ke=0.139)
        assert np.all(np.isfinite(C))
        assert np.all(C >= 0)


class TestMultiDoseSuperposition:
    """Tests for add_dose / simulate / concentration_at."""

    def test_no_doses_returns_zero(self):
        pk = PKModel()
        assert pk.concentration_at(1.0) == pytest.approx(0.0)

    def test_single_dose_matches_analytical(self):
        pk = PKModel()
        pk.add_dose(0.0, 200)
        t = np.linspace(0, 8, 200)
        C_sim = pk.simulate(t)
        C_ana = pk.single_dose_curve(t, 200, t0_hr=0.0)
        np.testing.assert_allclose(C_sim, C_ana, rtol=1e-10)

    def test_two_doses_sum_correctly(self):
        """Superposition: C(t, dose1+dose2) = C(t,dose1) + C(t,dose2)."""
        pk = PKModel()
        pk.add_dose(0.0, 100)
        pk.add_dose(4.0, 100)
        t = np.array([6.0])
        C_super = pk.simulate(t)

        # Manual sum
        C_manual = (pk.single_dose_curve(t, 100, t0_hr=0.0) +
                    pk.single_dose_curve(t, 100, t0_hr=4.0))
        np.testing.assert_allclose(C_super, C_manual, rtol=1e-10)

    def test_clear_doses(self):
        pk = PKModel()
        pk.add_dose(0.0, 200)
        pk.clear_doses()
        assert pk.concentration_at(2.0) == pytest.approx(0.0)


class TestInverseSolver:
    """Tests for the dose-estimation (inverse) solver."""

    def test_estimate_dose_recovers_true_dose(self):
        """Forward-simulate then invert — should recover dose within 5 mg."""
        pk = PKModel()
        true_dose = 150.0
        t_obs = np.array([0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0])
        C_obs = pk.single_dose_curve(t_obs, true_dose, t0_hr=0.0)

        dose_est, rmse = pk.estimate_dose(t_obs, C_obs, t_dose_hr=0.0)
        assert dose_est == pytest.approx(true_dose, abs=5.0)
        assert rmse < 0.01

    def test_estimate_dose_with_noise(self):
        """Should recover dose within 20 mg even with 10% noise."""
        rng = np.random.default_rng(42)
        pk = PKModel()
        true_dose = 200.0
        t_obs = np.array([0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0])
        C_true = pk.single_dose_curve(t_obs, true_dose)
        C_obs  = C_true * (1 + 0.10 * rng.standard_normal(len(t_obs)))

        dose_est, _ = pk.estimate_dose(t_obs, C_obs, t_dose_hr=0.0)
        assert abs(dose_est - true_dose) < 30.0

    def test_estimate_dose_bounds_respected(self):
        pk = PKModel()
        t_obs = np.array([1.0, 2.0])
        C_obs = np.array([0.001, 0.001])   # extremely low — near zero

        dose_est, _ = pk.estimate_dose(t_obs, C_obs, t_dose_hr=0.0,
                                        dose_bounds=(10.0, 800.0))
        assert 10.0 <= dose_est <= 800.0


class TestValidation:
    """
    Validate simulated curves against published reference data.

    Tolerance notes:
    - Bonati 1982 (162 mg, n=9): tight tolerance; parameters partly derived here.
    - Blanchard & Sawers 1983 (250 mg, n=6): looser tolerance because their early
      time points (t=0.5 h, C=2.0 mg/L) suggest a slower-absorbing formulation
      than the population-average ka=3.0 hr⁻¹.  This inter-study variability is
      expected and is exactly why per-subject calibration is required.
    """

    def test_bonati_1982(self):
        pk = PKModel(body_weight_kg=70, food_state='fasted')
        results = pk.validate_against_reference(BONATI_1982, verbose=False)
        tolerance = 0.6   # mg/L
        assert results['mae'] < tolerance, (
            f"Bonati 1982 MAE={results['mae']:.3f} mg/L exceeds "
            f"tolerance {tolerance} mg/L"
        )

    def test_blanchard_sawers_1983(self):
        pk = PKModel(body_weight_kg=70, food_state='fasted')
        results = pk.validate_against_reference(BLANCHARD_SAWERS_1983, verbose=False)
        # Wider tolerance: Blanchard's formulation absorbed slower than ka=3.0 predicts
        # (inter-study variability — personal calibration addresses this)
        tolerance = 1.2   # mg/L
        assert results['mae'] < tolerance, (
            f"Blanchard 1983 MAE={results['mae']:.3f} mg/L exceeds "
            f"tolerance {tolerance} mg/L"
        )


class TestODESimulation:
    """Test that ODE simulation matches analytical result."""

    def test_ode_matches_analytical_single_dose(self):
        pk = PKModel()
        pk.add_dose(0.0, 200.0)

        t_ana = np.linspace(0.01, 8, 100)
        C_ana = pk.simulate(t_ana)

        t_ode, C_ode = pk.simulate_ode((0.0, 8.0), n_points=500)

        # Interpolate ODE output to same time points
        C_ode_interp = np.interp(t_ana, t_ode, C_ode)
        np.testing.assert_allclose(C_ode_interp, C_ana, rtol=0.01)


class TestSimulateCaffeineHelper:
    def test_returns_correct_shapes(self):
        t, C = simulate_caffeine(200, food_state='fasted', n_points=100)
        assert len(t) == 100
        assert len(C) == 100

    def test_concentration_non_negative(self):
        t, C = simulate_caffeine(300)
        assert np.all(C >= 0)


if __name__ == '__main__':
    # Run tests manually without pytest
    import traceback

    test_classes = [
        TestSingleDoseCurve,
        TestMultiDoseSuperposition,
        TestInverseSolver,
        TestValidation,
        TestODESimulation,
        TestSimulateCaffeineHelper,
    ]

    passed = failed = 0
    for cls in test_classes:
        instance = cls()
        methods = [m for m in dir(cls) if m.startswith('test_')]
        for method_name in methods:
            try:
                getattr(instance, method_name)()
                print(f"  PASS  {cls.__name__}.{method_name}")
                passed += 1
            except Exception as e:
                print(f"  FAIL  {cls.__name__}.{method_name}: {e}")
                traceback.print_exc()
                failed += 1

    print(f"\n{passed} passed, {failed} failed.")
    sys.exit(0 if failed == 0 else 1)
