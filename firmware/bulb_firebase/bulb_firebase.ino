/*
  NodeMCU ESP8266 <-> Firebase Realtime Database (REST, no extra libraries)

  Database layout:
    /turn    : "on" | "off"          <- written by the website, read by the NodeMCU
    /status  : { temperature, humidity, ldrRaw, relayOn, wifiRssi, updatedAt }
                                     <- written by the NodeMCU, shown on the website

  Wiring: DHT11 DATA -> D4, LDR divider -> A0, relay IN -> D1 (active-low).
  Libraries needed: "DHT sensor library" + "Adafruit Unified Sensor" (Library Manager).
  Board: NodeMCU 1.0 (ESP-12E Module).
*/
#include <ESP8266WiFi.h>
#include <ESP8266HTTPClient.h>
#include <WiFiClientSecureBearSSL.h>
#include <DHT.h>

const char* WIFI_SSID     = "YOUR_WIFI_NAME";      // 2.4 GHz only
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";
const char* DB_URL = "https://iotbasedbulbonoff-default-rtdb.firebaseio.com";
const char* DB_AUTH = "";   // optional: database secret / token once you lock the rules

constexpr uint8_t RELAY_PIN = 5;   // D1
constexpr uint8_t DHT_PIN   = 2;   // D4
constexpr uint8_t LDR_PIN   = A0;
constexpr bool RELAY_ACTIVE_LOW = true;
constexpr uint32_t POLL_MS   = 2000;   // check /turn
constexpr uint32_t STATUS_MS = 5000;   // push sensors

DHT dht(DHT_PIN, DHT11);
bool relayOn = false;
uint32_t lastPoll = 0, lastStatus = 0;

void setRelay(bool on) {
  relayOn = on;
  digitalWrite(RELAY_PIN, (on == RELAY_ACTIVE_LOW) ? LOW : HIGH);
}

String url(const char* path) {
  String u = String(DB_URL) + path + ".json";
  if (strlen(DB_AUTH)) u += String("?auth=") + DB_AUTH;
  return u;
}

bool request(const char* method, const char* path, const String& body, String& out) {
  BearSSL::WiFiClientSecure client;
  client.setInsecure();             // simple; encrypted but certificate not verified
  client.setTimeout(8000);
  HTTPClient http;
  if (!http.begin(client, url(path))) return false;
  http.addHeader("Content-Type", "application/json");
  int code = strcmp(method, "GET") == 0 ? http.GET() : http.PUT(body);
  out = http.getString();
  http.end();
  if (code < 200 || code >= 300) { Serial.printf("HTTP %d on %s\n", code, path); return false; }
  return true;
}

void pushStatus() {
  float t = dht.readTemperature(), h = dht.readHumidity();
  String b = "{\"temperature\":" + (isnan(t) ? String("null") : String(t, 1));
  b += ",\"humidity\":" + (isnan(h) ? String("null") : String(h, 1));
  b += ",\"ldrRaw\":" + String(analogRead(LDR_PIN));
  b += String(",\"relayOn\":") + (relayOn ? "true" : "false");
  b += ",\"wifiRssi\":" + String(WiFi.RSSI());
  b += ",\"updatedAt\":{\".sv\":\"timestamp\"}}";
  String r;
  if (request("PUT", "/status", b, r)) Serial.println("Status sent: " + b);
}

void pollCommand() {
  String r;
  if (!request("GET", "/turn", "", r)) return;
  r.trim();
  bool want;
  if (r == "\"on\"") want = true;
  else if (r == "\"off\"") want = false;
  else return;                      // ignore null/garbage
  if (want != relayOn) {
    setRelay(want);
    Serial.printf("Bulb -> %s\n", want ? "ON" : "OFF");
    pushStatus();                   // confirm right away
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(RELAY_PIN, OUTPUT);
  setRelay(false);
  dht.begin();
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) { delay(500); Serial.print("."); }
  Serial.println("\nConnected, IP " + WiFi.localIP().toString());
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) { delay(500); return; }
  uint32_t now = millis();
  if (now - lastPoll >= POLL_MS)   { lastPoll = now;   pollCommand(); }
  if (now - lastStatus >= STATUS_MS) { lastStatus = now; pushStatus(); }
}
