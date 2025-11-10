"""Command-line tool for changing the CAN bitrate on the Arduino gateway.

The Arduino sketch listens for simple ASCII commands over the serial port.
This tool opens the serial connection, sends the selected bitrate command
and waits for the confirmation response.
"""

from __future__ import annotations

import argparse
import sys
import time

try:
    import serial
    from serial import Serial
except ImportError as exc:  # pragma: no cover - dependency availability depends on host
    raise SystemExit(
        "pyserial is required for this tool. Install it with 'pip install pyserial'."
    ) from exc

SUPPORTED_SPEEDS = {
    "125000": "125 kbps",
    "250000": "250 kbps",
    "500000": "500 kbps",
    "1000000": "1000 kbps",
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        required=True,
        help="Serial port where the Arduino is connected (e.g. COM3 or /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--speed",
        choices=SUPPORTED_SPEEDS.keys(),
        default="500000",
        help="Target CAN bitrate in bits per second",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=115200,
        help="Serial port baud rate used by the Arduino sketch",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=2.0,
        help="How many seconds to wait for a response",
    )
    return parser.parse_args(argv)


def build_command(speed: str) -> bytes:
    return f"B {speed}\n".encode("ascii")


def read_response(port: Serial, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    buffer = bytearray()

    while time.monotonic() < deadline:
        if port.in_waiting:
            buffer.extend(port.read(port.in_waiting))
            if buffer.endswith(b"\n"):
                break
        time.sleep(0.05)

    return buffer.decode("ascii", errors="replace").strip()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    try:
        with serial.Serial(args.port, args.baud, timeout=0.1) as port:
            port.reset_input_buffer()
            port.reset_output_buffer()
            port.write(build_command(args.speed))
            port.flush()
            response = read_response(port, args.timeout)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    if not response:
        print("No response received from device", file=sys.stderr)
        return 1

    if response.startswith("OK"):
        print(f"Bitrate changed to {SUPPORTED_SPEEDS[args.speed]} ({args.speed} bps)")
        return 0

    print(f"Device reported error: {response}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
