#include <Arduino.h>
#include <SoftwareSerial.h>
#include <TinyGPS++.h>
#include <TFT_eSPI.h>

// GPS pins: RX=GPIO4 (D2), TX=GPIO5 (D1)
static const uint8_t GPS_RX_PIN = 4;
static const uint8_t GPS_TX_PIN = 5;

SoftwareSerial gpsSerial(GPS_RX_PIN, GPS_TX_PIN);
TinyGPSPlus gps;
TFT_eSPI tft = TFT_eSPI();

unsigned long lastUpdate = 0;

// Determine if Central European Summer Time is in effect for the given date
bool isCEST(uint16_t year, uint8_t month, uint8_t day) {
  // CEST starts last Sunday of March, ends last Sunday of October
  auto lastSunday = [](uint16_t y, uint8_t m) {
    uint8_t daysInMonth[] = {0,31,28,31,30,31,30,31,31,30,31,30,31};
    bool leap = (y % 4 == 0 && y % 100 != 0) || (y % 400 == 0);
    if (m == 2 && leap) daysInMonth[2] = 29;
    uint8_t dom = daysInMonth[m];
    // Zeller's congruence for weekday: 0=Saturday
    int q = dom;
    int mm = (m < 3) ? m + 12 : m;
    int Y = (m < 3) ? y - 1 : y;
    int K = Y % 100;
    int J = Y / 100;
    int h = (q + (13*(mm + 1))/5 + K + K/4 + J/4 + 5*J) % 7;
    // Convert to 0=Sunday
    int d = (h + 6) % 7;
    return dom - d; // date of last Sunday
  };

  uint8_t marchSunday = lastSunday(year, 3);
  uint8_t octoberSunday = lastSunday(year, 10);

  if (month < 3 || month > 10) return false;
  if (month > 3 && month < 10) return true;
  if (month == 3) return day >= marchSunday;
  return day < octoberSunday;
}

String formatTime(TinyGPSTime &time, TinyGPSDate &date) {
  if (!time.isValid() || !date.isValid()) return "--:--:--";

  int hour = time.hour();
  int minute = time.minute();
  int second = time.second();

  int tzOffset = isCEST(date.year(), date.month(), date.day()) ? 2 : 1;
  hour = (hour + tzOffset) % 24;

  char buffer[9];
  snprintf(buffer, sizeof(buffer), "%02d:%02d:%02d", hour, minute, second);
  return String(buffer);
}

void updateDisplay() {
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);

  // Satellites
  tft.setCursor(0, 0);
  tft.print("Sat: ");
  if (gps.satellites.isValid()) {
    tft.println(gps.satellites.value());
  } else {
    tft.println("--");
  }

  // Time (Warsaw)
  tft.setCursor(0, 30);
  tft.print("Czas: ");
  tft.println(formatTime(gps.time, gps.date));

  // Speed in km/h without trailing decimals
  tft.setCursor(0, 60);
  tft.print("V: ");
  if (gps.speed.isValid()) {
    int speedKmh = static_cast<int>(gps.speed.kmph() + 0.5); // round to nearest integer
    tft.print(speedKmh);
    tft.println(" km/h");
  } else {
    tft.println("-- km/h");
  }
}

void setup() {
  Serial.begin(115200);
  gpsSerial.begin(9600);

  tft.init();
  tft.setRotation(1);
  tft.fillScreen(TFT_BLACK);
  tft.setTextColor(TFT_WHITE, TFT_BLACK);
  tft.setTextSize(2);
  tft.println("Oczekiwanie na GPS...");
}

void loop() {
  while (gpsSerial.available()) {
    gps.encode(gpsSerial.read());
  }

  unsigned long now = millis();
  if (now - lastUpdate >= 1000) {
    updateDisplay();
    lastUpdate = now;
  }
}
