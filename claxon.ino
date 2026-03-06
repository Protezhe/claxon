// ESP8266 Claxon Controller
// mDNS + UDP для управления клаксоном и обратной связи с пьезо
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
#define HORN_DURATION_MS 100  // длительность звука по умолчанию

// ===== ПЕРЕМЕННЫЕ =====
WiFiUDP udp;
char packetBuf[64];

// Состояние клаксона
bool hornActive = false;
unsigned long hornStartTime = 0;
unsigned long hornDuration = HORN_DURATION_MS;
int peakPiezo = 0;  // пиковое значение пьезо за время звучания

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

  // mDNS — регистрация имени и сервиса
  if (MDNS.begin(CLAXON_NAME)) {
    // Регистрируем сервис _claxon._udp для обнаружения
    MDNS.addService("claxon", "udp", UDP_PORT);
    Serial.printf("mDNS: %s.local\n", CLAXON_NAME);
  }

  // UDP
  udp.begin(UDP_PORT);
  Serial.printf("UDP listening on port %d\n", UDP_PORT);
  Serial.println("Ready. Commands: FIRE, FIRE:150, PING");
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

  // Управление клаксоном
  if (hornActive) {
    // Читаем пьезо и запоминаем пик
    int piezoVal = analogRead(PIEZO_PIN);
    if (piezoVal > peakPiezo) {
      peakPiezo = piezoVal;
    }

    // Проверяем время
    if (millis() - hornStartTime >= hornDuration) {
      // Выключаем клаксон
      digitalWrite(HORN_PIN, LOW);
      hornActive = false;

      // Отправляем ответ с уровнем звука
      char reply[32];
      snprintf(reply, sizeof(reply), "OK:%d", peakPiezo);
      sendReply(reply);

      Serial.printf("Horn off. Piezo peak: %d\n", peakPiezo);
    }
  }
}

void handleCommand(const char* cmd) {
  if (strncmp(cmd, "FIRE", 4) == 0) {
    // FIRE или FIRE:150 (с указанием длительности в мс)
    hornDuration = HORN_DURATION_MS;
    if (cmd[4] == ':') {
      unsigned long d = atol(cmd + 5);
      if (d > 0 && d <= 1000) {
        hornDuration = d;
      }
    }

    // Включаем клаксон
    peakPiezo = 0;
    hornStartTime = millis();
    hornActive = true;
    digitalWrite(HORN_PIN, HIGH);

    Serial.printf("Horn ON for %lu ms\n", hornDuration);

  } else if (strcmp(cmd, "PING") == 0) {
    // Ответ на пинг — для проверки связи
    char reply[64];
    snprintf(reply, sizeof(reply), "PONG:%s", CLAXON_NAME);
    sendReply(reply);

  } else if (strcmp(cmd, "STATUS") == 0) {
    // Текущее состояние
    int piezo = analogRead(PIEZO_PIN);
    char reply[64];
    snprintf(reply, sizeof(reply), "STATUS:%s:%d:%d", CLAXON_NAME, hornActive ? 1 : 0, piezo);
    sendReply(reply);
  }
}

void sendReply(const char* msg) {
  udp.beginPacket(replyIP, replyPort);
  udp.write(msg);
  udp.endPacket();
}
