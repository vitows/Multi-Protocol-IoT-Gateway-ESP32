README - LAMPIRAN A. DATA MENTAH
Proposal Lanjut Tugas Talenta Unggul
Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32 dengan Implementasi FreeRTOS
Vito Wicaksono Sulistyanto (612022018)

Folder ini adalah Lampiran A proposal, yaitu data mentah seluruh sesi pengujian. Data dicatat oleh program logger.py
di laptop selama pengujian dan tidak diubah sesudahnya. Data ini diolah menjadi tabel hasil pengujian pada Lampiran B,
yaitu folder Lampiran_B_Tabel_Hasil_TTU pada repositori yang sama.


ISI FOLDER
Folder ini berisi dua bagian berikut.
  README.txt     Penjelasan isi folder (file ini).
  Data_Mentah/   Data mentah 230 sesi pengujian utama dan 12 sesi uji pendahuluan, satu folder untuk setiap sesi.


SUSUNAN FOLDER DATA_MENTAH
Nama folder menunjukkan nomor skenario dan versi gateway, sedangkan subfolder R01 sampai R10 menunjukkan sesi 1 sampai 10.
Contoh nama folder dan artinya adalah sebagai berikut.
  S01_RTOS/R01           skenario 1, versi FreeRTOS, sesi 1
  S01_BM/R01             skenario 1, versi Bare Metal, sesi 1
  S10_RTOS_TAHAN2/R01    skenario 13 (jaringan tersendat 2 detik), versi FreeRTOS, sesi 1
  S10_RTOS_PUTUS10/R01   skenario 14 (jaringan putus 10 detik), versi FreeRTOS, sesi 1
  S10_RTOS_PUTUS30/R01   skenario 15 (jaringan putus 30 detik), versi FreeRTOS, sesi 1
  PENDAHULUAN/P1/R01     uji pendahuluan P1, sesi 1

Skenario 13 sampai 15 memakai beban skenario 10, sehingga nama foldernya diawali S10. Skenario 4 dan 8 tidak memiliki
folder sendiri, karena skenario 4 sama dengan skenario 1 dan skenario 8 sama dengan skenario 7 (Tabel 10 proposal).
Uji pendahuluan P2 juga tidak memiliki folder, karena P2 hanya berupa pemeriksaan pengaturan scan gateway.

Jumlah folder sesi pada setiap bagian pengujian adalah sebagai berikut.
  Skenario 1 sampai 12 (10 skenario yang diuji)   10 sesi x 2 versi x 10 skenario = 200 sesi
  Skenario 13 sampai 15 (uji gangguan jaringan)    5 sesi x 2 versi x 3 skenario  =  30 sesi
  Uji pendahuluan P1, P3, P4, dan P5               3 sesi x 4 skenario            =  12 sesi
  Jumlah                                                                           = 242 sesi


ISI SETIAP FOLDER SESI
Setiap folder sesi berisi lima file dari program logger.py, yaitu:
  summary_<sesi>.txt      ringkasan hasil sesi yang dapat dibaca langsung, yaitu pengaturan sesi, PLR, latensi,
                          kategori TIPHON, dan jumlah paket yang hilang di radio dan di dalam program
  result_<sesi>.json      hasil sesi dalam format JSON, termasuk nomor urut paket yang hilang (seq_hilang) dan
                          diagnostik radio BLE
  mqtt_<sesi>.csv         setiap paket yang diterima broker MQTT
  latency_<sesi>.csv      latensi setiap paket, yaitu T_receive dan T_send dari log Serial gateway
  serial_raw_<sesi>.log   log Serial gateway selama sesi
<sesi> adalah kode sesi, misalnya S01_BM_R1 untuk skenario 1, versi Bare Metal, sesi 1.


CATATAN TENTANG NAMA DI DALAM FILE
Data mentah tidak diubah setelah pengujian, kecuali nama jaringan Wi-Fi (lihat catatan terakhir di bawah).
Karena itu, beberapa nama di dalam file berbeda dengan proposal, yaitu:
  - "P_sent" pada summary_*.txt dan "p_sent" pada result_*.json sama dengan P_send pada proposal, yaitu jumlah paket
    yang dikirim node.
  - "Letak paket yang hilang" pada summary_*.txt dan kunci "letak" pada result_*.json berisi jumlah paket yang hilang
    di radio dan di dalam program gateway.
  - "Repetisi" atau "rep" sama dengan sesi, "Arsitektur" sama dengan versi, dan "Interval iklan" sama dengan
    advertising interval.
  - Nomor tabel di dalam summary_*.txt, misalnya "Tabel 4" dan "Tabel 22", adalah penomoran lama logger.py saat
    pengujian, bukan nomor tabel pada proposal.
  - Nama jaringan Wi-Fi (SSID) pada result_*.json dan serial_raw_*.log disamarkan untuk menjaga privasi, yaitu
    WiFi_Pengujian untuk jaringan yang dipakai gateway dan WiFi_Lain_01 sampai WiFi_Lain_13 untuk jaringan lain
    di sekitar lokasi uji. Nilai kanal dan kuat sinyal tidak diubah. Penyamaran ini tidak mengubah hasil olah data,
    karena Tabel_Hasil_Pengujian.xlsx yang dibuat ulang dari data ini sama persis dengan tabel aslinya.


ISTILAH (sama dengan proposal)
  Skenario   Satu kombinasi pengaturan uji, yaitu skenario 1 sampai 15 dan uji pendahuluan P1 sampai P5.
  Versi      Versi perangkat lunak gateway, yaitu FreeRTOS atau Bare Metal. Pada nama folder ditulis RTOS dan BM.
  Sesi       Satu kali pengujian selama 10 menit untuk satu skenario pada satu versi.
