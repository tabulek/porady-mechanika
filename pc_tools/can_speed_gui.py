"""Graficzne narzędzie do zmiany prędkości magistrali CAN przez Arduino.

Aplikacja tworzy prosty interfejs okienkowy (Tkinter), w którym można
wybrać prędkość z listy popularnych wartości lub wpisać własną i wysłać
komendę `B <prędkość>` do szkicu Arduino przez port szeregowy.
"""

from __future__ import annotations

import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

MAX_CAN_BITRATE = 1_000_000
CAN_SFF_MAX = 0x7FF
CAN_EFF_MAX = 0x1FFFFFFF

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
    import tkinter as tk
    from tkinter import messagebox, ttk
except ImportError as exc:  # pragma: no cover - zależne od środowiska wykonawczego
    raise SystemExit(
        "Biblioteka Tkinter (Tcl/Tk) jest wymagana. Na Windows zainstaluj oficjalnego "
        "Pythona z python.org i upewnij się, że moduł 'tcl/tk and IDLE' jest zaznaczony. "
        "Jeżeli korzystasz z wersji ze Sklepu Microsoft, użyj opcji 'Advanced options' » "
        "'Repair' lub przeinstaluj ją z python.org – samo 'pip install tk' nie dostarcza "
        "modułu _tkinter."
    ) from exc

try:
    import serial
    from serial import Serial
    from serial.tools import list_ports
except ImportError as exc:  # pragma: no cover - zależne od środowiska wykonawczego
    raise SystemExit(
        "Pakiet pyserial jest wymagany. Zainstaluj go poleceniem 'pip install pyserial'."
    ) from exc


SPEED_OPTIONS: list[tuple[str, str]] = [
    ("100 kb/s", "100000"),
    ("125 kb/s", "125000"),
    ("200 kb/s", "200000"),
    ("225 kb/s", "225000"),
    ("500 kb/s", "500000"),
    ("800 kb/s", "800000"),
    ("Niestandardowa", "custom"),
]

ARDUINO_RESET_DELAY = 2.0  # seconds
DEFAULT_RESPONSE_TIMEOUT = 5.0  # must exceed ARDUINO_RESET_DELAY to receive replies reliably
FRAME_DISPLAY_LIMIT = 30
SERIAL_POLL_INTERVAL_MS = 100


@dataclass
class FrameRow:
    hex_payload: str
    ascii_payload: str


def parse_frame_id(raw_id: str, extended_selected: bool) -> tuple[str, bool]:
    text = raw_id.strip()
    if not text:
        raise ValueError("Podaj identyfikator ramki.")

    if text.lower().startswith("0x"):
        text = text[2:]

    normalized = text.replace("_", "").strip()
    if not normalized:
        raise ValueError("Podaj identyfikator ramki.")

    try:
        value = int(normalized, 16)
    except ValueError as exc:
        raise ValueError("ID musi być liczbą szesnastkową.") from exc

    extended = extended_selected
    if value > CAN_SFF_MAX:
        extended = True

    if extended:
        if value > CAN_EFF_MAX:
            raise ValueError("ID 29-bitowe musi mieścić się w zakresie 0..1FFFFFFF.")
        formatted = f"{value:08X}"
    else:
        if value > CAN_SFF_MAX:
            raise ValueError("ID 11-bitowe musi mieścić się w zakresie 0..7FF.")
        formatted = f"{value:03X}"

    return formatted, extended


def parse_data_bytes(text: str) -> list[str]:
    stripped = text.strip()
    if not stripped:
        return []

    tokens: list[str] = []
    current: list[str] = []
    separators = {" ", "\t", "\r", "\n", ",", ";", "-", "/", "#"}

    for char in stripped:
        if char in separators:
            if current:
                tokens.append("".join(current))
                current = []
            continue
        current.append(char)

    if current:
        tokens.append("".join(current))

    bytes_out: list[str] = []
    for token in tokens:
        chunk = token.strip()
        if not chunk:
            continue
        if chunk.lower().startswith("0x"):
            chunk = chunk[2:]
        chunk = chunk.replace("_", "").replace(":", "").replace(".", "")
        if not chunk:
            raise ValueError("Niepoprawne dane ramki.")
        if len(chunk) % 2 != 0:
            if len(chunk) == 1:
                chunk = "0" + chunk
            else:
                raise ValueError("Podaj pary znaków HEX (np. 0A FF lub 0AFF).")
        for index in range(0, len(chunk), 2):
            if len(bytes_out) >= 8:
                raise ValueError("Ramka może zawierać maksymalnie 8 bajtów danych.")
            byte_str = chunk[index : index + 2]
            try:
                int(byte_str, 16)
            except ValueError as exc:
                raise ValueError("Dane ramki muszą być liczbami HEX.") from exc
            bytes_out.append(byte_str.upper())

    return bytes_out


def build_transmit_fields(
    raw_id: str,
    extended_selected: bool,
    is_remote: bool,
    dlc_value: int,
    data_text: str,
) -> tuple[str, str, bool, bool]:
    can_id, extended = parse_frame_id(raw_id, extended_selected)

    if is_remote:
        if dlc_value is None:
            raise ValueError("Podaj długość ramki RTR w zakresie 0-8.")
        if not 0 <= dlc_value <= 8:
            raise ValueError("Długość ramki RTR musi mieścić się w zakresie 0-8.")
        payload = f"RTR {dlc_value}" if dlc_value else "RTR"
        summary = f"{can_id} {payload}"
        return f"{can_id} {payload}", summary, extended, False

    data_bytes = parse_data_bytes(data_text)
    if len(data_bytes) > 8:
        raise ValueError("Ramka może zawierać maksymalnie 8 bajtów danych.")

    payload = "".join(data_bytes)
    display_payload = " ".join(data_bytes) if data_bytes else "(brak danych)"
    command = f"{can_id} {payload}".strip()
    summary = f"{can_id} {display_payload}".strip()
    return command, summary, extended, True

def build_command(speed_bps: int) -> bytes:
    return f"B {speed_bps}\n".encode("ascii")


def build_transmit_command(payload: str) -> bytes:
    payload = payload.strip()
    if payload:
        return f"T {payload}\n".encode("ascii")
    return b"T\n"


def format_speed_label(speed_bps: int) -> str:
    if speed_bps % 1000 == 0:
        return f"{speed_bps // 1000} kb/s"
    return f"{speed_bps} b/s"


def _parse_speed_value(text: str) -> tuple[int, bool]:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Podaj wartość prędkości.")

    lowered = stripped.lower()
    multiplier = 1
    suffix_used: str | None = None
    for suffix, factor in _SPEED_SUFFIXES:
        if lowered.endswith(suffix):
            multiplier = factor
            suffix_used = suffix
            stripped = stripped[: -len(suffix)].strip()
            lowered = stripped.lower()
            break

    normalized = stripped.replace(",", ".")
    if not normalized:
        raise ValueError("Podaj wartość prędkości.")

    try:
        numeric_value = float(normalized)
    except ValueError as exc:
        raise ValueError(
            "Prędkość musi być dodatnią liczbą, możesz użyć sufiksów k lub M (np. 250k, 0.5M)."
        ) from exc

    if numeric_value <= 0:
        raise ValueError("Prędkość musi być większa od zera.")

    bps = int(round(numeric_value * multiplier))
    explicit_bps = suffix_used in _BASE_SUFFIXES
    assumed_kilobits = False

    if suffix_used is None and not explicit_bps and multiplier == 1 and bps <= 2000:
        bps = int(round(numeric_value * 1000))
        assumed_kilobits = True

    if bps <= 0:
        raise ValueError("Prędkość musi być większa od zera.")
    if bps > MAX_CAN_BITRATE:
        raise ValueError("Obsługiwane są wartości do 1000000 b/s.")

    return bps, assumed_kilobits


def parse_ok_response(response: str) -> tuple[bool, int | None]:
    parts = response.split()
    if not parts or parts[0] != "OK":
        return False, None
    if len(parts) >= 2 and parts[1].isdigit():
        return True, int(parts[1])
    return True, None


def prepare_serial_port(port: Serial, reset_delay: float = ARDUINO_RESET_DELAY) -> None:
    """Flush buffers and wait for boards that reset when the port opens."""

    try:
        port.reset_input_buffer()
        port.reset_output_buffer()
    except serial.SerialException:
        pass

    if reset_delay > 0:
        time.sleep(reset_delay)
        try:
            port.reset_input_buffer()
        except serial.SerialException:
            pass


class CanSpeedApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Ustawianie prędkości CAN")
        self.root.resizable(True, True)

        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.serial_port: Serial | None = None
        self.serial_reader_thread: threading.Thread | None = None
        self.serial_reader_stop: threading.Event | None = None
        self.serial_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self.connection_lock = threading.Lock()
        self.port_write_lock = threading.Lock()
        self.pending_command: dict[str, object] | None = None
        self.pending_timer: str | None = None
        self.command_counter = 0
        self.current_port_name: str | None = None
        self.current_baud: int | None = None
        self.speed_header = "CAN: —"
        self.frame_rows: OrderedDict[str, FrameRow] = OrderedDict()
        self.serial_poll_id: str | None = None
        self._closing = False

        self.port_var = tk.StringVar()
        self.baud_var = tk.IntVar(value=115200)
        self.timeout_var = tk.DoubleVar(value=DEFAULT_RESPONSE_TIMEOUT)
        self.speed_var = tk.StringVar(value=SPEED_OPTIONS[4][1])  # domyślnie 500 kb/s
        self.status_var = tk.StringVar(value="Wybierz port i prędkość, a następnie naciśnij 'Ustaw'.")
        self.tx_id_var = tk.StringVar(value="123")
        self.tx_data_var = tk.StringVar()
        self.tx_extended_var = tk.BooleanVar(value=False)
        self.tx_remote_var = tk.BooleanVar(value=False)
        self.tx_dlc_var = tk.IntVar(value=0)

        self.send_frame_button: ttk.Button | None = None
        self.tx_data_entry: ttk.Entry | None = None
        self.tx_dlc_spin: tk.Spinbox | None = None

        main_frame = ttk.Frame(root, padding=16)
        main_frame.grid(row=0, column=0, sticky="nsew")
        main_frame.columnconfigure(0, weight=0)
        main_frame.columnconfigure(1, weight=1)
        main_frame.rowconfigure(0, weight=1)

        controls = ttk.Frame(main_frame)
        controls.grid(row=0, column=0, sticky="nw")

        frames_column = ttk.Frame(main_frame)
        frames_column.grid(row=0, column=1, sticky="nsew", padx=(16, 0))
        frames_column.columnconfigure(0, weight=1)
        frames_column.rowconfigure(0, weight=1)

        self._build_port_section(controls)
        self._build_serial_section(controls)
        self._build_speed_section(controls)
        self._build_actions(controls)
        self._build_transmit_section(controls)
        self._build_frame_section(frames_column)

        self.status_label = tk.Label(root, textvariable=self.status_var, anchor="w")
        self.status_label.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        self.refresh_ports()
        self._refresh_frame_display()
        self._schedule_serial_queue_poll()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_port_section(self, parent: ttk.Frame) -> None:
        port_frame = ttk.LabelFrame(parent, text="Port szeregowy")
        port_frame.grid(row=0, column=0, sticky="ew")

        ttk.Label(port_frame, text="Port:").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=8)

        self.port_combo = ttk.Combobox(port_frame, textvariable=self.port_var, width=15)
        self.port_combo.grid(row=0, column=1, sticky="ew", pady=8)
        self.port_combo['values'] = []

        refresh_button = ttk.Button(port_frame, text="Odśwież", command=self.refresh_ports)
        refresh_button.grid(row=0, column=2, padx=8, pady=8)

        port_frame.columnconfigure(1, weight=1)

    def _build_serial_section(self, parent: ttk.Frame) -> None:
        serial_frame = ttk.LabelFrame(parent, text="Parametry połączenia")
        serial_frame.grid(row=1, column=0, sticky="ew", pady=(12, 0))

        ttk.Label(serial_frame, text="Baud (UART):").grid(row=0, column=0, padx=(8, 4), pady=8, sticky="w")
        baud_entry = ttk.Entry(serial_frame, textvariable=self.baud_var, width=10)
        baud_entry.grid(row=0, column=1, pady=8, sticky="w")

        ttk.Label(serial_frame, text="Timeout [s]:").grid(row=0, column=2, padx=(16, 4), pady=8, sticky="w")
        timeout_entry = ttk.Entry(serial_frame, textvariable=self.timeout_var, width=10)
        timeout_entry.grid(row=0, column=3, pady=8, sticky="w")

        serial_frame.columnconfigure(1, weight=1)
        serial_frame.columnconfigure(3, weight=1)

    def _build_speed_section(self, parent: ttk.Frame) -> None:
        speed_frame = ttk.LabelFrame(parent, text="Prędkość magistrali CAN")
        speed_frame.grid(row=2, column=0, sticky="ew", pady=(12, 0))

        custom_row = 0
        for idx, (label, value) in enumerate(SPEED_OPTIONS):
            ttk.Radiobutton(
                speed_frame,
                text=label,
                value=value,
                variable=self.speed_var,
                command=self._on_speed_change,
            ).grid(row=idx, column=0, sticky="w", padx=(8, 4), pady=4)
            if value == "custom":
                custom_row = idx

        ttk.Label(speed_frame, text="Custom [bps]:").grid(
            row=custom_row, column=1, padx=(12, 4), pady=4, sticky="e"
        )
        self.custom_entry = ttk.Entry(speed_frame, width=12)
        self.custom_entry.grid(row=custom_row, column=2, pady=4, sticky="w")
        self.custom_entry.configure(state="disabled")

        speed_frame.columnconfigure(2, weight=1)

    def _build_actions(self, parent: ttk.Frame) -> None:
        actions = ttk.Frame(parent)
        actions.grid(row=3, column=0, sticky="ew", pady=(16, 0))

        self.set_button = ttk.Button(actions, text="Ustaw prędkość", command=self.on_set_speed)
        self.set_button.grid(row=0, column=0, padx=(0, 8))

        self.quit_button = ttk.Button(actions, text="Zamknij", command=self.root.destroy)
        self.quit_button.grid(row=0, column=1)

    def _build_transmit_section(self, parent: ttk.Frame) -> None:
        tx_frame = ttk.LabelFrame(parent, text="Wyślij ramkę CAN")
        tx_frame.grid(row=4, column=0, sticky="ew", pady=(16, 0))
        tx_frame.columnconfigure(1, weight=1)
        tx_frame.columnconfigure(2, weight=1)

        ttk.Label(tx_frame, text="ID (HEX):").grid(row=0, column=0, sticky="w", padx=(8, 4), pady=4)
        id_entry = ttk.Entry(tx_frame, textvariable=self.tx_id_var, width=12)
        id_entry.grid(row=0, column=1, sticky="w", pady=4)

        ttk.Checkbutton(
            tx_frame,
            text="29 bit (extended)",
            variable=self.tx_extended_var,
        ).grid(row=0, column=2, sticky="w", padx=(8, 8), pady=4)

        ttk.Label(tx_frame, text="Dane (HEX):").grid(row=1, column=0, sticky="nw", padx=(8, 4), pady=4)
        self.tx_data_entry = ttk.Entry(tx_frame, textvariable=self.tx_data_var, width=32)
        self.tx_data_entry.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 8), pady=4)

        ttk.Checkbutton(
            tx_frame,
            text="Ramka zdalna (RTR)",
            variable=self.tx_remote_var,
            command=self._on_tx_remote_toggle,
        ).grid(row=2, column=0, sticky="w", padx=(8, 4), pady=4)

        ttk.Label(tx_frame, text="Długość:").grid(row=2, column=1, sticky="e", pady=4)
        self.tx_dlc_spin = tk.Spinbox(tx_frame, from_=0, to=8, width=5, textvariable=self.tx_dlc_var, state="disabled")
        self.tx_dlc_spin.grid(row=2, column=2, sticky="w", padx=(0, 8), pady=4)

        self.send_frame_button = ttk.Button(tx_frame, text="Wyślij ramkę", command=self.on_send_frame)
        self.send_frame_button.grid(row=3, column=0, columnspan=3, sticky="w", padx=8, pady=(8, 8))

        self._on_tx_remote_toggle()

    def _build_frame_section(self, parent: ttk.Frame) -> None:
        frame_box = ttk.LabelFrame(parent, text="Ramki CAN")
        frame_box.grid(row=0, column=0, sticky="nsew")
        frame_box.columnconfigure(0, weight=1)
        frame_box.rowconfigure(0, weight=1)

        scrollbar = ttk.Scrollbar(frame_box, orient="vertical")
        scrollbar.grid(row=0, column=1, sticky="ns", pady=8)

        self.frame_text = tk.Text(
            frame_box,
            width=52,
            height=20,
            font=("Courier New", 10),
            state="disabled",
            wrap="none",
        )
        self.frame_text.grid(row=0, column=0, padx=(8, 0), pady=8, sticky="nsew")
        self.frame_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.configure(command=self.frame_text.yview)

    def request_frame_transmission(
        self,
        payload: str,
        summary: str,
        clear_data: bool,
    ) -> bool:
        if self.pending_command is not None:
            self._set_status("Inne polecenie jest aktualnie wykonywane.", False)
            return False

        port_name = self.port_var.get().strip()
        if not port_name:
            self._set_status("Wybierz port szeregowy w głównym oknie.", False)
            return False

        try:
            baud = int(self.baud_var.get())
        except (TypeError, ValueError):
            self._set_status("Niepoprawny baud w ustawieniach portu.", False)
            return False

        try:
            timeout = float(self.timeout_var.get())
        except (TypeError, ValueError):
            self._set_status("Niepoprawny timeout w ustawieniach portu.", False)
            return False

        if timeout < DEFAULT_RESPONSE_TIMEOUT:
            timeout = DEFAULT_RESPONSE_TIMEOUT
            self.timeout_var.set(timeout)

        self.command_counter += 1
        command_id = self.command_counter
        timeout_ms = int(timeout * 1000)

        self.pending_command = {
            "type": "frame",
            "id": command_id,
            "summary": summary,
            "port": port_name,
            "baud": baud,
            "clear_data": clear_data,
        }
        self.pending_timer = self.root.after(timeout_ms, lambda: self._on_command_timeout(command_id))

        threading.Thread(
            target=self._send_frame_command_worker,
            args=(command_id, port_name, baud, payload),
            daemon=True,
        ).start()

        self._set_status(f"Wysyłanie ramki {summary}...", None)
        return True

    def _on_tx_remote_toggle(self) -> None:
        is_remote = bool(self.tx_remote_var.get())
        if self.tx_data_entry is not None:
            state = "disabled" if is_remote else "normal"
            self.tx_data_entry.configure(state=state)
        if self.tx_dlc_spin is not None:
            state = "normal" if is_remote else "disabled"
            self.tx_dlc_spin.configure(state=state)

    def on_send_frame(self) -> None:
        try:
            dlc_value = int(self.tx_dlc_var.get())
        except (TypeError, ValueError):
            dlc_value = 0

        try:
            command, summary, actual_extended, clear_data = build_transmit_fields(
                self.tx_id_var.get(),
                bool(self.tx_extended_var.get()),
                bool(self.tx_remote_var.get()),
                dlc_value,
                self.tx_data_var.get(),
            )
        except ValueError as exc:
            self._set_status(str(exc), False)
            if self.send_frame_button is not None:
                self.send_frame_button.configure(state="normal")
            return

        self.tx_extended_var.set(actual_extended)

        if self.send_frame_button is not None:
            self.send_frame_button.configure(state="disabled")

        if not self.request_frame_transmission(command, summary, clear_data):
            if self.send_frame_button is not None:
                self.send_frame_button.configure(state="normal")

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        current = self.port_var.get()
        self.port_combo['values'] = ports

        if ports:
            if current in ports:
                self.port_var.set(current)
            else:
                self.port_var.set(ports[0])
        else:
            self.port_var.set("")
        self._set_status("Znalezione porty: " + (", ".join(ports) if ports else "brak"))

    def _on_speed_change(self) -> None:
        if self.speed_var.get() == "custom":
            self.custom_entry.configure(state="normal")
            self.custom_entry.focus_set()
        else:
            self.custom_entry.delete(0, tk.END)
            self.custom_entry.configure(state="disabled")

    def on_set_speed(self) -> None:
        if self.pending_command is not None:
            return

        port_name = self.port_var.get().strip()
        if not port_name:
            messagebox.showwarning("Brak portu", "Wybierz port szeregowy z listy.")
            return

        try:
            baud = int(self.baud_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("Niepoprawny baud", "Wprowadź prawidłową prędkość UART (liczba całkowita).")
            return

        try:
            timeout = float(self.timeout_var.get())
        except (TypeError, ValueError):
            messagebox.showerror("Niepoprawny timeout", "Wprowadź poprawny czas oczekiwania w sekundach.")
            return
        if timeout < DEFAULT_RESPONSE_TIMEOUT:
            timeout = DEFAULT_RESPONSE_TIMEOUT
            self.timeout_var.set(timeout)

        speed_value = self.speed_var.get()
        interpretation_note: str | None = None
        if speed_value == "custom":
            custom_text = self.custom_entry.get().strip()
            if not custom_text:
                messagebox.showwarning("Brak wartości", "Podaj prędkość w bitach na sekundę.")
                return
            try:
                speed_bps, assumed_kilobits = _parse_speed_value(custom_text)
            except ValueError as exc:
                messagebox.showerror("Niepoprawna wartość", str(exc))
                return
            if assumed_kilobits:
                interpretation_note = (
                    f"(Zinterpretowano {custom_text} jako {format_speed_label(speed_bps)})"
                )
        else:
            speed_bps = int(speed_value)

        if speed_bps <= 0:
            messagebox.showerror("Niepoprawna wartość", "Prędkość musi być dodatnia.")
            return

        requested_label = format_speed_label(speed_bps)
        status_message = f"Łączenie z {port_name} i wysyłanie polecenia ({requested_label})..."
        if interpretation_note:
            status_message += f" {interpretation_note}"
        self._set_status(status_message)
        self.set_button.configure(state="disabled")

        self.command_counter += 1
        command_id = self.command_counter
        timeout_ms = int(timeout * 1000)
        self.pending_command = {
            "type": "speed",
            "id": command_id,
            "speed_bps": speed_bps,
            "requested_label": requested_label,
            "interpretation_note": interpretation_note,
            "port": port_name,
            "baud": baud,
        }
        self.pending_timer = self.root.after(timeout_ms, lambda: self._on_command_timeout(command_id))

        threading.Thread(
            target=self._send_command_worker,
            args=(command_id, port_name, baud, speed_bps),
            daemon=True,
        ).start()

    def _send_frame_command_worker(
        self,
        command_id: int,
        port_name: str,
        baud: int,
        payload: str,
    ) -> None:
        try:
            port = self._ensure_serial_connection(port_name, baud)
        except serial.SerialException as exc:
            self.root.after(0, self._command_failed, command_id, f"Błąd portu: {exc}")
            return

        if port is None:
            self.root.after(0, self._command_failed, command_id, "Nie udało się otworzyć portu.")
            return

        try:
            with self.port_write_lock:
                port.write(build_transmit_command(payload))
                port.flush()
        except serial.SerialException as exc:
            self.root.after(0, self._command_failed, command_id, f"Błąd portu: {exc}")
            return

    def _send_command_worker(
        self,
        command_id: int,
        port_name: str,
        baud: int,
        speed_bps: int,
    ) -> None:
        try:
            port = self._ensure_serial_connection(port_name, baud)
        except serial.SerialException as exc:
            self.root.after(0, self._command_failed, command_id, f"Błąd portu: {exc}")
            return

        if port is None:
            self.root.after(0, self._command_failed, command_id, "Nie udało się otworzyć portu.")
            return

        try:
            with self.port_write_lock:
                port.write(build_command(speed_bps))
                port.flush()
        except serial.SerialException as exc:
            self.root.after(0, self._command_failed, command_id, f"Błąd portu: {exc}")
            return

    def _ensure_serial_connection(self, port_name: str, baud: int) -> Serial | None:
        with self.connection_lock:
            if (
                self.serial_port
                and self.serial_port.is_open
                and self.current_port_name == port_name
                and self.current_baud == baud
            ):
                return self.serial_port

            old_port = self.serial_port
            old_stop = self.serial_reader_stop
            old_thread = self.serial_reader_thread
            self.serial_port = None
            self.serial_reader_stop = None
            self.serial_reader_thread = None
            self.current_port_name = None
            self.current_baud = None

        if old_stop is not None:
            old_stop.set()
        if old_thread is not None:
            old_thread.join(timeout=1.0)
        if old_port is not None:
            try:
                old_port.close()
            except serial.SerialException:
                pass

        port = Serial(port_name, baud, timeout=0.1)

        try:
            prepare_serial_port(port)
        except serial.SerialException:
            port.close()
            raise

        stop_event = threading.Event()
        reader = threading.Thread(
            target=self._serial_reader_loop,
            args=(port, stop_event),
            daemon=True,
        )
        reader.start()

        with self.connection_lock:
            self.serial_port = port
            self.serial_reader_stop = stop_event
            self.serial_reader_thread = reader
            self.current_port_name = port_name
            self.current_baud = baud

        return port

    def _serial_reader_loop(self, port: Serial, stop_event: threading.Event) -> None:
        buffer = bytearray()

        while not stop_event.is_set():
            try:
                data = port.read(port.in_waiting or 1)
            except serial.SerialException as exc:
                self.serial_queue.put(("error", f"Błąd portu: {exc}"))
                break

            if not data:
                time.sleep(0.05)
                continue

            buffer.extend(data)
            while b"\n" in buffer:
                line, _, buffer = buffer.partition(b"\n")
                text = line.decode("ascii", errors="replace").strip()
                if text:
                    self.serial_queue.put(("line", text))

    def _schedule_serial_queue_poll(self) -> None:
        if self._closing:
            return
        self.serial_poll_id = self.root.after(SERIAL_POLL_INTERVAL_MS, self._process_serial_queue)

    def _process_serial_queue(self) -> None:
        if self._closing:
            return
        try:
            while True:
                kind, payload = self.serial_queue.get_nowait()
                if kind == "line":
                    self._handle_serial_line(payload)
                elif kind == "error":
                    self._handle_serial_error(payload)
        except queue.Empty:
            pass

        self._schedule_serial_queue_poll()

    def _store_frame(self, frame_text: str) -> None:
        frame_text = frame_text.strip()
        if not frame_text:
            return

        parts = frame_text.split(" ", 1)
        can_id = parts[0]
        payload = parts[1].strip() if len(parts) > 1 else ""

        ascii_payload = self._payload_to_ascii(payload)

        if can_id in self.frame_rows:
            self.frame_rows[can_id] = FrameRow(payload, ascii_payload)
            return

        if len(self.frame_rows) >= FRAME_DISPLAY_LIMIT:
            self.frame_rows.popitem(last=True)

        self.frame_rows[can_id] = FrameRow(payload, ascii_payload)

    @staticmethod
    def _payload_to_ascii(payload: str) -> str:
        if not payload:
            return ""

        if payload.upper() == "RTR":
            return "[RTR]"

        cleaned = "".join(payload.split())
        if len(cleaned) < 2:
            return ""

        ascii_chars: list[str] = []
        for i in range(0, len(cleaned), 2):
            byte_hex = cleaned[i : i + 2]
            if len(byte_hex) < 2:
                break
            try:
                byte_value = int(byte_hex, 16)
            except ValueError:
                return ""
            if 32 <= byte_value <= 126:
                ascii_chars.append(chr(byte_value))
            else:
                ascii_chars.append('.')

        return "".join(ascii_chars)

    def _handle_serial_error(self, message: str) -> None:
        self._command_failed_from_main(message)

    def _handle_serial_line(self, line: str) -> None:
        if line == "FRAME_RESET":
            self.frame_rows.clear()
            self._refresh_frame_display()
            return

        if line.startswith("FRAME "):
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                try:
                    slot = int(parts[1])
                except ValueError:
                    return
                if slot >= 0:
                    self._store_frame(parts[2])
                    self._refresh_frame_display()
            return

        if line.startswith("SPEED "):
            parts = line.split()
            if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                applied = int(parts[1])
                requested = int(parts[2])
                header = f"CAN: {format_speed_label(applied)}"
                if requested != applied:
                    header += f" (req {format_speed_label(requested)})"
                self.speed_header = header
                self._refresh_frame_display()
            return

        if line.startswith("OK") or line.startswith("ERR"):
            self._handle_command_response(line)
            return

    def _handle_command_response(self, line: str) -> None:
        command = self.pending_command
        if command is None or command.get("id") is None:
            return

        command_id = int(command["id"])
        command_type = command.get("type", "speed")
        ok, reported = parse_ok_response(line)
        if command_type == "frame":
            summary = str(command.get("summary", "")).strip()
            if ok:
                _, _, remainder = line.partition(" ")
                remainder = remainder.strip()
                if summary:
                    message = f"Ramka wysłana: {summary}"
                else:
                    message = "Ramka wysłana."
                if remainder:
                    message += f" [{remainder}]"
                self._finish_command(command_id, message, True)
            else:
                message = f"Błąd wysyłania ramki: {line}"
                if summary:
                    message += f" ({summary})"
                self._finish_command(command_id, message, False)
            return

        interpretation_note = command.get("interpretation_note")
        requested_label = command.get("requested_label", "")
        speed_bps = int(command.get("speed_bps", 0))

        if ok:
            if reported is not None:
                if reported != speed_bps:
                    message = (
                        "Sukces: urządzenie ustawiło "
                        f"{format_speed_label(reported)} (żądano {requested_label})"
                    )
                else:
                    message = f"Sukces: prędkość ustawiona na {format_speed_label(reported)}"
            else:
                message = (
                    "Sukces: polecenie przyjęte dla "
                    f"{requested_label}"
                )
            if interpretation_note:
                message += f" {interpretation_note}"
            self._finish_command(command_id, message, True)
            return

        error_message = f"Urządzenie zgłosiło błąd: {line}"
        if interpretation_note:
            error_message += f" {interpretation_note}"
        self._finish_command(command_id, error_message, False)

    def _command_failed(self, command_id: int, message: str) -> None:
        self._finish_command(command_id, message, False)

    def _command_failed_from_main(self, message: str) -> None:
        if self.pending_command is not None:
            command_id = int(self.pending_command.get("id", 0))
            self._finish_command(command_id, message, False)
        else:
            self._set_status(message, False)

    def _finish_command(self, command_id: int, message: str, success: bool) -> None:
        command = self.pending_command
        if command is None or command.get("id") != command_id:
            return

        if self.pending_timer is not None:
            self.root.after_cancel(self.pending_timer)
            self.pending_timer = None

        self.pending_command = None

        command_type = command.get("type")
        if command_type == "frame":
            if self.send_frame_button is not None:
                self.send_frame_button.configure(state="normal")
            if success and command.get("clear_data"):
                self.tx_data_var.set("")
            self._on_tx_remote_toggle()
            self._set_status(message, success)
            return

        if command_type == "speed":
            self.set_button.configure(state="normal")
            self._set_status(message, success)
            return

        self._set_status(message, success)

    def _on_command_timeout(self, command_id: int) -> None:
        command = self.pending_command
        if command is None or command.get("id") != command_id:
            return

        command_type = command.get("type")
        if command_type == "frame":
            summary = str(command.get("summary", "")).strip()
            message = "Brak odpowiedzi podczas wysyłania ramki."
            if summary:
                message += f" ({summary})"
            self._finish_command(command_id, message, False)
            return

        interpretation_note = command.get("interpretation_note")
        message = "Brak odpowiedzi z urządzenia."
        if interpretation_note:
            message += f" {interpretation_note}"

        self._finish_command(command_id, message, False)

    def _refresh_frame_display(self) -> None:
        lines = [self.speed_header, ""]

        if self.frame_rows:
            for can_id, row in self.frame_rows.items():
                if row.hex_payload:
                    line = f"{can_id} {row.hex_payload}"
                else:
                    line = can_id
                if row.ascii_payload:
                    line += f"  {row.ascii_payload}"
                lines.append(line)
        else:
            lines.append("(brak ramek)")

        text = "\n".join(lines)
        self.frame_text.configure(state="normal")
        self.frame_text.delete("1.0", tk.END)
        self.frame_text.insert(tk.END, text)
        self.frame_text.configure(state="disabled")

    def _set_status(self, message: str, success: bool | None = None) -> None:
        self.status_var.set(message)
        if success is None:
            color = "black"
        else:
            color = "#008000" if success else "#a00000"
        self.status_label.configure(fg=color)

    def _close_serial(self) -> None:
        with self.connection_lock:
            port = self.serial_port
            stop_event = self.serial_reader_stop
            thread = self.serial_reader_thread
            self.serial_port = None
            self.serial_reader_stop = None
            self.serial_reader_thread = None
            self.current_port_name = None
            self.current_baud = None

        if stop_event is not None:
            stop_event.set()
        if thread is not None:
            thread.join(timeout=1.0)
        if port is not None:
            try:
                port.close()
            except serial.SerialException:
                pass

    def _on_close(self) -> None:
        self._closing = True
        if self.serial_poll_id is not None:
            self.root.after_cancel(self.serial_poll_id)
            self.serial_poll_id = None
        self._close_serial()
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    app = CanSpeedApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
