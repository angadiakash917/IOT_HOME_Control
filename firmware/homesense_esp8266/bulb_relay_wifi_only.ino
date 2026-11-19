/*
  Relay-only Wi-Fi firmware for NodeMCU V3 / ESP8266.
  Receives bulb ON/OFF commands from the existing website. No sensors.

  Copy WIFI_SSID, WIFI_PASSWORD, SERVER_URL, and DEVICE_TOKEN from the
  configured sketch downloaded using the existing Connect to Wi-Fi option.
*/
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecureBearSSL.h>
#include <time.h>

const char* WIFI_SSID = "__WIFI_SSID__";
const char* WIFI_PASSWORD = "__WIFI_PASSWORD__";
const char* SERVER_URL = "__SERVER_URL__";
const char* DEVICE_TOKEN = "__DEVICE_TOKEN__";

constexpr uint8_t RELAY_PIN = 5; // NodeMCU D1 / GPIO5
constexpr bool RELAY_ACTIVE_LOW = true; // Set false only for active-high relay modules
constexpr uint32_t POLL_INTERVAL_MS = 3000;
constexpr uint32_t WIFI_RETRY_MS = 10000;
constexpr uint32_t CLOUD_FAILSAFE_MS = 30000;

const char ROOT_CA[] PROGMEM = R"ROOTCA(
-----BEGIN CERTIFICATE-----
MIIFazCCA1OgAwIBAgIRAIIQz7DSQONZRGPgu2OCiwAwDQYJKoZIhvcNAQELBQAw
TzELMAkGA1UEBhMCVVMxKTAnBgNVBAoTIEludGVybmV0IFNlY3VyaXR5IFJlc2Vh
cmNoIEdyb3VwMRUwEwYDVQQDEwxJU1JHIFJvb3QgWDEwHhcNMTUwNjA0MTEwNDM4
WhcNMzUwNjA0MTEwNDM4WjBPMQswCQYDVQQGEwJVUzEpMCcGA1UEChMgSW50ZXJu
ZXQgU2VjdXJpdHkgUmVzZWFyY2ggR3JvdXAxFTATBgNVBAMTDElTUkcgUm9vdCBY
MTCCAiIwDQYJKoZIhvcNAQEBBQADggIPADCCAgoCggIBAK3oJHP0FDfzm54rVygc
h77ct984kIxuPOZXoHj3dcKi/vVqbvYATyjb3miGbESTtrFj/RQSa78f0uoxmyF+
0TM8ukj13Xnfs7j/EvEhmkvBioZxaUpmZmyPfjxwv60pIgbz5MDmgK7iS4+3mX6U
A5/TR5d8mUgjU+g4rk8Kb4Mu0UlXjIB0ttov0DiNewNwIRt18jA8+o+u3dpjq+sW
T8KOEUt+zwvo/7V3LvSye0rgTBIlDHCNAymg4VMk7BPZ7hm/ELNKjD+Jo2FR3qyH
B5T0Y3HsLuJvW5iB4YlcNHlsdu87kGJ55tukmi8mxdAQ4Q7e2RCOFvu396j3x+UC
B5iPNgiV5+I3lg02dZ77DnKxHZu8A/lJBdiB3QW0KtZB6awBdpUKD9jf1b0SHzUv
KBds0pjBqAlkd25HN7rOrFleaJ1/ctaJxQZBKT5ZPt0m9STJEadao0xAH0ahmbWn
OlFuhjuefXKnEgV4We0+UXgVCwOPjdAvBbI+e0ocS3MFEvzG6uBQE3xDk3SzynTn
jh8BCNAw1FtxNrQHusEwMFxIt4I7mKZ9YIqioymCzLq9gwQbooMDQaHWBfEbwrbw
qHyGO0aoSCqI3Haadr8faqU9GY/rOPNk3sgrDQoo//fb4hVC1CLQJ13hef4Y53CI
rU7m2Ys6xt0nUW7/vGT1M0NPAgMBAAGjQjBAMA4GA1UdDwEB/wQEAwIBBjAPBgNV
HRMBAf8EBTADAQH/MB0GA1UdDgQWBBR5tFnme7bl5AFzgAiIyBpY9umbbjANBgkq
hkiG9w0BAQsFAAOCAgEAVR9YqbyyqFDQDLHYGmkgJykIrGF1XIpu+ILlaS/V9lZL
ubhzEFnTIZd+50xx+7LSYK05qAvqFyFWhfFQDlnrzuBZ6brJFe+GnY+EgPbk6ZGQ
3BebYhtF8GaV0nxvwuo77x/Py9auJ/GpsMiu/X1+mvoiBOv/2X/qkSsisRcOj/KK
NFtY2PwByVS5uCbMiogziUwthDyC3+6WVwW6LLv3xLfHTjuCvjHIInNzktHCgKQ5
ORAzI4JMPJ+GslWYHb4phowim57iaztXOoJwTdwJx4nLCgdNbOhdjsnvzqvHu7Ur
TkXWStAmzOVyyghqpZXjFaH3pO3JLF+l+/+sKAIuvtd7u+Nxe5AW0wdeRlN8NwdC
jNPElpzVmbUq4JUagEiuTDkHzsxHpFKVK7q4+63SM1N95R1NbdWhscdCb+ZAJzVc
oyi3B43njTOQ5yOf+1CceWxG1bQVs5ZufpsMljq4Ui0/1lvh+wjChP4kqKOJ2qxq
4RgqsahDYVvTH9w7jXbyLeiNdd8XM2w9U/t7y0Ff/9yi0GE44Za4rF2LN9d11TPA
mRGunUHBcnWEvgJBQl9nJEiU0Zsnvgc/ubhPgXRR4Xq37Z0j4r7g1SgEEzwxA57d
emyPxgcYxn/eR44/KJ4EBs+lVDR3veyJm+kXQ99b21/+jh5Xos1AnX5iItreGCc=
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIIFVzCCAz+gAwIBAgINAgPlk28xsBNJiGuiFzANBgkqhkiG9w0BAQwFADBHMQsw
CQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEU
MBIGA1UEAxMLR1RTIFJvb3QgUjEwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAw
MDAwWjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZp
Y2VzIExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjEwggIiMA0GCSqGSIb3DQEBAQUA
A4ICDwAwggIKAoICAQC2EQKLHuOhd5s73L+UPreVp0A8of2C+X0yBoJx9vaMf/vo
27xqLpeXo4xL+Sv2sfnOhB2x+cWX3u+58qPpvBKJXqeqUqv4IyfLpLGcY9vXmX7w
Cl7raKb0xlpHDU0QM+NOsROjyBhsS+z8CZDfnWQpJSMHobTSPS5g4M/SCYe7zUjw
TcLCeoiKu7rPWRnWr4+wB7CeMfGCwcDfLqZtbBkOtdh+JhpFAz2weaSUKK0Pfybl
qAj+lug8aJRT7oM6iCsVlgmy4HqMLnXWnOunVmSPlk9orj2XwoSPwLxAwAtcvfaH
szVsrBhQf4TgTM2S0yDpM7xSma8ytSmzJSq0SPly4cpk9+aCEI3oncKKiPo4Zor8
Y/kB+Xj9e1x3+naH+uzfsQ55lVe0vSbv1gHR6xYKu44LtcXFilWr06zqkUspzBmk
MiVOKvFlRNACzqrOSbTqn3yDsEB750Orp2yjj32JgfpMpf/VjsPOS+C12LOORc92
wO1AK/1TD7Cn1TsNsYqiA94xrcx36m97PtbfkSIS5r762DL8EGMUUXLeXdYWk70p
aDPvOmbsB4om3xPXV2V4J95eSRQAogB/mqghtqmxlbCluQ0WEdrHbEg8QOB+DVrN
VjzRlwW5y0vtOUucxD/SVRNuJLDWcfr0wbrM7Rv1/oFB2ACYPTrIrnqYNxgFlQID
AQABo0IwQDAOBgNVHQ8BAf8EBAMCAYYwDwYDVR0TAQH/BAUwAwEB/zAdBgNVHQ4E
FgQU5K8rJnEaK0gnhS9SZizv8IkTcT4wDQYJKoZIhvcNAQEMBQADggIBAJ+qQibb
C5u+/x6Wki4+omVKapi6Ist9wTrYggoGxval3sBOh2Z5ofmmWJyq+bXmYOfg6LEe
QkEzCzc9zolwFcq1JKjPa7XSQCGYzyI0zzvFIoTgxQ6KfF2I5DUkzps+GlQebtuy
h6f88/qBVRRiClmpIgUxPoLW7ttXNLwzldMXG+gnoot7TiYaelpkttGsN/H9oPM4
7HLwEXWdyzRSjeZ2axfG34arJ45JK3VmgRAhpuo+9K4l/3wV3s6MJT/KYnAK9y8J
ZgfIPxz88NtFMN9iiMG1D53Dn0reWVlHxYciNuaCp+0KueIHoI17eko8cdLiA6Ef
MgfdG+RCzgwARWGAtQsgWSl4vflVy2PFPEz0tv/bal8xa5meLMFrUKTX5hgUvYU/
Z6tGn6D/Qqc6f1zLXbBwHSs09dR2CQzreExZBfMzQsNhFRAbd03OIozUhfJFfbdT
6u9AWpQKXCBfTkBdYiJ23//OYb2MI3jSNwLgjt7RETeJ9r/tSQdirpLsQBqvFAnZ
0E6yove+7u7Y/9waLd64NnHi/Hm3lCXRSHNboTXns5lndcEZOitHTtNCjv0xyBZm
2tIMPNuzjsmhDYAPexZ3FL//2wmUspO8IFgV6dtxQ/PeEMMA3KgqlbbC1j+Qa3bb
bP6MvPJwNQzcmRk13NfIRmPVNnGuV/u3gm3c
-----END CERTIFICATE-----
-----BEGIN CERTIFICATE-----
MIICCTCCAY6gAwIBAgINAgPlwGjvYxqccpBQUjAKBggqhkjOPQQDAzBHMQswCQYD
VQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2VzIExMQzEUMBIG
A1UEAxMLR1RTIFJvb3QgUjQwHhcNMTYwNjIyMDAwMDAwWhcNMzYwNjIyMDAwMDAw
WjBHMQswCQYDVQQGEwJVUzEiMCAGA1UEChMZR29vZ2xlIFRydXN0IFNlcnZpY2Vz
IExMQzEUMBIGA1UEAxMLR1RTIFJvb3QgUjQwdjAQBgcqhkjOPQIBBgUrgQQAIgNi
AATzdHOnaItgrkO4NcWBMHtLSZ37wWHO5t5GvWvVYRg1rkDdc/eJkTBa6zzuhXyi
QHY7qca4R9gq55KRanPpsXI5nymfopjTX15YhmUPoYRlBtHci8nHc8iMai/lxKvR
HYqjQjBAMA4GA1UdDwEB/wQEAwIBhjAPBgNVHRMBAf8EBTADAQH/MB0GA1UdDgQW
BBSATNbrdP9JNqPV2Py1PsVq8JQdjDAKBggqhkjOPQQDAwNpADBmAjEA6ED/g94D
9J+uHXqnLrmvT/aDHQ4thQEd0dlq7A/Cr8deVl5c1RxYIigL9zC2L7F8AjEA8GE8
p/SgguMh1YQdc4acLa/KNJvxn7kjNuK8YAOdgLOaVsjh4rsUecrNIdSUtUlD
-----END CERTIFICATE-----
)ROOTCA";
BearSSL::X509List trustAnchor(ROOT_CA);

bool relayOn = false;
bool timeReady = false;
uint32_t lastPollAt = 0;
uint32_t lastWiFiAttemptAt = 0;
uint32_t lastCloudOkAt = 0;
uint32_t lastTimeSyncAttemptAt = 0;

void setRelay(bool on) {
  const uint8_t activeLevel = RELAY_ACTIVE_LOW ? LOW : HIGH;
  const uint8_t inactiveLevel = RELAY_ACTIVE_LOW ? HIGH : LOW;
  digitalWrite(RELAY_PIN, on ? activeLevel : inactiveLevel);
  relayOn = on;
}

bool cloudRequest(const char* method, const String& path, const String& body, String& responseBody) {
  BearSSL::WiFiClientSecure client;
  client.setTrustAnchors(&trustAnchor);
  client.setTimeout(8000);

  HTTPClient http;
  http.setTimeout(8000);
  const String url = String(SERVER_URL) + path;
  if (!http.begin(client, url)) return false;

  http.addHeader("Authorization", String("Bearer ") + DEVICE_TOKEN);
  http.addHeader("Accept", "application/json");
  int status = -1;
  if (strcmp(method, "POST") == 0) {
    http.addHeader("Content-Type", "application/json");
    status = http.POST(body);
  } else {
    status = http.GET();
  }
  responseBody = http.getString();
  http.end();
  if (status < 200 || status >= 300) {
    Serial.printf("Website HTTP error: %d\n", status);
    return false;
  }
  return true;
}

void sendRelayStatus() {
  String response;
  String body = "{\"temperature\":null,\"humidity\":null,\"ldrRaw\":null,\"powerW\":null,\"relayOn\":";
  body += relayOn ? "true" : "false";
  body += ",\"wifiRssi\":" + String(WiFi.RSSI());
  body += ",\"firmwareVersion\":\"relay-only-wifi-1.0\"}";

  if (cloudRequest("POST", "/api/device/telemetry", body, response)) {
    lastCloudOkAt = millis();
    Serial.printf("Relay status sent: %s\n", relayOn ? "ON" : "OFF");
  }
}

void receiveWebsiteCommand() {
  String response;
  if (!cloudRequest("GET", "/api/device/command", "", response)) return;
  lastCloudOkAt = millis();
  if (response.indexOf("\"command\":null") >= 0) return;

  const int idStart = response.indexOf("\"id\":\"");
  if (idStart < 0) return;
  const int idValueStart = idStart + 6;
  const int idEnd = response.indexOf('"', idValueStart);
  if (idEnd < 0) return;
  const int stateKey = response.indexOf("\"state\":", idEnd);
  if (stateKey < 0) return;

  const String commandId = response.substring(idValueStart, idEnd);
  const String stateText = response.substring(stateKey + 8);
  bool desiredState;
  if (stateText.startsWith("true")) desiredState = true;
  else if (stateText.startsWith("false")) desiredState = false;
  else return;

  setRelay(desiredState);
  Serial.printf("Website command: Bulb %s\n", relayOn ? "ON" : "OFF");

  String result;
  String body = String("{\"commandId\":\"") + commandId +
      "\",\"success\":true,\"relayOn\":" + (relayOn ? "true" : "false") + "}";
  if (!cloudRequest("POST", "/api/device/command/result", body, result)) {
    Serial.println("Could not acknowledge the command; website will retry it.");
  }
}

void connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  lastWiFiAttemptAt = millis();
  Serial.printf("Connecting to Wi-Fi: %s\n", WIFI_SSID);
}

void setup() {
  Serial.begin(115200);
  digitalWrite(RELAY_PIN, RELAY_ACTIVE_LOW ? HIGH : LOW);
  pinMode(RELAY_PIN, OUTPUT);
  setRelay(false); // Start with bulb OFF.
  connectWiFi();
}

void loop() {
  const uint32_t now = millis();
  if (WiFi.status() != WL_CONNECTED) {
    if (relayOn && now - lastCloudOkAt >= CLOUD_FAILSAFE_MS) setRelay(false);
    if (now - lastWiFiAttemptAt >= WIFI_RETRY_MS) connectWiFi();
    delay(10);
    return;
  }

  if (!timeReady && (lastTimeSyncAttemptAt == 0 || now - lastTimeSyncAttemptAt >= 30000)) {
    lastTimeSyncAttemptAt = now;
    configTime(0, 0, "pool.ntp.org", "time.nist.gov");
    const uint32_t waitStart = millis();
    while (time(nullptr) < 1700000000 && millis() - waitStart < 12000) delay(250);
    timeReady = time(nullptr) >= 1700000000;
    if (timeReady) {
      lastCloudOkAt = millis();
      Serial.println("Clock synchronized; secure website connection ready.");
    } else {
      Serial.println("Waiting for network time for HTTPS verification.");
    }
  }

  if (!timeReady) {
    delay(10);
    return;
  }

  if (now - lastPollAt >= POLL_INTERVAL_MS) {
    lastPollAt = now;
    sendRelayStatus();
    receiveWebsiteCommand();
  }

  if (relayOn && now - lastCloudOkAt >= CLOUD_FAILSAFE_MS) {
    setRelay(false);
    Serial.println("Website connection lost; relay switched OFF.");
  }
  delay(10);
}

