"""
ingestion.py – Module 1
=======================
Dedicated serial reader thread that:
  • Never blocks the main thread (all I/O in a daemon thread).
  • Parses ASCII CSV packets: seq,ts_ms,ax,ay,az,gx,gy,gz,ppg
  • Detects dropped packets via 16-bit sequence-counter gaps.
  • Logs every raw packet (valid or malformed) to a timestamped
    session_YYYYMMDD_HHMMSS.csv in the sessions/ directory.
  • Exposes a thread-safe queue so downstream modules can consume
    parsed rows without ever touching the serial port.

Usage
-----
    ingestion = SerialIngestion(port='COM3')
    ingestion.start()

    while True:
        row = ingestion.get(timeout=1.0)   # blocks until data arrives
        if row:
            process(row)

    ingestion.stop()

The session CSV is the critical decoupling point: once it exists,
every other module can be developed and tested offline via replay mode.
"""

from __future__ import annotations

import csv
import queue
import threading
import time
from collections import namedtuple
from datetime import datetime
from pathlib import Path
from typing import Optional

import serial

# ── Packet format ─────────────────────────────────────────────────────────────
# Columns transmitted by Arduino firmware
_FIRMWARE_COLUMNS = ('seq', 'ts_ms', 'ax', 'ay', 'az', 'gx', 'gy', 'gz', 'ppg')

# Columns written to the session CSV (prepend wall-clock time, append flags)
SESSION_COLUMNS = ('wall_time', 'seq', 'ts_ms', 'ax', 'ay', 'az',
                   'gx', 'gy', 'gz', 'ppg', 'dropped_before')

SensorRow = namedtuple('SensorRow', SESSION_COLUMNS)

# Maximum number of parsed rows held in the in-memory queue before the oldest
# samples are silently discarded.  At 100 Hz this is ~60 s of data.
_QUEUE_MAXSIZE = 6_000

# How long (seconds) to wait for a line from the serial port before looping
_SERIAL_READ_TIMEOUT = 0.05  # 50 ms — keeps stop_event responsive


class SerialIngestion:
    """
    Thread-safe serial reader and CSV logger.

    Parameters
    ----------
    port : str
        System serial port identifier, e.g. 'COM3' or '/dev/ttyUSB0'.
    baud : int
        Must match the Arduino firmware (115 200).
    sessions_dir : str | Path
        Directory where session CSVs are written.  Created if absent.
    """

    def __init__(
        self,
        port: str,
        baud: int = 115_200,
        sessions_dir: str | Path = 'sessions',
    ) -> None:
        self.port = port
        self.baud = baud
        self._sessions_dir = Path(sessions_dir)
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # Thread-safe delivery queue
        self._queue: queue.Queue[SensorRow] = queue.Queue(maxsize=_QUEUE_MAXSIZE)

        # Control
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

        # Session file (set in start())
        self.session_path: Optional[Path] = None

        # Live statistics (updated atomically enough for display purposes)
        self.total_packets: int = 0
        self.bad_packets: int = 0
        self.total_dropped: int = 0   # sum of sequence gaps
        self._last_seq: Optional[int] = None

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Open the serial port and start the reader thread."""
        if self._thread and self._thread.is_alive():
            raise RuntimeError("SerialIngestion is already running")

        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        self.session_path = self._sessions_dir / f'session_{ts}.csv'

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name='serial-reader',
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        """Signal the reader thread to stop and wait for it to finish."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def get(self, timeout: float = 1.0) -> Optional[SensorRow]:
        """
        Return the next parsed row, or None if the queue is empty after
        *timeout* seconds.  Safe to call from any thread.
        """
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def get_nowait(self) -> Optional[SensorRow]:
        """Non-blocking variant — returns None immediately if queue is empty."""
        try:
            return self._queue.get_nowait()
        except queue.Empty:
            return None

    @property
    def drop_rate(self) -> float:
        """Fraction of expected packets that were never received."""
        expected = self.total_packets + self.total_dropped
        if expected == 0:
            return 0.0
        return self.total_dropped / expected

    # ── Internal ──────────────────────────────────────────────────────────────

    def _reader_loop(self) -> None:
        """Main loop — runs in its own daemon thread."""
        try:
            with (
                open(self.session_path, 'w', newline='', buffering=1) as csv_file,
                serial.Serial(self.port, self.baud, timeout=_SERIAL_READ_TIMEOUT) as ser,
            ):
                writer = csv.writer(csv_file)
                writer.writerow(SESSION_COLUMNS)

                # Flush any stale bytes in the hardware buffer
                ser.reset_input_buffer()

                while not self._stop_event.is_set():
                    raw = ser.readline()
                    if not raw:
                        continue  # Timeout — loop to check stop_event

                    wall_time = time.time()

                    try:
                        line = raw.decode('ascii', errors='ignore').strip()
                    except Exception:
                        continue

                    # Skip the firmware CSV header row or empty lines
                    if not line or line.startswith('seq') or line.startswith('ERR'):
                        if line.startswith('ERR'):
                            print(f"[FIRMWARE ERROR] {line}", flush=True)
                        continue

                    row, dropped = self._parse(line, wall_time)

                    if row is not None:
                        self.total_packets += 1
                        self.total_dropped += dropped
                        writer.writerow(row)

                        # Non-blocking enqueue: if full, drop the oldest sample
                        # and push the new one so the queue never starves the
                        # consumer of fresh data.
                        try:
                            self._queue.put_nowait(row)
                        except queue.Full:
                            try:
                                self._queue.get_nowait()
                            except queue.Empty:
                                pass
                            try:
                                self._queue.put_nowait(row)
                            except queue.Full:
                                pass
                    else:
                        self.bad_packets += 1

        except serial.SerialException as exc:
            print(f"[INGESTION] Serial error: {exc}", flush=True)
        except Exception as exc:
            print(f"[INGESTION] Unexpected error: {exc}", flush=True)

    def _parse(self, line: str, wall_time: float) -> tuple[Optional[SensorRow], int]:
        """
        Parse one CSV line.

        Returns
        -------
        (SensorRow, dropped_count) on success, or (None, 0) on failure.
        dropped_count is the number of sequence numbers that were skipped
        between the previous packet and this one.
        """
        parts = line.split(',')
        if len(parts) != len(_FIRMWARE_COLUMNS):
            return None, 0

        try:
            seq    = int(parts[0])
            ts_ms  = int(parts[1])
            ax     = float(parts[2])
            ay     = float(parts[3])
            az     = float(parts[4])
            gx     = float(parts[5])
            gy     = float(parts[6])
            gz     = float(parts[7])
            ppg    = int(parts[8])
        except ValueError:
            return None, 0

        # Detect sequence gaps (counter wraps at 65 536)
        dropped = 0
        if self._last_seq is not None:
            expected = (self._last_seq + 1) % 65_536
            if seq != expected:
                dropped = (seq - expected) % 65_536
                print(
                    f"[DROP] seq={seq}, expected={expected}, gap={dropped}",
                    flush=True,
                )
        self._last_seq = seq

        row = SensorRow(
            wall_time=wall_time,
            seq=seq,
            ts_ms=ts_ms,
            ax=ax, ay=ay, az=az,
            gx=gx, gy=gy, gz=gz,
            ppg=ppg,
            dropped_before=dropped,
        )
        return row, dropped


# ── Offline replay from saved session CSV ─────────────────────────────────────

def replay_session(session_csv: str | Path, realtime: bool = False):
    """
    Generator that yields SensorRow objects from a recorded session CSV.

    Parameters
    ----------
    session_csv : path
        Path to a session_YYYYMMDD_HHMMSS.csv written by SerialIngestion.
    realtime : bool
        If True, sleep between rows to approximate the original timing
        (useful for testing live visualisations).

    Yields
    ------
    SensorRow
    """
    path = Path(session_csv)
    if not path.exists():
        raise FileNotFoundError(f"Session file not found: {path}")

    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        prev_wall: Optional[float] = None

        for raw_row in reader:
            try:
                row = SensorRow(
                    wall_time      = float(raw_row['wall_time']),
                    seq            = int(raw_row['seq']),
                    ts_ms          = int(raw_row['ts_ms']),
                    ax             = float(raw_row['ax']),
                    ay             = float(raw_row['ay']),
                    az             = float(raw_row['az']),
                    gx             = float(raw_row['gx']),
                    gy             = float(raw_row['gy']),
                    gz             = float(raw_row['gz']),
                    ppg            = int(raw_row['ppg']),
                    dropped_before = int(raw_row.get('dropped_before', 0)),
                )
            except (KeyError, ValueError):
                continue

            if realtime and prev_wall is not None:
                dt = row.wall_time - prev_wall
                if 0 < dt < 1.0:   # cap sleep to avoid blocking on large gaps
                    time.sleep(dt)
            prev_wall = row.wall_time

            yield row
