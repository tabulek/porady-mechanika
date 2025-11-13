#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>

constexpr uint8_t SCREEN_WIDTH = 128;
constexpr uint8_t SCREEN_HEIGHT = 64;
constexpr int8_t OLED_RESET_PIN = -1;

constexpr uint8_t PRIMARY_ADDRESS = 0x3C;
constexpr uint8_t SECONDARY_ADDRESS = 0x3D;
constexpr uint8_t ADDRESS_COUNT = 2;
const uint8_t POSSIBLE_ADDRESSES[ADDRESS_COUNT] = {PRIMARY_ADDRESS, SECONDARY_ADDRESS};

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET_PIN);

uint8_t activeAddress = PRIMARY_ADDRESS;
bool displayReady = false;
unsigned long lastInitAttempt = 0;
constexpr unsigned long INIT_RETRY_INTERVAL_MS = 2000UL;

unsigned long lastPatternChange = 0;
constexpr unsigned long PATTERN_INTERVAL_MS = 2000UL;
uint8_t currentPattern = 0;
constexpr uint8_t PATTERN_COUNT = 4;

void drawTextPattern();
void drawCheckerPattern();
void drawRectanglePattern();
void drawScrollPattern();

bool tryInitializeDisplay() {
  for (uint8_t i = 0; i < ADDRESS_COUNT; ++i) {
    uint8_t addr = POSSIBLE_ADDRESSES[i];
    if (display.begin(SSD1306_SWITCHCAPVCC, addr)) {
      activeAddress = addr;
      Serial.print(F("SSD1306 ready at 0x"));
      if (addr < 16) {
        Serial.print('0');
      }
      Serial.println(addr, HEX);
      display.clearDisplay();
      display.display();
      return true;
    }
  }
  Serial.println(F("Nie udało się zainicjalizować SSD1306 (adresy 0x3C/0x3D)."));
  return false;
}

void ensureDisplayReady() {
  if (displayReady) {
    return;
  }
  unsigned long now = millis();
  if (now - lastInitAttempt >= INIT_RETRY_INTERVAL_MS) {
    lastInitAttempt = now;
    displayReady = tryInitializeDisplay();
  }
}

void setup() {
  Serial.begin(115200);
  Serial.println(F("Test wyświetlacza SSD1306"));
  Serial.println(F("Próbuję wykryć moduł..."));

  ensureDisplayReady();
  if (!displayReady) {
    Serial.println(F("Podłącz SDA do A4, SCL do A5 i upewnij się, że moduł jest zasilony."));
  }
}

void loop() {
  ensureDisplayReady();
  if (!displayReady) {
    return;
  }

  unsigned long now = millis();
  if (now - lastPatternChange >= PATTERN_INTERVAL_MS) {
    lastPatternChange = now;
    currentPattern = (currentPattern + 1) % PATTERN_COUNT;
  }

  display.clearDisplay();
  switch (currentPattern) {
    case 0:
      drawTextPattern();
      break;
    case 1:
      drawCheckerPattern();
      break;
    case 2:
      drawRectanglePattern();
      break;
    default:
      drawScrollPattern();
      break;
  }
  display.display();
}

void drawTextPattern() {
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(F("SSD1306 TEST"));
  display.print(F("I2C 0x"));
  if (activeAddress < 16) {
    display.print('0');
  }
  display.println(activeAddress, HEX);
  display.println(F("1: tekst"));
  display.println(F("2: szachownica"));
  display.println(F("3: ramki"));
  display.println(F("4: przesuw"));
}

void drawCheckerPattern() {
  for (uint8_t y = 0; y < SCREEN_HEIGHT; ++y) {
    for (uint8_t x = 0; x < SCREEN_WIDTH; ++x) {
      bool bit = ((x / 8) + (y / 8)) % 2 == 0;
      if (bit) {
        display.drawPixel(x, y, SSD1306_WHITE);
      }
    }
  }
}

void drawRectanglePattern() {
  for (uint8_t i = 0; i < 6; ++i) {
    int16_t inset = i * 4;
    display.drawRect(inset, inset, SCREEN_WIDTH - inset * 2, SCREEN_HEIGHT - inset * 2, SSD1306_WHITE);
  }
}

void drawScrollPattern() {
  static int16_t offset = 0;
  offset = (offset + 2) % SCREEN_WIDTH;

  for (int16_t y = 0; y < SCREEN_HEIGHT; y += 10) {
    display.drawFastHLine(0, y, SCREEN_WIDTH, SSD1306_WHITE);
  }
  for (int16_t x = -SCREEN_WIDTH; x < SCREEN_WIDTH; x += 16) {
    display.setCursor(x + offset, 2);
    display.setTextSize(1);
    display.setTextColor(SSD1306_WHITE);
    display.print(F("OLED"));
  }
}
