# Kode Program

| Folder | Isi |
| --- | --- |
| `Gateway_FreeRTOS/` | Gateway versi FreeRTOS: `TaskLoRa` dan `TaskBLE` pada *core* 0, `TaskMQTT` dan `TaskOLED` pada *core* 1, dihubungkan oleh *queue* BLE (128) dan *queue* data (384). |
| `Gateway_BareMetal/` | Gateway versi Bare Metal: seluruh pekerjaan berjalan berurutan di `loop()` dengan *mailbox* satu paket untuk setiap sumber. |
| `Gateway_BLE_Only_Test/` | Sketch diagnostik untuk uji pendahuluan P3: gateway hanya memindai BLE tanpa Wi-Fi dan MQTT. |
| `Node_LoRa/` | *Node* LoRa (ID 0), SX1278 433 MHz, SF7, lebar pita 125 kHz, *coding rate* 4/5, 17 dBm. |
| `Node_BLE/` | *Node* BLE (ID 1-4), *non-connectable legacy advertising* dengan interval *advertising* 20 ms. |
| `Pengujian/` | `logger.py` (pencatat sesi pengujian) serta konfigurasi Mosquitto `ttu.conf` dan `ttu_gangguan.conf`. |

## Perangkat lunak yang dipakai

- Arduino IDE dengan ESP32 Arduino core 3.0.7
- NimBLE-Arduino (h2zero) 2.5.0 atau lebih baru
- LoRa (Sandeep Mistry), PubSubClient (Nick O'Leary), Adafruit SSD1306, Adafruit GFX
- Mosquitto 2.x sebagai *broker* MQTT
- Python 3 dengan `paho-mqtt`, `pyserial`, dan `openpyxl` untuk `logger.py`

## Pengaturan sebelum diunggah

1. Pada `Gateway_FreeRTOS.ino` dan `Gateway_BareMetal.ino`, isi `WIFI_SSID`, `WIFI_PASSWORD`, dan
   `MQTT_HOST` (alamat IP laptop yang menjalankan Mosquitto). Nilai di repositori ini hanya contoh.
2. Pada `Node_BLE.ino`, isi `NODE_ID` dengan 1, 2, 3, atau 4 sesuai papan.
3. Pengaturan radio (`WIFI_NO_SLEEP`, `COEX_PREFER_BT`) harus sama pada kedua versi gateway.

## Menjalankan satu sesi pengujian

```bash
mosquitto -c Kode_Program/Pengujian/ttu.conf -v
python Kode_Program/Pengujian/logger.py --skenario 10 --arch RTOS --rep 1 --serial COM5
```

Setelah `logger.py` menampilkan `SIAP`, tekan tombol BOOT pada semua *node*. Pilihan lain, seperti
uji gangguan (`--gangguan putus:10:180`), uji pendahuluan (`--pendahuluan P1`), dan rekap
(`--rekap hasil`), dijelaskan pada awal berkas `logger.py`.

Nama Wi-Fi, kata sandi, dan alamat IP pada kode diganti dengan contoh. Pesan Serial pada kode
tidak diubah, sehingga tetap sama dengan isi `serial_raw_*.log` pada data mentah.
