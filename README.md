# Porady mechanika

## Arduino: wyświetlacz CAN

Kod w katalogu `arduino/can_display/can_display.ino` obsługuje moduł MCP2515
połączony z magistralą SPI (CS na D10, SO na D12, SI na D11, SCK na D13, INT na D2)
oraz wyświetlacz OLED SSD1306 podłączony do magistrali I²C (SDA=A4, SCL=A5)
z adresem ustawionym na 0x3C (pin ADDR zwarty do GND). Jeśli używasz innego
adresu, zmień stałą `OLED_I2C_ADDRESS` w szkicu.

### Samodzielny test wyświetlacza OLED

Jeżeli chcesz najpierw sprawdzić sam wyświetlacz (bez MCP2515 i magistrali CAN),
użyj szkicu `arduino/oled_test/oled_test.ino`. Program:

- próbuje zainicjalizować SSD1306 kolejno pod adresami 0x3C i 0x3D oraz wypisuje
  wynik w monitorze szeregowym (115200 b/s),
- co kilka sekund zmienia wzorce testowe (tekst, szachownica, koncentryczne
  ramki, przesuwający się napis),
- ponawia próby inicjalizacji co 2 sekundy, więc ekran wstanie nawet wtedy, gdy
  moduł startuje z opóźnieniem.

Wgraj szkic do Arduino z podłączonym wyświetlaczem (SDA=A4, SCL=A5, VCC=5V lub
3,3V – zależnie od modułu). Jeśli ekran działa prawidłowo, powinieneś zobaczyć
rotujące wzorce; w przeciwnym razie monitor szeregowy pomoże ustalić, czy błąd
dotyczy zasilania bądź linii I²C.

Program:

- Inicjuje magistralę CAN z prędkością 500 kb/s (domyślnie).
- Wyświetla na ekranie do sześciu ramek CAN (ID oraz dane w HEX); wiersze są
  przypisywane do identyfikatorów, więc nowe dane tej samej ramki aktualizują
  istniejącą pozycję.
- Udostępnia przez port szeregowy komendę `B <prędkość>` do zmiany prędkości
  CAN. Wartość należy podać w bitach na sekundę (np. 100000, 125000, 200000,
  225000, 500000, 800000). Szkic oblicza parametry czasowe dla rezonatora MCP2515
  (domyślnie 8 MHz) i odsyła na UART komunikat `OK <ustawiona_prędkość>`, aby
  poinformować o faktycznie zaprogramowanej wartości. Gdy żądana prędkość nie
  może być ustawiona z dokładnością poniżej 5%, szkic odpowiada `ERR Unsupported
  speed`.
- Obsługuje komendę `T <id> <dane>` pozwalającą nadać ramkę CAN. `id` należy
  podać w postaci szesnastkowej (11‑bitowe ID mają 3 cyfry, 29‑bitowe 8 cyfr).
  Pole danych można wpisać jako ciąg bajtów HEX (np. `112233` lub `11 22 33`),
  natomiast zdalną ramkę (RTR) wysyła się jako `RTR <dlc>` – np. `T 123 RTR 8`.
  Po poprawnym nadaniu szkic odpowiada `OK TX …` i aktualizuje zarówno wyświetlacz,
  jak i strumień `FRAME`, tak aby aplikacje PC od razu zobaczyły wysłaną ramkę.

> Przykład: dla żądania `B 225000` układ ustawia 222222 b/s – wartość mieści się
> w tolerancji i zostanie zwrócona jako `OK 222222`. Aplikacje PC pokażą zarówno
> żądanie, jak i odpowiedź urządzenia.

> **Uwaga (kompilacja w Arduino IDE):** błąd `invalid preprocessing directive #If`
> oznacza, że w pliku szkicu znalazła się linia rozpoczynająca się znakiem `#`
> z tekstem, którego preprocesor C++ nie rozpoznaje. Najczęściej dzieje się tak,
> gdy do `.ino` przypadkowo trafiły fragmenty skryptów w Pythonie (np. linie
> zaczynające się od `#`). Usuń takie linie lub ponownie skopiuj szkic z pliku
> `arduino/can_display/can_display.ino` z repozytorium.

## Narzędzia PC do zmiany prędkości CAN

Do obsługi komendy `B <prędkość>` dostępne są dwie aplikacje korzystające z
pakietu `pyserial` (`pip install pyserial`).

### Interfejs graficzny (Windows)

Skrypt `pc_tools/can_speed_gui.py` otwiera okno z listą gotowych wartości
prędkości (100, 125, 200, 225, 500, 800 kb/s) oraz opcją „Niestandardowa”, w
której można wpisać własną liczbę bitów na sekundę. W polu niestandardowym
można używać również sufiksów `k` lub `M` (np. `250k`, `0.5M`) – program
automatycznie przeliczy takie wartości na b/s. Jeśli wpiszesz liczbę mniejszą
lub równą 2000 bez sufiksu (np. `250`), aplikacja potraktuje ją jako kilobity na
sekundę i pokaże w statusie informację, jaką dokładnie wartość wysłano. Aplikacja
automatycznie wyświetla dostępne porty COM i pozwala zmieniać parametry portu
szeregowego (baud, timeout).

Uruchomienie:

```bash
python pc_tools/can_speed_gui.py
```

Po wybraniu portu i prędkości kliknij „Ustaw prędkość”, aby wysłać polecenie do
Arduino. Status operacji wyświetli się w dolnej części okna – jeżeli urządzenie
zwróci inną prędkość niż żądana (np. 222222 zamiast 225000 b/s), komunikat
uwzględni obie wartości. Po otwarciu portu program automatycznie odczeka krótką
chwilę (ok. 2 s), aby Arduino, które zwykle resetuje się przy zestawianiu
połączenia, zdążyło ponownie uruchomić szkic i odebrać komendę. Domyślny
timeout odpowiedzi wynosi 5 s – aplikacja pilnuje, aby wartość nie spadła
poniżej tej granicy, dzięki czemu Arduino ma czas na zakończenie zmian
konfiguracji i odesłanie odpowiedzi.

Obok przycisku „Odśwież” znajduje się druga kolumna z panelem „Ramki CAN”.
Pierwszy wiersz panelu zawsze pokazuje bieżącą prędkość (oraz wartość żądaną,
jeżeli różni się od ustawionej), a poniżej widoczna jest lista nawet 30 ramek.
Lista ma własny pasek przewijania, więc w razie potrzeby można przejrzeć
dłuższą historię. Każda pozycja przyjmuje postać `ID DANE  ASCII`, gdzie `DANE`
to ciąg bajtów w HEX dokładnie taki sam jak na OLED‑zie, a część `ASCII` zawiera
znaki odpowiadające tym bajtom (litery i cyfry są wyświetlane wprost, pozostałe
zamieniane są na kropki). Ramki są przypisywane do wierszy na podstawie
identyfikatora CAN – aktualizacja danych tej samej ramki zastępuje zawartość
dotychczasowego wiersza. Gdy pojawi się nowy identyfikator i lista jest pełna,
zostanie zastąpiona ostatnia pozycja.

Sekcja „Wyślij ramkę CAN” w lewej kolumnie pozwala nadać ramkę bez otwierania
dodatkowych okien. W polu „ID (HEX)” wpisz identyfikator szesnastkowy – jeżeli
wartość przekroczy 0x7FF, pole „29 bit (extended)” zostanie ustawione
automatycznie. Obszar „Dane (HEX)” przyjmuje zarówno ciąg bez spacji
(`0A0B0C`), jak i pary oddzielone spacjami lub przecinkami (`0A 0B 0C`). Opcja
„Ramka zdalna (RTR)” odblokowuje pole długości, aby określić oczekiwaną liczbę
bajtów. Po kliknięciu „Wyślij ramkę” program nadaje komendę `T …`, czeka na
`OK TX …` lub `ERR …` i prezentuje wynik w pasku statusu. Wysłane ramki od razu
pojawiają się w panelu historii, dzięki czemu lista w aplikacji Windows
odzwierciedla to, co widać na OLED‑zie.

> **Rozwiązywanie problemów (Windows):** jeśli podczas uruchamiania pojawi się
> błąd `ModuleNotFoundError: No module named 'tkinter'`, oznacza to, że bieżąca
> instalacja Pythona nie zawiera komponentu Tcl/Tk. Pobierz instalator Pythona z
> [python.org](https://www.python.org/downloads/windows/) i podczas instalacji
> upewnij się, że zaznaczona jest pozycja „tcl/tk and IDLE”. W przypadku wersji
> Pythona ze Sklepu Microsoft otwórz „Apps & Features” → „Advanced options” przy
> Pythonie i wybierz „Repair”; jeśli to nie pomoże, przeinstaluj Pythona z
> python.org. Instalacja pakietu `pip install tk` **nie** dodaje modułu `_tkinter`.

#### Budowa pliku EXE dla aplikacji graficznej

Najpierw upewnij się, że PyInstaller jest zainstalowany:

```bash
python -m pip install pyinstaller
```

Następnie zbuduj plik EXE poleceniem:

```bash
pyinstaller --onefile --windowed --name can_speed_gui pc_tools/can_speed_gui.py
```

Powstały plik (`dist/can_speed_gui.exe`) można uruchomić dwuklikiem w Windows.

### Narzędzie wiersza poleceń

Skrypt `pc_tools/can_speed_cli.py` wysyła do Arduino polecenie zmiany prędkości
CAN przez port szeregowy. Parametr `--speed` przyjmuje zarówno liczby w bitach na
sekundę (np. `225000`), jak i wartości z sufiksami `k`/`M` (`250k`, `0.5M`).
Wartości całkowite do 2000 bez sufiksu są traktowane jako kilobity na sekundę i
narzędzie wypisze w konsoli, jaką dokładną prędkość wysłano.

Uruchomienie:

```bash
python pc_tools/can_speed_cli.py --port /dev/ttyUSB0 --speed 225000
```

W razie potrzeby ustaw parametry `--baud` (prędkość UART, domyślnie 115200) i
`--timeout` (czas oczekiwania na odpowiedź w sekundach, domyślnie 5 – narzędzie
nie pozwoli zejść poniżej tej wartości, aby Arduino zdążyło odpowiedzieć po
ewentualnym restarcie).

Skrypt również odczekuje po otwarciu portu, aby uniknąć sytuacji, w której
Arduino zresetowane automatycznie nie zdąży odpowiedzieć i narzędzie zgłasza
komunikat „No response received from device”. Po otrzymaniu odpowiedzi `OK` CLI
wyświetla zarówno żądaną prędkość, jak i prędkość zgłoszoną przez urządzenie –
wraz z informacją o ewentualnym przeliczeniu wartości `--speed`.

#### Budowa pliku wykonywalnego dla CLI

Jeżeli PyInstaller nie jest jeszcze dostępny, zainstaluj go poleceniem:

```bash
python -m pip install pyinstaller
```

Budowę pliku wykonaj komendą:

```bash
pyinstaller --onefile --name can_speed_cli pc_tools/can_speed_cli.py
```

Gotowy plik znajdziesz w katalogu `dist/` (np. `dist/can_speed_cli.exe` na
Windows lub `dist/can_speed_cli` na Linux). Aby uruchomić program, wykonaj go w
linii poleceń, przekazując te same opcje co w przypadku wersji `python`:

```bash
dist/can_speed_cli --port COM3 --speed 500000
```

> Uwaga: na Windowsie może być konieczne uruchomienie terminala z uprawnieniami
> dostępu do portu szeregowego oraz wskazanie właściwego portu (np. `COM3`).
