"""
dual_usb_capture.py
===================
Bridge two USB serial streams into one session CSV compatible with replay mode.

Typical setup for split hardware:
  - IMU on one USB serial port (ICM board)
  - PPG on another USB serial port (ESP32 + MAX30102)

Output CSV columns match ingestion.SESSION_COLUMNS:
  wall_time,seq,ts_ms,ax,ay,az,gx,gy,gz,ppg,dropped_before

Example:
  python src/dual_usb_capture.py --imu-port COM5 --ppg-port COM3
"""

from __future__ import annotations

import argparse
import csv
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import serial

SESSION_COLUMNS = (
    "wall_time",
    "seq",
    "ts_ms",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "ppg",
    "dropped_before",
)


@dataclass(frozen=True)
class IMUSample:
    host_time: float
    seq: Optional[int]
    ts_ms: Optional[int]
    ax: float
    ay: float
    az: float
    gx: float
    gy: float
    gz: float


@dataclass(frozen=True)
class PPGSample:
    host_time: float
    seq: Optional[int]
    ts_ms: Optional[int]
    ppg: int


def _to_int(token: str) -> int:
    # Allow accidental float formatting like "123.0".
    return int(float(token))


def _split_csv_numbers(line: str) -> list[str]:
    return [p.strip() for p in line.split(",") if p.strip()]


def _parse_imu_line(line: str, host_time: float) -> Optional[IMUSample]:
    parts = _split_csv_numbers(line)
    if not parts:
        return None

    try:
        # Format A: ax,ay,az,gx,gy,gz
        if len(parts) == 6:
            ax, ay, az, gx, gy, gz = [float(x) for x in parts]
            return IMUSample(host_time, None, None, ax, ay, az, gx, gy, gz)

        # Format B: seq,ts_ms,ax,ay,az,gx,gy,gz
        # Format C: seq,ts_ms,ax,ay,az,gx,gy,gz,ppg (ignore ppg tail)
        if len(parts) >= 8:
            seq = _to_int(parts[0])
            ts_ms = _to_int(parts[1])
            ax = float(parts[2])
            ay = float(parts[3])
            az = float(parts[4])
            gx = float(parts[5])
            gy = float(parts[6])
            gz = float(parts[7])
            return IMUSample(host_time, seq, ts_ms, ax, ay, az, gx, gy, gz)
    except ValueError:
        return None

    return None


def _parse_ppg_line(line: str, host_time: float) -> Optional[PPGSample]:
    parts = _split_csv_numbers(line)
    if not parts:
        return None

    try:
        # Format A: ppg
        if len(parts) == 1:
            return PPGSample(host_time, None, None, _to_int(parts[0]))

        # Format B: seq,ts_ms,ppg
        if len(parts) == 3:
            return PPGSample(host_time, _to_int(parts[0]), _to_int(parts[1]), _to_int(parts[2]))

        # Format C: seq,ts_ms,ax,ay,az,gx,gy,gz,ppg
        if len(parts) >= 9:
            return PPGSample(host_time, _to_int(parts[0]), _to_int(parts[1]), _to_int(parts[8]))
    except ValueError:
        return None

    return None


def _should_skip_line(line: str) -> bool:
    if not line:
        return True
    lower = line.lower()
    if lower.startswith("seq,") or lower.startswith("ets ") or lower.startswith("rst:"):
        return True
    if lower.startswith("load:") or lower.startswith("entry "):
        return True
    return False


class SerialReader(threading.Thread):
    def __init__(
        self,
        *,
        name: str,
        port: str,
        baud: int,
        out_queue: queue.Queue,
        parser,
    ) -> None:
        super().__init__(name=name, daemon=True)
        self.port = port
        self.baud = baud
        self.out_queue = out_queue
        self.parser = parser
        self.stop_event = threading.Event()
        self.bad_lines = 0
        self.good_lines = 0

    def stop(self) -> None:
        self.stop_event.set()

    def run(self) -> None:
        try:
            with serial.Serial(self.port, self.baud, timeout=0.05) as ser:
                ser.reset_input_buffer()
                while not self.stop_event.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue
                    host_time = time.time()
                    try:
                        line = raw.decode("ascii", errors="ignore").strip()
                    except Exception:
                        self.bad_lines += 1
                        continue

                    if _should_skip_line(line):
                        continue

                    sample = self.parser(line, host_time)
                    if sample is None:
                        self.bad_lines += 1
                        continue

                    self.good_lines += 1
                    self.out_queue.put((self.name, sample))
        except serial.SerialException as exc:
            print(f"[{self.name}] Serial error on {self.port}: {exc}", flush=True)
        except Exception as exc:
            print(f"[{self.name}] Unexpected error: {exc}", flush=True)


def _nearest_imu(ppg_time: float, imu_buffer: deque[IMUSample]) -> tuple[Optional[IMUSample], float]:
    if not imu_buffer:
        return None, float("inf")

    best = None
    best_age = float("inf")
    for sample in imu_buffer:
        age = abs(sample.host_time - ppg_time)
        if age < best_age:
            best = sample
            best_age = age
    return best, best_age


def run_capture(
    *,
    imu_port: str,
    ppg_port: str,
    imu_baud: int,
    ppg_baud: int,
    sessions_dir: Path,
    output_csv: Optional[Path],
    max_sync_age_ms: float,
    print_every: int,
    duration_s: float,
) -> Path:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    if output_csv is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_csv = sessions_dir / f"session_{ts}.csv"

    event_queue: queue.Queue = queue.Queue(maxsize=20_000)
    imu_reader = SerialReader(
        name="IMU",
        port=imu_port,
        baud=imu_baud,
        out_queue=event_queue,
        parser=_parse_imu_line,
    )
    ppg_reader = SerialReader(
        name="PPG",
        port=ppg_port,
        baud=ppg_baud,
        out_queue=event_queue,
        parser=_parse_ppg_line,
    )

    imu_reader.start()
    ppg_reader.start()

    print(f"[bridge] IMU  port: {imu_port} @ {imu_baud}", flush=True)
    print(f"[bridge] PPG  port: {ppg_port} @ {ppg_baud}", flush=True)
    print(f"[bridge] Output  : {output_csv}", flush=True)
    print("[bridge] Press Ctrl+C to stop capture.", flush=True)

    imu_buffer: deque[IMUSample] = deque(maxlen=300)
    ppg_packets = 0
    rows_written = 0
    dropped_total = 0
    stale_imu_rows = 0
    synthetic_seq = 0
    last_seq: Optional[int] = None
    start_wall = time.time()

    try:
        with open(output_csv, "w", newline="", buffering=1) as f:
            writer = csv.writer(f)
            writer.writerow(SESSION_COLUMNS)

            while True:
                if duration_s > 0 and (time.time() - start_wall) >= duration_s:
                    break

                try:
                    source, sample = event_queue.get(timeout=0.2)
                except queue.Empty:
                    continue

                if source == "IMU":
                    imu_buffer.append(sample)
                    continue

                # Source == "PPG": emit one merged row per PPG sample.
                ppg_packets += 1
                ppg_sample: PPGSample = sample
                imu_sample, imu_age_s = _nearest_imu(ppg_sample.host_time, imu_buffer)
                imu_is_fresh = imu_sample is not None and imu_age_s * 1000.0 <= max_sync_age_ms
                if not imu_is_fresh:
                    stale_imu_rows += 1
                    imu_sample = None

                seq = ppg_sample.seq if ppg_sample.seq is not None else synthetic_seq
                if ppg_sample.seq is None:
                    synthetic_seq = (synthetic_seq + 1) % 65_536

                ts_ms = ppg_sample.ts_ms
                if ts_ms is None:
                    ts_ms = int((ppg_sample.host_time - start_wall) * 1000.0)

                dropped_before = 0
                if last_seq is not None:
                    expected = (last_seq + 1) % 65_536
                    if seq != expected:
                        dropped_before = (seq - expected) % 65_536
                last_seq = seq
                dropped_total += dropped_before

                if imu_sample is None:
                    ax = ay = az = gx = gy = gz = 0.0
                else:
                    ax = imu_sample.ax
                    ay = imu_sample.ay
                    az = imu_sample.az
                    gx = imu_sample.gx
                    gy = imu_sample.gy
                    gz = imu_sample.gz

                writer.writerow(
                    (
                        ppg_sample.host_time,
                        seq,
                        ts_ms,
                        ax,
                        ay,
                        az,
                        gx,
                        gy,
                        gz,
                        ppg_sample.ppg,
                        dropped_before,
                    )
                )
                rows_written += 1

                if print_every > 0 and rows_written % print_every == 0:
                    fresh_pct = 0.0 if rows_written == 0 else 100.0 * (rows_written - stale_imu_rows) / rows_written
                    print(
                        "[bridge] rows="
                        f"{rows_written} ppg={ppg_packets} imu={imu_reader.good_lines} "
                        f"dropped={dropped_total} fresh_imu={fresh_pct:.1f}%",
                        flush=True,
                    )
    except KeyboardInterrupt:
        pass
    finally:
        imu_reader.stop()
        ppg_reader.stop()
        imu_reader.join(timeout=1.0)
        ppg_reader.join(timeout=1.0)

    fresh_rows = rows_written - stale_imu_rows
    fresh_pct = 0.0 if rows_written == 0 else 100.0 * fresh_rows / rows_written
    print(f"[bridge] Done. Rows written: {rows_written}", flush=True)
    print(f"[bridge] IMU reader: good={imu_reader.good_lines} bad={imu_reader.bad_lines}", flush=True)
    print(f"[bridge] PPG reader: good={ppg_reader.good_lines} bad={ppg_reader.bad_lines}", flush=True)
    print(f"[bridge] Merged rows with fresh IMU: {fresh_rows}/{rows_written} ({fresh_pct:.1f}%)", flush=True)
    print(f"[bridge] Total dropped_before: {dropped_total}", flush=True)
    print(f"[bridge] Session CSV: {output_csv}", flush=True)
    return output_csv


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture IMU + PPG serial streams and merge to one session CSV."
    )
    parser.add_argument("--imu-port", required=True, help="IMU serial port (example: COM5)")
    parser.add_argument("--ppg-port", required=True, help="PPG serial port (example: COM3)")
    parser.add_argument("--imu-baud", type=int, default=115200, help="IMU baud rate")
    parser.add_argument("--ppg-baud", type=int, default=115200, help="PPG baud rate")
    parser.add_argument(
        "--sessions-dir",
        default="sessions",
        help="Directory where merged session CSV is written",
    )
    parser.add_argument(
        "--output-csv",
        default="",
        help="Optional explicit output CSV path",
    )
    parser.add_argument(
        "--max-sync-age-ms",
        type=float,
        default=200.0,
        help="Max allowed host-time mismatch between IMU and PPG sample",
    )
    parser.add_argument(
        "--print-every",
        type=int,
        default=500,
        help="Print bridge stats every N merged rows",
    )
    parser.add_argument(
        "--duration-s",
        type=float,
        default=0.0,
        help="Optional auto-stop duration in seconds (0 = run until Ctrl+C)",
    )
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    output_csv = Path(args.output_csv) if args.output_csv else None
    run_capture(
        imu_port=args.imu_port,
        ppg_port=args.ppg_port,
        imu_baud=args.imu_baud,
        ppg_baud=args.ppg_baud,
        sessions_dir=Path(args.sessions_dir),
        output_csv=output_csv,
        max_sync_age_ms=args.max_sync_age_ms,
        print_every=args.print_every,
        duration_s=args.duration_s,
    )


if __name__ == "__main__":
    main()
