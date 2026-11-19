/*
 * ============================================================================
 *  Node LoRa - ESP32 + LoRa SX1278 (SPI) + OLED SSD1306 (I2C)
 *
 *  Penelitian: Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32
 *  dengan Implementasi FreeRTOS (Teknik Elektro, UKSW)
 * ============================================================================
 *  Satu papan ESP32 khusus LoRa (ID 0), terpisah dari empat Node_BLE.
 *
 *  Tombol
 *    GPIO14       : ganti ukuran payload (7, 16, 32 byte)
 *    BOOT (GPIO0) : mulai pengujian; tahan 2 s untuk membatalkan
 *  Tombol GPIO14 dihubungkan ke GND dan memakai pull-up internal. Jangan
 *  hubungkan pin GPIO ke 5 V (batas tegangan pin ESP32 adalah 3,6 V).
 *
 *  Format paket
 *    Byte 0: ID (0) | byte 1-2: seq (little-endian) | byte 3-6: timestamp
 *    millis() (little-endian) | byte pengisi sampai ukuran payload.
 *
 *  Parameter radio: 433 MHz, SF7, lebar pita 125 kHz, coding rate 4/5,
 *  daya pancar 17 dBm. Interval kirim mengikuti ukuran payload agar siklus
 *  kerja (duty cycle) tetap di bawah 1%.
 *
 *  Pustaka : LoRa (Sandeep Mistry), Adafruit SSD1306, Adafruit GFX
 *
 *  Wiring (ESP32 DevKit 30-pin):
 *      SX1278 SCK  -> GPIO18     SX1278 NSS  -> GPIO5
 *      SX1278 MISO -> GPIO19     SX1278 RST  -> GPIO4
 *      SX1278 MOSI -> GPIO23     SX1278 DIO0 -> GPIO26
 *      SX1278 VCC  -> 3V3
 *      OLED SDA    -> GPIO21     OLED SCL    -> GPIO22
 *      Tombol      : GPIO14 <-> GND
 * ============================================================================
 */
#include <Arduino.h>
#include <stdarg.h>
#include <SPI.h>
#include <LoRa.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
enum BtnEvent { BTN_NONE, BTN_PRESSED };
// ============================ MODE DEBUG ======================================
#define DEBUG_MODE     1
#define DEBUG_VERBOSE  1

#define SERIAL_BAUD        115200

// ------------------------- KONFIGURASI UJI -------------------------------------
// Interval kirim LoRa mengikuti ukuran payload agar duty cycle tetap < 1%
// (time on air SF7/BW125: 7 B 36,1 ms, 16 B 51,5 ms, 32 B 71,9 ms).
//   7 B -> 4 s (150 paket per 10 menit, duty cycle 0,90%) | 16 B -> 6 s (100) | 32 B -> 8 s (75)
static const uint32_t LORA_INTERVAL_OPTIONS[3] = {4000UL, 6000UL, 8000UL};
#define TEST_DURATION_MS   600000UL   // 10 menit
// ---- PENGUJIAN BERURUTAN: harus sama dengan Node_BLE.ino ----------------------
#define REPEAT_RUNS        10          // 1 = satu putaran saja
#define REPEAT_PAUSE_MS    60000UL     // jeda antarputaran (1 menit)
#define ID_LORA1           0

#define MAX_PAYLOAD_SIZE   32         // ukuran buffer untuk payload terbesar
static const uint8_t PAYLOAD_OPTIONS[3] = {7, 16, 32};   // ukuran payload (byte)
static uint8_t payloadIdx = 0;        // indeks PAYLOAD_OPTIONS, diubah dengan tombol
#define CUR_PAYLOAD  (PAYLOAD_OPTIONS[payloadIdx])
#define CUR_INTERVAL (LORA_INTERVAL_OPTIONS[payloadIdx])
#define EXPECTED_SENT ((uint16_t)((TEST_DURATION_MS + CUR_INTERVAL - 1) / CUR_INTERVAL))

#define PIN_LORA_SCK   18
#define PIN_LORA_MISO  19
#define PIN_LORA_MOSI  23
#define PIN_LORA_SS    5
#define PIN_LORA_RST   4   // GPIO14 tidak dipakai karena menjadi pin tombol
#define PIN_LORA_DIO0  26
#define PIN_SDA        21
#define PIN_SCL        22
#define PIN_CONFIG_BTN 14   // tombol ganti payload (ke GND, pull-up internal)
#define PIN_START_BTN  0    // tombol BOOT pada papan (mulai pengujian)

#define LORA_FREQ      433E6
#define LORA_SF        7
#define LORA_BW        125E3
#define LORA_CR        5
#define LORA_SYNC      0x12
#define LORA_TXPOWER   17

// ------------------------- OBJEK & STATE --------------------------------------
Adafruit_SSD1306 display(128, 64, &Wire, -1);
static bool oledOk = false;

enum State { IDLE, RUNNING, PAUSE, DONE };
static State    state       = IDLE;
static uint16_t loraSeq     = 0;
static uint32_t runStart    = 0;
static uint32_t nextLoraTx  = 0;
static uint32_t lastOled    = 0;
static uint32_t loraAirtime = 0;
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

// ============================ TOMBOL ===========================================
// Mendeteksi tombol yang ditekan lalu dilepas, dengan debounce 50 ms.
// Dipanggil di loop() saat status IDLE atau DONE.
static BtnEvent readConfigButton() {
  static bool lastLow = false;
  static uint32_t pressStart = 0;
  bool cur = (digitalRead(PIN_CONFIG_BTN) == LOW);
  BtnEvent ev = BTN_NONE;
  if (!lastLow && cur) {                 // baru ditekan
    pressStart = millis();
  } else if (lastLow && !cur) {          // baru dilepas
    if (millis() - pressStart >= 50) ev = BTN_PRESSED;   // debounce
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
  for (int i = 7; i < payloadSize; i++) buf[i] = (uint8_t)((i - 7) & 0xFF);   // byte pengisi
}

// ============================ FUNGSI UTAMA ====================================
static void oledDraw() {
  if (!oledOk) return;
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(0, 0);
  display.println("NODE LoRa1 (ID=0)");
  switch (state) {
    case IDLE:
      display.println("Status: SIAP");
      display.printf("Payload: %d byte\n", CUR_PAYLOAD);
      display.printf("Interval: %lus (%u pkt)\n", (unsigned long)(CUR_INTERVAL / 1000), EXPECTED_SENT);
      display.println("Tekan GPIO14: ganti");
      display.println("Tekan BOOT  : mulai");
      break;
    case RUNNING: {
      uint32_t sisa = (TEST_DURATION_MS - (millis() - runStart)) / 1000;
      display.printf("Payload:%dB int:%lus\n", CUR_PAYLOAD, (unsigned long)(CUR_INTERVAL / 1000));
      display.printf("KIRIM run %u/%d\n", runIdx, REPEAT_RUNS);
      display.printf("Sisa waktu: %lus\n", (unsigned long)sisa);
      display.printf("Seq      : %u\n", loraSeq);
      display.printf("airtime~%lums\n", (unsigned long)loraAirtime);
      break;
    }
    case PAUSE: {
      int32_t sisa = (int32_t)(runStartAt(runIdx + 1) - millis());
      display.printf("Payload: %dB\n", CUR_PAYLOAD);
      display.printf("JEDA run %u/%d\n", runIdx, REPEAT_RUNS);
      display.printf("P_sent = %u\n", loraSeq);
      display.printf("Run %u mulai %lds\n", runIdx + 1, (long)(sisa > 0 ? sisa / 1000 : 0));
      display.println("Batal: tahan BOOT 2s");
      break;
    }
    case DONE:
      display.printf("SELESAI %u/%d run\n", runIdx, REPEAT_RUNS);
      display.printf("Payload: %dB\n", CUR_PAYLOAD);
      display.printf("P_sent = %u\n", loraSeq);
      display.println("Tekan GPIO14: ganti");
      display.println("Tekan BOOT  : ulang");
      break;
  }
  display.display();
}

static void sendLora() {
  uint8_t buf[MAX_PAYLOAD_SIZE];
  uint32_t ts = millis();
  encodePacket(buf, ID_LORA1, loraSeq, ts, CUR_PAYLOAD);

  uint32_t t0 = millis();
  LoRa.beginPacket();
  LoRa.write(buf, CUR_PAYLOAD);
  LoRa.endPacket();                 // menunggu sampai pengiriman selesai
  loraAirtime = millis() - t0;

  dbgPacket("LoRa TX seq=%u ts=%lu ukuran=%dB airtime=%lums", loraSeq, (unsigned long)ts,
            CUR_PAYLOAD, (unsigned long)loraAirtime);
  loraSeq++;
}

static void startTest(uint32_t tStart) {
  loraSeq = 0;
  runStart   = tStart;
  nextLoraTx = runStart;
  loraAirtime = 0;
  state = RUNNING;
  dbgStatus("MULAI putaran %u/%d (10 menit). Payload=%dB. Interval LoRa=%lums (P_sent diharapkan=%u)",
            runIdx, REPEAT_RUNS, CUR_PAYLOAD, (unsigned long)CUR_INTERVAL, EXPECTED_SENT);
}

static void stopTest(bool batal = false) {
  LoRa.sleep();                     // beginPacket() pada putaran berikutnya mengaktifkan radio kembali
  state = (!batal && runIdx < REPEAT_RUNS) ? PAUSE : DONE;
  dbgStatus("SELESAI putaran %u/%d%s. LoRa P_sent=%u (payload=%dB)", runIdx, REPEAT_RUNS,
            batal ? " (SERI DIBATALKAN)" : "", loraSeq, CUR_PAYLOAD);
}

// ============================ SETUP & LOOP ====================================
static void fatal(const char* msg) {
  Serial.println(msg);
  if (oledOk) { display.clearDisplay(); display.setCursor(0, 0); display.println(msg); display.display(); }
  while (true) delay(1000);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(200);
  dbgStatus("Boot Node LoRa1 (ID=0)");

  pinMode(PIN_CONFIG_BTN, INPUT_PULLUP);   // tombol ke GND, tanpa catu 5 V atau 3V3 eksternal
  pinMode(PIN_START_BTN, INPUT_PULLUP);

  Wire.begin(PIN_SDA, PIN_SCL);
  Wire.setClock(400000);
  oledOk = display.begin(SSD1306_SWITCHCAPVCC, 0x3C);
  dbgStatus("OLED init: %s", oledOk ? "OK" : "TIDAK TERDETEKSI");

  SPI.begin(PIN_LORA_SCK, PIN_LORA_MISO, PIN_LORA_MOSI, PIN_LORA_SS);
  LoRa.setPins(PIN_LORA_SS, PIN_LORA_RST, PIN_LORA_DIO0);
  if (!LoRa.begin(LORA_FREQ)) fatal("LoRa init GAGAL - cek wiring SPI/RST/DIO0!");
  LoRa.setSpreadingFactor(LORA_SF);
  LoRa.setSignalBandwidth(LORA_BW);
  LoRa.setCodingRate4(LORA_CR);
  LoRa.setPreambleLength(8);
  LoRa.setSyncWord(LORA_SYNC);
  LoRa.setTxPower(LORA_TXPOWER);
  LoRa.enableCrc();
  dbgStatus("LoRa init OK @ %.0f Hz, SF%d, BW%.0f (RST=GPIO%d)", LORA_FREQ, LORA_SF, LORA_BW, PIN_LORA_RST);

  dbgStatus("Siap. GPIO14=ganti payload, BOOT=mulai.");
  oledDraw();
}

void loop() {
  uint32_t now = millis();

  if (state == IDLE || state == DONE) {
    if (readConfigButton() == BTN_PRESSED) {
      payloadIdx = (payloadIdx + 1) % 3;
      dbgStatus("Payload diganti -> %d byte, interval LoRa %lu ms (P_sent %u)",
                CUR_PAYLOAD, (unsigned long)CUR_INTERVAL, EXPECTED_SENT);
      state = IDLE;
      oledDraw();
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
    } else if ((int32_t)(now - nextLoraTx) >= 0) {
      sendLora();
      nextLoraTx += CUR_INTERVAL;
    }
  } else if (state == PAUSE) {
    if ((int32_t)(now - runStartAt(runIdx + 1)) >= 0) {
      runIdx++;
      startTest(runStartAt(runIdx));
    }
  }

  // Pembatalan: tahan BOOT >= 2 s saat mengirim atau jeda (5 s pertama diabaikan).
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
