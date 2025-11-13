"""Command-line tool for changing the CAN bitrate on the Arduino gateway.

The Arduino sketch listens for simple ASCII commands over the serial port.
This tool opens the serial connection, sends the selected bitrate command
and waits for the confirmation response.
"""

from __future__ import annotations

import argparse
import sys
import time

MAX_CAN_BITRATE = 1_000_000

_SPEED_SUFFIXES = [
    ("mbps", 1_000_000),
    ("mb/s", 1_000_000),
    ("mbit/s", 1_000_000),
    ("mbits", 1_000_000),
    ("mbit", 1_000_000),
    ("mb", 1_000_000),
    ("m", 1_000_000),
    ("kbps", 1_000),
    ("kb/s", 1_000),
    ("kbit/s", 1_000),
    ("kbits", 1_000),
    ("kbit", 1_000),
    ("kb", 1_000),
    ("k", 1_000),
    ("bps", 1),
    ("b/s", 1),
    ("bits", 1),
    ("bit/s", 1),
    ("bit", 1),
    ("b", 1),
]

_BASE_SUFFIXES = {"bps", "b/s", "bits", "bit/s", "bit", "b"}

try:
    import serial
    from serial import Serial
except ImportError as exc:  # pragma: no cover - dependency availability depends on host
    raise SystemExit(
        "pyserial is required for this tool. Install it with 'pip install pyserial'."
    ) from exc

SUPPORTED_SPEEDS = {
    "100000": "100 kbps",
    "125000": "125 kbps",
    "200000": "200 kbps",
    "225000": "225 kbps",
    "500000": "500 kbps",
    "800000": "800 kbps",
}

ARDUINO_RESET_DELAY = 2.0  # seconds, allows boards that reset on port open to boot
DEFAULT_RESPONSE_TIMEOUT = 5.0  # must remain higher than ARDUINO_RESET_DELAY


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port",
        required=True,
        help="Serial port where the Arduino is connected (e.g. COM3 or /dev/ttyUSB0)",
    )
    parser.add_argument(
        "--speed",
        default="500000",
        help=(
            "Target CAN bitrate. Provide a numeric value (e.g. 225000), use suffixes such as "
            "250k or 0.5M, or choose one of the presets: "
            + ", ".join(f"{label} ({value})" for value, label in SUPPORTED_SPEEDS.items())
        ),
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
        default=DEFAULT_RESPONSE_TIMEOUT,
        help="How many seconds to wait for a response",
    )
    return parser.parse_args(argv)


def build_command(speed: str) -> bytes:
    return f"B {speed}\n".encode("ascii")


def human_readable_speed(bps: int) -> str:
    if bps % 1000 == 0:
        return f"{bps // 1000} kbps"
    return f"{bps} bps"


def _parse_speed_value(value: str) -> tuple[int, bool]:
    text = value.strip()
    if not text:
        raise ValueError("Speed value cannot be empty.")

    lowered = text.lower()
    multiplier = 1
    suffix_used: str | None = None
    for suffix, factor in _SPEED_SUFFIXES:
        if lowered.endswith(suffix):
            multiplier = factor
            suffix_used = suffix
            text = text[: -len(suffix)].strip()
            lowered = text.lower()
            break

    normalized = text.replace(",", ".")
    if not normalized:
        raise ValueError("Speed value cannot be empty.")

    try:
        numeric_value = float(normalized)
    except ValueError as exc:
        raise ValueError(
            "Speed must be a positive number, optionally with suffix k or M (e.g. 250k, 0.5M)."
        ) from exc

    if numeric_value <= 0:
        raise ValueError("Speed must be greater than zero.")

    bps = int(round(numeric_value * multiplier))
    explicit_bps = suffix_used in _BASE_SUFFIXES
    assumed_kilobits = False

    if suffix_used is None and not explicit_bps and multiplier == 1 and bps <= 2000:
        bps = int(round(numeric_value * 1000))
        assumed_kilobits = True

    if bps <= 0:
        raise ValueError("Speed must be greater than zero.")
    if bps > MAX_CAN_BITRATE:
        raise ValueError("Maximum supported speed is 1000000 bps for the MCP2515.")

    return bps, assumed_kilobits


def normalise_speed(value: str) -> tuple[str, str, bool]:
    text = value.strip()
    if not text:
        raise ValueError("Speed value cannot be empty.")

    if text in SUPPORTED_SPEEDS:
        return text, SUPPORTED_SPEEDS[text], False

    bps, assumed_kilobits = _parse_speed_value(text)
    return str(bps), human_readable_speed(bps), assumed_kilobits


def format_speed_label(value: str) -> str:
    if value in SUPPORTED_SPEEDS:
        return SUPPORTED_SPEEDS[value]
    if value.isdigit():
        numeric = int(value)
        return human_readable_speed(numeric)
    try:
        numeric, _ = _parse_speed_value(value)
    except ValueError:
        return value
    return human_readable_speed(numeric)


def parse_ok_response(response: str) -> tuple[bool, str | None]:
    parts = response.split()
    if not parts or parts[0] != "OK":
        return False, None
    if len(parts) >= 2 and parts[1].isdigit():
        return True, parts[1]
    return True, None


def prepare_serial_port(port: Serial, reset_delay: float = ARDUINO_RESET_DELAY) -> None:
    """Flush buffers and optionally wait for boards that reset on connect."""

    try:
        port.reset_input_buffer()
        port.reset_output_buffer()
    except serial.SerialException:
        pass  # Flushing can fail on some drivers; continue so writes raise later.

    if reset_delay > 0:
        time.sleep(reset_delay)
        try:
            port.reset_input_buffer()
        except serial.SerialException:
            pass  # Ignore errors from boards that reset mid-operation.


def read_response(port: Serial, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    buffer = bytearray()

    while time.monotonic() < deadline:
        if port.in_waiting:
            buffer.extend(port.read(port.in_waiting))
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                text = line.decode("ascii", errors="replace").strip()
                if not text:
                    continue
                if text.startswith(("OK", "ERR")):
                    return text
        time.sleep(0.05)

    return ""


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    if args.timeout < DEFAULT_RESPONSE_TIMEOUT:
        args.timeout = DEFAULT_RESPONSE_TIMEOUT

    try:
        requested_speed, requested_label, assumed_kilobits = normalise_speed(args.speed)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    try:
        with serial.Serial(args.port, args.baud, timeout=0.1) as port:
            prepare_serial_port(port)
            port.write(build_command(requested_speed))
            port.flush()
            response = read_response(port, args.timeout)
    except serial.SerialException as exc:
        print(f"Serial error: {exc}", file=sys.stderr)
        return 1

    if not response:
        message = "No response received from device"
        if assumed_kilobits:
            message += (
                f" (interpreted {args.speed} as {requested_label})"
            )
        print(message, file=sys.stderr)
        return 1

    is_ok, reported_speed = parse_ok_response(response)
    if is_ok:
        if reported_speed and reported_speed != requested_speed:
            message = (
                "Bitrate set to "
                f"{format_speed_label(reported_speed)} ({reported_speed} bps); requested "
                f"{requested_label} ({requested_speed} bps)"
            )
        elif reported_speed:
            message = (
                f"Bitrate changed to {format_speed_label(reported_speed)} ({reported_speed} bps)"
            )
        else:
            message = (
                f"Bitrate change command accepted for {requested_label} ({requested_speed} bps)"
            )
        if assumed_kilobits:
            message += f" (interpreted {args.speed} as {requested_label})"
        print(message)
        return 0

    error_message = f"Device reported error: {response}"
    if assumed_kilobits:
        error_message += f" (interpreted {args.speed} as {requested_label})"
    print(error_message, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
