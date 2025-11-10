#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <string.h>
#include <mcp2515.h>

// Pin configuration
constexpr uint8_t OLED_RESET_PIN = -1;      // SSD1306 reset pin (shared reset not used)
constexpr uint8_t MCP2515_CS_PIN = 10;      // Chip select pin for MCP2515
constexpr uint8_t MCP2515_INT_PIN = 2;      // Interrupt pin (not used in this sketch but reserved)

// Display configuration
constexpr uint8_t SCREEN_WIDTH = 128;
constexpr uint8_t SCREEN_HEIGHT = 64;
constexpr uint8_t OLED_I2C_ADDRESS = 0x3C;  // Most SSD1306 modules use 0x3C

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET_PIN);
MCP2515 mcp2515(MCP2515_CS_PIN);

struct FrameEntry {
  can_frame frame;
  unsigned long timestamp;
};

constexpr size_t FRAME_HISTORY_SIZE = 4;
FrameEntry frameHistory[FRAME_HISTORY_SIZE];

can_speed_t currentSpeed = CAN_500KBPS;
constexpr can_clock_t CAN_CLOCK = MCP_8MHZ;

bool applyCanSpeed(can_speed_t speed) {
  if (mcp2515.reset() != MCP2515::ERROR_OK) {
    return false;
  }

  if (mcp2515.setBitrate(speed, CAN_CLOCK) != MCP2515::ERROR_OK) {
    return false;
  }

  if (mcp2515.setNormalMode() != MCP2515::ERROR_OK) {
    return false;
  }

  currentSpeed = speed;
  return true;
}

const __FlashStringHelper* speedLabel(can_speed_t speed) {
  switch (speed) {
    case CAN_125KBPS:
      return F("125 kbps");
    case CAN_250KBPS:
      return F("250 kbps");
    case CAN_500KBPS:
      return F("500 kbps");
    case CAN_1000KBPS:
      return F("1000 kbps");
    default:
      return F("custom");
  }
}

void drawHeader(const __FlashStringHelper* status) {
  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.print(F("CAN: "));
  display.println(speedLabel(currentSpeed));
  display.println(status);
}

void showStatus(const __FlashStringHelper* status) {
  drawHeader(status);
  display.display();
}

void updateDisplay() {
  drawHeader(F("Frames:"));

  for (size_t i = 0; i < FRAME_HISTORY_SIZE; ++i) {
    const FrameEntry &entry = frameHistory[i];
    if (entry.timestamp == 0) {
      continue;
    }

    display.print(F("ID:"));
    if (entry.frame.can_id & CAN_EFF_FLAG) {
      display.print(F("X"));
      display.print(entry.frame.can_id & CAN_EFF_MASK, HEX);
    } else {
      display.print(entry.frame.can_id & CAN_SFF_MASK, HEX);
    }

    display.print(F(" D"));
    display.print(entry.frame.can_dlc, DEC);
    display.print(F(" "));

    for (uint8_t i = 0; i < entry.frame.can_dlc; ++i) {
      if (entry.frame.data[i] < 0x10) {
        display.print('0');
      }
      display.print(entry.frame.data[i], HEX);
      display.print(' ');
    }

    display.println();
  }

  display.display();
}

bool parseSpeedCommand(const String &value, can_speed_t &outSpeed) {
  if (value == F("125000")) {
    outSpeed = CAN_125KBPS;
    return true;
  }
  if (value == F("250000")) {
    outSpeed = CAN_250KBPS;
    return true;
  }
  if (value == F("500000")) {
    outSpeed = CAN_500KBPS;
    return true;
  }
  if (value == F("1000000")) {
    outSpeed = CAN_1000KBPS;
    return true;
  }
  return false;
}

void processSerialCommand(const String &line) {
  if (line.length() < 3) {
    Serial.println(F("ERR Unknown command"));
    return;
  }

  if (line.charAt(0) == 'B' && line.charAt(1) == ' ') {
    String speedValue = line.substring(2);
    speedValue.trim();

    can_speed_t newSpeed;
    if (!parseSpeedCommand(speedValue, newSpeed)) {
      Serial.println(F("ERR Unsupported speed"));
      showStatus(F("Unsupported speed"));
      return;
    }

    if (applyCanSpeed(newSpeed)) {
      Serial.println(F("OK"));
      showStatus(F("Speed updated"));
    } else {
      Serial.println(F("ERR CAN init"));
      showStatus(F("CAN init failed"));
    }
    return;
  }

  Serial.println(F("ERR Unknown command"));
}

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    ;
  }

  Wire.begin();

  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS)) {
    for (;;) {
      delay(1000);
    }
  }

  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println(F("Starting..."));
  display.display();

  SPI.begin();

  if (!applyCanSpeed(currentSpeed)) {
    showStatus(F("CAN init failed"));
  } else {
    showStatus(F("Listening"));
  }

  memset(frameHistory, 0, sizeof(frameHistory));
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      processSerialCommand(line);
    }
  }

  struct can_frame frame;
  if (mcp2515.readMessage(&frame) == MCP2515::ERROR_OK) {
    for (size_t i = FRAME_HISTORY_SIZE - 1; i > 0; --i) {
      frameHistory[i] = frameHistory[i - 1];
    }
    frameHistory[0].frame = frame;
    frameHistory[0].timestamp = millis();
    updateDisplay();
  }
}
