/*
 * ============================================================================
 *  Gateway IoT Multiprotokol - versi Bare Metal (super-loop)
 *  ESP32 + LoRa SX1278 (SPI) + OLED SSD1306 (I2C) + BLE internal + Wi-Fi/MQTT
 *
 *  Penelitian: Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32
 *  dengan Implementasi FreeRTOS (Teknik Elektro, UKSW)
 * ============================================================================
 *  Versi pembanding untuk Gateway_FreeRTOS.ino. Pin, parameter LoRa dan BLE,
 *  format paket, payload MQTT, dan format log dibuat sama dengan versi
 *  FreeRTOS, sehingga yang berbeda hanya susunan programnya.
 *
 *  Susunan program
 *    Seluruh pekerjaan berjalan berurutan di loop(): membaca LoRa, memeriksa
 *    mailbox, mengirim paket ke broker, dan memperbarui OLED. Program tidak
 *    membuat task dan antrean sendiri. Setiap sumber memiliki mailbox
 *    berkapasitas satu paket, sehingga paket baru yang datang sebelum paket
 *    lama terkirim akan menimpa paket lama (dihitung "overwrite").
 *
 *  Batasan istilah "bare metal"
 *    Pada ESP32 dengan Arduino, FreeRTOS tetap berjalan di bawah setup() dan
 *    loop(). Callback pemindaian BLE dijalankan oleh task host NimBLE, dan
 *    Wi-Fi/LwIP juga memiliki task sendiri. "Bare metal" di sini berarti
 *    program aplikasi tidak membuat task dan antrean sendiri.
 *
 *  Pustaka sama dengan Gateway_FreeRTOS.ino.
 * ============================================================================
 */
#include <Arduino.h>
#include <stdarg.h>
#include <WiFi.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <PubSubClient.h>
#include <NimBLEDevice.h>
#include "esp_coexist.h"

// ============================ MODE DEBUG ======================================
#define DEBUG_MODE      1
#define DEBUG_VERBOSE   0   // 1 = cetak setiap paket (hanya untuk debug; membebani CPU)
#define DATA_LOG_MODE   1   // harus 1 saat pengambilan data (baris D dan S untuk logger.py)
#define DIAG_MODE       1   // baris G setiap 10 s (sama dengan Gateway_FreeRTOS.ino)

// ============================ PENGATURAN RADIO ===============================
// Nilainya harus sama dengan Gateway_FreeRTOS.ino agar kedua versi dapat dibandingkan.
#define WIFI_NO_SLEEP   1
#define COEX_PREFER_BT  0

// ============================ 1. KONFIGURASI =================================
#define WIFI_SSID        "NAMA_WIFI"        // ganti dengan nama jaringan Wi-Fi
#define WIFI_PASSWORD    "KATA_SANDI_WIFI"  // ganti dengan kata sandi Wi-Fi
#define MQTT_HOST        "192.168.1.100"    // ganti dengan IP laptop yang menjalankan broker Mosquitto
#define MQTT_PORT        1883
#define MQTT_CLIENT_ID   "ttu-gateway-loop"

#define SERIAL_BAUD      115200

#define MIN_PAYLOAD_SIZE   7
#define MAX_PAYLOAD_SIZE   32
#define BLE_PRIMARY_MAX    24   // sama dengan Node_BLE.ino: bagian paket di iklan utama; sisa paket 32 B ada di scan response

#define PIN_LORA_SCK     18
#define PIN_LORA_MISO    19
#define PIN_LORA_MOSI    23
#define PIN_LORA_SS      5
#define PIN_LORA_RST     4     // sama dengan Gateway_FreeRTOS.ino (papan gateway yang sama)
#define PIN_LORA_DIO0    26
#define PIN_SDA          21
#define PIN_SCL          22

#define LORA_FREQ        433E6
#define LORA_SF          7
#define LORA_BW          125E3
#define LORA_CR          5
#define LORA_SYNC        0x12

#define BLE_SCAN_INTERVAL_MS     100
#define BLE_SCAN_WINDOW_MS       100
#define BLE_SCAN_RSP_TIMEOUT_MS  100

// ============================ 2. TIPE DATA ===================================
enum : uint8_t { SRC_LORA1 = 0, SRC_BLE1, SRC_BLE2, SRC_BLE3, SRC_BLE4, SRC_COUNT };
static const char* const SRC_NAME[SRC_COUNT]  = {"LORA1", "BLE1", "BLE2", "BLE3", "BLE4"};
static const char* const SRC_TOPIC[SRC_COUNT] = {"ttu/gw/lora1", "ttu/gw/ble1", "ttu/gw/ble2",
                                                 "ttu/gw/ble3", "ttu/gw/ble4"};

struct Packet { uint8_t src; uint16_t seq; uint32_t ts; uint32_t rxMs; uint8_t len; };

static inline void decodePacket(const uint8_t* b, uint8_t& id, uint16_t& seq, uint32_t& ts) {
  id  = b[0];
  seq = (uint16_t)(b[1] | (b[2] << 8));
  ts  = (uint32_t)b[3] | ((uint32_t)b[4] << 8) | ((uint32_t)b[5] << 16) | ((uint32_t)b[6] << 24);
}

#define INC(x) ((x) = (x) + 1)
static volatile uint32_t stRx[SRC_COUNT];
static volatile uint32_t stOverwrite[SRC_COUNT];
static volatile uint32_t stPub[SRC_COUNT];
static volatile uint32_t stLatSum[SRC_COUNT];
static volatile uint32_t stLatMax[SRC_COUNT];
static volatile uint8_t  stLastLen[SRC_COUNT];
static volatile uint32_t stPubFail, stLoraBadLen, stBadId;
static volatile uint32_t stRestOk, stRestMissing;
static volatile bool     stWifiOk, stMqttOk;

static volatile uint32_t dgRawTot[SRC_COUNT];
static volatile uint32_t dgRawWin[SRC_COUNT];
static volatile uint32_t dgLastRaw[SRC_COUNT];
static volatile uint32_t dgMaxGapWin[SRC_COUNT];
static volatile int32_t  dgRssiSumWin[SRC_COUNT];
static volatile int32_t  dgRssiMinWin[SRC_COUNT];
static volatile uint32_t dgSeqMissTot[SRC_COUNT];

// ============================ 3. MAILBOX (satu paket per sumber) ==============
struct Slot { bool full; Packet p; };
static Slot slot[SRC_COUNT];
static portMUX_TYPE slotMux = portMUX_INITIALIZER_UNLOCKED;

// ============================ FUNGSI LOG ======================================
#if DEBUG_MODE
static void dbgStatus(const char* fmt, ...) {
  char buf[160];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  Serial.print("[STATUS] "); Serial.println(buf);
}
#else
static inline void dbgStatus(const char* fmt, ...) { (void)fmt; }
#endif

#if DEBUG_VERBOSE
static void dbgPacket(const char* fmt, ...) {
  char buf[160];
  va_list ap; va_start(ap, fmt); vsnprintf(buf, sizeof(buf), fmt, ap); va_end(ap);
  Serial.print("[PKT] "); Serial.println(buf);
}
#else
static inline void dbgPacket(const char* fmt, ...) { (void)fmt; }
#endif

#if DATA_LOG_MODE
static void logPacket(uint8_t src, uint16_t seq, uint32_t rx, uint32_t tSend, uint32_t lat, uint8_t len) {
  Serial.printf("D,%s,%u,%lu,%lu,%lu,%u\n", SRC_NAME[src], (unsigned)seq,
                (unsigned long)rx, (unsigned long)tSend, (unsigned long)lat, (unsigned)len);
}
static void logSummary() {
  Serial.printf("S,%lu", (unsigned long)millis());
  for (int i = 0; i < SRC_COUNT; i++)
    Serial.printf(",%s=%lu/%lu/%lu/len%u", SRC_NAME[i], (unsigned long)stRx[i],
                  (unsigned long)stPub[i], (unsigned long)stOverwrite[i], (unsigned)stLastLen[i]);
  Serial.printf(",pubfail=%lu,lorabadlen=%lu,badid=%lu,restok=%lu,restmiss=%lu,arch=BM\n",
                (unsigned long)stPubFail, (unsigned long)stLoraBadLen, (unsigned long)stBadId,
                (unsigned long)stRestOk, (unsigned long)stRestMissing);
}
#else
static inline void logPacket(uint8_t, uint16_t, uint32_t, uint32_t, uint32_t, uint8_t) {}
static inline void logSummary() {}
#endif

#if DIAG_MODE
static void logDiag() {
  Serial.printf("G,%lu", (unsigned long)millis());
  for (int i = SRC_BLE1; i < SRC_COUNT; i++) {
    uint32_t n = dgRawWin[i];
    int32_t avg = n ? (int32_t)(dgRssiSumWin[i] / (int32_t)n) : 0;
    int32_t mn  = n ? dgRssiMinWin[i] : 0;
    Serial.printf(",%s=%lu/%lu/%lu/%ld/%ld/%lu", SRC_NAME[i], (unsigned long)n,
                  (unsigned long)dgRawTot[i], (unsigned long)dgMaxGapWin[i],
                  (long)avg, (long)mn, (unsigned long)dgSeqMissTot[i]);
    dgRawWin[i] = 0; dgMaxGapWin[i] = 0; dgRssiSumWin[i] = 0; dgRssiMinWin[i] = 0;
  }
  Serial.println();
}
#else
static inline void logDiag() {}
#endif

static void slotPut(const Packet& p) {
  bool ow = false;
  portENTER_CRITICAL(&slotMux);
  if (slot[p.src].full) { INC(stOverwrite[p.src]); ow = true; }
  slot[p.src].p = p;
  slot[p.src].full = true;
  portEXIT_CRITICAL(&slotMux);
  // Dicetak di luar critical section (Serial di dalam spinlock dapat membuat sistem
  // berhenti) dan hanya saat DEBUG_VERBOSE, karena pada beban tinggi kejadian ini sering.
  if (ow) dbgPacket("Slot %s PENUH -> paket lama tertimpa (seq baru=%u)", SRC_NAME[p.src], p.seq);
}

static bool slotTake(uint8_t s, Packet& out) {
  bool got = false;
  portENTER_CRITICAL(&slotMux);
  if (slot[s].full) { out = slot[s].p; slot[s].full = false; got = true; }
  portEXIT_CRITICAL(&slotMux);
  return got;
}

// ============================ 4. OBJEK GLOBAL ================================
static WiFiClient   netClient;
static PubSubClient mqtt(netClient);
static Adafruit_SSD1306 display(128, 64, &Wire, -1);
static bool oledOk = false;

static volatile bool     loraFlag  = false;
static volatile uint32_t loraIsrMs = 0;
static int32_t bleLastSeq[SRC_COUNT];

// ============================ 5. ISR & CALLBACK ==============================
void IRAM_ATTR loraIsr() {
  loraIsrMs = millis();
  loraFlag  = true;
}

class BleScanCb : public NimBLEScanCallbacks {
 public:
  void onResult(const NimBLEAdvertisedDevice* dev) override {
    uint32_t rx = millis();
    uint8_t count = dev->getManufacturerDataCount();
    if (count == 0) return;

    int priIdx = -1; size_t priSize = 0;
    for (uint8_t i = 0; i < count; i++) {
      auto md = dev->getManufacturerData(i);
      if (md.size() >= (size_t)(2 + MIN_PAYLOAD_SIZE) &&
          (uint8_t)md[0] == 0xFF && (uint8_t)md[1] == 0xFF && md.size() > priSize) {
        priSize = md.size();
        priIdx = i;
      }
    }
    if (priIdx < 0) return;

    auto mdPri = dev->getManufacturerData((uint8_t)priIdx);
    uint8_t actualLen = (uint8_t)(mdPri.size() - 2);
    if (actualLen > MAX_PAYLOAD_SIZE) actualLen = MAX_PAYLOAD_SIZE;

    uint8_t pri[MAX_PAYLOAD_SIZE];
    for (int i = 0; i < actualLen && i < (int)(mdPri.size() - 2); i++) pri[i] = (uint8_t)mdPri[2 + i];
    uint8_t id; uint16_t seq; uint32_t ts;
    decodePacket(pri, id, seq, ts);

    if (id < 1 || id > 4) { INC(stBadId); dbgStatus("BLE: ID paket tidak valid (%u)", id); return; }
    uint8_t s = id;
#if DIAG_MODE
    {
      int32_t rssi = dev->getRSSI();
      uint32_t gap = (dgLastRaw[s] != 0) ? rx - dgLastRaw[s] : 0;
      if (gap < 30000 && gap > dgMaxGapWin[s]) dgMaxGapWin[s] = gap;
      dgLastRaw[s] = rx;
      INC(dgRawTot[s]);
      if (dgRawWin[s] == 0 || rssi < dgRssiMinWin[s]) dgRssiMinWin[s] = rssi;
      INC(dgRawWin[s]);
      dgRssiSumWin[s] = dgRssiSumWin[s] + rssi;
    }
#endif
    // Paket 32 B: iklan utama berisi BLE_PRIMARY_MAX (24 B) dan sisanya ada di scan
    // response (FF FF id seqLo seqHi sisa...). Paket dianggap diterima bila kedua
    // bagian sampai dengan ID dan seq yang sama. Bila scan response tidak ada, iklan
    // tetap dihitung pada diagnostik, dan salinan iklan berikutnya masih dapat
    // melengkapi paket tersebut.
    if (actualLen == BLE_PRIMARY_MAX) {
      uint8_t restLen = 0;
      for (uint8_t i = 0; i < count; i++) {
        if ((int)i == priIdx) continue;
        auto md = dev->getManufacturerData(i);
        if (md.size() >= 6 && (uint8_t)md[0] == 0xFF && (uint8_t)md[1] == 0xFF &&
            (uint8_t)md[2] == id &&
            (uint16_t)((uint8_t)md[3] | ((uint16_t)(uint8_t)md[4] << 8)) == seq) {
          restLen = (uint8_t)(md.size() - 5);
          break;
        }
      }
      if (restLen == 0) { INC(stRestMissing); return; }
      INC(stRestOk);
      actualLen = (uint8_t)(actualLen + restLen);
      if (actualLen > MAX_PAYLOAD_SIZE) actualLen = MAX_PAYLOAD_SIZE;
    }

    if ((int32_t)seq == bleLastSeq[s]) return;   // salinan iklan dengan seq yang sama diabaikan
    if (bleLastSeq[s] >= 0 && (int32_t)seq > bleLastSeq[s] + 1)
      dgSeqMissTot[s] = dgSeqMissTot[s] + (uint32_t)((int32_t)seq - bleLastSeq[s] - 1);
    bleLastSeq[s] = seq;
    Packet p = { s, seq, ts, rx, actualLen };
    INC(stRx[s]); stLastLen[s] = actualLen;
    dbgPacket("BLE%d RX seq=%u rx=%lu len=%dB", s, seq, (unsigned long)rx, actualLen);
    slotPut(p);
  }
};
static BleScanCb bleCb;

// ============================ 6. LAYANAN DI DALAM LOOP ========================
static void serviceLoRa() {
  if (!loraFlag && digitalRead(PIN_LORA_DIO0) == LOW) return;
  uint32_t rx = loraFlag ? loraIsrMs : millis();
  loraFlag = false;

  int len = LoRa.parsePacket();
  if (len >= MIN_PAYLOAD_SIZE && len <= MAX_PAYLOAD_SIZE) {
    uint8_t buf[MAX_PAYLOAD_SIZE];
    for (int i = 0; i < len; i++) buf[i] = (uint8_t)LoRa.read();
    uint8_t id; uint16_t seq; uint32_t ts;
    decodePacket(buf, id, seq, ts);
    if (id != SRC_LORA1) {
      INC(stBadId);
      dbgStatus("LoRa: ID paket tidak terduga (%u), paket dibuang", id);
    } else {
      Packet p; p.src = SRC_LORA1; p.rxMs = rx; p.seq = seq; p.ts = ts; p.len = (uint8_t)len;
      INC(stRx[SRC_LORA1]); stLastLen[SRC_LORA1] = (uint8_t)len;
      dbgPacket("LoRa RX seq=%u rx=%lu len=%dB", p.seq, (unsigned long)rx, len);
      slotPut(p);
    }
  } else if (len > 0) {
    while (LoRa.available()) LoRa.read();
    INC(stLoraBadLen);
    dbgStatus("LoRa terima panjang di luar rentang wajar (%d byte, harus %d-%d)",
              len, MIN_PAYLOAD_SIZE, MAX_PAYLOAD_SIZE);
  }
  LoRa.receive();
}

static bool mqttEnsure() {
  if (WiFi.status() != WL_CONNECTED) {
    if (stWifiOk) dbgStatus("Wi-Fi TERPUTUS, mencoba sambung ulang...");
    stWifiOk = false; stMqttOk = false;
    static uint32_t lastTry = 0;
    if (millis() - lastTry > 8000) { lastTry = millis(); WiFi.disconnect(); WiFi.begin(WIFI_SSID, WIFI_PASSWORD); }
    return false;
  }
  if (!stWifiOk) { stWifiOk = true; dbgStatus("Wi-Fi TERSAMBUNG, IP=%s", WiFi.localIP().toString().c_str()); }
  if (mqtt.connected()) return true;
  stMqttOk = false;
  static uint32_t lastConn = 0;
  if (millis() - lastConn < 2000) return false;
  lastConn = millis();
  if (mqtt.connect(MQTT_CLIENT_ID)) {
    netClient.setNoDelay(true);
    stMqttOk = true;
    dbgStatus("MQTT TERSAMBUNG ke %s:%d", MQTT_HOST, MQTT_PORT);
    return true;
  }
  dbgStatus("MQTT gagal connect (rc=%d)", mqtt.state());
  return false;
}

// Aturan publish sama dengan TaskMQTT di Gateway_FreeRTOS.ino, sehingga yang berbeda
// hanya susunan programnya:
//  - Bila Wi-Fi atau MQTT belum siap, paket tidak dibuang dan tetap menunggu di
//    mailbox (pada versi FreeRTOS, paket menunggu di antrean). Selama menunggu,
//    paket baru dari sumber yang sama menimpanya dan dihitung "tertimpa".
//  - Bila publish gagal, paket dikirim ulang paling banyak 3 kali sebelum
//    dihitung sebagai pubfail.
static uint8_t pubTries[SRC_COUNT];

static void slotPutBack(const Packet& p) {
  // Kembalikan paket yang belum terkirim ke mailbox. Bila callback sudah
  // mengisi mailbox dengan paket yang lebih baru, paket lama ini hilang (tertimpa).
  portENTER_CRITICAL(&slotMux);
  if (slot[p.src].full) INC(stOverwrite[p.src]);
  else { slot[p.src].p = p; slot[p.src].full = true; }
  portEXIT_CRITICAL(&slotMux);
}

static void servicePublish() {
  for (uint8_t s = 0; s < SRC_COUNT; s++) {
    Packet p;
    if (!slotTake(s, p)) continue;

    if (!mqttEnsure()) {          // belum tersambung: paket kembali menunggu di mailbox
      slotPutBack(p);
      return;
    }

    char payload[112];
    snprintf(payload, sizeof(payload), "{\"src\":\"%s\",\"seq\":%u,\"ts\":%lu,\"rx\":%lu,\"len\":%u}",
             SRC_NAME[p.src], (unsigned)p.seq, (unsigned long)p.ts, (unsigned long)p.rxMs, (unsigned)p.len);

    bool ok = mqtt.publish(SRC_TOPIC[p.src], payload);
    uint32_t tSend = millis();
    if (ok) {
      uint32_t lat = tSend - p.rxMs;   // termasuk waktu menunggu di mailbox
      INC(stPub[p.src]);
      stLatSum[p.src] = stLatSum[p.src] + lat;
      if (lat > stLatMax[p.src]) stLatMax[p.src] = lat;
      logPacket(p.src, p.seq, p.rxMs, tSend, lat, p.len);
      pubTries[s] = 0;
    } else {
      dbgStatus("Publish GAGAL (src=%s seq=%u, percobaan ke-%d)", SRC_NAME[p.src], p.seq, pubTries[s] + 1);
      if (++pubTries[s] >= 3) { INC(stPubFail); pubTries[s] = 0; }
      else { slotPutBack(p); mqtt.disconnect(); return; }
    }
  }
}

static void oledDraw() {
  uint32_t owAll = 0, pubAll = 0, latAll = 0;
  for (int i = 0; i < SRC_COUNT; i++) { owAll += stOverwrite[i]; pubAll += stPub[i]; latAll += stLatSum[i]; }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.printf("GW-LOOP W:%c M:%c\n", stWifiOk ? 'Y' : 'N', stMqttOk ? 'Y' : 'N');
  display.printf("LoRa rx%lu(%uB)\n", (unsigned long)stRx[0], (unsigned)stLastLen[0]);
  display.printf("B1%lu(%uB) B2%lu(%uB)\n", (unsigned long)stRx[1], (unsigned)stLastLen[1],
                 (unsigned long)stRx[2], (unsigned)stLastLen[2]);
  display.printf("B3%lu(%uB) B4%lu(%uB)\n", (unsigned long)stRx[3], (unsigned)stLastLen[3],
                 (unsigned long)stRx[4], (unsigned)stLastLen[4]);
  display.printf("overwrite %lu\n", (unsigned long)owAll);
  display.printf("pubfail %lu\n", (unsigned long)stPubFail);
  display.printf("lat avg %lu ms\n", (unsigned long)(pubAll ? latAll / pubAll : 0));
  display.printf("frag ok%lu mis%lu\n", (unsigned long)stRestOk, (unsigned long)stRestMissing);
  display.display();
}

// ============================ 7. SETUP & LOOP =================================
static void fatal(const char* msg) {
  Serial.println(msg);
  if (oledOk) { display.clearDisplay(); display.setCursor(0, 0); display.println(msg); display.display(); }
  while (true) delay(1000);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  dbgStatus("=== Gateway Sekuensial (tanpa FreeRTOS): boot (payload-agnostic) ===");

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  dbgStatus("OLED init: %s", oledOk ? "OK" : "TIDAK TERDETEKSI");

  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_SS);
  LoRa.setPins(PIN_LORA_SS, PIN_LORA_RST, PIN_LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) fatal("LoRa init GAGAL - cek wiring SPI/RST/DIO0");
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setPreambleLength(8);
  LoRa.setSyncWord(LORA_SYNC);
  LoRa.enableCrc();
  LoRa.receive();
  pinMode(PIN_LORA_DIO0, INPUT);
  dbgStatus("LoRa init OK, mode RX continuous (RST=GPIO%d)", PIN_LORA_RST);

  WiFi.mode(WIFI_STA);
  WiFi.persistent(false);
  WiFi.setAutoReconnect(true);
#if WIFI_NO_SLEEP
  WiFi.setSleep(false);   // sama dengan Gateway_FreeRTOS.ino
#endif
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  dbgStatus("Menyambung ke Wi-Fi SSID=%s ...", WIFI_SSID);
  uint32_t t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) delay(200);
  stWifiOk = (WiFi.status() == WL_CONNECTED);
  dbgStatus("Wi-Fi: %s  IP: %s", stWifiOk ? "OK" : "BELUM TERSAMBUNG", WiFi.localIP().toString().c_str());

  mqtt.setServer(MQTT_HOST, MQTT_PORT);
  mqtt.setBufferSize(256);
  mqtt.setKeepAlive(30);
  mqtt.setSocketTimeout(2);

  for (int i = 0; i < SRC_COUNT; i++) bleLastSeq[i] = -1;
  NimBLEDevice::init("");
  NimBLEScan* sc = NimBLEDevice::getScan();
  sc->setScanCallbacks(&bleCb, true);
  sc->setDuplicateFilter(0);
  sc->setActiveScan(true);     // pemindaian aktif, sama dengan Gateway_FreeRTOS.ino
  sc->setScanResponseTimeout(BLE_SCAN_RSP_TIMEOUT_MS);
  sc->setInterval(BLE_SCAN_INTERVAL_MS);
  sc->setWindow(BLE_SCAN_WINDOW_MS);
  sc->setMaxResults(0);
#if COEX_PREFER_BT
  esp_err_t ce = esp_coex_preference_set(ESP_COEX_PREFER_BT);
  dbgStatus("Coexistence: PREFER_BT (%s)", ce == ESP_OK ? "OK" : "GAGAL");
#endif
  dbgStatus("Radio: WIFI_NO_SLEEP=%d COEX_PREFER_BT=%d", WIFI_NO_SLEEP, COEX_PREFER_BT);
  sc->start(0, false, true);
  dbgStatus("BLE scanner init OK (ACTIVE, scanRspTimeout=%dms, interval=%dms window=%dms)",
            BLE_SCAN_RSP_TIMEOUT_MS, BLE_SCAN_INTERVAL_MS, BLE_SCAN_WINDOW_MS);

  attachInterrupt(digitalPinToInterrupt(PIN_LORA_DIO0), loraIsr, RISING);
  dbgStatus("Gateway BERJALAN. Format log data: D,<src>,<seq>,<T_rx>,<T_send>,<lat_ms>,<len>");
}

void loop() {
  static uint32_t lastOled = 0, lastSum = 0, lastScan = 0;

  serviceLoRa();
  servicePublish();
  if (mqtt.connected()) mqtt.loop();

  uint32_t now = millis();
  if (now - lastOled >= 500)   { lastOled = now; if (oledOk) oledDraw(); }
  if (now - lastSum  >= 10000) { lastSum  = now; logSummary(); logDiag(); }
  if (now - lastScan >= 2000) {
    lastScan = now;
    NimBLEScan* sc = NimBLEDevice::getScan();
    if (!sc->isScanning()) { sc->start(0, false, true); dbgStatus("Scan BLE mati, distart ulang"); }
  }

  // mqtt.connect() dapat menahan loop sampai 2 s berulang kali saat Wi-Fi atau MQTT
  // terputus. yield() memberi kesempatan task idle berjalan agar watchdog tidak
  // mereset ESP32.
  yield();
}
