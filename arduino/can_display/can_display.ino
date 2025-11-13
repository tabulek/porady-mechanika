#include <SPI.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <Print.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>
#include <mcp2515.h>

#ifndef MCP_CNF1
#define MCP_CNF1 0x2A
#endif

#ifndef MCP_CNF2
#define MCP_CNF2 0x29
#endif

#ifndef MCP_CNF3
#define MCP_CNF3 0x28
#endif

#ifndef CAN_ERR_FLAG
#define CAN_ERR_FLAG 0x20000000UL
#endif

// Pin configuration
constexpr int8_t OLED_RESET_PIN = -1;       // SSD1306 reset pin (shared reset not used)
constexpr uint8_t MCP2515_CS_PIN = 10;      // Chip select pin for MCP2515
constexpr uint8_t MCP2515_INT_PIN = 2;      // Interrupt pin (not used in this sketch but reserved)

// Display configuration
constexpr uint8_t SCREEN_WIDTH = 128;
constexpr uint8_t SCREEN_HEIGHT = 64;
constexpr uint8_t OLED_I2C_ADDRESS = 0x3C;

uint8_t detectedDisplayAddress = OLED_I2C_ADDRESS;

Adafruit_SSD1306 display(SCREEN_WIDTH, SCREEN_HEIGHT, &Wire, OLED_RESET_PIN);
MCP2515 mcp2515(MCP2515_CS_PIN);

constexpr uint8_t MCP2515_INSTRUCTION_WRITE = 0x02;
const SPISettings MCP2515_SPI_SETTINGS(8000000, MSBFIRST, SPI_MODE0);

constexpr unsigned long STATUS_DISPLAY_DURATION_MS = 1500UL;
constexpr unsigned long DISPLAY_INIT_RETRY_INTERVAL_MS = 2000UL;
constexpr unsigned long STARTUP_BANNER_DURATION_MS = 3000UL;

bool displayReady = false;
bool startupBannerShown = false;
unsigned long lastDisplayInitAttempt = 0;

struct FrameEntry {
  can_frame frame;
  unsigned long timestamp;
};

constexpr size_t FRAME_HISTORY_SIZE = 6;
FrameEntry frameHistory[FRAME_HISTORY_SIZE];
size_t frameHistoryCount = 0;

constexpr uint8_t CAN_TX_MAX_ATTEMPTS = 5;
constexpr unsigned long CAN_TX_RETRY_DELAY_MS = 10UL;

using CanClock = decltype(MCP_8MHZ);

constexpr CanClock CAN_CLOCK = MCP_8MHZ;

void emitSpeedStatus();
void emitFrameUpdate(size_t slot);
void emitFrameReset();
void updateDisplay();
void clearFrameHistory(bool notifySerial);
bool ensureDisplayReady();
bool tryInitializeDisplay();
void showStartupBanner();
void onDisplayReady();

struct BitTimingConfig {
  uint8_t cnf1;
  uint8_t cnf2;
  uint8_t cnf3;
  uint32_t actualBps;
  uint16_t samplePointPermille;
  bool tripleSampling;
};

struct SpeedStatus {
  uint32_t requestedBps;
  uint32_t appliedBps;
  uint16_t samplePointPermille;
  bool tripleSampling;
};

enum BitrateApplyResult {
  BitrateApplyResult_Success = 0,
  BitrateApplyResult_Unsupported,
  BitrateApplyResult_HardwareError,
};

SpeedStatus currentSpeed = {500000UL, 500000UL, 800U, false};

constexpr uint32_t clockToHz(CanClock clock) {
  return (clock == MCP_16MHZ)
             ? 16000000UL
             : ((clock == MCP_20MHZ) ? 20000000UL : 8000000UL);
}

constexpr uint32_t CAN_CLOCK_HZ = clockToHz(CAN_CLOCK);

void printBitrate(Print &out, uint32_t bps) {
  if (bps >= 1000 && (bps % 1000) == 0) {
    out.print(bps / 1000);
    out.print(F(" kbps"));
  } else {
    out.print(bps);
    out.print(F(" bps"));
  }
}

void printPercent(Print &out, uint16_t permille) {
  uint16_t whole = permille / 10;
  uint16_t tenths = permille % 10;
  out.print(whole);
  out.print('.');
  out.print(tenths);
  out.print('%');
}

bool computeBitTiming(uint32_t targetBps, BitTimingConfig &config) {
  if (targetBps == 0 || targetBps > 1000000UL) {
    return false;
  }

  bool found = false;
  uint32_t bestError = 0xFFFFFFFFUL;
  uint16_t bestSampleDiff = 0xFFFF;
  uint8_t bestBrp = 63;
  const uint16_t desiredSamplePoint = 800;  // 80%

  for (uint8_t brp = 0; brp < 64; ++brp) {
    const uint32_t denominatorBase = 2UL * (static_cast<uint32_t>(brp) + 1UL);

    for (uint8_t propSeg = 1; propSeg <= 8; ++propSeg) {
      for (uint8_t phaseSeg1 = 1; phaseSeg1 <= 8; ++phaseSeg1) {
        for (uint8_t phaseSeg2 = 2; phaseSeg2 <= 8; ++phaseSeg2) {
          const uint8_t totalSegments = 1 + propSeg + phaseSeg1 + phaseSeg2;
          if (totalSegments < 5 || totalSegments > 25) {
            continue;
          }

          const uint32_t denominator = denominatorBase * totalSegments;
          if (denominator == 0) {
            continue;
          }

          const uint32_t actualBps = (CAN_CLOCK_HZ + (denominator / 2UL)) / denominator;
          if (actualBps == 0) {
            continue;
          }

          const uint32_t error = (actualBps > targetBps) ? (actualBps - targetBps) : (targetBps - actualBps);
          const uint16_t samplePoint = static_cast<uint16_t>((1000UL * (1UL + propSeg + phaseSeg1)) / totalSegments);
          const uint16_t sampleDiff = (samplePoint > desiredSamplePoint)
                                           ? static_cast<uint16_t>(samplePoint - desiredSamplePoint)
                                           : static_cast<uint16_t>(desiredSamplePoint - samplePoint);

          bool takeCandidate = false;
          if (!found || error < bestError) {
            takeCandidate = true;
          } else if (error == bestError) {
            if (sampleDiff < bestSampleDiff) {
              takeCandidate = true;
            } else if (sampleDiff == bestSampleDiff && brp < bestBrp) {
              takeCandidate = true;
            }
          }

          if (takeCandidate) {
            const uint8_t sjw = (phaseSeg2 > 4) ? 4 : phaseSeg2;
            bool tripleSampling = (actualBps <= 125000UL) && (phaseSeg1 >= 2);

            config.cnf1 = static_cast<uint8_t>(((sjw - 1) << 6) | brp);
            config.cnf2 = static_cast<uint8_t>(0x80 | (((phaseSeg1 - 1) & 0x07) << 3) | ((propSeg - 1) & 0x07));
            if (tripleSampling) {
              config.cnf2 |= 0x40;
            }
            config.cnf3 = static_cast<uint8_t>((phaseSeg2 - 1) & 0x07);
            config.actualBps = actualBps;
            config.samplePointPermille = samplePoint;
            config.tripleSampling = tripleSampling;

            bestError = error;
            bestSampleDiff = sampleDiff;
            bestBrp = brp;
            found = true;
          }
        }
      }
    }
  }

  if (!found) {
    return false;
  }

  if ((bestError * 100UL) > (targetBps * 5UL)) {  // >5% error
    return false;
  }

  return true;
}

void writeRegisterRaw(uint8_t address, uint8_t value) {
  SPI.beginTransaction(MCP2515_SPI_SETTINGS);
  digitalWrite(MCP2515_CS_PIN, LOW);
  SPI.transfer(MCP2515_INSTRUCTION_WRITE);
  SPI.transfer(address);
  SPI.transfer(value);
  digitalWrite(MCP2515_CS_PIN, HIGH);
  SPI.endTransaction();
}

bool transmitFrameWithRetry(can_frame &frame) {
  mcp2515.setNormalMode();
  for (uint8_t attempt = 0; attempt < CAN_TX_MAX_ATTEMPTS; ++attempt) {
    if (mcp2515.sendMessage(&frame) == MCP2515::ERROR_OK) {
      return true;
    }

    if (attempt + 1 < CAN_TX_MAX_ATTEMPTS) {
      delay(CAN_TX_RETRY_DELAY_MS);
      mcp2515.setNormalMode();
    }
  }

  return false;
}

bool programCanBitrate(uint32_t requestedBps, const BitTimingConfig &config) {
  if (mcp2515.reset() != MCP2515::ERROR_OK) {
    return false;
  }

  if (mcp2515.setConfigMode() != MCP2515::ERROR_OK) {
    return false;
  }

  writeRegisterRaw(MCP_CNF1, config.cnf1);
  writeRegisterRaw(MCP_CNF2, config.cnf2);
  writeRegisterRaw(MCP_CNF3, config.cnf3);

  if (mcp2515.setNormalMode() != MCP2515::ERROR_OK) {
    return false;
  }

  currentSpeed.requestedBps = requestedBps;
  currentSpeed.appliedBps = config.actualBps;
  currentSpeed.samplePointPermille = config.samplePointPermille;
  currentSpeed.tripleSampling = config.tripleSampling;
  emitSpeedStatus();
  clearFrameHistory(true);
  updateDisplay();
  return true;
}

BitrateApplyResult configureCanBitrate(uint32_t requestedBps) {
  BitTimingConfig config;
  if (!computeBitTiming(requestedBps, config)) {
    return BitrateApplyResult_Unsupported;
  }

  if (!programCanBitrate(requestedBps, config)) {
    return BitrateApplyResult_HardwareError;
  }

  return BitrateApplyResult_Success;
}

bool initializeDisplay() {
  if (!display.begin(SSD1306_SWITCHCAPVCC, OLED_I2C_ADDRESS)) {
    return false;
  }

  detectedDisplayAddress = OLED_I2C_ADDRESS;
  displayReady = true;
  display.clearDisplay();
  display.display();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.cp437(true);
  display.setTextWrap(false);

  if (Serial) {
    Serial.print(F("SSD1306 ready at 0x"));
    if (OLED_I2C_ADDRESS < 16) {
      Serial.print('0');
    }
    Serial.println(OLED_I2C_ADDRESS, HEX);
  }

  return true;
}

bool ensureDisplayReady() {
  if (displayReady) {
    return true;
  }

  unsigned long now = millis();
  if (lastDisplayInitAttempt == 0 || now - lastDisplayInitAttempt >= DISPLAY_INIT_RETRY_INTERVAL_MS) {
    lastDisplayInitAttempt = now;
    if (initializeDisplay()) {
      onDisplayReady();
      return true;
    }
    displayReady = false;
    if (Serial) {
      Serial.println(F("SSD1306 init failed on 0x3C"));
    }
  }

  return false;
}

void showStartupBanner() {
  if (!displayReady) {
    return;
  }

  display.clearDisplay();
  display.setTextSize(2);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(10, 16);
  display.println(F("CAN"));
  display.println(F("HACK"));
  display.display();
  delay(STARTUP_BANNER_DURATION_MS);
}

void onDisplayReady() {
  if (!startupBannerShown) {
    showStartupBanner();
    startupBannerShown = true;
  }
  updateDisplay();
}

void showStatus(const __FlashStringHelper* status) {
  if (!displayReady) {
    return;
  }

  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  if (status != nullptr) {
    display.println(status);
  }
  display.display();
  delay(STATUS_DISPLAY_DURATION_MS);
  updateDisplay();
}

void printNibble(Print &out, uint8_t value) {
  out.print(static_cast<uint8_t>(value & 0x0F), HEX);
}

void printByteHex(Print &out, uint8_t value) {
  uint8_t high = (value >> 4) & 0x0F;
  uint8_t low = value & 0x0F;
  printNibble(out, high);
  printNibble(out, low);
}

void printFrameId(Print &out, const can_frame &frame) {
  if (frame.can_id & CAN_EFF_FLAG) {
    uint32_t id = frame.can_id & CAN_EFF_MASK;
    for (int8_t shift = 28; shift >= 0; shift -= 4) {
      printNibble(out, static_cast<uint8_t>((id >> shift) & 0x0F));
    }
  } else {
    uint16_t id = static_cast<uint16_t>(frame.can_id & CAN_SFF_MASK);
    for (int8_t shift = 8; shift >= 0; shift -= 4) {
      printNibble(out, static_cast<uint8_t>((id >> shift) & 0x0F));
    }
  }
}

void printFrameLine(const can_frame &frame, Print &out) {
  printFrameId(out, frame);
  out.print(' ');

  if (frame.can_id & CAN_RTR_FLAG) {
    out.print(F("RTR"));
    if (frame.can_dlc > 0) {
      out.print(' ');
      out.print(static_cast<unsigned long>(frame.can_dlc));
    }
    return;
  }

  const uint8_t length = (frame.can_dlc > 8) ? 8 : frame.can_dlc;
  for (uint8_t i = 0; i < length; ++i) {
    printByteHex(out, frame.data[i]);
  }
}

void emitSpeedStatus() {
  Serial.print(F("SPEED "));
  Serial.print(currentSpeed.appliedBps);
  Serial.print(' ');
  Serial.println(currentSpeed.requestedBps);
}

void emitFrameUpdate(size_t slot) {
  if (slot >= FRAME_HISTORY_SIZE) {
    return;
  }

  Serial.print(F("FRAME "));
  Serial.print(static_cast<unsigned long>(slot));
  Serial.print(' ');
  printFrameLine(frameHistory[slot].frame, Serial);
  Serial.println();
}

void emitFrameReset() {
  Serial.println(F("FRAME_RESET"));
}

void clearFrameHistory(bool notifySerial) {
  for (size_t i = 0; i < FRAME_HISTORY_SIZE; ++i) {
    frameHistory[i].frame.can_id = 0;
    frameHistory[i].frame.can_dlc = 0;
    memset(frameHistory[i].frame.data, 0, sizeof(frameHistory[i].frame.data));
    frameHistory[i].timestamp = 0;
  }

  frameHistoryCount = 0;

  if (notifySerial) {
    emitFrameReset();
  }
}

bool framesShareId(const can_frame &lhs, const can_frame &rhs) {
  const bool lhsError = (lhs.can_id & CAN_ERR_FLAG) != 0;
  const bool rhsError = (rhs.can_id & CAN_ERR_FLAG) != 0;
  if (lhsError || rhsError) {
    return lhsError == rhsError && lhs.can_id == rhs.can_id;
  }

  const bool lhsExtended = (lhs.can_id & CAN_EFF_FLAG) != 0;
  const bool rhsExtended = (rhs.can_id & CAN_EFF_FLAG) != 0;
  if (lhsExtended != rhsExtended) {
    return false;
  }

  if (lhsExtended) {
    return (lhs.can_id & CAN_EFF_MASK) == (rhs.can_id & CAN_EFF_MASK);
  }

  return (lhs.can_id & CAN_SFF_MASK) == (rhs.can_id & CAN_SFF_MASK);
}

void recordFrame(const can_frame &frame) {
  unsigned long now = millis();

  for (size_t i = 0; i < frameHistoryCount; ++i) {
    if (framesShareId(frameHistory[i].frame, frame)) {
      frameHistory[i].frame = frame;
      frameHistory[i].timestamp = now;
      emitFrameUpdate(i);
      return;
    }
  }

  size_t slot;
  if (frameHistoryCount < FRAME_HISTORY_SIZE) {
    slot = frameHistoryCount;
    ++frameHistoryCount;
  } else {
    slot = FRAME_HISTORY_SIZE - 1;
  }

  frameHistory[slot].frame = frame;
  frameHistory[slot].timestamp = now;
  emitFrameUpdate(slot);
}

void updateDisplay() {
  if (!displayReady) {
    return;
  }

  display.clearDisplay();
  display.setCursor(0, 0);
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.print(F("CAN: "));
  printBitrate(display, currentSpeed.appliedBps);
  if (currentSpeed.appliedBps != currentSpeed.requestedBps) {
    display.print(F(" (req "));
    printBitrate(display, currentSpeed.requestedBps);
    display.print(')');
  }
  display.println();
  display.println();

  for (size_t i = 0; i < frameHistoryCount; ++i) {
    const FrameEntry &entry = frameHistory[i];
    if (entry.timestamp == 0) {
      continue;
    }

    printFrameLine(entry.frame, display);
    display.println();
  }

  display.display();
}

bool parseHexValue(const String &text, uint32_t &outValue) {
  if (text.length() == 0) {
    return false;
  }

  char buffer[12];
  text.toCharArray(buffer, sizeof(buffer));

  char *endPtr = nullptr;
  unsigned long value = strtoul(buffer, &endPtr, 16);
  if (endPtr == nullptr || *endPtr != '\0') {
    return false;
  }

  outValue = static_cast<uint32_t>(value);
  return true;
}

bool parseDecimalValue(const String &text, uint8_t &outValue) {
  if (text.length() == 0) {
    return false;
  }

  char buffer[6];
  text.toCharArray(buffer, sizeof(buffer));

  char *endPtr = nullptr;
  unsigned long value = strtoul(buffer, &endPtr, 10);
  if (endPtr == nullptr || *endPtr != '\0' || value > 15UL) {
    return false;
  }

  outValue = static_cast<uint8_t>(value);
  return true;
}

bool appendHexToken(const String &token, uint8_t &byteIndex, can_frame &outFrame) {
  String chunk = token;
  chunk.trim();
  if (chunk.length() == 0) {
    return true;
  }

  if (chunk.startsWith("0x") || chunk.startsWith("0X")) {
    chunk = chunk.substring(2);
  }

  String cleaned;
  cleaned.reserve(chunk.length());
  for (size_t i = 0; i < chunk.length(); ++i) {
    char c = chunk.charAt(i);
    if (c == '_' || c == ':' || c == '.') {
      continue;
    }
    if (!isxdigit(static_cast<unsigned char>(c))) {
      return false;
    }
    cleaned += static_cast<char>(toupper(static_cast<unsigned char>(c)));
  }

  if (cleaned.length() == 0) {
    return false;
  }

  if ((cleaned.length() % 2) != 0) {
    if (cleaned.length() == 1) {
      cleaned = String('0') + cleaned;
    } else {
      return false;
    }
  }

  for (uint16_t i = 0; i < cleaned.length(); i += 2) {
    if (byteIndex >= 8) {
      return false;
    }
    String byteString = cleaned.substring(i, i + 2);
    uint32_t value;
    if (!parseHexValue(byteString, value) || value > 0xFFUL) {
      return false;
    }
    outFrame.data[byteIndex] = static_cast<uint8_t>(value);
    ++byteIndex;
  }

  return true;
}

bool isCommandSeparator(char c) {
  switch (c) {
    case ' ':
    case '\t':
    case '\r':
    case '\n':
    case ',':
    case ';':
    case '-':
    case '/':
    case '#':
    case ':':
    case '.':
    case '=':
    case '|':
      return true;
    default:
      return false;
  }
}

bool parseTransmitCommand(const String &arguments, can_frame &outFrame) {
  String trimmed = arguments;
  trimmed.trim();
  if (trimmed.length() == 0) {
    return false;
  }

  // Guard against pathological inputs that could overflow the local buffer.
  if (trimmed.length() >= 120) {
    return false;
  }

  char buffer[120];
  trimmed.toCharArray(buffer, sizeof(buffer));

  char *cursor = buffer;
  while (*cursor && isCommandSeparator(*cursor)) {
    ++cursor;
  }

  if (*cursor == '\0') {
    return false;
  }

  // Extract identifier token.
  char *idStart = cursor;
  while (*cursor && !isCommandSeparator(*cursor)) {
    ++cursor;
  }
  char saved = *cursor;
  *cursor = '\0';

  String idPart(idStart);
  *cursor = saved;
  if (saved != '\0') {
    ++cursor;
  }

  idPart.trim();
  if (idPart.length() == 0) {
    return false;
  }

  if (idPart.startsWith("0x") || idPart.startsWith("0X")) {
    idPart = idPart.substring(2);
  }

  idPart.toUpperCase();

  uint32_t idValue;
  if (!parseHexValue(idPart, idValue)) {
    return false;
  }

  bool extended = (idValue > CAN_SFF_MASK) || (idPart.length() > 3);
  if (idValue > CAN_EFF_MASK) {
    return false;
  }

  if (extended) {
    outFrame.can_id = (idValue & CAN_EFF_MASK) | CAN_EFF_FLAG;
  } else {
    outFrame.can_id = (idValue & CAN_SFF_MASK);
  }

  bool remoteFrame = false;
  bool expectRemoteLength = false;
  uint8_t byteIndex = 0;

  while (true) {
    while (*cursor && isCommandSeparator(*cursor)) {
      ++cursor;
    }

    if (*cursor == '\0') {
      break;
    }

    char *tokenStart = cursor;
    while (*cursor && !isCommandSeparator(*cursor)) {
      ++cursor;
    }
    saved = *cursor;
    *cursor = '\0';

    String token(tokenStart);
    *cursor = saved;
    if (saved != '\0') {
      ++cursor;
    }

    token.trim();
    if (token.length() == 0) {
      continue;
    }

    if (expectRemoteLength) {
      uint8_t requestedLength;
      if (!parseDecimalValue(token, requestedLength) || requestedLength > 8) {
        return false;
      }
      outFrame.can_dlc = requestedLength;
      expectRemoteLength = false;
      continue;
    }

    String upperToken = token;
    upperToken.toUpperCase();

    if (!remoteFrame && upperToken.startsWith("RTR")) {
      remoteFrame = true;
      upperToken.remove(0, 3);
      upperToken.trim();

      if (upperToken.length() > 0) {
        uint8_t requestedLength;
        if (!parseDecimalValue(upperToken, requestedLength) || requestedLength > 8) {
          return false;
        }
        outFrame.can_dlc = requestedLength;
      } else {
        expectRemoteLength = true;
        outFrame.can_dlc = 0;
      }

      continue;
    }

    if (remoteFrame) {
      // Remote frames cannot contain payload data beyond the RTR length.
      return false;
    }

    if (!appendHexToken(token, byteIndex, outFrame)) {
      return false;
    }
  }

  if (expectRemoteLength) {
    outFrame.can_dlc = 0;
  }

  if (remoteFrame) {
    outFrame.can_id |= CAN_RTR_FLAG;
    return true;
  }

  outFrame.can_dlc = byteIndex;
  return true;
}

bool parseSpeedCommand(const String &value, uint32_t &outSpeed) {
  if (value.length() == 0) {
    return false;
  }

  uint32_t parsed = 0;
  for (size_t i = 0; i < value.length(); ++i) {
    const char c = value.charAt(i);
    if (c < '0' || c > '9') {
      return false;
    }
    parsed = parsed * 10 + static_cast<uint32_t>(c - '0');
    if (parsed > 2000000UL) {  // guard against overflow and clearly invalid speeds
      return false;
    }
  }

  if (parsed == 0) {
    return false;
  }

  outSpeed = parsed;
  return true;
}

void processSerialCommand(const String &line) {
  if (line.length() < 3) {
    Serial.println(F("ERR Unknown command"));
    return;
  }

  if (line.charAt(0) == 'B' && line.charAt(1) == ' ') {
    String speedValue = line.substring(2);
    speedValue.trim();

    uint32_t requestedBps;
    if (!parseSpeedCommand(speedValue, requestedBps)) {
      Serial.println(F("ERR Unsupported speed"));
      showStatus(F("Unsupported speed"));
      return;
    }

    switch (configureCanBitrate(requestedBps)) {
      case BitrateApplyResult_Success:
        Serial.print(F("OK "));
        Serial.println(currentSpeed.appliedBps);
        showStatus(F("Speed updated"));
        break;
      case BitrateApplyResult_Unsupported:
        Serial.println(F("ERR Unsupported speed"));
        showStatus(F("Unsupported speed"));
        break;
      case BitrateApplyResult_HardwareError:
        Serial.println(F("ERR CAN init"));
        showStatus(F("CAN init failed"));
        break;
    }
    return;
  }

  if (line.charAt(0) == 'T' && line.charAt(1) == ' ') {
    String frameArgs = line.substring(2);
    struct can_frame txFrame;
    memset(&txFrame, 0, sizeof(txFrame));

    if (!parseTransmitCommand(frameArgs, txFrame)) {
      Serial.println(F("ERR Frame format"));
      showStatus(F("TX parse error"));
      return;
    }

    if (!transmitFrameWithRetry(txFrame)) {
      Serial.println(F("ERR TX failed"));
      showStatus(F("TX failed"));
      return;
    }

    recordFrame(txFrame);
    updateDisplay();

    Serial.print(F("OK TX "));
    printFrameLine(txFrame, Serial);
    Serial.println();
    showStatus(F("Frame sent"));
    return;
  }

  Serial.println(F("ERR Unknown command"));
}

void setup() {
  Wire.begin();
  delay(100);

  ensureDisplayReady();

  Serial.begin(115200);

  pinMode(MCP2515_CS_PIN, OUTPUT);
  digitalWrite(MCP2515_CS_PIN, HIGH);
  SPI.begin();

  clearFrameHistory(false);

  if (configureCanBitrate(currentSpeed.requestedBps) != BitrateApplyResult_Success) {
    showStatus(F("CAN init failed"));
  } else {
    showStatus(F("Listening"));
  }
}

void loop() {
  ensureDisplayReady();

  struct can_frame frame;
  if (mcp2515.readMessage(&frame) == MCP2515::ERROR_OK) {
    recordFrame(frame);
    updateDisplay();
  }

  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line.length() > 0) {
      processSerialCommand(line);
    }
  }
}
