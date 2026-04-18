// ESP8266 Claxon Controller — 2 канала на одной ESP
// mDNS + UDP для управления двумя клаксонами и обратной связи с пьезо
// Два пьезо параллельно на A0 (одновременно играет только один канал)
// Wemos D1 Mini / ESP8266

#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>
#include <WiFiUdp.h>

// ===== НАСТРОЙКИ =====
const char* WIFI_SSID     = "claxon";
const char* WIFI_PASSWORD = "asd567fgh";

// Уникальное имя этого модуля (менять для каждого: esp-1, esp-2, esp-3, esp-4)
const char* ESP_NAME = "esp-3";

#define NUM_CHANNELS  2

// Пины клаксонов
#define HORN_1_PIN  D2    // GPIO4  — клаксон 1
#define HORN_2_PIN  D1    // GPIO14 — клаксон 2

// Два пьезо параллельно на один аналоговый вход
#define PIEZO_PIN   A0

#define UDP_PORT    5000

#define DEFAULT_SOUND_MS  50
#define DEFAULT_THRESHOLD 50
#define MAX_WAIT_MS      500
#define TAIL_MS           20
#define STARTUP_PWM     1023

// ===== СОСТОЯНИЯ =====
enum HornState {
  IDLE,
  WAITING_SOUND,
  SOUNDING,
  TAIL
};

// ===== КАНАЛ КЛАКСОНА =====
struct HornChannel {
  int hornPin;
  HornState state;
  int piezoThreshold;
  int hornPwm;
  bool useFeedback;
  bool sendReply;
  bool powerReduced;
  unsigned long boostMs;
  unsigned long hornOnTime;
  unsigned long soundStartTime;
  unsigned long soundStopTime;
  unsigned long soundDuration;
  int peakPiezo;
  unsigned long startupDelay;
  unsigned long actualSoundMs;
  IPAddress replyIP;
  uint16_t replyPort;
};

// ===== ПЕРЕМЕННЫЕ =====
WiFiUDP udp;
char packetBuf[64];

HornChannel ch[NUM_CHANNELS];
const int hornPins[NUM_CHANNELS] = { HORN_1_PIN, HORN_2_PIN };

bool anyChannelActive() {
  for (int i = 0; i < NUM_CHANNELS; i++) {
    if (ch[i].state != IDLE) return true;
  }
  return false;
}

bool anyFeedbackActive() {
  for (int i = 0; i < NUM_CHANNELS; i++) {
    if (ch[i].state != IDLE && ch[i].useFeedback) return true;
  }
  return false;
}

void setup() {
  Serial.begin(115200);

  for (int i = 0; i < NUM_CHANNELS; i++) {
    pinMode(hornPins[i], OUTPUT);
    digitalWrite(hornPins[i], LOW);

    ch[i].hornPin = hornPins[i];
    ch[i].state = IDLE;
    ch[i].piezoThreshold = DEFAULT_THRESHOLD;
    ch[i].hornPwm = 1023;
    ch[i].useFeedback = true;
    ch[i].sendReply = true;
    ch[i].powerReduced = false;
    ch[i].boostMs = 0;
    ch[i].soundDuration = DEFAULT_SOUND_MS;
    ch[i].peakPiezo = 0;
    ch[i].startupDelay = 0;
    ch[i].actualSoundMs = 0;
  }

  // WiFi
  WiFi.mode(WIFI_STA);
  WiFi.hostname(ESP_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("\nConnecting to %s", WIFI_SSID);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nIP: %s\n", WiFi.localIP().toString().c_str());

  // mDNS
  if (MDNS.begin(ESP_NAME)) {
    MDNS.addService("claxon", "udp", UDP_PORT);
    Serial.printf("mDNS: %s.local\n", ESP_NAME);
  }

  // UDP
  udp.begin(UDP_PORT);
  Serial.printf("UDP listening on port %d\n", UDP_PORT);
  Serial.println("Ready. Commands: FIRE:ch:ms, PLAY:ch:ms:boost, PING, STATUS");
}

void loop() {
  MDNS.update();

  int packetSize = udp.parsePacket();
  if (packetSize > 0) {
    int len = udp.read(packetBuf, sizeof(packetBuf) - 1);
    packetBuf[len] = '\0';

    IPAddress remoteIP = udp.remoteIP();
    uint16_t remotePort = udp.remotePort();

    handleCommand(packetBuf, remoteIP, remotePort);
  }

  // В PLAY можно обновлять оба канала параллельно.
  for (int i = 0; i < NUM_CHANNELS; i++) {
    if (ch[i].state != IDLE) {
      updateChannel(i);
    }
  }
}

void updateChannel(int idx) {
  HornChannel &c = ch[idx];

  switch (c.state) {

    case WAITING_SOUND: {
      int piezo = analogRead(PIEZO_PIN);
      if (piezo > c.peakPiezo) c.peakPiezo = piezo;

      if (piezo >= c.piezoThreshold) {
        c.soundStartTime = millis();
        c.startupDelay = c.soundStartTime - c.hornOnTime;
        // После подтверждения звука снижаем до рабочей мощности канала.
        analogWrite(c.hornPin, c.hornPwm);
        c.powerReduced = true;
        c.state = SOUNDING;
        Serial.printf("Ch%d: Sound after %lu ms (piezo=%d)\n", idx + 1, c.startupDelay, piezo);
      } else if (millis() - c.hornOnTime >= MAX_WAIT_MS) {
        digitalWrite(c.hornPin, LOW);
        c.state = IDLE;
        char reply[64];
        snprintf(reply, sizeof(reply), "FAIL:%d:no_sound:%d", idx + 1, c.peakPiezo);
        sendReply(reply, c.replyIP, c.replyPort);
        Serial.printf("Ch%d: Timeout! Peak piezo: %d\n", idx + 1, c.peakPiezo);
      }
      break;
    }

    case SOUNDING: {
      if (c.useFeedback) {
        int piezo = analogRead(PIEZO_PIN);
        if (piezo > c.peakPiezo) c.peakPiezo = piezo;

        c.actualSoundMs = millis() - c.soundStartTime;
      } else {
        unsigned long elapsed = millis() - c.hornOnTime;
        if (!c.powerReduced && elapsed >= c.boostMs) {
          analogWrite(c.hornPin, c.hornPwm);
          c.powerReduced = true;
        }
        c.actualSoundMs = elapsed > c.boostMs ? elapsed - c.boostMs : 0;
      }

      bool shouldStop = false;
      if (c.useFeedback) {
        shouldStop = c.actualSoundMs >= c.soundDuration;
      } else {
        unsigned long elapsedFromOn = millis() - c.hornOnTime;
        shouldStop = elapsedFromOn >= (c.boostMs + c.soundDuration);
      }
      if (shouldStop) {
        digitalWrite(c.hornPin, LOW);
        c.soundStopTime = millis();
        c.state = TAIL;
        Serial.printf("Ch%d: Horn off after %lu ms\n", idx + 1, c.actualSoundMs);
      }
      break;
    }

    case TAIL: {
      if (c.useFeedback) {
        int piezo = analogRead(PIEZO_PIN);
        if (piezo > c.peakPiezo) c.peakPiezo = piezo;
      }

      if (millis() - c.soundStopTime >= TAIL_MS) {
        c.state = IDLE;
        if (c.sendReply) {
          char reply[64];
          snprintf(reply, sizeof(reply), "OK:%d:%d:%lu:%lu", idx + 1, c.peakPiezo, c.startupDelay, c.actualSoundMs);
          sendReply(reply, c.replyIP, c.replyPort);
        }
        Serial.printf("Ch%d: Done. Peak=%d, delay=%lu, sound=%lu ms\n",
                       idx + 1, c.peakPiezo, c.startupDelay, c.actualSoundMs);
      }
      break;
    }

    case IDLE:
    default:
      break;
  }
}

void handleCommand(const char* cmd, IPAddress remoteIP, uint16_t remotePort) {
  if (strncmp(cmd, "FIRE:", 5) == 0) {
    // FIRE:ch:ms — канал 1 или 2, длительность
    int channel = cmd[5] - '0';
    if (channel < 1 || channel > NUM_CHANNELS) return;
    int idx = channel - 1;

    // Калибровка всегда эксклюзивна: только один активный канал на плате.
    if (anyChannelActive()) {
      char reply[64];
      snprintf(reply, sizeof(reply), "FAIL:%d:busy", channel);
      sendReply(reply, remoteIP, remotePort);
      return;
    }

    ch[idx].replyIP = remoteIP;
    ch[idx].replyPort = remotePort;

    unsigned long dur = DEFAULT_SOUND_MS;
    if (cmd[6] == ':') {
      unsigned long d = atol(cmd + 7);
      if (d >= 20 && d <= 1000) dur = d;
    }

    ch[idx].soundDuration = dur;
    ch[idx].useFeedback = true;
    ch[idx].sendReply = true;
    ch[idx].powerReduced = false;
    ch[idx].boostMs = 0;
    ch[idx].peakPiezo = 0;
    ch[idx].startupDelay = 0;
    ch[idx].actualSoundMs = 0;
    ch[idx].hornOnTime = millis();
    ch[idx].state = WAITING_SOUND;
    analogWrite(ch[idx].hornPin, STARTUP_PWM);

    Serial.printf("Ch%d: Horn ON, target: %lu ms\n", channel, dur);

  } else if (strncmp(cmd, "PLAY:", 5) == 0) {
    // PLAY:ch:ms:boost — воспроизведение без пьезо/ответа, по таймеру.
    int channel = 0;
    unsigned long dur = DEFAULT_SOUND_MS;
    unsigned long boost = 0;
    int parsed = sscanf(cmd, "PLAY:%d:%lu:%lu", &channel, &dur, &boost);
    if (parsed < 2) return;
    if (channel < 1 || channel > NUM_CHANNELS) return;
    if (dur < 20) dur = 20;
    if (dur > 1000) dur = 1000;
    if (boost > 1000) boost = 1000;
    int idx = channel - 1;

    // PLAY разрешаем параллельно по двум каналам, но не во время feedback-режима.
    if (anyFeedbackActive()) return;

    ch[idx].soundDuration = dur;
    ch[idx].useFeedback = false;
    ch[idx].sendReply = false;
    ch[idx].powerReduced = false;
    ch[idx].boostMs = boost;
    ch[idx].peakPiezo = 0;
    ch[idx].startupDelay = boost;
    ch[idx].actualSoundMs = 0;
    ch[idx].hornOnTime = millis();
    ch[idx].soundStartTime = ch[idx].hornOnTime + boost;
    ch[idx].state = SOUNDING;
    analogWrite(ch[idx].hornPin, STARTUP_PWM);

    Serial.printf("Ch%d: PLAY ON, dur=%lu, boost=%lu\n", channel, dur, boost);

  } else if (strcmp(cmd, "PING") == 0) {
    char reply[64];
    snprintf(reply, sizeof(reply), "PONG:%s:%d", ESP_NAME, NUM_CHANNELS);
    sendReply(reply, remoteIP, remotePort);

  } else if (strncmp(cmd, "THRESH:", 7) == 0) {
    // THRESH:ch:value
    int channel = cmd[7] - '0';
    if (channel < 1 || channel > NUM_CHANNELS) return;
    int idx = channel - 1;

    if (cmd[8] == ':') {
      int t = atoi(cmd + 9);
      if (t >= 1 && t <= 1023) {
        ch[idx].piezoThreshold = t;
        char reply[32];
        snprintf(reply, sizeof(reply), "THRESH:%d:%d", channel, ch[idx].piezoThreshold);
        sendReply(reply, remoteIP, remotePort);
        Serial.printf("Ch%d: Threshold=%d\n", channel, ch[idx].piezoThreshold);
      }
    }

  } else if (strncmp(cmd, "POWER:", 6) == 0) {
    // POWER:ch:percent
    int channel = cmd[6] - '0';
    if (channel < 1 || channel > NUM_CHANNELS) return;
    int idx = channel - 1;

    if (cmd[7] == ':') {
      float p = atof(cmd + 8);
      if (p >= 0.0 && p <= 100.0) {
        ch[idx].hornPwm = (int)(p * 1023.0 / 100.0 + 0.5);
        if (ch[idx].hornPwm > 1023) ch[idx].hornPwm = 1023;
        char reply[32];
        snprintf(reply, sizeof(reply), "POWER:%d:%d", channel, ch[idx].hornPwm);
        sendReply(reply, remoteIP, remotePort);
        Serial.printf("Ch%d: Power %.1f%% (PWM=%d)\n", channel, p, ch[idx].hornPwm);
      }
    }

  } else if (strcmp(cmd, "STATUS") == 0) {
    int piezo = analogRead(PIEZO_PIN);
    for (int i = 0; i < NUM_CHANNELS; i++) {
      char reply[64];
      snprintf(reply, sizeof(reply), "STATUS:%s:%d:%d:%d:%d",
               ESP_NAME, i + 1, ch[i].state != IDLE ? 1 : 0, piezo, ch[i].piezoThreshold);
      sendReply(reply, remoteIP, remotePort);
    }
  }
}

void sendReply(const char* msg, IPAddress ip, uint16_t port) {
  udp.beginPacket(ip, port);
  udp.write(msg);
  udp.endPacket();
}
