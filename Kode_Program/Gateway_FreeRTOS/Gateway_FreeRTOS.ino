/*
 * ============================================================================
 *  Gateway IoT Multiprotokol - versi FreeRTOS
 *  ESP32 + LoRa SX1278 (SPI) + OLED SSD1306 (I2C) + BLE internal + Wi-Fi/MQTT
 *
 *  Penelitian: Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32
 *  dengan Implementasi FreeRTOS (Teknik Elektro, UKSW)
 * ============================================================================
 *  Topologi
 *    Gateway menerima data dari lima node terpisah: satu Node_LoRa (ID 0) dan
 *    empat Node_BLE (ID 1-4). Nama sumber: LORA1, BLE1, BLE2, BLE3, BLE4.
 *
 *  Susunan program
 *    TaskLoRa (inti 0, prioritas 4)  menerima paket LoRa melalui interupsi DIO0.
 *    TaskBLE  (inti 0, prioritas 3)  memproses hasil pemindaian BLE.
 *    TaskMQTT (inti 1, prioritas 2)  mengirim paket ke broker satu per satu.
 *    TaskOLED (inti 1, prioritas 1)  memperbarui OLED dan mencetak ringkasan.
 *    Callback pemindaian BLE -> bleRawQ (128) -> TaskBLE -> dataQ (384) -> TaskMQTT
 *    TaskLoRa ------------------------------------------> dataQ
 *
 *  Ukuran payload
 *    Gateway tidak perlu diatur untuk ukuran payload tertentu. Panjang paket
 *    LoRa diambil dari LoRa.parsePacket() dan hanya diperiksa terhadap rentang
 *    7-32 byte. Pada BLE, ID, nomor urut (seq), dan timestamp dibaca dari
 *    Manufacturer Data terbesar pada satu hasil pemindaian, yaitu iklan utama.
 *    Setiap node boleh memakai ukuran payload yang berbeda.
 *
 *  Pemindaian BLE
 *    Pemindaian aktif dengan interval dan jendela 100 ms, filter duplikat
 *    dimatikan, dan batas tunggu scan response 100 ms. Paket 32 byte dibagi
 *    menjadi iklan utama (24 byte) dan scan response (8 byte); paket ini baru
 *    dihitung diterima bila kedua bagiannya tertangkap.
 *
 *  Keluaran Serial (dibaca Pengujian/logger.py)
 *    D,<src>,<seq>,<T_receive>,<T_send>,<latensi_ms>,<len>   setiap paket terkirim
 *    S,<ms>,...   ringkasan penghitung setiap 10 s
 *    G,<ms>,...   diagnostik radio BLE setiap 10 s (DIAG_MODE)
 *
 *  Pustaka: LoRa (Sandeep Mistry), NimBLE-Arduino (h2zero, >= 2.5.0),
 *           PubSubClient (Nick O'Leary), Adafruit SSD1306 + Adafruit GFX
 *  Diuji dengan ESP32 Arduino core 3.0.7.
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
#define DIAG_MODE       1   // baris G setiap 10 s: jumlah iklan BLE, celah terlama, dan RSSI

// ============================ PENGATURAN RADIO ===============================
// Nilainya harus sama dengan Gateway_BareMetal.ino agar kedua versi dapat dibandingkan.
#define WIFI_NO_SLEEP   1   // 1 = WiFi.setSleep(false) (dipakai pada pengujian), 0 = modem-sleep bawaan
#define COEX_PREFER_BT  0   // 1 = prioritas radio untuk Bluetooth (uji pendahuluan P4), 0 = seimbang (bawaan)

// ============================ 1. KONFIGURASI =================================
#define WIFI_SSID        "NAMA_WIFI"        // ganti dengan nama jaringan Wi-Fi
#define WIFI_PASSWORD    "KATA_SANDI_WIFI"  // ganti dengan kata sandi Wi-Fi
#define MQTT_HOST        "192.168.1.100"    // ganti dengan IP laptop yang menjalankan broker Mosquitto
#define MQTT_PORT        1883
#define MQTT_CLIENT_ID   "ttu-gateway-rtos"

#define SERIAL_BAUD      115200

#define MIN_PAYLOAD_SIZE   7    // ID (1) + seq (2) + timestamp (4)
#define MAX_PAYLOAD_SIZE   32   // payload terbesar yang diuji
#define BLE_PRIMARY_MAX    24   // sama dengan Node_BLE.ino: bagian paket di iklan utama; sisa paket 32 B ada di scan response

// Pin
#define PIN_LORA_SCK     18
#define PIN_LORA_MISO    19
#define PIN_LORA_MOSI    23
#define PIN_LORA_SS      5
#define PIN_LORA_RST     4    // GPIO14 tidak dipakai karena menjadi pin tombol pada node
#define PIN_LORA_DIO0    26
#define PIN_SDA          21
#define PIN_SCL          22

// LoRa (sama dengan Node_LoRa)
#define LORA_FREQ        433E6
#define LORA_SF          7
#define LORA_BW          125E3
#define LORA_CR          5
#define LORA_SYNC        0x12

// BLE scan
#define BLE_SCAN_INTERVAL_MS   100
#define BLE_SCAN_WINDOW_MS     100
#define BLE_SCAN_RSP_TIMEOUT_MS  100   // bawaan pustaka 10.240 ms terlalu lama untuk iklan setiap 20 ms

// FreeRTOS
#define DATA_QUEUE_LEN     384
#define BLE_RAW_QUEUE_LEN  128
#define CORE_ACQ           0
#define CORE_NET           1
#define PRIO_LORA          4
#define PRIO_BLE           3
#define PRIO_MQTT          2
#define PRIO_OLED          1

// ============================ 2. TIPE DATA ===================================
enum : uint8_t { SRC_LORA1 = 0, SRC_BLE1, SRC_BLE2, SRC_BLE3, SRC_BLE4, SRC_COUNT };
static const char* const SRC_NAME[SRC_COUNT]  = {"LORA1", "BLE1", "BLE2", "BLE3", "BLE4"};
static const char* const SRC_TOPIC[SRC_COUNT] = {"ttu/gw/lora1", "ttu/gw/ble1", "ttu/gw/ble2",
                                                 "ttu/gw/ble3", "ttu/gw/ble4"};

struct Packet  { uint8_t src; uint16_t seq; uint32_t ts; uint32_t rxMs; uint8_t len; };
struct BleRaw  { uint8_t id; uint16_t seq; uint32_t ts; uint32_t rxMs; uint8_t len; };

// Membaca 7 byte pertama (ID, seq, timestamp); byte pengisi sesudahnya diabaikan.
static inline void decodePacket(const uint8_t* b, uint8_t& id, uint16_t& seq, uint32_t& ts) {
  id  = b[0];
  seq = (uint16_t)(b[1] | (b[2] << 8));
  ts  = (uint32_t)b[3] | ((uint32_t)b[4] << 8) | ((uint32_t)b[5] << 16) | ((uint32_t)b[6] << 24);
}

#define INC(x) ((x) = (x) + 1)
static volatile uint32_t stRx[SRC_COUNT];
static volatile uint32_t stDropQ[SRC_COUNT];
static volatile uint32_t stPub[SRC_COUNT];
static volatile uint32_t stLatSum[SRC_COUNT];
static volatile uint32_t stLatMax[SRC_COUNT];
static volatile uint8_t  stLastLen[SRC_COUNT];   // ukuran payload terakhir setiap sumber (diagnostik)
static volatile uint32_t stPubFail, stBleRawDrop, stQueueMax, stLoraBadLen, stBadId;
static volatile uint32_t stRestOk, stRestMissing;   // diagnostik fragmentasi BLE
static volatile bool     stWifiOk, stMqttOk;

// Diagnostik radio BLE (DIAG_MODE), ditulis di onResult() pada task host NimBLE
static volatile uint32_t dgRawTot[SRC_COUNT];   // semua iklan yang sampai di onResult(), termasuk salinan seq yang sama
static volatile uint32_t dgRawWin[SRC_COUNT];   // sama, tetapi untuk jendela 10 s yang sedang berjalan
static volatile uint32_t dgLastRaw[SRC_COUNT];  // millis() iklan terakhir
static volatile uint32_t dgMaxGapWin[SRC_COUNT];// celah terlama antariklan dalam jendela 10 s
static volatile int32_t  dgRssiSumWin[SRC_COUNT];
static volatile int32_t  dgRssiMinWin[SRC_COUNT];
static volatile uint32_t dgSeqMissTot[SRC_COUNT];// seq yang tidak tertangkap radio gateway (sebelum MQTT)

// ============================ 3. OBJEK GLOBAL ================================
static QueueHandle_t dataQ, bleRawQ;
static TaskHandle_t  hLoRaTask = nullptr;
static volatile uint32_t loraIsrMs = 0;

static WiFiClient   netClient;
static PubSubClient mqtt(netClient);
static Adafruit_SSD1306 display(128, 64, &Wire, -1);
static bool oledOk = false;

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
                  (unsigned long)stPub[i], (unsigned long)stDropQ[i], (unsigned)stLastLen[i]);
  Serial.printf(",qmax=%lu,rawdrop=%lu,pubfail=%lu,lorabadlen=%lu,badid=%lu,restok=%lu,restmiss=%lu,arch=RTOS\n",
                (unsigned long)stQueueMax, (unsigned long)stBleRawDrop, (unsigned long)stPubFail,
                (unsigned long)stLoraBadLen, (unsigned long)stBadId,
                (unsigned long)stRestOk, (unsigned long)stRestMissing);
}
#else
static inline void logPacket(uint8_t, uint16_t, uint32_t, uint32_t, uint32_t, uint8_t) {}
static inline void logSummary() {}
#endif

#if DIAG_MODE
// G,<ms>,BLEn=<rawWin>/<rawTot>/<maxGapWin_ms>/<rssiAvg>/<rssiMin>/<seqMissTot>,...
// Dicetak pada baris tersendiri agar format baris S tidak berubah.
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

// ============================ 4. ISR & CALLBACK ==============================
void IRAM_ATTR loraIsr() {
  loraIsrMs = millis();
  if (hLoRaTask) {
    BaseType_t hpw = pdFALSE;
    vTaskNotifyGiveFromISR(hLoRaTask, &hpw);
    if (hpw == pdTRUE) portYIELD_FROM_ISR();
  }
}

// Seq terakhir yang berhasil masuk antrean untuk setiap sumber. Dipakai untuk membuang
// salinan iklan dengan seq yang sama (interval iklan 20 ms menghasilkan 20-100 salinan per seq).
static int32_t cbLastQueued[SRC_COUNT] = {-1, -1, -1, -1, -1};

class BleScanCb : public NimBLEScanCallbacks {
 public:
  void onResult(const NimBLEAdvertisedDevice* dev) override {
    uint32_t rx = millis();
    uint8_t count = dev->getManufacturerDataCount();
    if (count == 0) return;

    // Cari Manufacturer Data terbesar yang diawali penanda 0xFFFF. Struktur ini
    // adalah iklan utama, karena iklan utama selalu lebih panjang atau sama dengan
    // scan response.
    int priIdx = -1; size_t priSize = 0;
    for (uint8_t i = 0; i < count; i++) {
      auto md = dev->getManufacturerData(i);
      if (md.size() >= (size_t)(2 + MIN_PAYLOAD_SIZE) &&
          (uint8_t)md[0] == 0xFF && (uint8_t)md[1] == 0xFF && md.size() > priSize) {
        priSize = md.size();
        priIdx = i;
      }
    }
    if (priIdx < 0) return;   // bukan paket dari node penelitian ini

    auto mdPri = dev->getManufacturerData((uint8_t)priIdx);
    uint8_t actualLen = (uint8_t)(mdPri.size() - 2);   // panjang bagian paket di iklan utama
    if (actualLen > MAX_PAYLOAD_SIZE) actualLen = MAX_PAYLOAD_SIZE;   // batasi agar tidak melebihi buffer

    uint8_t pri[MAX_PAYLOAD_SIZE];
    for (int i = 0; i < actualLen && i < (int)(mdPri.size() - 2); i++) pri[i] = (uint8_t)mdPri[2 + i];
    uint8_t id; uint16_t seq; uint32_t ts;
    decodePacket(pri, id, seq, ts);

#if DIAG_MODE
    if (id >= SRC_BLE1 && id < SRC_COUNT) {
      int32_t rssi = dev->getRSSI();
      uint32_t gap = (dgLastRaw[id] != 0) ? rx - dgLastRaw[id] : 0;
      // Celah lebih dari 30 s dianggap jeda antarsesi, bukan kehilangan di radio.
      if (gap < 30000 && gap > dgMaxGapWin[id]) dgMaxGapWin[id] = gap;
      dgLastRaw[id] = rx;
      INC(dgRawTot[id]);
      if (dgRawWin[id] == 0 || rssi < dgRssiMinWin[id]) dgRssiMinWin[id] = rssi;
      INC(dgRawWin[id]);
      dgRssiSumWin[id] = dgRssiSumWin[id] + rssi;
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

    // Salinan seq yang sama dibuang sebelum masuk antrean (diagnostik di atas tetap
    // menghitungnya). taskBle juga membuang duplikat sebagai pengaman kedua.
    bool idOk = (id >= SRC_BLE1 && id < SRC_COUNT);
    if (idOk && (int32_t)seq == cbLastQueued[id]) return;

    BleRaw r; r.id = id; r.seq = seq; r.ts = ts; r.rxMs = rx; r.len = actualLen;
    if (xQueueSend(bleRawQ, &r, 0) != pdTRUE) {
      INC(stBleRawDrop);
      dbgStatus("bleRawQ PENUH, 1 iklan BLE dibuang");
    } else if (idOk) {
      cbLastQueued[id] = (int32_t)seq;
    }
  }
};
static BleScanCb bleCb;

// ============================ 5. TASK ========================================
static void taskLoRa(void*) {
  uint8_t buf[MAX_PAYLOAD_SIZE];
  for (;;) {
    uint32_t n = ulTaskNotifyTake(pdTRUE, pdMS_TO_TICKS(500));
    if (n == 0 && digitalRead(PIN_LORA_DIO0) == LOW) continue;
    uint32_t rx = (n > 0) ? loraIsrMs : millis();

    int len = LoRa.parsePacket();
    if (len >= MIN_PAYLOAD_SIZE && len <= MAX_PAYLOAD_SIZE) {
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
        if (xQueueSend(dataQ, &p, 0) != pdTRUE) {
          INC(stDropQ[SRC_LORA1]);
          dbgStatus("dataQ PENUH, 1 paket LoRa dibuang (seq=%u)", p.seq);
        }
      }
    } else if (len > 0) {
      while (LoRa.available()) LoRa.read();
      INC(stLoraBadLen);
      dbgStatus("LoRa terima panjang di luar rentang wajar (%d byte, harus %d-%d)",
                len, MIN_PAYLOAD_SIZE, MAX_PAYLOAD_SIZE);
    }
    LoRa.receive();
  }
}

static void taskBle(void*) {
  int32_t lastSeq[SRC_COUNT];
  for (int i = 0; i < SRC_COUNT; i++) lastSeq[i] = -1;

  NimBLEScan* sc = NimBLEDevice::getScan();
  sc->start(0, false, true);
  dbgStatus("TaskBLE: scan dimulai");
  uint32_t lastCheck = millis();
  BleRaw r;

  for (;;) {
    if (xQueueReceive(bleRawQ, &r, pdMS_TO_TICKS(1000)) == pdTRUE) {
      if (r.id < 1 || r.id > 4) {
        INC(stBadId);
        dbgStatus("BLE: ID paket tidak valid (%u), paket dibuang", r.id);
      } else {
        uint8_t s = r.id;   // ID 1-4 sama dengan SRC_BLE1-SRC_BLE4
        if ((int32_t)r.seq != lastSeq[s]) {
          // Seq yang melompat berarti ada paket yang sama sekali tidak tertangkap radio.
          // Seq yang mundur berarti node memulai sesi baru (seq kembali ke 0).
          if (lastSeq[s] >= 0 && (int32_t)r.seq > lastSeq[s] + 1)
            dgSeqMissTot[s] = dgSeqMissTot[s] + (uint32_t)((int32_t)r.seq - lastSeq[s] - 1);
          lastSeq[s] = r.seq;
          Packet p = { s, r.seq, r.ts, r.rxMs, r.len };
          INC(stRx[s]); stLastLen[s] = r.len;
          dbgPacket("BLE%d RX seq=%u rx=%lu len=%dB", s, r.seq, (unsigned long)r.rxMs, r.len);
          if (xQueueSend(dataQ, &p, 0) != pdTRUE) {
            INC(stDropQ[s]);
            dbgStatus("dataQ PENUH, 1 paket BLE%d dibuang (seq=%u)", s, r.seq);
          }
        }
        // Salinan iklan dengan seq yang sama diabaikan tanpa dicetak agar Serial
        // tidak penuh, karena satu seq disiarkan berkali-kali.
      }
    }
    if (millis() - lastCheck > 2000) {
      lastCheck = millis();
      if (!sc->isScanning()) { sc->start(0, false, true); dbgStatus("TaskBLE: scan mati, distart ulang"); }
    }
  }
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
  if (mqtt.connect(MQTT_CLIENT_ID)) {
    netClient.setNoDelay(true);
    stMqttOk = true;
    dbgStatus("MQTT TERSAMBUNG ke %s:%d", MQTT_HOST, MQTT_PORT);
    return true;
  }
  dbgStatus("MQTT gagal connect (rc=%d)", mqtt.state());
  return false;
}

static void taskMqtt(void*) {
  Packet p;
  bool have = false;
  uint8_t tries = 0;

  for (;;) {
    if (!have) {
      if (xQueueReceive(dataQ, &p, pdMS_TO_TICKS(100)) == pdTRUE) {
        have = true; tries = 0;
        uint32_t depth = uxQueueMessagesWaiting(dataQ) + 1;
        if (depth > stQueueMax) stQueueMax = depth;
      } else {
        if (mqtt.connected()) mqtt.loop(); else mqttEnsure();
        continue;
      }
    }

    if (!mqttEnsure()) { vTaskDelay(pdMS_TO_TICKS(500)); continue; }

    char payload[112];
    snprintf(payload, sizeof(payload), "{\"src\":\"%s\",\"seq\":%u,\"ts\":%lu,\"rx\":%lu,\"len\":%u}",
             SRC_NAME[p.src], (unsigned)p.seq, (unsigned long)p.ts, (unsigned long)p.rxMs, (unsigned)p.len);

    bool ok = mqtt.publish(SRC_TOPIC[p.src], payload);
    uint32_t tSend = millis();

    if (ok) {
      uint32_t lat = tSend - p.rxMs;
      INC(stPub[p.src]);
      stLatSum[p.src] = stLatSum[p.src] + lat;
      if (lat > stLatMax[p.src]) stLatMax[p.src] = lat;
      logPacket(p.src, p.seq, p.rxMs, tSend, lat, p.len);
      have = false;
    } else {
      dbgStatus("Publish GAGAL (src=%s seq=%u, percobaan ke-%d)", SRC_NAME[p.src], p.seq, tries + 1);
      if (++tries >= 3) { INC(stPubFail); have = false; }
      else mqtt.disconnect();
    }
    mqtt.loop();
    vTaskDelay(pdMS_TO_TICKS(1));
  }
}

static void oledDraw() {
  uint32_t dropAll = 0, pubAll = 0, latAll = 0;
  for (int i = 0; i < SRC_COUNT; i++) { dropAll += stDropQ[i]; pubAll += stPub[i]; latAll += stLatSum[i]; }
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.printf("GW-RTOS W:%c M:%c\n", stWifiOk ? 'Y' : 'N', stMqttOk ? 'Y' : 'N');
  display.printf("LoRa rx%lu(%uB)\n", (unsigned long)stRx[0], (unsigned)stLastLen[0]);
  display.printf("B1%lu(%uB) B2%lu(%uB)\n", (unsigned long)stRx[1], (unsigned)stLastLen[1],
                 (unsigned long)stRx[2], (unsigned)stLastLen[2]);
  display.printf("B3%lu(%uB) B4%lu(%uB)\n", (unsigned long)stRx[3], (unsigned)stLastLen[3],
                 (unsigned long)stRx[4], (unsigned)stLastLen[4]);
  display.printf("Q %u max %lu\n", (unsigned)uxQueueMessagesWaiting(dataQ), (unsigned long)stQueueMax);
  display.printf("dropQ %lu pf %lu\n", (unsigned long)dropAll, (unsigned long)stPubFail);
  display.printf("lat avg %lu ms\n", (unsigned long)(pubAll ? latAll / pubAll : 0));
  display.printf("frag ok%lu mis%lu\n", (unsigned long)stRestOk, (unsigned long)stRestMissing);
  display.display();
}

static void taskOled(void*) {
  uint32_t lastSum = millis();
  for (;;) {
    if (oledOk) oledDraw();
    if (millis() - lastSum >= 10000) { lastSum = millis(); logSummary(); logDiag(); }
    vTaskDelay(pdMS_TO_TICKS(500));
  }
}

// ============================ 6. SETUP =======================================
static void fatal(const char* msg) {
  Serial.println(msg);
  if (oledOk) { display.clearDisplay(); display.setCursor(0, 0); display.println(msg); display.display(); }
  while (true) delay(1000);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  dbgStatus("=== Gateway FreeRTOS: boot (payload-agnostic) ===");

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
  // Modem-sleep Wi-Fi dimatikan. Radio ESP32 dipakai bergantian oleh Wi-Fi dan BLE
  // (dokumentasi RF Coexistence ESP-IDF), dan siklus hemat daya Wi-Fi menambah
  // waktu radio dipakai Wi-Fi. Pengaturan yang sama dipakai pada versi Bare Metal.
#if WIFI_NO_SLEEP
  WiFi.setSleep(false);
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

  NimBLEDevice::init("");
  NimBLEScan* sc = NimBLEDevice::getScan();
  sc->setScanCallbacks(&bleCb, true);
  sc->setDuplicateFilter(0);
  // Pemindaian aktif selalu dipakai karena node dapat mengirim paket 32 B yang
  // memerlukan scan response.
  sc->setActiveScan(true);
  sc->setScanResponseTimeout(BLE_SCAN_RSP_TIMEOUT_MS);
  sc->setInterval(BLE_SCAN_INTERVAL_MS);
  sc->setWindow(BLE_SCAN_WINDOW_MS);
  sc->setMaxResults(0);
#if COEX_PREFER_BT
  esp_err_t ce = esp_coex_preference_set(ESP_COEX_PREFER_BT);
  dbgStatus("Coexistence: PREFER_BT (%s)", ce == ESP_OK ? "OK" : "GAGAL");
#endif
  dbgStatus("Radio: WIFI_NO_SLEEP=%d COEX_PREFER_BT=%d", WIFI_NO_SLEEP, COEX_PREFER_BT);
  dbgStatus("BLE scanner init OK (ACTIVE, scanRspTimeout=%dms, interval=%dms window=%dms)",
            BLE_SCAN_RSP_TIMEOUT_MS, BLE_SCAN_INTERVAL_MS, BLE_SCAN_WINDOW_MS);

  dataQ   = xQueueCreate(DATA_QUEUE_LEN, sizeof(Packet));
  bleRawQ = xQueueCreate(BLE_RAW_QUEUE_LEN, sizeof(BleRaw));
  if (!dataQ || !bleRawQ) fatal("Queue GAGAL dibuat");

  xTaskCreatePinnedToCore(taskLoRa, "TaskLoRa", 4096, nullptr, PRIO_LORA, &hLoRaTask, CORE_ACQ);
  xTaskCreatePinnedToCore(taskBle,  "TaskBLE",  6144, nullptr, PRIO_BLE,  nullptr,    CORE_ACQ);
  xTaskCreatePinnedToCore(taskMqtt, "TaskMQTT", 8192, nullptr, PRIO_MQTT, nullptr,    CORE_NET);
  xTaskCreatePinnedToCore(taskOled, "TaskOLED", 4096, nullptr, PRIO_OLED, nullptr,    CORE_NET);
  dbgStatus("4 task FreeRTOS dibuat (LoRa prio%d, BLE prio%d, MQTT prio%d, OLED prio%d)",
            PRIO_LORA, PRIO_BLE, PRIO_MQTT, PRIO_OLED);

  attachInterrupt(digitalPinToInterrupt(PIN_LORA_DIO0), loraIsr, RISING);
  dbgStatus("Gateway BERJALAN. Format log data: D,<src>,<seq>,<T_rx>,<T_send>,<lat_ms>,<len>");
}

void loop() {
  vTaskDelete(NULL);
}
