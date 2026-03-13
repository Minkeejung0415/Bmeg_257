"""
main.py – Module 6
===================
Entry point and orchestration layer.

Two operating modes
--------------------
live    Reads from the Arduino serial stream in real time.  Requires the
        Arduino to be connected and the correct serial port to be specified.
        Runs the full calibration → monitoring pipeline.

replay  Reads a previously recorded session_YYYYMMDD_HHMMSS.csv without
        hardware.  Allows offline development, debugging, and testing of
        the signal processing and PK model modules.

CLI
---
    # Live mode
    python main.py live --port COM3 --food-state fasted --weight 70

    # Replay mode
    python main.py replay sessions/session_20260312_143000.csv

    # Replay at accelerated speed (10× real time)
    python main.py replay sessions/session_20260312_143000.csv --speed 10

    # Run PK model validation (no hardware needed)
    python main.py validate

Optional interactive commands during live / replay
---------------------------------------------------
  [d] + Enter   Manually register a dose event (prompts for mg)
  [q] + Enter   Quit cleanly
  [s] + Enter   Print current status summary

Calibration workflow (live mode only)
--------------------------------------
1. Subject sits still, no caffeine for ≥4 h.
2. Press Enter when ready; baseline capture runs for ≥3 min.
3. (Optional) perform a known-dose session to fit personal slope.
4. Monitoring begins; HR, tremor, and C(t) are displayed in the terminal.
5. On Ctrl-C or q+Enter the session CSV and baseline.json are preserved.
"""

from __future__ import annotations

import argparse
import signal
import sys
import os
import time
import threading
from pathlib import Path
from typing import Optional

# Ensure src/ is on the path when running from project root
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import matplotlib
matplotlib.use('Agg')   # headless-safe; switch to 'TkAgg' if you want a live window
import matplotlib.pyplot as plt

from ingestion        import SerialIngestion, replay_session, SensorRow
from signal_processing import SignalProcessor
from pk_model         import PKModel, BONATI_1982, BLANCHARD_SAWERS_1983
from calibration      import Calibration
from concentration    import ConcentrationEstimator, ConcentrationResult


# ── ANSI colour helpers (no-op on Windows when not supported) ─────────────────
def _col(code: str, text: str) -> str:
    if sys.platform == 'win32' and not os.environ.get('TERM'):
        return text
    return f"\033[{code}m{text}\033[0m"

GREEN  = lambda t: _col('32', t)
YELLOW = lambda t: _col('33', t)
RED    = lambda t: _col('31', t)
CYAN   = lambda t: _col('36', t)
BOLD   = lambda t: _col('1',  t)

# ── Shared quit flag ──────────────────────────────────────────────────────────
_quit = threading.Event()


def _handle_sigint(sig, frame):
    print("\n[main] Interrupt received — stopping.", flush=True)
    _quit.set()


signal.signal(signal.SIGINT, _handle_sigint)


# ─────────────────────────────────────────────────────────────────────────────
# Live mode
# ─────────────────────────────────────────────────────────────────────────────

def run_live(
    port: str,
    baud: int = 115_200,
    food_state: str = 'fasted',
    body_weight_kg: float = 70.0,
    baseline_file: str = 'baseline.json',
    sessions_dir: str = 'sessions',
    skip_baseline: bool = False,
) -> None:
    """Full live pipeline: serial ingestion → signal processing → PK → display."""

    print(BOLD("\n=== Caffeine Estimation System — LIVE MODE ==="))
    print(f"  Port        : {port}")
    print(f"  Food state  : {food_state}")
    print(f"  Body weight : {body_weight_kg:.0f} kg")

    # ── Initialise modules ────────────────────────────────────────────────────
    ingestion  = SerialIngestion(port=port, baud=baud, sessions_dir=sessions_dir)
    processor  = SignalProcessor(fs=100)
    calibration= Calibration(baseline_file=baseline_file)
    pk         = PKModel(body_weight_kg=body_weight_kg, food_state=food_state)
    estimator  = ConcentrationEstimator(calibration, pk)

    print(calibration.summary())

    # ── Start serial reader ───────────────────────────────────────────────────
    ingestion.start()
    print(f"\n[main] Serial reader started.  Session file: {ingestion.session_path}")
    print("[main] Waiting for packets from Arduino…")

    # Wait for first valid packet
    first = None
    while first is None and not _quit.is_set():
        first = ingestion.get(timeout=1.0)
    if _quit.is_set():
        ingestion.stop()
        return

    print(GREEN("[main] First packet received."))

    # ── Phase 1: baseline calibration ────────────────────────────────────────
    if skip_baseline and calibration.is_calibrated:
        print("[main] Skipping baseline capture (using saved baseline).")
    else:
        _run_baseline_phase(ingestion, processor, calibration, body_weight_kg)

    if _quit.is_set():
        ingestion.stop()
        return

    # ── Phase 2 (optional): known-dose personal slope ────────────────────────
    if not calibration.has_personal_slope:
        print(
            YELLOW(
                "\n[WARN] No personal slope fitted.\n"
                "  Expected accuracy: ±80–150 mg (population average).\n"
                "  Run a known-dose session and call calibration.fit_personal_slope()\n"
                "  to achieve MAE < 50 mg."
            )
        )

    # ── Interactive key-listener thread ───────────────────────────────────────
    _start_key_listener(estimator)

    # ── Main monitoring loop ──────────────────────────────────────────────────
    print(BOLD("\n=== Monitoring ===  (q+Enter to quit, d+Enter to log a manual dose)\n"))
    _monitoring_loop(ingestion, processor, calibration, estimator)

    # ── Teardown ──────────────────────────────────────────────────────────────
    ingestion.stop()
    _save_plots(estimator, sessions_dir)
    print(f"\n[main] Session saved: {ingestion.session_path}")
    print(f"[main] Total packets: {ingestion.total_packets}  "
          f"Dropped: {ingestion.total_dropped}  "
          f"Drop rate: {ingestion.drop_rate:.2%}")


def _run_baseline_phase(
    ingestion: SerialIngestion,
    processor: SignalProcessor,
    calibration: Calibration,
    body_weight_kg: float,
) -> None:
    """Collect 3–5 min of resting data and finalise the baseline."""
    print(BOLD("\n=== Phase 1: Resting Baseline Calibration ==="))
    print("  1. Sit completely still.")
    print("  2. Do NOT consume caffeine for at least 4 hours beforehand.")
    print("  3. Press ENTER when ready to begin (3 minute minimum).")
    input("  >> ")

    calibration.start_baseline_capture()
    processor.reset_buffers()

    print("  Capturing… press ENTER to finish (minimum 3 minutes).")
    capture_done = threading.Event()
    input_thread = threading.Thread(target=lambda: (input(), capture_done.set()), daemon=True)
    input_thread.start()

    last_status_t = time.monotonic()
    while not (_quit.is_set() or (capture_done.is_set() and calibration.baseline_complete)):
        row = ingestion.get(timeout=0.1)
        if row is None:
            continue

        hr_res, tr_res = processor.add_sample(
            ppg=row.ppg, ax=row.ax, ay=row.ay, az=row.az
        )

        if hr_res is not None and hr_res.valid and tr_res is not None:
            calibration.add_baseline_sample(
                hr_bpm=hr_res.hr_bpm,
                tremor_rms=tr_res.rms,
            )

        # Status update every 15 s
        if time.monotonic() - last_status_t >= 15.0:
            elapsed = calibration.baseline_elapsed_s
            n_hr = len(calibration._hr_samples)
            print(
                f"  Baseline: {elapsed:.0f}s elapsed, "
                f"{n_hr} HR windows collected "
                f"{'✓ ready' if calibration.baseline_complete else '(need 3+ min)'}",
                flush=True,
            )
            last_status_t = time.monotonic()

    if not _quit.is_set():
        calibration.finalise_baseline(body_weight_kg=body_weight_kg)


def _monitoring_loop(
    ingestion: SerialIngestion,
    processor: SignalProcessor,
    calibration: Calibration,
    estimator: ConcentrationEstimator,
) -> None:
    """Main sample-consumption and display loop for live mode."""
    last_display_t = time.monotonic()

    while not _quit.is_set():
        row: Optional[SensorRow] = ingestion.get(timeout=0.1)
        if row is None:
            continue

        _process_row(row, processor, calibration, estimator)

        # Terminal display every 5 s
        if time.monotonic() - last_display_t >= 5.0:
            last_display_t = time.monotonic()
            _print_status(estimator, ingestion)


# ─────────────────────────────────────────────────────────────────────────────
# Replay mode
# ─────────────────────────────────────────────────────────────────────────────

def run_replay(
    session_csv: str,
    food_state: str = 'fasted',
    body_weight_kg: float = 70.0,
    baseline_file: str = 'baseline.json',
    speed: float = 1.0,
    plot: bool = True,
) -> None:
    """Process a recorded session CSV offline."""

    session_path = Path(session_csv)
    if not session_path.exists():
        print(RED(f"Session file not found: {session_path}"))
        sys.exit(1)

    print(BOLD(f"\n=== Caffeine Estimation System — REPLAY MODE ==="))
    print(f"  File        : {session_path}")
    print(f"  Food state  : {food_state}")
    print(f"  Speed       : {speed}×")

    processor   = SignalProcessor(fs=100)
    calibration = Calibration(baseline_file=baseline_file)
    pk          = PKModel(body_weight_kg=body_weight_kg, food_state=food_state)
    estimator   = ConcentrationEstimator(calibration, pk,
                                         session_start_time=None)

    if not calibration.is_calibrated:
        print(
            YELLOW(
                "[REPLAY] No baseline.json found.  Baseline will be estimated "
                "from the first 180 s of the session."
            )
        )
        calibration = _estimate_baseline_from_session(
            session_path, processor, body_weight_kg
        )
        # Re-create estimator with fitted calibration
        processor   = SignalProcessor(fs=100)
        pk          = PKModel(body_weight_kg=body_weight_kg, food_state=food_state)
        estimator   = ConcentrationEstimator(calibration, pk)
    else:
        print(calibration.summary())

    print(BOLD("\n=== Replaying session ==="))

    total_rows   = 0
    last_print_t = 0.0

    for row in replay_session(session_path, realtime=(speed > 0 and speed < 100)):
        if _quit.is_set():
            break

        _process_row(row, processor, calibration, estimator)
        total_rows += 1

        if row.wall_time - last_print_t >= 30.0:
            last_print_t = row.wall_time
            _print_status(estimator, ingestion=None)

    print(f"\n[replay] Finished.  Processed {total_rows} rows.")
    _print_final_summary(estimator)

    if plot:
        _save_plots(estimator, output_dir=str(session_path.parent))


def _estimate_baseline_from_session(
    session_path: Path,
    processor_tmp: SignalProcessor,
    body_weight_kg: float,
) -> Calibration:
    """
    Estimate a resting baseline from the first 180 s of a session CSV.
    Used in replay mode when no baseline.json exists.
    """
    cal = Calibration(baseline_file='baseline_replay_auto.json')
    cal.body_weight_kg = body_weight_kg
    cal.start_baseline_capture()

    for row in replay_session(session_path):
        if _quit.is_set():
            break
        hr_res, tr_res = processor_tmp.add_sample(
            ppg=row.ppg, ax=row.ax, ay=row.ay, az=row.az
        )
        if hr_res is not None and hr_res.valid and tr_res is not None:
            cal.add_baseline_sample(hr_res.hr_bpm, tr_res.rms)

        if cal.baseline_elapsed_s >= 180 and cal.baseline_complete:
            break

    cal.finalise_baseline(body_weight_kg=body_weight_kg, verbose=True)
    return cal


# ─────────────────────────────────────────────────────────────────────────────
# Shared processing logic
# ─────────────────────────────────────────────────────────────────────────────

def _process_row(
    row: SensorRow,
    processor: SignalProcessor,
    calibration: Calibration,
    estimator: ConcentrationEstimator,
) -> None:
    """Process one sensor row through the full pipeline."""
    hr_res, tr_res = processor.add_sample(
        ppg=row.ppg, ax=row.ax, ay=row.ay, az=row.az
    )

    # Only forward to estimator when a new HR window fires
    if hr_res is None or tr_res is None:
        return

    # Motion gate: require resting window
    if not calibration.is_resting_window(tr_res.rms):
        return

    # Only accept high-quality HR windows
    if not hr_res.valid:
        return

    if not calibration.is_calibrated:
        return

    estimator.update(
        hr_bpm=hr_res.hr_bpm,
        tremor_rms=tr_res.rms,
        band_power_8_12=tr_res.band_power_8_12,
        wall_time=row.wall_time,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Display helpers
# ─────────────────────────────────────────────────────────────────────────────

def _print_status(
    estimator: ConcentrationEstimator,
    ingestion: Optional[SerialIngestion] = None,
) -> None:
    """Print a one-line status to the terminal."""
    summary = estimator.latest_summary()
    print(CYAN(f"[STATUS] {summary}"), flush=True)

    if ingestion is not None:
        drop_pct = ingestion.drop_rate * 100
        print(
            f"         Packets: {ingestion.total_packets}  "
            f"Dropped: {ingestion.total_dropped} ({drop_pct:.1f}%)",
            flush=True,
        )


def _print_final_summary(estimator: ConcentrationEstimator) -> None:
    """Print session summary at end of replay or live session."""
    doses = estimator._doses
    print(BOLD("\n=== Session Summary ==="))
    if doses:
        print(f"  Detected doses ({len(doses)}):")
        for t_hr, mg in doses:
            print(f"    t={t_hr:.2f} h  →  ~{mg:.0f} mg")
        print(f"  Total estimated daily intake: {estimator.daily_dose_mg:.0f} mg")
    else:
        print("  No dose events detected.")

    arr = estimator.export_history_as_arrays()
    if 'C_est_mg_L' in arr and len(arr['C_est_mg_L']) > 0:
        peak_C = float(np.max(arr['C_est_mg_L']))
        print(f"  Peak estimated concentration: {peak_C:.2f} mg/L")


# ─────────────────────────────────────────────────────────────────────────────
# Plot output
# ─────────────────────────────────────────────────────────────────────────────

def _save_plots(
    estimator: ConcentrationEstimator,
    output_dir: str = 'sessions',
) -> None:
    """Generate and save a summary plot of the session."""
    arrays = estimator.export_history_as_arrays()
    if not arrays or len(arrays.get('t_hr', [])) < 2:
        print("[main] Not enough data for plots.")
        return

    t    = arrays['t_hr']
    C    = arrays['C_est_mg_L']
    C_pk = arrays['C_pk_mg_L']
    dhr  = arrays['delta_hr']
    dtrm = arrays['delta_tremor']
    bp   = arrays['band_power_8_12']

    fig, axes = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    # ── C(t) ─────────────────────────────────────────────────────────────────
    ax0 = axes[0]
    ax0.plot(t, C,    label='C_est (best estimate)', lw=2)
    ax0.plot(t, C_pk, label='C_pk (PK model)',       lw=1.5, ls='--', alpha=0.7)
    for t_dose, mg in estimator._doses:
        ax0.axvline(t_dose, color='red', ls=':', alpha=0.6)
        ax0.text(t_dose, ax0.get_ylim()[1] * 0.9 if ax0.get_ylim()[1] > 0 else 1,
                 f'+{mg:.0f}mg', color='red', fontsize=8, rotation=90, va='top')
    ax0.set_ylabel('Plasma concentration (mg/L)')
    ax0.set_title('Estimated caffeine plasma concentration')
    ax0.legend()
    ax0.grid(True, alpha=0.3)

    # ── Delta HR ─────────────────────────────────────────────────────────────
    ax1 = axes[1]
    ax1.plot(t, dhr, color='steelblue', lw=1.5)
    ax1.axhline(0, color='k', lw=0.5)
    ax1.axhline(1.5, color='orange', lw=1, ls='--', label='Dose threshold (+1.5 BPM)')
    ax1.set_ylabel('Delta HR (BPM)')
    ax1.set_title('Heart-rate change from baseline')
    ax1.legend(fontsize=8)
    ax1.grid(True, alpha=0.3)

    # ── Tremor ───────────────────────────────────────────────────────────────
    ax2 = axes[2]
    ax2.plot(t, bp * 1000, color='darkorange', lw=1.5, label='8–12 Hz band power ×1000')
    ax2.plot(t, dtrm,      color='sienna',     lw=1.0, alpha=0.7, label='Δ tremor RMS')
    ax2.set_ylabel('Tremor metrics')
    ax2.set_xlabel('Time (hours into session)')
    ax2.set_title('Tremor — 8–12 Hz band (Hallett 1998 band)')
    ax2.legend(fontsize=8)
    ax2.grid(True, alpha=0.3)

    plt.tight_layout()
    out_path = Path(output_dir) / f'session_plot_{int(time.time())}.png'
    plt.savefig(out_path, dpi=120)
    plt.close(fig)
    print(f"[main] Plot saved: {out_path}")


# ─────────────────────────────────────────────────────────────────────────────
# Interactive key listener
# ─────────────────────────────────────────────────────────────────────────────

def _start_key_listener(estimator: ConcentrationEstimator) -> None:
    """Start a daemon thread that listens for interactive commands."""
    def _listen():
        while not _quit.is_set():
            try:
                cmd = input().strip().lower()
            except EOFError:
                break
            if cmd == 'q':
                print("[main] Quit requested.")
                _quit.set()
            elif cmd == 'd':
                try:
                    mg = float(input("Enter dose in mg: ").strip())
                    estimator.add_manual_dose(mg)
                except ValueError:
                    print("[main] Invalid dose.")
            elif cmd == 's':
                print(estimator.latest_summary())

    t = threading.Thread(target=_listen, daemon=True, name='key-listener')
    t.start()


# ─────────────────────────────────────────────────────────────────────────────
# Validation mode
# ─────────────────────────────────────────────────────────────────────────────

def run_validate() -> None:
    """Run PK model validation against published reference data."""
    from pk_model import PKModel, BONATI_1982, BLANCHARD_SAWERS_1983

    pk = PKModel(body_weight_kg=70, food_state='fasted')
    print(BOLD("\n=== PK Model Validation ==="))
    res1 = pk.validate_against_reference(BONATI_1982)
    res2 = pk.validate_against_reference(BLANCHARD_SAWERS_1983)

    target_mae = 0.5  # mg/L — roughly equivalent to ±20 mg at 70 kg, Vd=42 L
    for name, res in [("Bonati 1982", res1), ("Blanchard & Sawers 1983", res2)]:
        status = GREEN("PASS") if res['mae'] < target_mae else RED("FAIL")
        print(f"\n  {name}: {status}  MAE={res['mae']:.3f} mg/L")

    # Try to produce the validation plot if matplotlib is available
    try:
        import matplotlib.pyplot as plt
        t_sim = np.linspace(0, 10, 500)

        fig, axes = plt.subplots(1, 2, figsize=(12, 5))
        refs = [BONATI_1982, BLANCHARD_SAWERS_1983]
        ress = [res1, res2]
        titles = ['Bonati 1982 — 162 mg fasted', 'Blanchard & Sawers 1983 — 250 mg fasted']

        for ax, ref, res, title in zip(axes, refs, ress, titles):
            C_sim = pk.single_dose_curve(t_sim, ref['dose_mg'], t0_hr=0.0)
            ax.plot(t_sim, C_sim, '-', lw=2, label='Model')
            ax.scatter(ref['t_hr'], ref['C_mg_L'], s=60, zorder=5,
                       label=f'Reference (MAE={res["mae"]:.3f} mg/L)')
            ax.set_xlabel('Time (hr)')
            ax.set_ylabel('Plasma concentration (mg/L)')
            ax.set_title(title)
            ax.legend()
            ax.grid(True, alpha=0.3)

        plt.tight_layout()
        plt.savefig('pk_validation.png', dpi=120)
        plt.close(fig)
        print("\n  Saved pk_validation.png")
    except Exception as e:
        print(f"  (Plot skipped: {e})")


# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing & entry point
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='caffeine_estimator',
        description='Real-time caffeine estimation from wearable sensors.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='mode', required=True)

    # ── live ──────────────────────────────────────────────────────────────────
    p_live = sub.add_parser('live', help='Read from Arduino in real time')
    p_live.add_argument('--port',        required=True,
                        help='Serial port, e.g. COM3 or /dev/ttyUSB0')
    p_live.add_argument('--baud',        type=int,   default=115_200)
    p_live.add_argument('--food-state',  choices=['fasted', 'fed'], default='fasted',
                        help='Controls caffeine absorption rate (fasted = ka=3 hr⁻¹, fed = ka=0.8 hr⁻¹)')
    p_live.add_argument('--weight',      type=float, default=70.0,
                        help='Body weight in kg (scales Vd)')
    p_live.add_argument('--baseline',    default='baseline.json',
                        help='Path to baseline.json (created if absent)')
    p_live.add_argument('--sessions-dir', default='sessions',
                        help='Directory for session CSV logs')
    p_live.add_argument('--skip-baseline', action='store_true',
                        help='Skip baseline capture if baseline.json already exists')

    # ── replay ────────────────────────────────────────────────────────────────
    p_replay = sub.add_parser('replay', help='Process a saved session CSV offline')
    p_replay.add_argument('session_csv', help='Path to session_YYYYMMDD_HHMMSS.csv')
    p_replay.add_argument('--food-state',  choices=['fasted', 'fed'], default='fasted')
    p_replay.add_argument('--weight',      type=float, default=70.0)
    p_replay.add_argument('--baseline',    default='baseline.json')
    p_replay.add_argument('--speed',       type=float, default=0,
                          help='Replay speed multiplier (0=as-fast-as-possible)')
    p_replay.add_argument('--no-plot',     action='store_true')

    # ── validate ──────────────────────────────────────────────────────────────
    sub.add_parser('validate', help='Run PK model validation against reference data')

    return parser


def main() -> None:
    parser = _build_parser()
    args   = parser.parse_args()

    if args.mode == 'live':
        run_live(
            port            = args.port,
            baud            = args.baud,
            food_state      = args.food_state,
            body_weight_kg  = args.weight,
            baseline_file   = args.baseline,
            sessions_dir    = args.sessions_dir,
            skip_baseline   = args.skip_baseline,
        )

    elif args.mode == 'replay':
        run_replay(
            session_csv     = args.session_csv,
            food_state      = args.food_state,
            body_weight_kg  = args.weight,
            baseline_file   = args.baseline,
            speed           = args.speed,
            plot            = not args.no_plot,
        )

    elif args.mode == 'validate':
        run_validate()


if __name__ == '__main__':
    main()
