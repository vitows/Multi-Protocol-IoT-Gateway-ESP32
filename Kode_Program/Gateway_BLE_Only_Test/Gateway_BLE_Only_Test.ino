/* ============================================================================
 *  Gateway_BLE_Only_Test - sketch diagnostik untuk uji pendahuluan P3
 *
 *  Penelitian: Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32
 *  dengan Implementasi FreeRTOS (Teknik Elektro, UKSW)
 * ============================================================================
 *  Sketch ini diunggah ke papan gateway, tetapi tidak menyalakan Wi-Fi dan
 *  MQTT. Gateway hanya memindai BLE dengan parameter pemindaian dan aturan
 *  pembuangan duplikat yang sama dengan Gateway_FreeRTOS.ino. Hasilnya
 *  dibandingkan dengan gateway lengkap untuk mengetahui apakah paket BLE hilang
 *  karena radio dipakai bergantian dengan Wi-Fi atau karena kondisi sinyal.
 *
 *  Keluaran Serial
 *    D,<src>,<seq>,<T_receive>,<T_receive>,0,<len>  setiap paket unik (latensi tidak diukur)
 *    G,<ms>,BLEn=<rawWin>/<rawTot>/<maxGap>/<rssiAvg>/<rssiMin>/<seqMiss>  setiap 10 s
 *    Ringkasan putaran setiap 10 s. Statistik dihitung per putaran: saat seq
 *    kembali ke 0, ringkasan putaran sebelumnya dicetak lalu penghitung diatur ulang.
 *  Baris D dicetak dari loop(), bukan dari callback BLE, agar task host NimBLE
 *  tidak diperlambat oleh Serial.
 *
 *  Cara pakai
 *    1. Unggah sketch ini ke papan gateway.
 *    2. Jalankan logger.py --pendahuluan P3 (atau simpan keluaran Serial ke file).
 *    3. Nyalakan satu node BLE (interval kirim 2000 ms, payload 7 byte, jarak 2 m)
 *       lalu tekan BOOT pada node.
 *    4. Analisis ulang log dengan: python logger.py --replay <file_log> --adv-ms 100 --send-ms 2000
 *  P_sent pada ringkasan adalah perkiraan (seq terbesar + 1). Nilai resmi adalah
 *  P_sent yang dicetak node saat putaran selesai.
 * ============================================================================
 */
#include <Arduino.h>
#include <NimBLEDevice.h>

#define SERIAL_BAUD        115200
#define MIN_PAYLOAD_SIZE   7
#define MAX_PAYLOAD_SIZE   32

#define PRINT_D            1    // 1 = baris D untuk setiap paket unik, dicetak dari loop()
#define DIAG_MODE          1    // baris G setiap 10 s (format sama dengan gateway lengkap)

// -- Sama dengan Gateway_FreeRTOS.ino agar hasilnya dapat dibandingkan --
#define BLE_SCAN_INTERVAL_MS     100
#define BLE_SCAN_WINDOW_MS       100
#define BLE_SCAN_RSP_TIMEOUT_MS  100

// Celah lebih dari 30 s dianggap jeda antarputaran, bukan kehilangan di radio.
#define IDLE_GAP_MS        30000

// Indeks array sama dengan NODE_ID (1-4); indeks 0 tidak dipakai. Susunannya sama
// dengan enum SRC_* pada gateway lengkap sehingga baris G memakai nama dan urutan yang sama.
#define N_SRC 5
static const char* const SRC_NAME[N_SRC] = {"LORA1", "BLE1", "BLE2", "BLE3", "BLE4"};

static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

#if PRINT_D
// Antrean kecil untuk paket unik: diisi callback dan dicetak oleh loop(). Pada interval
// kirim 2000 ms, 64 tempat sudah lebih dari cukup; bila penuh, jumlahnya dicatat (dLost).
struct DRec { uint8_t s; uint16_t seq; uint32_t rx; uint8_t len; };
#define DQ_LEN 64
static DRec dq[DQ_LEN];
static volatile uint16_t dqHead = 0, dqTail = 0;
static volatile uint32_t dLost = 0;
#endif

// --- statistik per putaran (diatur ulang saat seq mundur) ---
static volatile uint32_t runCapture[N_SRC];   // semua iklan yang tertangkap, termasuk salinan seq yang sama
static volatile uint32_t runUnique[N_SRC];    // seq baru yang tertangkap (P_receive)
static volatile int32_t  runMaxSeq[N_SRC];    // seq terbesar, untuk memperkirakan P_sent
static volatile uint32_t runMiss[N_SRC];      // seq yang sama sekali tidak tertangkap
static volatile int32_t  lastSeq[N_SRC];
static volatile uint32_t runStartMs[N_SRC];
static volatile uint32_t runEndMs[N_SRC];

// --- statistik jendela 10 s dan kumulatif (untuk baris G) ---
static volatile uint32_t dgRawTot[N_SRC];
static volatile uint32_t dgRawWin[N_SRC];
static volatile uint32_t dgLastRaw[N_SRC];
static volatile uint32_t dgMaxGapWin[N_SRC];
static volatile int32_t  dgRssiSumWin[N_SRC];
static volatile int32_t  dgRssiMinWin[N_SRC];
static volatile uint32_t dgSeqMissTot[N_SRC];

static volatile uint8_t  pendingRunEnd = 0;   // bitmask sumber yang putarannya baru berakhir; ringkasan dicetak di loop()
static uint32_t prevSent[N_SRC], prevUnique[N_SRC], prevCapture[N_SRC], prevMiss[N_SRC];
static uint32_t prevDurS[N_SRC];

static inline void decodePacket(const uint8_t* b, uint8_t& id, uint16_t& seq, uint32_t& ts) {
  id  = b[0];
  seq = (uint16_t)b[1] | ((uint16_t)b[2] << 8);
  ts  = (uint32_t)b[3] | ((uint32_t)b[4] << 8) | ((uint32_t)b[5] << 16) | ((uint32_t)b[6] << 24);
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

    if (id < 1 || id > 4) return;   // bukan node BLE penelitian ini (ID 1-4)
    uint8_t s = id;
    int32_t rssi = dev->getRSSI();

    portENTER_CRITICAL(&mux);
    // --- seq mundur berarti node memulai putaran baru: tutup putaran sebelumnya ---
    if (lastSeq[s] >= 0 && (int32_t)seq < lastSeq[s]) {
      prevSent[s]    = (uint32_t)(runMaxSeq[s] + 1);
      prevUnique[s]  = runUnique[s];
      prevCapture[s] = runCapture[s];
      prevMiss[s]    = runMiss[s];
      prevDurS[s]    = (runEndMs[s] - runStartMs[s]) / 1000;
      pendingRunEnd |= (1 << s);
      runCapture[s] = 0; runUnique[s] = 0; runMiss[s] = 0; runMaxSeq[s] = -1;
      lastSeq[s] = -1;
    }
    if (lastSeq[s] < 0 && runCapture[s] == 0) runStartMs[s] = rx;
    runEndMs[s] = rx;

    runCapture[s] = runCapture[s] + 1;
    if ((int32_t)seq > runMaxSeq[s]) runMaxSeq[s] = seq;

    bool isNew = ((int32_t)seq != lastSeq[s]);
    if (isNew) {
      if (lastSeq[s] >= 0 && (int32_t)seq > lastSeq[s] + 1) {
        uint32_t m = (uint32_t)((int32_t)seq - lastSeq[s] - 1);
        runMiss[s] = runMiss[s] + m;
        dgSeqMissTot[s] = dgSeqMissTot[s] + m;
      }
      lastSeq[s] = seq;
      runUnique[s] = runUnique[s] + 1;
    }

#if DIAG_MODE
    {
      uint32_t gap = (dgLastRaw[s] != 0) ? rx - dgLastRaw[s] : 0;
      if (gap < IDLE_GAP_MS && gap > dgMaxGapWin[s]) dgMaxGapWin[s] = gap;
      dgLastRaw[s] = rx;
      dgRawTot[s] = dgRawTot[s] + 1;
      if (dgRawWin[s] == 0 || rssi < dgRssiMinWin[s]) dgRssiMinWin[s] = rssi;
      dgRawWin[s] = dgRawWin[s] + 1;
      dgRssiSumWin[s] = dgRssiSumWin[s] + rssi;
    }
#endif
#if PRINT_D
    if (isNew) {
      uint16_t nx = (uint16_t)((dqHead + 1) % DQ_LEN);
      if (nx != dqTail) { dq[dqHead] = {s, seq, rx, actualLen}; dqHead = nx; }
      else dLost = dLost + 1;
    }
#endif
    portEXIT_CRITICAL(&mux);
  }
};
static BleScanCb bleCb;

static void printRunSummary(const char* tag, uint8_t s, uint32_t sent, uint32_t uniq,
                            uint32_t cap, uint32_t miss, uint32_t durS) {
  uint32_t missing = (sent > uniq) ? (sent - uniq) : 0;
  float plr = sent ? (100.0f * missing / sent) : 0.0f;
  Serial.printf("%s %s: durasi=%lus capture_mentah=%lu unik=%lu estimasi_P_sent=%lu "
                "hilang=%lu seqMiss=%lu PLR~=%.2f%%\n",
                tag, SRC_NAME[s], (unsigned long)durS, (unsigned long)cap, (unsigned long)uniq,
                (unsigned long)sent, (unsigned long)missing, (unsigned long)miss, plr);
}

#if DIAG_MODE
// Format sama dengan logDiag() pada Gateway_FreeRTOS dan Gateway_BareMetal.
static void logDiag() {
  uint32_t n[N_SRC], tot[N_SRC], gap[N_SRC], miss[N_SRC];
  int32_t  sum[N_SRC], mn[N_SRC];
  portENTER_CRITICAL(&mux);
  for (int i = 1; i < N_SRC; i++) {
    n[i] = dgRawWin[i]; tot[i] = dgRawTot[i]; gap[i] = dgMaxGapWin[i];
    sum[i] = dgRssiSumWin[i]; mn[i] = dgRssiMinWin[i]; miss[i] = dgSeqMissTot[i];
    dgRawWin[i] = 0; dgMaxGapWin[i] = 0; dgRssiSumWin[i] = 0; dgRssiMinWin[i] = 0;
  }
  portEXIT_CRITICAL(&mux);
  Serial.printf("G,%lu", (unsigned long)millis());
  for (int i = 1; i < N_SRC; i++) {
    int32_t avg = n[i] ? sum[i] / (int32_t)n[i] : 0;
    int32_t m   = n[i] ? mn[i] : 0;
    Serial.printf(",%s=%lu/%lu/%lu/%ld/%ld/%lu", SRC_NAME[i], (unsigned long)n[i],
                  (unsigned long)tot[i], (unsigned long)gap[i], (long)avg, (long)m,
                  (unsigned long)miss[i]);
  }
  Serial.println();
}
#endif

static void printCurrentSummary() {
  Serial.println("\n=== RINGKASAN RUN BERJALAN (BLE-only, tanpa Wi-Fi/MQTT) ===");
  bool any = false;
  for (int i = 1; i < N_SRC; i++) {
    uint32_t cap, uniq, miss, sent, dur;
    portENTER_CRITICAL(&mux);
    cap = runCapture[i]; uniq = runUnique[i]; miss = runMiss[i];
    sent = (runMaxSeq[i] >= 0) ? (uint32_t)(runMaxSeq[i] + 1) : 0;
    dur = (runEndMs[i] - runStartMs[i]) / 1000;
    portEXIT_CRITICAL(&mux);
    if (cap == 0) continue;
    any = true;
    printRunSummary("[berjalan]", i, sent, uniq, cap, miss, dur);
  }
  if (!any) Serial.println("(belum ada iklan BLE node tertangkap)");
  Serial.println("(P_sent estimasi = seq terbesar+1; angka resmi = P_sent di node saat SELESAI.\n"
                 " Bila run berakhir, baris 'RINGKASAN RUN SELESAI' dicetak otomatis saat node\n"
                 " memulai run berikutnya, atau baca baris [berjalan] terakhir setelah node SELESAI.)");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(300);
  Serial.println("=== Gateway_BLE_Only_Test rev4 mulai (TIDAK ada Wi-Fi/MQTT) MODE=BLE_ONLY ===");
  for (int i = 0; i < N_SRC; i++) { lastSeq[i] = -1; runMaxSeq[i] = -1; }

  NimBLEDevice::init("");
  NimBLEScan* sc = NimBLEDevice::getScan();
  // Parameter kedua (wantDuplicates = true) sama dengan Gateway_FreeRTOS.ino agar
  // onResult() dipanggil untuk setiap iklan, bukan sekali per alamat MAC.
  sc->setScanCallbacks(&bleCb, true);
  sc->setDuplicateFilter(0);   // filter duplikat pada controller juga dimatikan
  sc->setActiveScan(true);
  sc->setScanResponseTimeout(BLE_SCAN_RSP_TIMEOUT_MS);
  sc->setInterval(BLE_SCAN_INTERVAL_MS);
  sc->setWindow(BLE_SCAN_WINDOW_MS);
  sc->setMaxResults(0);
  sc->start(0, false, true);
  Serial.println("Scan BLE dimulai. Nyalakan node BLE sekarang (BOOT).");
  Serial.println("Baris G, dan ringkasan tercetak tiap 10 detik.");
}

void loop() {
  static uint32_t lastPrint = 0;
  static uint32_t lastCheck = 0;
  NimBLEScan* sc = NimBLEDevice::getScan();

  if (millis() - lastCheck > 2000) {
    lastCheck = millis();
    if (!sc->isScanning()) {
      sc->start(0, false, true);
      Serial.println("[STATUS] scan mati, distart ulang");
    }
  }

  // Cetak ringkasan akhir untuk putaran yang baru berakhir
  uint8_t mask;
  portENTER_CRITICAL(&mux);
  mask = pendingRunEnd; pendingRunEnd = 0;
  portEXIT_CRITICAL(&mux);
  for (int i = 1; i < N_SRC; i++) {
    if (mask & (1 << i)) {
      Serial.println("\n=== RINGKASAN RUN SELESAI ===");
      printRunSummary("[final]", i, prevSent[i], prevUnique[i], prevCapture[i], prevMiss[i], prevDurS[i]);
    }
  }

#if PRINT_D
  // Cetak paket unik yang dicatat callback (format sama dengan gateway lengkap; latensi 0 = tidak diukur)
  while (true) {
    DRec r;
    bool ada = false;
    portENTER_CRITICAL(&mux);
    if (dqTail != dqHead) { r = dq[dqTail]; dqTail = (uint16_t)((dqTail + 1) % DQ_LEN); ada = true; }
    portEXIT_CRITICAL(&mux);
    if (!ada) break;
    Serial.printf("D,%s,%u,%lu,%lu,0,%u\n", SRC_NAME[r.s], (unsigned)r.seq, (unsigned long)r.rx,
                  (unsigned long)r.rx, (unsigned)r.len);
  }
#endif

  if (millis() - lastPrint > 10000) {
    lastPrint = millis();
#if DIAG_MODE
    logDiag();
#endif
    printCurrentSummary();
#if PRINT_D
    if (dLost) Serial.printf("[PERINGATAN] antrean baris D penuh, %lu paket unik tidak tercetak\n", (unsigned long)dLost);
#endif
  }
}
