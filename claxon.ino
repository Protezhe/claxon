// ESP8266 Claxon Controller
// mDNS + UDP для управления клаксоном и обратной связи с пьезо
// Управление реальной длительностью звука через обратную связь пьезо
// Wemos D1 Mini / ESP8266

#include <ESP8266WiFi.h>
#include <ESP8266mDNS.h>
#include <WiFiUdp.h>

// ===== НАСТРОЙКИ =====
const char* WIFI_SSID     = "claxon";
const char* WIFI_PASSWORD = "asd567fgh";

// Уникальное имя этого клаксона (менять для каждого модуля!)
const char* CLAXON_NAME = "claxon-1";

#define HORN_PIN    D2    // GPIO4 — управление клаксоном (реле/MOSFET)
#define PIEZO_PIN   A0    // аналоговый вход пьезо

#define UDP_PORT    5000  // порт для приёма команд

#define DEFAULT_SOUND_MS  50   // желаемая длительность реального звука (мс)
#define DEFAULT_THRESHOLD 50   // порог обнаружения звука на пьезо (по умолчанию)
#define MAX_WAIT_MS      500   // макс. ожидание начала звука (защита от зависания)
#define TAIL_MS           20   // время после выключения для замера затухания

// ===== СОСТОЯНИЯ =====
enum HornState {
  IDLE,            // ничего не делаем
  WAITING_SOUND,   // транзистор включён, ждём звук на пьезо
  SOUNDING,        // звук пошёл, отсчитываем желаемую длительность
  TAIL             // транзистор выключен, замеряем затухание
};

// ===== ПЕРЕМЕННЫЕ =====
WiFiUDP udp;
char packetBuf[64];

HornState state = IDLE;
int piezoThreshold = DEFAULT_THRESHOLD;  // текущий порог (настраивается по UDP)
unsigned long hornOnTime = 0;      // когда включили транзистор
unsigned long soundStartTime = 0;  // когда пьезо зафиксировал звук
unsigned long soundStopTime = 0;   // когда выключили транзистор
unsigned long soundDuration = DEFAULT_SOUND_MS;

int peakPiezo = 0;
unsigned long startupDelay = 0;    // задержка от включения до звука (мс)
unsigned long actualSoundMs = 0;   // реальная длительность звука

// Адрес отправителя для ответа
IPAddress replyIP;
uint16_t replyPort;

void setup() {
  Serial.begin(115200);

  pinMode(HORN_PIN, OUTPUT);
  digitalWrite(HORN_PIN, LOW);

  // WiFi
  WiFi.mode(WIFI_STA);
  WiFi.hostname(CLAXON_NAME);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("\nConnecting to %s", WIFI_SSID);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.printf("\nIP: %s\n", WiFi.localIP().toString().c_str());

  // mDNS
  if (MDNS.begin(CLAXON_NAME)) {
    MDNS.addService("claxon", "udp", UDP_PORT);
    Serial.printf("mDNS: %s.local\n", CLAXON_NAME);
  }

  // UDP
  udp.begin(UDP_PORT);
  Serial.printf("UDP listening on port %d\n", UDP_PORT);
  Serial.println("Ready. Commands: FIRE, FIRE:50, PING, STATUS");
}

void loop() {
  MDNS.update();

  // Обработка входящих UDP пакетов
  int packetSize = udp.parsePacket();
  if (packetSize > 0) {
    int len = udp.read(packetBuf, sizeof(packetBuf) - 1);
    packetBuf[len] = '\0';

    replyIP = udp.remoteIP();
    replyPort = udp.remotePort();

    handleCommand(packetBuf);
  }

  // Машина состояний клаксона
  switch (state) {

    case WAITING_SOUND: {
      int piezo = analogRead(PIEZO_PIN);
      if (piezo > peakPiezo) peakPiezo = piezo;

      if (piezo >= piezoThreshold) {
        // Звук пошёл! Начинаем отсчёт реальной длительности
        soundStartTime = millis();
        startupDelay = soundStartTime - hornOnTime;
        state = SOUNDING;
        Serial.printf("Sound detected after %lu ms (piezo=%d)\n", startupDelay, piezo);
      } else if (millis() - hornOnTime >= MAX_WAIT_MS) {
        // Таймаут — звук не появился, выключаем
        digitalWrite(HORN_PIN, LOW);
        state = IDLE;
        char reply[64];
        snprintf(reply, sizeof(reply), "FAIL:no_sound:%d", peakPiezo);
        sendReply(reply);
        Serial.printf("Timeout! No sound detected. Peak piezo: %d\n", peakPiezo);
      }
      break;
    }

    case SOUNDING: {
      int piezo = analogRead(PIEZO_PIN);
      if (piezo > peakPiezo) peakPiezo = piezo;

      actualSoundMs = millis() - soundStartTime;
      if (actualSoundMs >= soundDuration) {
        // Нужная длительность звука достигнута — выключаем транзистор
        digitalWrite(HORN_PIN, LOW);
        soundStopTime = millis();
        state = TAIL;
        Serial.printf("Horn off after %lu ms of sound\n", actualSoundMs);
      }
      break;
    }

    case TAIL: {
      // Короткая пауза после выключения для замера затухания
      int piezo = analogRead(PIEZO_PIN);
      if (piezo > peakPiezo) peakPiezo = piezo;

      if (millis() - soundStopTime >= TAIL_MS) {
        state = IDLE;
        // Ответ: OK:пик_пьезо:задержка_старта:реальная_длительность
        char reply[64];
        snprintf(reply, sizeof(reply), "OK:%d:%lu:%lu", peakPiezo, startupDelay, actualSoundMs);
        sendReply(reply);
        Serial.printf("Done. Peak=%d, delay=%lu ms, sound=%lu ms\n",
                       peakPiezo, startupDelay, actualSoundMs);
      }
      break;
    }

    case IDLE:
    default:
      break;
  }
}

void handleCommand(const char* cmd) {
  if (strncmp(cmd, "FIRE", 4) == 0) {
    // FIRE или FIRE:50 (желаемая длительность реального звука, 20-100 мс)
    soundDuration = DEFAULT_SOUND_MS;
    if (cmd[4] == ':') {
      unsigned long d = atol(cmd + 5);
      if (d >= 20 && d <= 100) {
        soundDuration = d;
      }
    }

    // Включаем транзистор и ждём звук
    peakPiezo = 0;
    startupDelay = 0;
    actualSoundMs = 0;
    hornOnTime = millis();
    state = WAITING_SOUND;
    digitalWrite(HORN_PIN, HIGH);

    Serial.printf("Horn ON, target sound: %lu ms\n", soundDuration);

  } else if (strcmp(cmd, "PING") == 0) {
    char reply[64];
    snprintf(reply, sizeof(reply), "PONG:%s", CLAXON_NAME);
    sendReply(reply);

  } else if (strncmp(cmd, "THRESH:", 7) == 0) {
    // THRESH:значение — установить порог пьезо (1-1023)
    int t = atoi(cmd + 7);
    if (t >= 1 && t <= 1023) {
      piezoThreshold = t;
      char reply[32];
      snprintf(reply, sizeof(reply), "THRESH:%d", piezoThreshold);
      sendReply(reply);
      Serial.printf("Threshold set to %d\n", piezoThreshold);
    }

  } else if (strcmp(cmd, "STATUS") == 0) {
    int piezo = analogRead(PIEZO_PIN);
    char reply[64];
    snprintf(reply, sizeof(reply), "STATUS:%s:%d:%d:%d", CLAXON_NAME, state != IDLE ? 1 : 0, piezo, piezoThreshold);
    sendReply(reply);
  }
}

void sendReply(const char* msg) {
  udp.beginPacket(replyIP, replyPort);
  udp.write(msg);
  udp.endPacket();
}
