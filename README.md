# Multi-Protocol IoT Gateway Berbasis ESP32: FreeRTOS dan Bare Metal

Repositori ini berisi kode program dan data penelitian untuk makalah berikut.

> V. W. Sulistyanto, I. Setyawan, and I. K. Timotius, "Perbandingan FreeRTOS dan *Bare Metal* pada *Gateway* IoT
> Multiprotokol LoRa dan BLE Berbasis ESP32," *Techné: Jurnal Ilmiah Elektroteknika* (dalam proses pengajuan).

Penelitian ini merupakan bagian dari Tugas Talenta Unggul *Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32
dengan Implementasi FreeRTOS*, Program Studi Teknik Elektro, Fakultas Teknik Elektronika dan Komputer, Universitas
Kristen Satya Wacana, Salatiga.

## Ringkasan penelitian

- *Gateway* ESP32 menerima data dari 1 *node* LoRa (SX1278, 433 MHz) dan 4 *node* BLE (*advertising*), lalu
  meneruskannya ke *broker* MQTT melalui Wi-Fi.
- Dua versi perangkat lunak dibandingkan pada perangkat keras yang sama: versi FreeRTOS (empat *task* pada dua inti
  yang dihubungkan oleh *queue*) dan versi Bare Metal (satu `loop()` dengan *mailbox* satu paket per sumber).
- Pengujian terdiri atas 15 skenario (230 sesi berdurasi 10 menit) dan uji pendahuluan (12 sesi). Kinerja diukur
  dengan *packet loss rate* (PLR) dan latensi di dalam *gateway*.

## Isi repositori

| Folder | Isi |
| --- | --- |
| `Kode_Program/` | Program *gateway* versi FreeRTOS dan Bare Metal, *node* LoRa dan BLE, sketch diagnostik uji pendahuluan, serta program pencatat `logger.py`. Penjelasan ada di `Kode_Program/README.md`. |
| `Lampiran_A_Data_Mentah_TTU/` | Data mentah 242 sesi pengujian dari program pencatat (`summary`, `result`, `mqtt`, `latency`, dan `serial_raw` setiap sesi). Penjelasan lengkap ada di `README.txt` folder ini. |
| `Lampiran_B_Tabel_Hasil_TTU/` | Tabel hasil pengujian (`Tabel_Hasil_Pengujian.xlsx`, Tabel B.1 sampai B.12) dan program `Program/olah_data.py` yang membuatnya dari data mentah. |

## Menjalankan ulang pengolahan data

Program pengolah data memakai Python 3 beserta NumPy, SciPy, dan openpyxl.

```bash
pip install numpy scipy openpyxl
python Lampiran_B_Tabel_Hasil_TTU/Program/olah_data.py
```

Program membaca folder `Lampiran_A_Data_Mentah_TTU/Data_Mentah`, lalu menulis ulang
`Lampiran_B_Tabel_Hasil_TTU/Tabel_Hasil_Pengujian.xlsx`.

## Catatan privasi

Nama jaringan Wi-Fi (SSID) pada data mentah disamarkan menjadi `WiFi_Pengujian` dan `WiFi_Lain_01` sampai
`WiFi_Lain_13`. Nilai kanal dan kuat sinyal tidak diubah, dan tabel hasil yang dibuat ulang dari data ini sama
persis dengan tabel aslinya.
