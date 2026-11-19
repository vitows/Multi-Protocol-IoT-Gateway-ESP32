/*
 * ============================================================================
 *  Node BLE - ESP32 (BLE internal) + OLED SSD1306 (I2C)
 *
 *  Penelitian: Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32
 *  dengan Implementasi FreeRTOS (Teknik Elektro, UKSW)
 * ============================================================================
 *  Sketch ini diunggah ke empat papan ESP32 dengan NODE_ID 1, 2, 3, dan 4.
 *  Bersama satu Node_LoRa (ID 0), terdapat lima node pengirim.
 *
 *  Tombol
 *    GPIO14, tekan singkat (< 700 ms) : ganti sesi (interval kirim 2000, 1000,
 *                                       500, 100 ms)
 *    GPIO14, tekan lama (>= 700 ms)   : ganti ukuran payload (7, 16, 32 byte)
 *    BOOT (GPIO0)                     : mulai pengujian; tahan 2 s untuk membatalkan
 *  Tombol GPIO14 dihubungkan ke GND dan memakai pull-up internal. Jangan
 *  hubungkan pin GPIO ke 5 V (batas tegangan pin ESP32 adalah 3,6 V).
 *
 *  Format paket
 *    Byte 0: ID (NODE_ID) | byte 1-2: seq (little-endian) |
 *    byte 3-6: timestamp millis() (little-endian) | byte pengisi sampai
 *    ukuran payload.
 *
 *  Pengiriman
 *    Node memakai non-connectable legacy advertising. Paket baru dibuat setiap
 *    interval kirim, lalu disiarkan berulang setiap interval advertising
 *    (ADV_INTERVAL_FIXED_MS). Satu iklan legacy memuat paling banyak 31 byte,
 *    sehingga paket 32 byte dibagi menjadi iklan utama (24 byte) dan scan
 *    response (8 byte). Scan response mengulang ID dan seq agar gateway dapat
 *    mencocokkan kedua bagian.
 *
 *  Alasan tidak memakai koneksi GATT
 *    Koneksi GATT dapat mengirim data lebih panjang, tetapi NimBLE-Arduino
 *    secara bawaan hanya mendukung tiga koneksi serentak, sedangkan gateway
 *    harus melayani empat node. Advertising juga tidak memerlukan pengelolaan
 *    koneksi di gateway.
 *
 *  Catatan NimBLE
 *    setManufacturerData() yang dipanggil berulang menambahkan data, bukan
 *    menggantinya. Karena itu data iklan dibangun ulang setiap kali paket
 *    berganti, lalu dipasang dengan setAdvertisementData().
 *
 *  Pustaka : NimBLE-Arduino (h2zero, >= 2.1.3), Adafruit SSD1306, Adafruit GFX
 *  Wiring  : OLED SDA -> GPIO21, SCL -> GPIO22, VCC -> 3V3
 *            Tombol: GPIO14 <-> GND
 * ============================================================================
 */
#include <Arduino.h>
#include <stdarg.h>
#include <vector>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <NimBLEDevice.h>
enum BtnEvent { BTN_NONE, BTN_SHORT, BTN_LONG };
// ============================ MODE DEBUG ======================================
#define DEBUG_MODE     1
#define DEBUG_VERBOSE  1

#define SERIAL_BAUD        115200

// ------------------------- KONFIGURASI TETAP (per board) ----------------------
#define NODE_ID             1          // isi 1, 2, 3, atau 4 sesuai papan
#define TEST_DURATION_MS   600000UL    // 10 menit
// ---- PENGUJIAN BERURUTAN -------------------------------------------------------
// Satu kali tekan BOOT menjalankan REPEAT_RUNS putaran: kirim selama 10 menit,
// jeda REPEAT_PAUSE_MS, lalu kirim lagi. Jadwal setiap putaran dihitung dari saat
// BOOT ditekan sehingga tidak bergeser. Seq kembali ke 0 setiap putaran, dan
// logger.py --runs memisahkan putaran secara otomatis. Nilainya harus sama di
// semua node (BLE dan LoRa). Tahan BOOT >= 2 s untuk membatalkan.
#define REPEAT_RUNS        10          // 1 = satu putaran saja
#define REPEAT_PAUSE_MS    60000UL     // jeda antarputaran (1 menit)

#define PIN_SDA        21
#define PIN_SCL        22
#define PIN_CONFIG_BTN 14   // tombol ganti sesi/payload (ke GND, pull-up internal)
#define PIN_START_BTN  0    // tombol BOOT pada papan (mulai pengujian)

#define MAX_PAYLOAD_SIZE   32
#define BLE_PRIMARY_MAX    24   // bagian paket terpanjang yang dimuat di iklan utama

// ------------------------- PILIHAN YANG DIUBAH DENGAN TOMBOL ------------------
static const uint32_t SESSION_INTERVAL_MS[4] = {2000, 1000, 500, 100};   // interval kirim BLE (ms)
static const uint8_t  PAYLOAD_OPTIONS[3]     = {7, 16, 32};              // ukuran payload (byte)
static uint8_t sessionIdx = 0;   // sesi = sessionIdx + 1
static uint8_t payloadIdx = 0;   // payload = PAYLOAD_OPTIONS[payloadIdx]
#define CUR_SESSION   (sessionIdx + 1)
#define CUR_INTERVAL  (SESSION_INTERVAL_MS[sessionIdx])
#define CUR_PAYLOAD   (PAYLOAD_OPTIONS[payloadIdx])

// Nilai tetap selama satu putaran, dihitung di startTest()
static uint8_t  runPayload   = 7;
static uint8_t  runPrimary   = 7;
static uint8_t  runRest      = 0;
static bool     runFragmented = false;

// ------------------------- INTERVAL ADVERTISING ------------------------------
// Satu seq disiarkan berulang setiap interval advertising sampai seq berikutnya,
// dan gateway cukup menangkap salah satu salinannya.
//
// Nilai 20 ms dipakai pada seluruh pengujian utama. Dengan interval sekitar 100 ms
// (rata-rata 105 ms karena jeda acak 0-10 ms) dan beacon Wi-Fi 102,4 ms, terjadi
// pelayangan (beat) dengan periode sekitar 4,14 s. Selama sebagian periode itu,
// iklan terus bertepatan dengan waktu radio gateway dipakai Wi-Fi sehingga ada
// paket yang hilang seluruhnya (uji pendahuluan P1: PLR 15,56%). Dengan interval
// 20 ms, sekitar empat iklan terjadi dalam satu interval beacon sehingga
// pelayangan tidak terbentuk (uji pendahuluan P5: PLR 0%).
//
// 0 = interval kirim / 3 (dibatasi 20-100 ms), hanya untuk pembanding.
// Nilai terkecil yang diizinkan spesifikasi Bluetooth adalah 20 ms. Bila
// controller menolak, startAdvertising() menaikkan interval ke 32, 50, lalu
// 100 ms dan mencetak nilai yang dipakai.
#define ADV_INTERVAL_FIXED_MS   20

static uint32_t advIntervalMs(uint32_t sendIntervalMs) {
  if (ADV_INTERVAL_FIXED_MS >= 20) return ADV_INTERVAL_FIXED_MS;
  uint32_t a = sendIntervalMs / 3;
  if (a > 100) a = 100;
  if (a < 20)  a = 20;     // 20 ms = batas minimum non-connectable advertising
  return a;
}

// ------------------------- OBJEK & STATE --------------------------------------
Adafruit_SSD1306 display(128, 64, &Wire, -1);
static bool oledOk = false;
static NimBLEAdvertising* pAdv = nullptr;

enum State { IDLE, RUNNING, PAUSE, DONE };
static State    state    = IDLE;
static uint16_t seqNo    = 0;
static uint32_t runStart = 0;
static uint32_t nextSend = 0;
static uint32_t lastOled = 0;
static uint32_t advFail  = 0;
static uint32_t runAdvMs = 100;   // interval advertising yang dipakai pada putaran ini (ms)
static uint8_t  runIdx      = 0;   // nomor putaran (1-REPEAT_RUNS)
static uint32_t seriesStart = 0;   // millis() saat BOOT ditekan
static inline uint32_t runStartAt(uint8_t k) {   // jadwal mulai putaran ke-k (mulai dari 1)
  return seriesStart + (uint32_t)(k - 1) * (TEST_DURATION_MS + REPEAT_PAUSE_MS);
}

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

// ============================ TOMBOL (tekan singkat dan lama) =================
// Mendeteksi tombol yang ditekan lalu dilepas: < 700 ms = singkat (ganti sesi),
// >= 700 ms = lama (ganti payload). Debounce 50 ms. Tidak memblokir dan
// dipanggil di loop() saat status IDLE atau DONE.
static BtnEvent readConfigButton() {
  static bool lastLow = false;
  static uint32_t pressStart = 0;
  bool cur = (digitalRead(PIN_CONFIG_BTN) == LOW);
  BtnEvent ev = BTN_NONE;
  if (!lastLow && cur) {
    pressStart = millis();
  } else if (lastLow && !cur) {
    uint32_t held = millis() - pressStart;
    if (held >= 50) ev = (held >= 700) ? BTN_LONG : BTN_SHORT;
  }
  lastLow = cur;
  return ev;
}

// ============================ PENYUSUNAN PAKET ================================
static void encodePacket(uint8_t* buf, uint8_t id, uint16_t seq, uint32_t ts, uint8_t payloadSize) {
  buf[0] = id;
  buf[1] = seq & 0xFF;          buf[2] = (seq >> 8) & 0xFF;
  buf[3] = ts & 0xFF;           buf[4] = (ts >> 8) & 0xFF;
  buf[5] = (ts >> 16) & 0xFF;   buf[6] = (ts >> 24) & 0xFF;
  for (int i = 7; i < payloadSize; i++) buf[i] = (uint8_t)((i - 7) & 0xFF);
}

// ============================ FUNGSI UTAMA ====================================
static void oledDraw() {
  if (!oledOk) return;
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.printf("NODE BLE%d\n", NODE_ID);
  switch (state) {
    case IDLE:
      display.println("Status: SIAP");
      display.printf("Sesi: %d (%lums)\n", CUR_SESSION, (unsigned long)CUR_INTERVAL);
      display.printf("Payload: %d byte\n", CUR_PAYLOAD);
      display.println("GPIO14: pendek=sesi");
      display.println("        lama=payload");
      display.println("BOOT: mulai");
      break;
    case RUNNING:
      display.printf("Sesi%d %dB\n", CUR_SESSION, runPayload);
      display.printf("KIRIM run %u/%d\n", runIdx, REPEAT_RUNS);
      display.printf("Seq   : %u\n", seqNo);
      display.printf("Sisa  : %lus\n",
                     (unsigned long)((TEST_DURATION_MS - (millis() - runStart)) / 1000));
      display.printf("adv%lums fail%lu\n", (unsigned long)runAdvMs, (unsigned long)advFail);
      break;
    case PAUSE: {
      int32_t sisa = (int32_t)(runStartAt(runIdx + 1) - millis());
      display.printf("Sesi%d %dB\n", CUR_SESSION, runPayload);
      display.printf("JEDA run %u/%d\n", runIdx, REPEAT_RUNS);
      display.printf("P_sent = %u\n", seqNo);
      display.printf("Run %u mulai %lds\n", runIdx + 1, (long)(sisa > 0 ? sisa / 1000 : 0));
      display.println("Batal: tahan BOOT 2s");
      break;
    }
    case DONE:
      display.printf("SELESAI %u/%d run\n", runIdx, REPEAT_RUNS);
      display.printf("Sesi%d %dB\n", CUR_SESSION, runPayload);
      display.printf("P_sent = %u\n", seqNo);
      display.println("GPIO14=ganti BOOT=ulang");
      break;
  }
  display.display();
}

static bool setPayload(uint16_t seq, uint32_t ts) {
  uint8_t pkt[MAX_PAYLOAD_SIZE];
  encodePacket(pkt, (uint8_t)NODE_ID, seq, ts, runPayload);

  // --- Iklan utama: byte 0 sampai runPrimary-1 dari paket -------------------
  uint8_t mfgPri[2 + BLE_PRIMARY_MAX];
  mfgPri[0] = 0xFF; mfgPri[1] = 0xFF;
  memcpy(mfgPri + 2, pkt, runPrimary);
  std::vector<uint8_t> priVec(mfgPri, mfgPri + (2 + runPrimary));

  NimBLEAdvertisementData advData;
  bool ok = true;
  ok &= advData.setFlags(0x06);
  ok &= advData.setManufacturerData(priVec);
  ok &= pAdv->setAdvertisementData(advData);

  if (runFragmented) {
    // --- Scan response: sisa paket, dengan ID dan seq diulang untuk pencocokan --
    uint8_t mfgRsp[2 + 1 + 2 + (MAX_PAYLOAD_SIZE - BLE_PRIMARY_MAX)];
    mfgRsp[0] = 0xFF; mfgRsp[1] = 0xFF;
    mfgRsp[2] = (uint8_t)NODE_ID;
    mfgRsp[3] = seq & 0xFF; mfgRsp[4] = (seq >> 8) & 0xFF;
    memcpy(mfgRsp + 5, pkt + runPrimary, runRest);
    std::vector<uint8_t> rspVec(mfgRsp, mfgRsp + (5 + runRest));
    NimBLEAdvertisementData rspData;
    ok &= rspData.setManufacturerData(rspVec);
    ok &= pAdv->setScanResponseData(rspData);
  }

  if (pAdv->isAdvertising()) ok &= pAdv->refreshAdvertisingData();
  return ok;
}

// Memulai advertising. Bila controller menolak interval pendek (start() bernilai
// false), interval dinaikkan bertahap agar kegagalan tidak terjadi tanpa pesan.
// runAdvMs diperbarui ke nilai yang benar-benar dipakai.
static bool startAdvertising() {
  uint32_t chain[4]; uint8_t n = 0;
  chain[n++] = runAdvMs;
  const uint32_t fb[3] = {32, 50, 100};
  for (uint8_t i = 0; i < 3; i++) if (fb[i] > runAdvMs) chain[n++] = fb[i];
  for (uint8_t i = 0; i < n; i++) {
    pAdv->setAdvertisingInterval((chain[i] * 16) / 10);   // satuan 0,625 ms
    if (pAdv->start()) {
      if (chain[i] != runAdvMs)
        dbgStatus("!! advInterval %lums DITOLAK controller, dipakai %lums",
                  (unsigned long)runAdvMs, (unsigned long)chain[i]);
      runAdvMs = chain[i];
      return true;
    }
    dbgStatus("!! pAdv->start() GAGAL pada advInterval=%lums", (unsigned long)chain[i]);
  }
  return false;
}

static void startTest(uint32_t tStart) {
  // Konfigurasi putaran ini tetap selama pengiriman, karena tombol hanya dibaca
  // saat status IDLE atau DONE.
  runPayload    = CUR_PAYLOAD;
  runPrimary    = (runPayload < BLE_PRIMARY_MAX) ? runPayload : BLE_PRIMARY_MAX;
  runRest       = runPayload - runPrimary;
  runFragmented = (runRest > 0);

  pAdv->enableScanResponse(runFragmented);   // harus dipanggil sebelum setAdvertisementData() pertama
  runAdvMs = advIntervalMs(CUR_INTERVAL);
  pAdv->setAdvertisingInterval((runAdvMs * 16) / 10);   // satuan 0,625 ms

  seqNo = 0; runStart = tStart; nextSend = runStart; advFail = 0;
  state = RUNNING;

  bool firstOk = setPayload(0, runStart);
  if (!startAdvertising()) {
    advFail++;
    dbgStatus("!!! IKLAN TIDAK BERJALAN sama sekali -- data run ini TIDAK VALID, hentikan & periksa");
  }
  if (!firstOk) {
    dbgStatus("!! PERINGATAN: payload BLE%d %dB GAGAL dipasang sejak awal -- cek versi "
              "NimBLE-Arduino Anda >= 2.1.3.", NODE_ID, runPayload);
  }
  dbgStatus("MULAI putaran %u/%d (10 menit). BLE%d sesi=%d interval=%lums payload=%dB "
            "(primary=%dB scanrsp=%dB) advInterval=%lums (~%lu salinan/seq)", runIdx, REPEAT_RUNS, NODE_ID, CUR_SESSION,
            (unsigned long)CUR_INTERVAL, runPayload, runPrimary, runRest,
            (unsigned long)runAdvMs, (unsigned long)(CUR_INTERVAL / runAdvMs));
}

static void sendPacket() {
  uint32_t ts = millis();
  if (!setPayload(seqNo, ts)) {
    advFail++;
    dbgStatus("BLE%d GAGAL update iklan (seq=%u)", NODE_ID, seqNo);
  }
  dbgPacket("BLE%d ADV seq=%u ts=%lu ukuran=%dB", NODE_ID, seqNo, (unsigned long)ts, runPayload);
  seqNo++;
}

static void stopTest(bool batal = false) {
  pAdv->stop();
  state = (!batal && runIdx < REPEAT_RUNS) ? PAUSE : DONE;
  // Perkiraan jumlah iklan yang dipancarkan (jeda acak 0-10 ms membuat angka
  // sebenarnya sedikit lebih kecil). Dipakai untuk menghitung capture rate:
  // rawTot (baris G gateway) / advEvents.
  uint32_t advEvents = TEST_DURATION_MS / (runAdvMs + 5);
  dbgStatus("SELESAI putaran %u/%d%s. BLE%d P_sent=%u (advFail=%lu) advInterval=%lums advEvents~%lu",
            runIdx, REPEAT_RUNS, batal ? " (SERI DIBATALKAN)" : "", NODE_ID, seqNo,
            (unsigned long)advFail, (unsigned long)runAdvMs, (unsigned long)advEvents);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  dbgStatus("Boot Node BLE%d", NODE_ID);

  pinMode(PIN_CONFIG_BTN, INPUT_PULLUP);   // tombol ke GND, tanpa catu 5 V atau 3V3 eksternal
  pinMode(PIN_START_BTN, INPUT_PULLUP);

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  dbgStatus("OLED init: %s", oledOk ? "OK" : "TIDAK TERDETEKSI");

  NimBLEDevice::init("");
  pAdv = NimBLEDevice::getAdvertising();
  pAdv->setConnectableMode(BLE_GAP_CONN_MODE_NON);

  char name[8];
  snprintf(name, sizeof(name), "TTU-B%d", NODE_ID);
  pAdv->setName(name);   // hanya nama perangkat; gateway membaca ID dari isi paket

  dbgStatus("BLE%d init OK. Siap. GPIO14 pendek=ganti sesi, lama=ganti payload, BOOT=mulai.", NODE_ID);
  oledDraw();
}

void loop() {
  uint32_t now = millis();

  if (state == IDLE || state == DONE) {
    BtnEvent ev = readConfigButton();
    if (ev == BTN_SHORT) {
      sessionIdx = (sessionIdx + 1) % 4;
      dbgStatus("Sesi diganti -> %d (interval %lums)", CUR_SESSION, (unsigned long)CUR_INTERVAL);
      state = IDLE; oledDraw();
    } else if (ev == BTN_LONG) {
      payloadIdx = (payloadIdx + 1) % 3;
      dbgStatus("Payload diganti -> %d byte", CUR_PAYLOAD);
      state = IDLE; oledDraw();
    }
    if (digitalRead(PIN_START_BTN) == LOW) {
      delay(50);
      if (digitalRead(PIN_START_BTN) == LOW) {
        seriesStart = millis(); runIdx = 1;
        startTest(seriesStart);
      }
    }
  } else if (state == RUNNING) {
    if (now - runStart >= TEST_DURATION_MS) {
      stopTest();
    } else if ((int32_t)(now - nextSend) >= 0) {
      sendPacket();
      nextSend += CUR_INTERVAL;
    }
  } else if (state == PAUSE) {
    if ((int32_t)(now - runStartAt(runIdx + 1)) >= 0) {
      runIdx++;
      startTest(runStartAt(runIdx));
    }
  }

  // Pembatalan: tahan BOOT >= 2 s saat mengirim atau jeda. Lima detik pertama setelah
  // mulai diabaikan agar tekanan BOOT untuk memulai tidak langsung membatalkan.
  {
    static uint32_t bootLowSince = 0;
    bool low = (digitalRead(PIN_START_BTN) == LOW);
    if ((state == RUNNING || state == PAUSE) && low && now - seriesStart > 5000) {
      if (bootLowSince == 0) bootLowSince = now;
      else if (now - bootLowSince >= 2000) { stopTest(true); bootLowSince = 0; }
    } else {
      bootLowSince = 0;
    }
  }

  if (now - lastOled >= 500) { lastOled = now; oledDraw(); }
  delay(1);
}
