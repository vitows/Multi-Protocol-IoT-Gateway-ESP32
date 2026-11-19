README - LAMPIRAN B. TABEL HASIL PENGUJIAN
Proposal Lanjut Tugas Talenta Unggul
Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32 dengan Implementasi FreeRTOS
Vito Wicaksono Sulistyanto (612022018)

Folder ini adalah Lampiran B proposal, yaitu tabel hasil pengujian beserta program yang membuatnya. Seluruh tabel
dihitung dari data mentah pada Lampiran A, yaitu folder Lampiran_A_Data_Mentah_TTU pada repositori yang sama.
Nomor tabel pada file Excel sama dengan nomor yang dirujuk pada Bab VI sampai Bab VIII proposal.
Simpangan baku yang lebih besar daripada rata-rata dijelaskan pada bagian CATATAN SIMPANGAN BAKU di akhir file ini.


ISI FOLDER
Folder ini berisi tiga bagian berikut.
  README.txt                   Penjelasan isi folder (file ini).
  Tabel_Hasil_Pengujian.xlsx   Tabel B.1 sampai B.12. Buka lembar "Daftar isi" lebih dulu.
  Program/olah_data.py         Program Python yang membuat Tabel_Hasil_Pengujian.xlsx dari data mentah.


ISI TABEL_HASIL_PENGUJIAN.XLSX
Setiap tabel disimpan pada satu lembar Excel yang diberi nama sesuai nomor tabelnya, yaitu:
  Tabel B.1    Catatan seluruh sesi pengujian, seperti tanggal dan jam, pengaturan node, dan kanal Wi-Fi.
  Tabel B.2    Hasil uji pendahuluan setiap sesi.
  Tabel B.3    PLR, latensi, capture rate, dan jumlah paket yang hilang di radio dan di dalam program pada setiap sesi.
  Tabel B.4    Rekapitulasi PLR dan latensi rata-rata setiap skenario beserta median, nilai terkecil, dan nilai
               terbesar.
  Tabel B.5    Uji Kruskal-Wallis dan korelasi Spearman antartingkat beban.
  Tabel B.6    Uji post hoc Dunn untuk semua pasangan tingkat beban.
  Tabel B.7    Jumlah paket yang hilang di radio dan di dalam program pada setiap skenario.
  Tabel B.8    Hasil uji gangguan jaringan pada setiap sesi.
  Tabel B.9    Uji beda FreeRTOS dan Bare Metal pada uji gangguan jaringan.
  Tabel B.10   Uji normalitas Shapiro-Wilk.
  Tabel B.11   Uji beda FreeRTOS dan Bare Metal beserta selang kepercayaan 95%.
  Tabel B.12   Batas atas 95% PLR dan latensi rata-rata.
Setiap lembar diawali judul tabel dan catatan singkat tentang isi tabel. Contoh perhitungan setiap tabel dijelaskan
pada Lampiran D proposal.


ISTILAH (sama dengan proposal)
  Skenario   Satu kombinasi pengaturan uji, yaitu skenario 1 sampai 15 dan uji pendahuluan P1 sampai P5.
  Versi      Versi perangkat lunak gateway, yaitu FreeRTOS atau Bare Metal.
  Sesi       Satu kali pengujian selama 10 menit untuk satu skenario pada satu versi.


CATATAN SIMPANGAN BAKU
Pada beberapa baris Tabel B.4 dan B.9, simpangan baku lebih besar daripada rata-rata, misalnya latensi rata-rata
versi FreeRTOS pada skenario 9 (7,8 +/- 12,8 ms). Hal ini bukan kesalahan hitung dan tidak berarti ada latensi
atau PLR yang negatif.

1. Simpangan baku mengukur seberapa jauh nilai sesi menyebar dari rata-rata, bukan batas bawah atau batas atas
   data. Nilai terkecil yang benar-benar terjadi dimuat pada kolom "terkecil" Tabel B.4.
2. Dari rumus s = akar( jumlah (x_i - rata-rata)^2 / (n - 1) ), simpangan baku lebih besar daripada rata-rata
   bila jumlah kuadrat selisih lebih besar daripada (n - 1) x rata-rata^2. PLR dan latensi tidak pernah negatif
   sehingga kuadrat selisih sesi yang bernilai di bawah rata-rata paling besar sama dengan rata-rata^2. Sesi yang
   bernilai jauh di atas rata-rata menyumbang kuadrat selisih yang besar karena selisihnya dikuadratkan.
3. Pada skenario 9, tujuh sesi versi FreeRTOS bernilai 1,0 sampai 1,4 ms, sedangkan sesi 5, 8, dan 10 bernilai
   35,9 ms, 26,8 ms, dan 7,4 ms karena publish MQTT sempat melambat sehingga sebagian paket tertahan di queue.
   Rata-ratanya 7,774 ms sehingga (n - 1) x rata-rata^2 = 9 x 7,774^2 = 543,9, sedangkan kuadrat selisih sesi 5
   saja sudah (35,869 - 7,774)^2 = 789,3. Akibatnya, simpangan baku menjadi 12,8 ms, sedangkan mediannya tetap
   1,1 ms. Perhitungan lengkapnya dimuat pada Lampiran D.4 proposal.
4. Pada PLR skenario 1 sampai 10, hal yang sama terjadi karena banyak sesi tidak kehilangan paket (PLR 0%).
5. Untuk baris seperti ini, gunakan median, nilai terkecil, dan nilai terbesar pada Tabel B.4, atau nilai setiap
   sesi pada Tabel B.3 dan B.8.

Baris yang mengalami hal ini:
  Tabel B.4, PLR total       skenario 1, 2, 3, 5, 6, 7, 9, dan 10 (satu atau kedua versi).
  Tabel B.4, latensi         skenario 9 (FreeRTOS).
  Tabel B.9                  paket hilang saat gangguan (tersendat 2 s dan putus 10 s) dan latensi maksimum
                             (tersendat 2 s).
