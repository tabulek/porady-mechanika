# Porady mechanika

## Arduino: wyświetlacz CAN

Kod w katalogu `arduino/can_display/can_display.ino` obsługuje moduł MCP2515
połączony z magistralą SPI (CS na D10, SO na D12, SI na D11, SCK na D13, INT na D2)
oraz wyświetlacz OLED SSD1306 podłączony do magistrali I²C (SDA=A4, SCL=A5).

Program:

- Inicjuje magistralę CAN z prędkością 500 kb/s (domyślnie).
- Wyświetla na ekranie ostatnie cztery ramki CAN (ID, długość, dane w HEX).
- Udostępnia przez port szeregowy komendę `B <prędkość>` do zmiany prędkości
  CAN (obsługiwane: 125000, 250000, 500000, 1000000 bit/s).

## Narzędzie PC do zmiany prędkości CAN

Skrypt `pc_tools/can_speed_cli.py` wysyła do Arduino odpowiednie polecenie
zmiany prędkości CAN przez port szeregowy.

Uruchomienie:

```bash
python pc_tools/can_speed_cli.py --port /dev/ttyUSB0 --speed 250000
```

W razie potrzeby ustaw parametry `--baud` (prędkość UART, domyślnie 115200) i
`--timeout` (czas oczekiwania na odpowiedź w sekundach).

Do działania wymagane jest zainstalowanie pakietu `pyserial`:

```bash
pip install pyserial
```
