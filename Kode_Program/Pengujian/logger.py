#!/usr/bin/env python3
"""
logger.py -- program pencatat pengujian Multi-Protocol IoT Gateway berbasis ESP32

Program ini berjalan di laptop selama pengujian. Pesan dari broker MQTT dan
keluaran Serial gateway dicatat, lalu PLR, latensi, dan letak paket yang hilang
dihitung untuk setiap sesi. Nomor tabel yang disebut di bawah (Tabel 4, 8, 12,
14, 20-23) adalah nomor tabel pada proposal penelitian.

MODE PROGRAM
------------
1) Mode sesi (live)  -- satu sesi uji 10 menit:
      python logger.py --skenario 10 --arch RTOS --rep 1 --serial COM5
   --skenario mengisi otomatis payload, jumlah node, interval BLE, interval LoRa,
   dan P_sent setiap sumber sesuai Tabel 14. Nama sesi otomatis: S10_RTOS_R1.
   Logger menunggu baris S, pertama dari gateway, lalu mencetak "SIAP: tekan BOOT".
   Waktu 10 menit dihitung sejak paket PERTAMA diterima broker, ditambah --grace
   detik agar paket terakhir dan baris S, terakhir ikut tercatat.

   MODE SERI (10 putaran tanpa campur tangan): node dengan REPEAT_RUNS 10 cukup
   ditekan BOOT sekali; logger merekam ke-10 putaran sebagai R1..R10:
      python logger.py --skenario 10 --arch RTOS --runs 10 --serial COM5
   (lanjutkan seri yang terputus dengan --rep 7 --runs 4, dst.)

   UJI GANGGUAN JARINGAN: Mosquitto dijalankan dengan ttu_gangguan.conf
   (port 1884); logger menjadi perantara di port 1883 dan menahan/memutus koneksi
   gateway sesuai jadwal. Firmware gateway TIDAK diubah.
      python logger.py --skenario 10 --arch RTOS --runs 5 --serial COM5 --gangguan putus:10:180
      python logger.py --skenario 10 --arch RTOS --runs 5 --serial COM5 --gangguan tahan:10:60
   Hasil: hasil/S10_RTOS_PUTUS10/..., ringkasan di rekap_hasil.xlsx sheet "Uji Gangguan".

   UJI PENDAHULUAN P1-P5 (1 node BLE, 2000 ms, 7 B, 10 menit). P1 dan P5 sekaligus menjadi uji A/B.
      python logger.py --pendahuluan P1 --runs 3 --serial COM5      (P1, P4, P5: Mosquitto ttu.conf)
      python logger.py --pendahuluan P3 --runs 3 --serial COM5      (gateway Gateway_BLE_Only_Test, tanpa MQTT)
   Hasil: hasil/PENDAHULUAN/<kode>/R01..., tabel di hasil/PENDAHULUAN/tabel_uji_pendahuluan.xlsx.

2) Mode rekap -- setelah banyak sesi selesai (Tabel 20-23):
      python logger.py --rekap hasil
   Membaca semua result_*.json di folder "hasil", menghitung rata-rata ± simpangan
   baku, minimum, dan maksimum per skenario dan arsitektur.

3) Mode hitung ulang -- sesi tersimpan dihitung lagi dari file-nya, mis. bila
   P_sent di OLED node berbeda dari teori:
      python logger.py --ulang S11_RTOS_R1 --sent BLE1=5998 BLE3=5999

4) Mode replay -- analisis ulang log Serial tanpa MQTT:
      python logger.py --replay hasil/serial_raw_S10_RTOS_R1.log --adv-ms 20 --send-ms 500

STRUKTUR FOLDER (default "hasil"): lihat hasil/BACA_SAYA.txt. Ringkasnya:
  hasil/S01_RTOS/seri_S01_RTOS.xlsx   10 repetisi + rata-rata ± SD (satu skenario, satu arsitektur)
  hasil/S01_RTOS/R01..R10/            5 file mentah per repetisi (di bawah)
  hasil/tabel12_catatan_sesi.xlsx, hasil/rekap_hasil.xlsx (Tabel 20-23, oleh --rekap)
  File lama yang masih di satu folder dirapikan dengan: python logger.py --rapikan hasil
  File Excel butuh: pip install openpyxl (tanpa itu, dibuat .csv)

FILE YANG DIHASILKAN PER SESI (di folder hasil/<skenario>/<repetisi>/)
  summary_<sesi>.txt     laporan yang sama dengan yang tampil di layar
  result_<sesi>.json     angka-angka sesi dalam bentuk mesin (dipakai --rekap)
  mqtt_<sesi>.csv        setiap pesan yang tiba di broker (termasuk JSON aslinya)
  latency_<sesi>.csv     T_receive, T_send, latensi tiap paket (baris D, gateway)
  serial_raw_<sesi>.log  semua keluaran Serial gateway apa adanya

TABEL 12 (catatan sesi) dibuat OTOMATIS: hasil/tabel12_catatan_sesi.csv diperbarui
setiap sesi selesai. Tanggal/jam, skenario, repetisi, arsitektur, node, interval,
payload, dan interval iklan diisi dari --skenario. Kanal Wi-Fi, jumlah jaringan, dan
posisi node ditanyakan di layar sebelum sesi (Enter = sama dengan sesi sebelumnya),
atau diberikan lewat --kanal, --jaringan, --posisi.

DEFINISI
  PLR (pers. 7)  = (P_sent - P_receive) / P_sent x 100%, P_receive = paket UNIK
                   (ID + nomor urut) yang tercatat di broker.
  Latensi (8)    = T_send - T_receive, keduanya jam gateway (millis()).
  Capture rate(9)= N_tertangkap / (t_uji / T_adv), T_adv = interval iklan + 5 ms
                   (rata-rata jeda acak 0-10 ms).
  TIPHON (Tab.4) = <3% sangat baik | 3-<15% baik | 15-<25% sedang | >=25% buruk.
  Letak paket hilang (Tab. 22), dari selisih penghitung baris S, gateway:
     hilang di radio      = P_sent - paket diterima gateway
     hilang di program    = dibuang antrean (FreeRTOS) / tertimpa di mailbox (Bare Metal)
     gagal publish        = pubfail

Kebutuhan: pip install paho-mqtt pyserial   (mode --rekap dan --replay tidak butuh)
"""
import argparse
import csv
import glob
import json
import math
import os
import re
import statistics
import sys
import threading
import time

try:
    import paho.mqtt.client as mqtt
except ImportError:          # mode --rekap / --replay tidak butuh paho
    mqtt = None

try:
    import serial
except ImportError:
    serial = None

SOURCES = ["LORA1", "BLE1", "BLE2", "BLE3", "BLE4"]
BLE_SOURCES = ["BLE1", "BLE2", "BLE3", "BLE4"]
TEST_S = 600                 # durasi uji node (10 menit)
ADV_MS_DEFAULT = 20          # interval advertising node BLE (ms)
LORA_INTERVAL_S = {7: 4, 16: 6, 32: 8}   # interval kirim LoRa (s) per ukuran payload (Tabel 8); 7 B -> 150 paket per 10 menit

# Skenario pengujian (Tabel 14): no -> (jenis uji, payload, node BLE, interval BLE ms)
SKENARIO = {
    1: ("Payload", 7, 1, 2000),
    2: ("Payload", 16, 1, 2000),
    3: ("Payload", 32, 1, 2000),
    4: ("Skalabilitas", 7, 1, 2000),
    5: ("Skalabilitas", 7, 2, 2000),
    6: ("Skalabilitas", 7, 3, 2000),
    7: ("Skalabilitas", 7, 4, 2000),
    8: ("Kepadatan", 7, 4, 2000),
    9: ("Kepadatan", 7, 4, 1000),
    10: ("Kepadatan", 7, 4, 500),
    11: ("Kepadatan", 7, 4, 100),
    12: ("Stress test", 32, 4, 100),
}
SAMA_DENGAN = {4: 1, 8: 7}   # skenario yang sama: datanya dipakai bersama
ARCH_NAME = {"RTOS": "FreeRTOS", "BM": "Bare Metal"}

# Uji pendahuluan: 1 node BLE, interval kirim 2000 ms, payload 7 B, 10 menit.
# wns/coex = pengaturan radio gateway yang harus terbaca pada baris "Radio: WIFI_NO_SLEEP=.. COEX_PREFER_BT=..";
# None = gateway khusus BLE (tanpa Wi-Fi/MQTT). P1 dan P5 memakai firmware gateway yang sama (uji A/B).
PENDAHULUAN = {
    "P1":  {"kondisi": "Iklan 100 ms, Wi-Fi aktif", "adv": 100, "gateway": "Gateway_FreeRTOS.ino (bawaan)",
            "wns": 1, "coex": 0},
    "P3":  {"kondisi": "Iklan 100 ms, tanpa Wi-Fi", "adv": 100, "gateway": "tools/Gateway_BLE_Only_Test.ino",
            "wns": None, "coex": None},
    "P4":  {"kondisi": "Iklan 100 ms, pengaturan Wi-Fi diubah", "adv": 100,
            "gateway": "Gateway_FreeRTOS.ino dengan WIFI_NO_SLEEP 0 dan COEX_PREFER_BT 1", "wns": 0, "coex": 1},
    "P5":  {"kondisi": "Iklan 20 ms, Wi-Fi aktif", "adv": 20, "gateway": "Gateway_FreeRTOS.ino (bawaan)",
            "wns": 1, "coex": 0},
}
P_SEND_MS, P_SENT_BLE = 2000, 300
SERIAL_ONLY = [False]                        # True untuk P3: tanpa MQTT, P_receive dari baris D, gateway
ble_only_seen = [False]                      # gateway khusus BLE terdeteksi di Serial
RADIO_RE = re.compile(r"WIFI_NO_SLEEP=(\d+)\s+COEX_PREFER_BT=(\d+)")


def skenario_config(no):
    jenis, payload, nodes, send_ms = SKENARIO[no]
    lora_s = LORA_INTERVAL_S[payload]
    sent = {"LORA1": math.ceil(TEST_S / lora_s)}
    for i in range(1, nodes + 1):
        sent[f"BLE{i}"] = TEST_S * 1000 // send_ms
    return {"jenis": jenis, "payload": payload, "nodes": nodes, "send_ms": send_ms,
            "lora_s": lora_s, "sent": sent}


def norm_arch(a):
    a = (a or "").strip().upper().replace(" ", "").replace("_", "")
    if a in ("RTOS", "FREERTOS"):
        return "RTOS"
    if a in ("BM", "BAREMETAL", "LOOP"):
        return "BM"
    return a


# ============================ STATE SESI =======================================
lock = threading.Lock()
received = {s: set() for s in SOURCES}      # nomor urut unik per sumber (dari broker)
msg_count = {s: 0 for s in SOURCES}
mqtt_rows = []                               # (pc_time, src, seq, node_ts, gw_rx, len, json)
lat_rows = []                                # (src, seq, rx, tsend, lat, len)
lat_by_src = {s: [] for s in SOURCES}
d_seqs = {s: set() for s in SOURCES}
len_by_src = {s: set() for s in SOURCES}
diag_rows = {s: [] for s in BLE_SOURCES}
s_rows = []                                  # baris S, yang sudah diurai (dict)
stop_flag = threading.Event()
first_pkt_time = [None]
mqtt_ok = threading.Event()
serial_ok = threading.Event()
ble_scan_restart_count = 0
raw_serial_lines = []
radio_cfg_line = [None]                      # baris "Radio: WIFI_NO_SLEEP=.. COEX_PREFER_BT=.."
adv_lines = []
last_msg_time = [0.0]                        # waktu pesan MQTT terakhir (untuk memisahkan putaran)
accepting = [True]                           # False sesaat ketika satu putaran sedang ditutup
raw_out = {"fh": None}                       # file serial_raw putaran yang sedang berjalan
raw_lock = threading.Lock()


def reset_run_state():
    """Kosongkan semua data per-sesi sebelum putaran berikutnya (mode --runs)."""
    global ble_scan_restart_count
    with lock:
        for s in SOURCES:
            received[s].clear(); msg_count[s] = 0
            lat_by_src[s].clear(); d_seqs[s].clear(); len_by_src[s].clear()
        for s in BLE_SOURCES:
            diag_rows[s].clear()
        mqtt_rows.clear(); lat_rows.clear(); s_rows.clear()
        raw_serial_lines.clear(); adv_lines.clear()
        first_pkt_time[0] = None
        ble_scan_restart_count = 0
        gangguan_events.clear()


# ============================ UJI GANGGUAN JARINGAN ===========================
#  Logger menjadi PERANTARA antara gateway dan Mosquitto:
#     gateway --(port 1883)--> logger.py (perantara) --(port 1884)--> Mosquitto
#  Firmware gateway tidak diubah (tetap MQTT_PORT 1883); Mosquitto dijalankan
#  dengan ttu_gangguan.conf (port 1884, hanya localhost). Logger sendiri membaca
#  broker langsung di 1884, jadi pengukurannya tidak ikut terganggu.
#    tahan : sambungan tetap ada, tetapi aliran data dihentikan DUR detik
#            (jaringan tersendat; publish gateway tertahan).
#    putus : semua sambungan gateway ditutup; selama DUR detik sambungan baru
#            diterima tetapi tidak diteruskan (broker tak terjangkau) -> gateway
#            harus menyambung ulang setelah gangguan selesai.
import select
import socket

gangguan_events = []                         # [{"mulai": t, "selesai": t}] putaran berjalan


RCVBUF_PERANTARA = [2048]   # byte; 0 = ukuran bawaan sistem operasi. Lihat --buffer-lama


class PerantaraGangguan:
    def __init__(self, listen_port, broker_host, broker_port):
        self.listen_port, self.broker = listen_port, (broker_host, broker_port)
        self.mode = None
        self.aktif = threading.Event()
        self.lock = threading.Lock()
        self.conns = []                       # [(sock_gateway, sock_broker or None)]
        self.n_sambung = 0

    def start(self):
        self.srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # Buffer terima dibuat kecil (diwarisi socket gateway). Tanpa ini laptop menampung puluhan KB
        # data selama "tahan", sehingga gateway tidak pernah ikut tertahan dan kedua arsitektur sama saja.
        if RCVBUF_PERANTARA[0]:
            self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_PERANTARA[0])
        try:
            self.srv.bind(("0.0.0.0", self.listen_port))
        except OSError as e:
            sys.exit(f"[GANGGUAN] port {self.listen_port} sedang dipakai ({e}). Hentikan Mosquitto yang memakai "
                     f"ttu.conf, lalu jalankan Mosquitto dengan ttu_gangguan.conf (port {self.broker[1]}).")
        self.srv.listen(8)
        threading.Thread(target=self._accept, daemon=True).start()
        print(f"[GANGGUAN] perantara aktif: gateway -> :{self.listen_port} -> Mosquitto :{self.broker[1]}")

    def _accept(self):
        while not stop_flag.is_set():
            try:
                g, addr = self.srv.accept()
            except OSError:
                return
            g.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if RCVBUF_PERANTARA[0]:
                g.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, RCVBUF_PERANTARA[0])
            self.n_sambung += 1
            if self.aktif.is_set() and self.mode == "putus":
                with self.lock:                # broker "tak terjangkau": diterima, tidak diteruskan
                    self.conns.append((g, None))
                threading.Thread(target=self._buang, args=(g,), daemon=True).start()
                continue
            try:
                b = socket.create_connection(self.broker, timeout=5)
                b.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                g.close()
                continue
            with self.lock:
                self.conns.append((g, b))
            threading.Thread(target=self._pipa, args=(g, b), daemon=True).start()
            threading.Thread(target=self._pipa, args=(b, g), daemon=True).start()

    def _buang(self, g):
        try:
            while not stop_flag.is_set():
                r, _, _ = select.select([g], [], [], 0.2)
                if r and not g.recv(4096):
                    break
        except OSError:
            pass

    def _pipa(self, src, dst):
        try:
            while not stop_flag.is_set():
                if self.aktif.is_set() and self.mode == "tahan":
                    time.sleep(0.02)          # berhenti membaca -> buffer TCP penuh -> pengirim tertahan
                    continue
                r, _, _ = select.select([src], [], [], 0.2)
                if not r:
                    continue
                data = src.recv(4096)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        for sk in (src, dst):
            try:
                sk.close()
            except OSError:
                pass

    def _tutup_semua(self):
        with self.lock:
            cs, self.conns = self.conns, []
        for g, b in cs:
            for sk in (g, b):
                if sk:
                    try:
                        sk.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
                    try:
                        sk.close()
                    except OSError:
                        pass

    def mulai(self, mode):
        self.mode = mode
        self.aktif.set()
        if mode == "putus":
            self._tutup_semua()

    def selesai(self):
        self.aktif.clear()
        if self.mode == "putus":
            self._tutup_semua()               # tutup sambungan "buntu" -> gateway menyambung ulang ke broker


def jadwal_gangguan(proxy, mode, dur, period, offset, duration):
    """Thread: untuk setiap putaran, gangguan pertama 'offset' s setelah paket pertama,
    lalu tiap 'period' s, berhenti sebelum 30 s terakhir putaran."""
    while not stop_flag.is_set():
        fp = first_pkt_time[0]
        if fp is None:
            time.sleep(0.2)
            continue
        t = fp + offset
        while not stop_flag.is_set() and first_pkt_time[0] == fp and t + dur <= fp + duration - 30:
            while time.time() < t and first_pkt_time[0] == fp and not stop_flag.is_set():
                time.sleep(0.1)
            if first_pkt_time[0] != fp or stop_flag.is_set():
                break
            proxy.mulai(mode)
            ev = {"mulai": time.time(), "selesai": None}
            with lock:
                gangguan_events.append(ev)
            print(f"[GANGGUAN] {mode.upper()} dimulai ({dur} s)")
            t_end = time.time() + dur
            while time.time() < t_end and not stop_flag.is_set():
                time.sleep(0.1)
            proxy.selesai()
            ev["selesai"] = time.time()
            print(f"[GANGGUAN] {mode.upper()} selesai")
            t += period
        while first_pkt_time[0] == fp and not stop_flag.is_set():
            time.sleep(0.2)                   # tunggu putaran berikutnya


def analisis_gangguan(events, sent_arg, send_ms, lora_s, rows=None):
    """Hilang saat/di luar gangguan, waktu pulih, dan paket terlambat, dari waktu tiba di broker."""
    rows = mqtt_rows if rows is None else rows
    ev = [dict(e) for e in events if e.get("selesai")]
    if not ev:
        return None
    arr = {}                                    # (src, seq) -> waktu tiba pertama di broker
    for r in rows:
        k = (r[1], int(r[2]))
        t = float(r[0])
        if k not in arr or t < arr[k]:
            arr[k] = t
    # waktu pulih: dari gangguan selesai sampai paket pertama (sumber mana pun) tiba lagi di broker
    times = sorted(arr.values())
    pulih = []
    for e in ev:
        nxt = next((t for t in times if t >= e["selesai"]), None)
        pulih.append(round(nxt - e["selesai"], 2) if nxt is not None else None)
        e["pulih_s"] = pulih[-1] or 0
    out = {"jumlah_gangguan": len(ev), "sumber": {}}
    tot = {"hilang_saat": 0, "hilang_luar": 0, "terlambat": 0, "diterima": 0}
    for s, n in sent_arg.items():
        itv = (lora_s or 0) if s == "LORA1" else (send_ms or 0) / 1000.0
        got = [(q, t) for (src, q), t in arr.items() if src == s and q < n]
        if not got or not itv:
            continue
        offs = sorted(t - q * itv for q, t in got)
        t0 = offs[max(0, int(len(offs) * 0.1) - 1)]      # persentil 10: perkiraan waktu kirim seq 0
        win = [(e["mulai"], e["selesai"] + e.get("pulih_s", 0) + 2.0) for e in ev]
        saat = luar = 0
        got_set = {q for q, _ in got}
        for q in range(n):
            if q in got_set:
                continue
            tk = t0 + q * itv
            if any(a <= tk <= b for a, b in win):
                saat += 1
            else:
                luar += 1
        telat = sum(1 for q, t in got if t - (t0 + q * itv) > 1.0)
        out["sumber"][s] = {"hilang_saat_gangguan": saat, "hilang_di_luar": luar, "terlambat_gt1s": telat}
        tot["hilang_saat"] += saat; tot["hilang_luar"] += luar; tot["terlambat"] += telat
        tot["diterima"] += len(got)
    out["pulih_s"] = pulih
    pv = [x for x in pulih if x is not None]
    out["pulih_rata_s"] = round(statistics.mean(pv), 2) if pv else None
    out["pulih_maks_s"] = max(pv) if pv else None
    out["total"] = tot
    return out


def open_raw(path):
    with raw_lock:
        if raw_out["fh"]:
            raw_out["fh"].close()
        raw_out["fh"] = open(path, "w", encoding="utf-8") if path else None

LINE_RE = re.compile(r"(?:^|[\s\]>])([GDS],.*)$")
G_FIELD_RE = re.compile(r"(BLE\d)=(\d+)/(\d+)/(\d+)/(-?\d+)/(-?\d+)/(\d+)")
S_SRC_RE = re.compile(r"(LORA1|BLE\d)=(\d+)/(\d+)/(\d+)/len(\d+)")
S_KV_RE = re.compile(r"(qmax|rawdrop|pubfail|lorabadlen|badid|restok|restmiss)=(\d+)")
S_ARCH_RE = re.compile(r"arch=(\w+)")


def parse_s_line(body):
    d = {"src": {}, "arch": None}
    for m in S_SRC_RE.finditer(body):
        d["src"][m.group(1)] = {"rx": int(m.group(2)), "pub": int(m.group(3)),
                                "lost_prog": int(m.group(4)), "len": int(m.group(5))}
    for m in S_KV_RE.finditer(body):
        d[m.group(1)] = int(m.group(2))
    m = S_ARCH_RE.search(body)
    if m:
        d["arch"] = m.group(1).upper()
    return d if d["src"] else None


def on_connect(client, userdata, flags, rc, properties=None):
    print(f"[MQTT] terhubung ke broker (rc={rc}), subscribe {userdata['topic']}")
    client.subscribe(userdata["topic"], qos=0)
    mqtt_ok.set()


def on_message(client, userdata, msg):
    raw = msg.payload.decode(errors="replace")
    try:
        d = json.loads(raw)
        src, seq = d["src"], int(d["seq"])
    except Exception:
        return
    if src not in received or not accepting[0]:
        return
    with lock:
        last_msg_time[0] = time.time()
        if first_pkt_time[0] is None:
            first_pkt_time[0] = time.time()
        received[src].add(seq)
        msg_count[src] += 1
        mqtt_rows.append((f"{time.time():.3f}", src, seq, d.get("ts"), d.get("rx"), d.get("len"), raw))


def handle_serial_line(line):
    """Proses satu baris Serial gateway. Dipakai mode live dan --replay."""
    global ble_scan_restart_count
    with lock:
        raw_serial_lines.append(line)
        if "distart ulang" in line or "scan mati" in line:
            ble_scan_restart_count += 1
        if "Radio: WIFI_NO_SLEEP" in line:
            radio_cfg_line[0] = line[line.index("Radio:"):]
        if "advInterval" in line:
            adv_lines.append(line)
        if "BLE_ONLY" in line or "BLE-only" in line:
            ble_only_seen[0] = True

    m = LINE_RE.search(line)
    if not m:
        return
    body = m.group(1)

    if body.startswith("D,"):
        p = body.split(",")
        if len(p) in (6, 7):
            try:
                src, seq = p[1], int(p[2])
                rx, ts, lat = int(p[3]), int(p[4]), int(p[5])
                plen = int(p[6]) if len(p) == 7 else None
            except ValueError:
                return
            with lock:
                lat_rows.append((src, seq, rx, ts, lat, plen))
                if src in lat_by_src:
                    if not SERIAL_ONLY[0]:             # gateway khusus BLE tidak mengukur latensi
                        lat_by_src[src].append(lat)
                    d_seqs[src].add(seq)
                if plen is not None and src in len_by_src:
                    len_by_src[src].add(plen)
                if SERIAL_ONLY[0] and src in received and accepting[0]:
                    last_msg_time[0] = time.time()     # tanpa MQTT: paket "sampai" = tercatat gateway
                    if first_pkt_time[0] is None:
                        first_pkt_time[0] = time.time()
                    received[src].add(seq)
                    msg_count[src] += 1
    elif body.startswith("S,"):
        d = parse_s_line(body)
        if d:
            with lock:
                d["pc_time"] = time.time()
                d["before_first_pkt"] = first_pkt_time[0] is None
                s_rows.append(d)
            serial_ok.set()
    elif body.startswith("G,"):
        parts = body.split(",", 2)
        if len(parts) < 3:
            return
        try:
            ms = int(parts[1])
        except ValueError:
            return
        with lock:
            for fm in G_FIELD_RE.finditer(parts[2]):
                src = fm.group(1)
                if src in diag_rows:
                    diag_rows[src].append({
                        "ms": ms, "raw": int(fm.group(2)), "rawTot": int(fm.group(3)),
                        "gap": int(fm.group(4)), "rssiAvg": int(fm.group(5)),
                        "rssiMin": int(fm.group(6)), "miss": int(fm.group(7)),
                    })


def serial_reader(port, baud):
    if serial is None:
        print("[SERIAL] pyserial belum terpasang (pip install pyserial) -> latensi & diagnostik dilewati")
        return
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except Exception as e:
        print(f"[SERIAL] GAGAL membuka {port}: {e}")
        print("         Tutup Serial Monitor Arduino IDE (satu port hanya bisa dibuka satu program).")
        return
    print(f"[SERIAL] membaca {port} @ {baud}")
    while not stop_flag.is_set():
        try:
            line = ser.readline().decode(errors="ignore").strip()
        except Exception:
            continue
        if not line:
            continue
        with raw_lock:
            if raw_out["fh"]:
                raw_out["fh"].write(f"[{time.time():.3f}] {line}\n")
                raw_out["fh"].flush()
        handle_serial_line(line)
    ser.close()


# ============================ PERHITUNGAN ======================================
def tiphon_category(plr):
    """Kategori TIPHON (Tabel 4), batas tanpa celah."""
    if plr is None or plr != plr:
        return "-"
    if plr < 3:
        return "Sangat baik"
    if plr < 15:
        return "Baik"
    if plr < 25:
        return "Sedang"
    return "Buruk"


def pct(values, q):
    if not values:
        return None
    v = sorted(values)
    k = min(len(v) - 1, int(round(q / 100.0 * (len(v) - 1))))
    return v[k]


def lat_stats(values):
    if not values:
        return None
    return {"n": len(values), "mean": statistics.mean(values), "p50": pct(values, 50),
            "p95": pct(values, 95), "max": max(values)}


def s_delta():
    """Selisih penghitung baris S, selama sesi: (terakhir) - (baseline sebelum paket pertama).
    Penghitung gateway kumulatif sejak boot, jadi baseline wajib dikurangkan."""
    if not s_rows:
        return None
    base = None
    for r in s_rows:
        if r.get("before_first_pkt"):
            base = r
    last = s_rows[-1]
    if base is None or base is last:
        base = None
    out = {"arch": last.get("arch"), "src": {}, "baseline": base is not None}
    reset = False
    if base:
        for s, v in last["src"].items():
            b = base["src"].get(s)
            if b and v["rx"] < b["rx"]:
                reset = True
    if reset:
        base = None
        out["baseline"] = False
        out["catatan"] = "gateway reset di tengah sesi; penghitung dipakai apa adanya"
    for s, v in last["src"].items():
        b = base["src"].get(s) if base else None
        out["src"][s] = {k: v[k] - (b[k] if b else 0) for k in ("rx", "pub", "lost_prog")}
        out["src"][s]["len"] = v["len"]
    for k in ("qmax", "rawdrop", "pubfail", "lorabadlen", "badid", "restok", "restmiss"):
        if k in last:
            out[k] = last[k] - (base.get(k, 0) if (base and k != "qmax") else 0)
    return out


def active_span(rows):
    idx = [i for i, r in enumerate(rows) if r["raw"] > 0]
    if not idx:
        return None
    i0, i1 = idx[0], idx[-1]
    start_ms = rows[i0 - 1]["ms"] if i0 > 0 else rows[i0]["ms"] - 10000
    base_miss = rows[i0 - 1]["miss"] if i0 > 0 else 0
    dur_s = max(1, rows[i1]["ms"] - start_ms) / 1000.0
    return rows[i0:i1 + 1], dur_s, max(0, rows[i1]["miss"] - base_miss)


def diag_source(s, adv_ms, send_ms):
    span = active_span(diag_rows[s])
    if not span:
        return None
    ws, dur_s, radio_miss = span
    raw = sum(w["raw"] for w in ws)
    act = [w for w in ws if w["raw"] > 0]
    d = {"aktif_s": round(dur_s), "iklan_tertangkap": raw, "seq_terlewat_radio": radio_miss,
         "celah_terlama_ms": max(w["gap"] for w in ws),
         "jendela_kosong": sum(1 for w in ws if w["raw"] == 0),
         "rssi_avg": round(sum(w["rssiAvg"] for w in act) / len(act)),
         "rssi_min": min(w["rssiMin"] for w in act)}
    if adv_ms:
        adv_events = dur_s * 1000.0 / (adv_ms + 5.0)
        d["iklan_dipancarkan_est"] = round(adv_events)
        d["capture_rate"] = round(100.0 * min(raw / adv_events, 1.0), 1) if adv_events else None
        if send_ms:
            d["salinan_per_seq"] = round(send_ms / (adv_ms + 5.0), 1)
    return d


def compute(sent_arg, adv_ms, send_ms):
    """Semua angka sesi dalam satu dict (dipakai laporan dan result_*.json)."""
    res = {"sumber": {}, "protokol": {}, "total": None}
    sd = s_delta()
    groups = {"LoRa": ["LORA1"], "BLE": BLE_SOURCES}
    tot_sent = tot_recv = 0
    grp = {g: [0, 0, []] for g in groups}
    all_lat = []
    for s in SOURCES:
        got = received[s]
        if s not in sent_arg and not got:
            continue
        sent_n = sent_arg.get(s, (max(got) + 1) if got else 0)
        recv_n = len([x for x in got if x < sent_n])
        loss = sent_n - recv_n
        plr = 100.0 * loss / sent_n if sent_n else None
        lat = lat_by_src[s]
        r = {"p_sent": sent_n, "p_recv": recv_n, "hilang": loss,
             "plr": round(plr, 4) if plr is not None else None, "tiphon": tiphon_category(plr),
             "latensi": lat_stats(lat), "payload_teramati": sorted(len_by_src[s]),
             "duplikat_broker": max(0, msg_count[s] - len(got)),
             "seq_hilang": [x for x in range(sent_n) if x not in got]}
        if sd and s in sd["src"]:
            g = sd["src"][s]
            r["letak"] = {"diterima_gateway": g["rx"],
                          "hilang_radio": max(0, sent_n - g["rx"]),
                          "hilang_program": g["lost_prog"],
                          "dipublish_gateway": g["pub"]}
        if s in diag_rows:
            dg = diag_source(s, adv_ms, send_ms)
            if dg:
                r["radio"] = dg
        res["sumber"][s] = r
        tot_sent += sent_n
        tot_recv += recv_n
        all_lat += lat
        for gname, members in groups.items():
            if s in members:
                grp[gname][0] += sent_n
                grp[gname][1] += recv_n
                grp[gname][2] += lat
    for gname, (gs, gr, gl) in grp.items():
        if gs:
            p = 100.0 * (gs - gr) / gs
            res["protokol"][gname] = {"p_sent": gs, "p_recv": gr, "plr": round(p, 4),
                                      "tiphon": tiphon_category(p), "latensi": lat_stats(gl)}
    if tot_sent:
        p = 100.0 * (tot_sent - tot_recv) / tot_sent
        res["total"] = {"p_sent": tot_sent, "p_recv": tot_recv, "hilang": tot_sent - tot_recv,
                        "plr": round(p, 4), "tiphon": tiphon_category(p), "latensi": lat_stats(all_lat)}
    if sd:
        lr = sum(v["letak"]["hilang_radio"] for v in res["sumber"].values() if "letak" in v)
        lp = sum(v["letak"]["hilang_program"] for v in res["sumber"].values() if "letak" in v)
        pf = sd.get("pubfail", 0)
        rd = sd.get("rawdrop", 0)
        res["letak_total"] = {
            "hilang_radio": max(0, lr - rd), "hilang_program": lp + rd, "gagal_publish": pf,
            "rawdrop": rd, "qmax": sd.get("qmax"), "restok": sd.get("restok"),
            "restmiss": sd.get("restmiss"), "arch_gateway": sd.get("arch"),
            "baseline": sd.get("baseline"), "catatan": sd.get("catatan")}
    return res


def fmt_lat(ls):
    if not ls:
        return f"{'-':>8} {'-':>6} {'-':>6} {'-':>6}"
    return f"{ls['mean']:8.1f} {ls['p50']:6.0f} {ls['p95']:6.0f} {ls['max']:6.0f}"


def report_text(meta, res, adv_ms, send_ms, files_note=True, session=""):
    L = [f"=== HASIL SESI: {session} ==="]
    L += meta
    hdr = (f"{'Sumber':7} {'P_sent':>7} {'P_recv':>7} {'Hilang':>7} {'PLR%':>7} | "
           f"{'Lat avg':>8} {'p50':>6} {'p95':>6} {'max':>6} (ms)  Kategori TIPHON")
    L.append(hdr)
    L.append("-" * len(hdr))
    for s, r in res["sumber"].items():
        note = f"  payload teramati={r['payload_teramati']}B" if r["payload_teramati"] else ""
        L.append(f"{s:7} {r['p_sent']:7d} {r['p_recv']:7d} {r['hilang']:7d} {r['plr']:7.2f} | "
                 f"{fmt_lat(r['latensi'])}  {r['tiphon']}{note}")
    L.append("-" * len(hdr))
    for g, r in res["protokol"].items():
        L.append(f"{g:7} {r['p_sent']:7d} {r['p_recv']:7d} {r['p_sent'] - r['p_recv']:7d} {r['plr']:7.2f} | "
                 f"{fmt_lat(r['latensi'])}  {r['tiphon']}")
    t = res["total"]
    if t:
        L.append(f"{'TOTAL':7} {t['p_sent']:7d} {t['p_recv']:7d} {t['hilang']:7d} {t['plr']:7.2f} | "
                 f"{fmt_lat(t['latensi'])}  {t['tiphon']}")
    L.append("\nKategori TIPHON (Tabel 4): <3% sangat baik | 3-<15% baik | 15-<25% sedang | >=25% buruk")

    miss = [(s, r["seq_hilang"]) for s, r in res["sumber"].items() if r["seq_hilang"]]
    if miss:
        L.append("\nNomor urut yang hilang:")
        for s, m in miss:
            L.append(f"  {s}: {len(m)} hilang, contoh seq: {m[:30]}{' ...' if len(m) > 30 else ''}")
    dup = [(s, r["duplikat_broker"]) for s, r in res["sumber"].items() if r["duplikat_broker"]]
    for s, n in dup:
        L.append(f"  {s}: {n} pesan duplikat di broker (tidak dihitung ganda)")

    lt = res.get("letak_total")
    if lt:
        arch = lt.get("arch_gateway")
        prog = "tertimpa di mailbox" if arch == "BM" else "dibuang antrean"
        L.append(f"\nLetak paket yang hilang (Tabel 22) -- gateway melapor arch={arch or '?'}:")
        L.append(f"  {'Sumber':7} {'P_sent':>7} {'diterima gw':>12} {'hilang radio':>13} {prog:>20} {'dipublish gw':>13}")
        for s, r in res["sumber"].items():
            if "letak" in r:
                g = r["letak"]
                L.append(f"  {s:7} {r['p_sent']:7d} {g['diterima_gateway']:12d} {g['hilang_radio']:13d} "
                         f"{g['hilang_program']:20d} {g['dipublish_gateway']:13d}")
        L.append(f"  TOTAL: hilang di radio={lt['hilang_radio']} | hilang di program={lt['hilang_program']}"
                 f" | gagal publish={lt['gagal_publish']}"
                 + (f" | rawdrop={lt['rawdrop']}" if lt.get("rawdrop") else "")
                 + (f" | antrean maks (qmax)={lt['qmax']}" if lt.get("qmax") is not None else ""))
        if t:
            selisih = t["hilang"] - (lt["hilang_radio"] + lt["hilang_program"] + lt["gagal_publish"])
            if selisih:
                L.append(f"  Selisih dengan hilang di broker: {selisih} paket (positif = hilang antara gateway "
                         "dan broker/logger; negatif = paket di luar rentang P_sent atau baseline tidak tepat)")
        if not lt.get("baseline"):
            L.append("  PERINGATAN: baseline baris S, sebelum paket pertama tidak ada"
                     + (f" ({lt['catatan']})" if lt.get("catatan") else "")
                     + " -> angka di atas memuat paket dari sebelum sesi. Lain kali tekan BOOT setelah 'SIAP'.")
        if lt.get("restok") is not None and (lt.get("restok") or lt.get("restmiss")):
            L.append(f"  Payload 32 B: iklan lengkap (utama + scan response)={lt['restok']}, "
                     f"tanpa scan response={lt['restmiss']}")

    gg = res.get("gangguan")
    if gg:
        t = gg["total"]
        L.append(f"\nUji gangguan jaringan ({gg.get('mode', '-')} {gg.get('durasi_s', '-')} s tiap "
                 f"{gg.get('periode_s', '-')} s, {gg['jumlah_gangguan']} kali):")
        L.append(f"  Paket hilang SAAT gangguan (termasuk masa pulih)={t['hilang_saat']} | di luar gangguan="
                 f"{t['hilang_luar']} | terlambat >1 s tetapi sampai={t['terlambat']}")
        L.append(f"  Waktu pulih (gangguan selesai -> paket pertama tiba lagi): rata-rata "
                 f"{gg.get('pulih_rata_s', '-')} s, maks {gg.get('pulih_maks_s', '-')} s  {gg.get('pulih_s')}")
        for s, v in gg["sumber"].items():
            L.append(f"  {s}: hilang saat gangguan={v['hilang_saat_gangguan']}, di luar={v['hilang_di_luar']}, "
                     f"terlambat={v['terlambat_gt1s']}")

    rad = [(s, r["radio"]) for s, r in res["sumber"].items() if "radio" in r]
    if rad:
        L.append("\nDiagnostik radio BLE (baris G, gateway):")
        for s, d in rad:
            cap = f" | capture rate={d['capture_rate']}%" if d.get("capture_rate") is not None else ""
            L.append(f"  {s}: iklan tertangkap={d['iklan_tertangkap']}{cap} | seq terlewat di radio="
                     f"{d['seq_terlewat_radio']} | celah terlama={d['celah_terlama_ms']} ms | "
                     f"RSSI rata2={d['rssi_avg']} dBm, terlemah={d['rssi_min']} dBm")
        if not adv_ms:
            L.append("  (beri --adv-ms untuk capture rate)")
    elif files_note:
        L.append("\nDiagnostik radio BLE: tidak ada baris 'G,' (DIAG_MODE 0, firmware lama, atau --serial tidak dipakai).")
    if radio_cfg_line[0]:
        L.append(f"\nPengaturan radio gateway: {radio_cfg_line[0]}")
    L.append(f"Scan BLE restart selama sesi: {ble_scan_restart_count}x")
    return "\n".join(L)


# ============================ MODE REPLAY ======================================
def run_replay(path, adv_ms, send_ms, sent_arg):
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                handle_serial_line(line)
    # Baris S, pertama di log = baseline (log dimulai sebelum node menekan BOOT).
    for i, r in enumerate(s_rows):
        r["before_first_pkt"] = (i == 0)
    n_g = sum(len(v) for v in diag_rows.values())
    print(f"=== ANALISIS OFFLINE: {path} ===")
    print(f"{len(raw_serial_lines)} baris dibaca, {n_g} catatan G, , {len(lat_rows)} baris D, , {len(s_rows)} baris S,")
    print("(Mode replay tidak punya data broker; PLR dihitung dari baris D, = paket yang berhasil dipublish gateway.)")
    for s in SOURCES:
        received[s] |= d_seqs[s]
        msg_count[s] = len(received[s])
    res = compute(sent_arg, adv_ms, send_ms)
    print(report_text([], res, adv_ms, send_ms, session=os.path.basename(path)))


# ============================ STRUKTUR FOLDER HASIL ===========================
#  hasil/
#    BACA_SAYA.txt                   penjelasan isi folder
#    tabel12_catatan_sesi.xlsx       Tabel 12: satu baris per sesi
#    rekap_hasil.xlsx, rekap.txt     Tabel 20-23 (dibuat oleh --rekap)
#    S01_RTOS/                       satu folder per skenario + arsitektur
#      seri_S01_RTOS.xlsx / .txt     10 repetisi dalam satu tabel + rata-rata ± SD
#      R01/ ... R10/                 data mentah tiap repetisi (5 file)
#    lainnya/<sesi>/                 sesi di luar matriks (mis. uji A/B)
#    _logger/                        pengaturan internal logger (boleh diabaikan)
SESI_RE = re.compile(r"^S(\d{2})_(RTOS|BM)(_(?:TAHAN|PUTUS)\d+)?_R(\d+)$")


PEND_RE = re.compile(r"^(P\d[a-z]?)_R(\d+)$")


def lokasi_sesi(outdir, session):
    """(folder sesi, nama grup) -- grup = 'S01_RTOS' untuk sesi matriks, None untuk lainnya."""
    mp = PEND_RE.match(session)
    if mp:
        return os.path.join(outdir, "PENDAHULUAN", mp.group(1), f"R{int(mp.group(2)):02d}"), None
    m = SESI_RE.match(session)
    if m:
        grp = f"S{m.group(1)}_{m.group(2)}{m.group(3) or ''}"
        return os.path.join(outdir, grp, f"R{int(m.group(4)):02d}"), grp
    return os.path.join(outdir, "lainnya", session), None


def path_sesi(outdir, session, prefix, ext, buat=False):
    d, _ = lokasi_sesi(outdir, session)
    if buat:
        os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"{prefix}_{session}.{ext}")
    if not buat and not os.path.exists(p):
        flat = os.path.join(outdir, f"{prefix}_{session}.{ext}")   # struktur lama (semua di satu folder)
        if os.path.exists(flat):
            return flat
    return p


def semua_result(outdir):
    return sorted(set(glob.glob(os.path.join(outdir, "**", "result_*.json"), recursive=True)))


def tulis_tabel(path_dasar, sheets):
    """Tulis tabel ke Excel (.xlsx, butuh openpyxl) atau CSV bila openpyxl tidak ada.
    sheets = [(nama_sheet, baris...)], baris pertama = judul kolom. Mengembalikan daftar file."""
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:
        openpyxl = None
    if openpyxl:
        wb = openpyxl.Workbook()
        wb.remove(wb.active)
        for name, rows in sheets:
            ws = wb.create_sheet(name[:31])
            for r in rows:
                ws.append(list(r))
            if rows:
                for c in ws[1]:
                    c.font = Font(bold=True)
                    c.fill = PatternFill("solid", fgColor="DDEBF7")
                    c.alignment = Alignment(wrap_text=True, vertical="top")
                ws.freeze_panes = "A2"
                for i in range(1, ws.max_column + 1):
                    w = max(len(str(ws.cell(row=j, column=i).value or "")) for j in range(1, ws.max_row + 1))
                    ws.column_dimensions[get_column_letter(i)].width = min(max(8, w + 2), 45)
                for row in ws.iter_rows(min_row=2):
                    if row and isinstance(row[0].value, str) and row[0].value in STAT_LABEL:
                        for c in row:
                            c.font = Font(bold=True)
        out = path_dasar + ".xlsx"
        wb.save(out)
        return [out]
    outs = []
    for name, rows in sheets:
        out = path_dasar + ("" if len(sheets) == 1 else "_" + re.sub(r"\W+", "", name)) + ".csv"
        with open(out, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerows(rows)
        outs.append(out)
    return outs


STAT_LABEL = ("Rata-rata", "Simpangan baku", "Minimum", "Maksimum")


def _num(v, nd=3):
    return round(v, nd) if isinstance(v, (int, float)) else v


def tulis_seri(outdir, grp):
    """seri_<grp>.xlsx/.txt: semua repetisi satu skenario + arsitektur, plus rata-rata ± SD."""
    gdir = os.path.join(outdir, grp)
    rs = []
    for fp in glob.glob(os.path.join(gdir, "R*", "result_*.json")):
        try:
            with open(fp, encoding="utf-8") as f:
                rs.append(json.load(f))
        except Exception:
            pass
    if not rs:
        return []
    rs.sort(key=lambda r: r["meta"].get("rep") or 0)
    m0 = rs[0]["meta"]
    arch = m0.get("arch")
    prog = "Tertimpa di mailbox" if arch == "BM" else "Dibuang antrean"
    gg_on = bool(m0.get("gangguan"))
    kol = ["Repetisi", "Tanggal dan jam", "P_sent total", "P_recv total", "PLR total (%)", "Kategori TIPHON",
           "PLR LoRa (%)", "PLR BLE (%)"] + [f"PLR {s} (%)" for s in SOURCES] + \
          ["Latensi rata-rata (ms)", "Latensi p95 (ms)", "Latensi maks (ms)",
           "Hilang di radio", prog, "Gagal publish", "Kanal Wi-Fi", "Jaringan sekitar", "Nama sesi"] + \
          (["Hilang saat gangguan", "Hilang di luar gangguan", "Terlambat >1 s", "Waktu pulih rata-rata (s)",
            "Waktu pulih maks (s)"] if gg_on else [])
    rows = []
    for r in rs:
        h, m = r["hasil"], r["meta"]
        t = h.get("total") or {}
        lat = t.get("latensi") or {}
        lt = h.get("letak_total") or {}
        rows.append([m.get("rep"), m.get("tanggal_jam"), t.get("p_sent"), t.get("p_recv"), _num(t.get("plr")),
                     t.get("tiphon"),
                     _num((h["protokol"].get("LoRa") or {}).get("plr")),
                     _num((h["protokol"].get("BLE") or {}).get("plr"))]
                    + [_num(h["sumber"][s]["plr"]) if s in h["sumber"] else None for s in SOURCES]
                    + [_num(lat.get("mean")), lat.get("p95"), lat.get("max"),
                       lt.get("hilang_radio"), lt.get("hilang_program"), lt.get("gagal_publish"),
                       m.get("kanal_wifi"), m.get("jaringan_sekitar"), m.get("sesi")]
                    + ([(h.get("gangguan") or {}).get("total", {}).get("hilang_saat"),
                        (h.get("gangguan") or {}).get("total", {}).get("hilang_luar"),
                        (h.get("gangguan") or {}).get("total", {}).get("terlambat"),
                        (h.get("gangguan") or {}).get("pulih_rata_s"),
                        (h.get("gangguan") or {}).get("pulih_maks_s")] if gg_on else []))
    num_cols = [i for i, k in enumerate(kol) if i >= 2 and k not in ("Kategori TIPHON", "Kanal Wi-Fi",
                                                                      "Jaringan sekitar", "Nama sesi")]
    stats = []
    for label in STAT_LABEL:
        row = [label] + [None] * (len(kol) - 1)
        for i in num_cols:
            v = [x[i] for x in rows if isinstance(x[i], (int, float))]
            if not v:
                continue
            row[i] = round({"Rata-rata": statistics.mean(v),
                            "Simpangan baku": statistics.stdev(v) if len(v) > 1 else 0.0,
                            "Minimum": min(v), "Maksimum": max(v)}[label], 3)
        if label == "Rata-rata" and isinstance(row[4], (int, float)):
            row[5] = tiphon_category(row[4])
        stats.append(row)
    base = os.path.join(gdir, f"seri_{grp}")
    files = tulis_tabel(base, [(f"Seri {grp}", [kol] + rows + [[]] + stats)])

    # versi teks (mudah dibaca tanpa Excel)
    no = m0.get("skenario")
    head = (f"SERI {grp}: skenario {no} ({m0.get('jenis_uji') or '-'}), {ARCH_NAME.get(arch, arch)}, "
            + (f"GANGGUAN {m0['gangguan']}, " if gg_on else "") +
            f"payload {m0.get('payload')} B, {m0.get('nodes')} node BLE, interval BLE {m0.get('send_ms')} ms, "
            f"interval LoRa {m0.get('lora_s')} s, interval iklan {m0.get('adv_ms')} ms")
    L = [head, "=" * len(head),
         f"{'Rep':>3} | {'Tanggal dan jam':19} | {'PLR total%':>10} | {'TIPHON':11} | {'PLR LoRa%':>9} | "
         f"{'PLR BLE%':>8} | {'Lat avg':>7} | {'p95':>5} | {'maks':>5} | {'radio':>5} | {prog:>19} | {'pubfail':>7}"]
    L.append("-" * len(L[-1]))
    f = lambda v, fmt: (format(v, fmt) if isinstance(v, (int, float)) else "-")
    for x in rows:
        L.append(f"{f(x[0], 'd'):>3} | {str(x[1] or '-'):19} | {f(x[4], '.2f'):>10} | {str(x[5] or '-'):11} | "
                 f"{f(x[6], '.2f'):>9} | {f(x[7], '.2f'):>8} | {f(x[13], '.1f'):>7} | {f(x[14], 'd'):>5} | "
                 f"{f(x[15], 'd'):>5} | {f(x[16], 'd'):>5} | {f(x[17], 'd'):>19} | {f(x[18], 'd'):>7}")
    L.append("-" * len(L[2]))
    mean, sd = stats[0], stats[1]
    ms = lambda i, nd: (f"{mean[i]:.{nd}f} ± {sd[i]:.{nd}f}" if isinstance(mean[i], (int, float)) else "-")
    L.append(f"Jumlah repetisi : {len(rows)}")
    L.append(f"PLR total       : {ms(4, 2)} %  (min {f(stats[2][4], '.2f')}, maks {f(stats[3][4], '.2f')}) "
             f"-> {mean[5] or '-'}")
    L.append(f"PLR LoRa / BLE  : {ms(6, 2)} % / {ms(7, 2)} %")
    L.append(f"Latensi         : rata-rata {ms(13, 2)} ms | p95 {ms(14, 1)} ms | maks {ms(15, 1)} ms")
    L.append(f"Letak hilang    : radio {ms(16, 1)} | {prog.lower()} {ms(17, 1)} | gagal publish {ms(18, 1)} "
             "(paket per repetisi)")
    if gg_on:
        L.append(f"Uji gangguan    : hilang saat gangguan {ms(len(kol) - 5, 1)} | di luar {ms(len(kol) - 4, 1)} | "
                 f"terlambat >1 s {ms(len(kol) - 3, 1)} | waktu pulih rata-rata {ms(len(kol) - 2, 2)} s "
                 "(per repetisi)")
    with open(base + ".txt", "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    return files + [base + ".txt"]


def tulis_baca_saya(outdir):
    teks = """ISI FOLDER HASIL PENGUJIAN
Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32 dengan Implementasi FreeRTOS
(dibuat otomatis oleh logger.py)

tabel12_catatan_sesi.xlsx   Tabel 12: catatan setiap sesi uji (tanggal/jam, skenario, repetisi,
                            arsitektur, node, interval, payload, kanal Wi-Fi, jaringan sekitar, posisi).
rekap_hasil.xlsx            Tabel 20-23: rata-rata ± simpangan baku seluruh skenario (dibuat oleh --rekap).
rekap.txt                   Isi yang sama dalam bentuk teks.

S<nn>_<ARSITEKTUR>/         Satu folder per skenario (Tabel 14) dan arsitektur.
                            RTOS = gateway FreeRTOS, BM = gateway Bare Metal.
  seri_S<nn>_<ARS>.xlsx     Semua repetisi skenario ini dalam satu tabel + rata-rata, simpangan baku,
  seri_S<nn>_<ARS>.txt      minimum, dan maksimum.
  R01 ... R10/              Data mentah satu repetisi (10 menit):
    summary_*.txt           laporan sesi: PLR per sumber/protokol/total, kategori TIPHON, latensi,
                            nomor urut hilang, letak paket hilang, diagnostik radio BLE.
    result_*.json           semua angka sesi dalam bentuk mesin.
    mqtt_*.csv              setiap pesan yang diterima broker MQTT (termasuk JSON aslinya) -> dasar PLR.
    latency_*.csv           T_receive, T_send, dan latensi setiap paket (jam gateway) -> dasar latensi.
    serial_raw_*.log        seluruh keluaran Serial gateway selama sesi (bukti mentah).

PENDAHULUAN/                Uji pendahuluan P1-P5 (1 node BLE, 2000 ms, 7 B) dan uji A/B (P1 vs P5).
  tabel_uji_pendahuluan.xlsx  baris tabel uji pendahuluan + uji A/B, dibuat otomatis.
lainnya/                    Sesi di luar matriks utama.
_logger/                    Pengaturan internal logger.py, tidak perlu dibuka.

Definisi:
  PLR = (P_sent - P_receive) / P_sent x 100%, P_receive = paket unik (ID + nomor urut) di broker.
  Latensi = T_send - T_receive, keduanya jam internal gateway.
  Kategori TIPHON: <3% sangat baik, 3-<15% baik, 15-<25% sedang, >=25% buruk.
  Letak paket hilang: hilang di radio = P_sent - paket diterima gateway; hilang di program =
  dibuang antrean (FreeRTOS) atau tertimpa di mailbox (Bare Metal); gagal publish = publish MQTT gagal.
"""
    try:
        with open(os.path.join(outdir, "BACA_SAYA.txt"), "w", encoding="utf-8") as f:
            f.write(teks)
    except Exception:
        pass


def run_rapikan(outdir):
    """Pindahkan file hasil lama (semua di satu folder) ke struktur folder baru."""
    pindah = 0
    for fp in glob.glob(os.path.join(outdir, "*_*.*")):
        name = os.path.basename(fp)
        m = re.match(r"^(summary|result|mqtt|latency|serial_raw)_(.+)\.(txt|json|csv|log)$", name)
        if not m:
            continue
        d, _ = lokasi_sesi(outdir, m.group(2))
        os.makedirs(d, exist_ok=True)
        os.replace(fp, os.path.join(d, name))
        pindah += 1
    old = os.path.join(outdir, "lokasi_terakhir.json")
    if os.path.exists(old):
        os.makedirs(os.path.join(outdir, "_logger"), exist_ok=True)
        os.replace(old, os.path.join(outdir, "_logger", "lokasi_terakhir.json"))
    for fp in glob.glob(os.path.join(outdir, "rekap_tabel2*.csv")) + glob.glob(os.path.join(outdir, "tabel12_catatan_sesi.csv")):
        os.makedirs(os.path.join(outdir, "_logger", "lama"), exist_ok=True)
        os.replace(fp, os.path.join(outdir, "_logger", "lama", os.path.basename(fp)))
    grps = sorted({os.path.basename(os.path.dirname(os.path.dirname(fp))) for fp in semua_result(outdir)
                   if SESI_RE.match(os.path.basename(fp)[7:-5])})
    for g in grps:
        tulis_seri(outdir, g)
    tulis_tabel12(outdir)
    tulis_baca_saya(outdir)
    print(f"{pindah} file dipindahkan ke struktur folder baru di '{outdir}'. "
          f"File seri diperbarui untuk: {', '.join(grps) or '-'}")


# ============================ TABEL 12 (catatan sesi) ==========================
POSISI_DEFAULT = "2 m, LoS, posisi bertanda"
TABEL12_KOLOM = ["Tanggal dan jam", "Nomor skenario", "Repetisi", "Arsitektur", "Jumlah node BLE",
                 "Interval kirim BLE (ms)", "Ukuran payload (byte)", "Interval kirim LoRa (s)",
                 "Interval iklan BLE (ms)", "Kanal Wi-Fi router", "Jumlah jaringan Wi-Fi di sekitar",
                 "Posisi node", "Catatan", "Nama sesi"]


WIFI_SSID_UJI = "NAMA_WIFI"   # ganti dengan nama jaringan Wi-Fi pengujian
_CH_RE = re.compile(r"^\s*(Channel|Saluran|Kanal)\s*:\s*(\d+)", re.I)
_SSID_RE = re.compile(r"^SSID\s+\d+\s*:\s*(.*)$")
_SIG_RE = re.compile(r"^\s*(Signal|Sinyal)\s*:\s*(\d+)%", re.I)


def parse_netsh(text):
    """Urai keluaran 'netsh wlan show networks mode=bssid' -> [{ssid, channel, signal}]."""
    nets, cur = [], None
    for line in text.splitlines():
        m = _SSID_RE.match(line.strip())
        if m:
            cur = {"ssid": m.group(1).strip(), "channel": None, "signal": None}
            nets.append(cur)
            continue
        if cur is None:
            continue
        m = _CH_RE.match(line)
        if m and cur["channel"] is None:
            cur["channel"] = int(m.group(2))
        m = _SIG_RE.match(line)
        if m and cur["signal"] is None:
            cur["signal"] = int(m.group(2))
    return nets


def pindai_wifi(ssid_uji):
    """Pindai Wi-Fi di sekitar laptop (Windows, adapter Wi-Fi menyala; tidak perlu tersambung).
    Hasil: (kanal router uji atau None, jumlah jaringan 2,4 GHz, daftar jaringan) atau None."""
    if os.name != "nt":
        return None
    import subprocess
    try:
        out = subprocess.run(["netsh", "wlan", "show", "networks", "mode=bssid"],
                             capture_output=True, text=True, errors="replace", timeout=15).stdout
    except Exception:
        return None
    nets = [n for n in parse_netsh(out) if n["channel"] is not None]
    if not nets:
        return None
    n24 = [n for n in nets if n["channel"] <= 14]
    ch = next((n["channel"] for n in nets if n["ssid"] == ssid_uji), None)
    return ch, len(n24), n24


def isi_lokasi(a):
    """Kanal, jumlah jaringan, dan posisi node (Tabel 12). Bila tidak diberi lewat argumen,
    ditanyakan di layar dengan nilai sesi sebelumnya sebagai bawaan (tekan Enter = sama)."""
    os.makedirs(os.path.join(a.outdir, "_logger"), exist_ok=True)
    path = os.path.join(a.outdir, "_logger", "lokasi_terakhir.json")
    last = {}
    try:
        with open(path, encoding="utf-8") as f:
            last = json.load(f)
    except Exception:
        pass
    scan = None
    if not (a.kanal and a.jaringan):
        print(f"Memindai Wi-Fi di sekitar laptop (mencari '{a.ssid}') ...")
        scan = pindai_wifi(a.ssid)
        if scan:
            ch, n, nets = scan
            a.wifi_scan = nets
            print(f"  Ditemukan {n} jaringan 2,4 GHz: " +
                  ", ".join(f"{x['ssid'] or '(tersembunyi)'} [ch {x['channel']}]" for x in nets))
            if ch is None:
                print(f"  '{a.ssid}' tidak terlihat di pindaian -> kanal pakai nilai sebelumnya / isi manual.")
            else:
                last["kanal"] = str(ch)
            last["jaringan"] = str(n)
        else:
            print("  Pindaian otomatis tidak tersedia (bukan Windows atau adapter Wi-Fi mati) -> isi manual.")
    print("Tekan Enter untuk menerima nilai dalam kurung siku.")
    fields = (("kanal", "Kanal Wi-Fi router"),
              ("jaringan", "Jumlah jaringan Wi-Fi 2,4 GHz di sekitar"),
              ("posisi", "Posisi node"))
    for key, label in fields:
        if getattr(a, key):
            continue
        dflt = last.get(key) or (POSISI_DEFAULT if key == "posisi" else "")
        val = ""
        if sys.stdin and sys.stdin.isatty():
            try:
                val = input(f"{label} [{dflt or 'kosong'}]: ").strip()
            except EOFError:
                val = ""
        setattr(a, key, val or dflt)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"kanal": a.kanal, "jaringan": a.jaringan, "posisi": a.posisi}, f, ensure_ascii=False)
    except Exception:
        pass


def pindai_ulang(a):
    """Pindai Wi-Fi tanpa bertanya (antar-putaran mode --runs); nilai lama dipakai bila gagal."""
    scan = pindai_wifi(a.ssid)
    if scan:
        ch, n, nets = scan
        a.wifi_scan = nets
        a.jaringan = str(n)
        if ch is not None:
            a.kanal = str(ch)
        print(f"  Pindaian Wi-Fi: kanal {a.kanal or '-'}, {n} jaringan 2,4 GHz")


def tulis_tabel12(outdir):
    """Kumpulkan meta semua sesi (result_*.json) menjadi tabel12_catatan_sesi.xlsx/.csv."""
    rows = []
    for fp in semua_result(outdir):
        try:
            with open(fp, encoding="utf-8") as f:
                m = json.load(f)["meta"]
        except Exception:
            continue
        arch = m.get("arch") or ""
        rows.append([m.get("tanggal_jam") or "", m.get("skenario") or "-", m.get("rep") or "-",
                     ARCH_NAME.get(arch, arch or "-"), m.get("nodes") or "", m.get("send_ms") or "",
                     m.get("payload") or "", m.get("lora_s") or "", m.get("adv_ms") or "",
                     m.get("kanal_wifi") or "", m.get("jaringan_sekitar") or "",
                     m.get("posisi_node") or "",
                     "; ".join(x for x in [m.get("catatan") or "",
                                          f"uji gangguan {m['gangguan']}" if m.get("gangguan") else "",
                                          f"uji pendahuluan {m['pendahuluan']}" if m.get("pendahuluan") else ""]
                               if x),
                     m.get("sesi") or ""])
    rows.sort(key=lambda r: r[0])
    files = tulis_tabel(os.path.join(outdir, "tabel12_catatan_sesi"), [("Tabel 12", [TABEL12_KOLOM] + rows)])
    return files[0], len(rows)


def baris_pendahuluan(res, kode):
    """Satu baris tabel uji pendahuluan dari hasil sesi: (P_sent, P_recv, PLR, iklan, rasio, celah, sumber)."""
    ble = [(s, r) for s, r in res["sumber"].items() if s.startswith("BLE")]
    if not ble:
        return None
    s, r = max(ble, key=lambda x: x[1]["p_recv"])
    rad = r.get("radio") or {}
    return {"sumber": s, "p_sent": r["p_sent"], "p_recv": r["p_recv"], "plr": r["plr"],
            "iklan": rad.get("iklan_tertangkap"), "rasio": rad.get("capture_rate"),
            "celah": rad.get("celah_terlama_ms"), "seq_radio": rad.get("seq_terlewat_radio"),
            "rssi_avg": rad.get("rssi_avg"), "rssi_min": rad.get("rssi_min"),
            "lat": ((r.get("latensi") or {}).get("mean"))}


def tulis_tabel_pendahuluan(outdir):
    """hasil/PENDAHULUAN/tabel_uji_pendahuluan.xlsx: rincian per sesi, tabel ringkas, dan uji A/B (P1 vs P5)."""
    per = {}
    rinci = [["Kode", "Kondisi", "Repetisi", "Tanggal dan jam", "Sumber", "P_sent", "P_receive", "PLR BLE (%)",
              "Iklan tertangkap", "Rasio penerimaan iklan (%)", "Celah terlama (ms)", "Seq terlewat di radio",
              "RSSI rata-rata (dBm)", "RSSI terlemah (dBm)", "Latensi rata-rata (ms)", "Pengaturan radio gateway",
              "Nama sesi"]]
    for fp in semua_result(outdir):
        try:
            with open(fp, encoding="utf-8") as f:
                r = json.load(f)
        except Exception:
            continue
        m = r.get("meta") or {}
        kode = m.get("pendahuluan")
        if not kode:
            continue
        b = baris_pendahuluan(r["hasil"], kode)
        if not b:
            continue
        per.setdefault(kode, []).append(b)
        rinci.append([kode, m.get("kondisi"), m.get("rep"), m.get("tanggal_jam"), b["sumber"], b["p_sent"],
                      b["p_recv"], b["plr"], b["iklan"], b["rasio"], b["celah"], b["seq_radio"], b["rssi_avg"],
                      b["rssi_min"], round(b["lat"], 2) if b["lat"] is not None else "-",
                      m.get("radio_gateway") or "-", m.get("sesi")])
    if not per:
        return None

    def ringkas(vals, nd):
        v = [x for x in vals if isinstance(x, (int, float))]
        if not v:
            return "-"
        if len(v) == 1:
            return round(v[0], nd)
        return f"{statistics.mean(v):.{nd}f} ± {statistics.stdev(v):.{nd}f}".replace(".", ",")

    def rentang(vals):
        v = [x for x in vals if isinstance(x, (int, float))]
        return "-" if not v else (v[0] if min(v) == max(v) else f"{min(v)}–{max(v)}")

    ringkas_rows = [["Kode", "Kondisi", "n", "P_sent", "P_receive", "PLR BLE (%)", "Rasio penerimaan iklan (%)",
                     "Celah terlama (ms)"]]
    for kode in sorted(per, key=lambda k: (k[:2], k)):
        bs = per[kode]
        ringkas_rows.append([kode, PENDAHULUAN.get(kode, {}).get("kondisi", ""), len(bs),
                             rentang([b["p_sent"] for b in bs]), rentang([b["p_recv"] for b in bs]),
                             ringkas([b["plr"] for b in bs], 2), ringkas([b["rasio"] for b in bs], 1),
                             ringkas([b["celah"] for b in bs], 0)])
    ab = [["Interval iklan", "Kode", "n", "P_sent", "P_receive", "PLR (%)", "Iklan tertangkap",
           "Capture rate (%)", "Celah terlama (ms)"]]
    for kode, lbl in (("P1", "100 ms"), ("P5", "20 ms")):
        bs = per.get(kode)
        if bs:
            ab.append([lbl, kode, len(bs), rentang([b["p_sent"] for b in bs]), rentang([b["p_recv"] for b in bs]),
                       ringkas([b["plr"] for b in bs], 2), ringkas([b["iklan"] for b in bs], 0),
                       ringkas([b["rasio"] for b in bs], 1), ringkas([b["celah"] for b in bs], 0)])
    d = os.path.join(outdir, "PENDAHULUAN")
    os.makedirs(d, exist_ok=True)
    return tulis_tabel(os.path.join(d, "tabel_uji_pendahuluan"),
                       [("Tabel uji pendahuluan", ringkas_rows), ("Uji AB (P1 vs P5)", ab), ("Rincian per sesi", rinci)])


# ============================ MODE HITUNG ULANG ================================
def run_ulang(session, outdir, sent_over, adv_ms_arg):
    """Hitung ulang sesi yang sudah tersimpan dari mqtt_*.csv + serial_raw_*.log
    (mis. P_sent di OLED node berbeda dari teori). Menimpa summary_ dan result_."""
    P = lambda prefix, ext: path_sesi(outdir, session, prefix, ext)
    if not os.path.exists(P("result", "json")):
        sys.exit(f"Tidak ditemukan {P('result', 'json')}")
    with open(P("result", "json"), encoding="utf-8") as f:
        old = json.load(f)
    meta = old["meta"]
    sent_arg = {s: r["p_sent"] for s, r in old["hasil"]["sumber"].items()}
    sent_arg.update(sent_over)
    first = None
    with open(P("mqtt", "csv"), encoding="utf-8") as f:
        for row in csv.DictReader(f):
            src = row["src"]
            if src not in received:
                continue
            t = float(row["pc_time"])
            first = t if first is None else min(first, t)
            received[src].add(int(row["seq"]))
            msg_count[src] += 1
            mqtt_rows.append((row["pc_time"], src, int(row["seq"]), row.get("node_ts"), row.get("gw_rx_ms"),
                              row.get("len"), row.get("json_asli")))
    first_pkt_time[0] = first
    if meta.get("pendahuluan") and meta.get("gateway_uji") == "Gateway_BLE_Only_Test":
        SERIAL_ONLY[0] = True                  # P3: paket yang sampai = baris D, gateway khusus BLE
    ts_re = re.compile(r"^\[(\d+\.\d+)\] (.*)$")
    if os.path.exists(P("serial_raw", "log")):
        with open(P("serial_raw", "log"), encoding="utf-8", errors="replace") as f:
            for line in f:
                m = ts_re.match(line.rstrip("\r\n"))
                if not m:
                    continue
                t, body = float(m.group(1)), m.group(2)
                n_before = len(s_rows)
                handle_serial_line(body)
                if len(s_rows) > n_before:
                    s_rows[-1]["before_first_pkt"] = (first is None or t < first)
    adv_ms = adv_ms_arg or meta.get("adv_ms")
    res = compute(sent_arg, adv_ms, meta.get("send_ms"))
    if meta.get("gangguan_events"):
        gg = analisis_gangguan(meta["gangguan_events"], sent_arg, meta.get("send_ms"), meta.get("lora_s"))
        if gg:
            gg.update({k: (old["hasil"].get("gangguan") or {}).get(k) for k in ("mode", "durasi_s", "periode_s")})
            res["gangguan"] = gg
    arch = meta.get("arch") or ""
    meta_lines = [
        f"Tanggal/jam: {meta.get('tanggal_jam')} | Skenario: {meta.get('skenario') or '-'} | "
        f"Repetisi: {meta.get('rep') or '-'} | Arsitektur: {ARCH_NAME.get(arch, arch or '-')}",
        f"Payload: {meta.get('payload') or '-'} B | Node BLE: {meta.get('nodes') or '-'} | "
        f"Interval BLE: {meta.get('send_ms') or '-'} ms | Interval LoRa: {meta.get('lora_s') or '-'} s | "
        f"Interval iklan: {adv_ms or '-'} ms",
        f"Kanal Wi-Fi: {meta.get('kanal_wifi') or '-'} | Jaringan sekitar: {meta.get('jaringan_sekitar') or '-'} | "
        f"Posisi node: {meta.get('posisi_node') or '-'} | Catatan: {meta.get('catatan') or '-'}",
        "(DIHITUNG ULANG dengan P_sent: " + ", ".join(f"{k}={v}" for k, v in sent_arg.items()) + ")",
    ]
    text = report_text(meta_lines, res, adv_ms, meta.get("send_ms"), session=session)
    print(text)
    with open(P("summary", "txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    meta["hitung_ulang"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(P("result", "json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "hasil": res}, f, ensure_ascii=False, indent=1)
    tulis_tabel12(outdir)
    if meta.get("pendahuluan"):
        tulis_tabel_pendahuluan(outdir)
    _, grp = lokasi_sesi(outdir, session)
    if grp and os.path.dirname(P("result", "json")) != outdir:
        tulis_seri(outdir, grp)
    print(f"\nsummary_{session}.txt dan result_{session}.json diperbarui"
          + (f", seri_{grp} diperbarui." if grp else "."))


# ============================ MODE REKAP =======================================
def mean_sd(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None, None, None, None
    m = statistics.mean(vals)
    sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
    return m, sd, min(vals), max(vals)


def f_ms(m, sd, dec=2):
    if m is None:
        return "-"
    return f"{m:.{dec}f} ± {sd:.{dec}f}"


def run_rekap(folder, target_rep=10):
    files = semua_result(folder)
    if not files:
        sys.exit(f"Tidak ada result_*.json di folder '{folder}'.")
    data = {}
    data_gg = {}
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            r = json.load(f)
        no, arch = r["meta"].get("skenario"), r["meta"].get("arch")
        if no is None or arch not in ("RTOS", "BM"):
            print(f"  dilewati (bukan sesi matriks): {os.path.basename(fp)}")
            continue
        if r["meta"].get("gangguan"):
            data_gg.setdefault((r["meta"]["gangguan"], no, arch), []).append(r)
            continue
        data.setdefault((no, arch), []).append(r)

    def rows_for(no, arch):
        return data.get((SAMA_DENGAN.get(no, no), arch), [])

    out = []
    t20 = [["No", "Konfigurasi", "n RTOS", "PLR FreeRTOS (%)", "n BM", "PLR Bare Metal (%)",
            "TIPHON FreeRTOS / Bare Metal", "Latensi rata2 FreeRTOS (ms)", "Latensi rata2 Bare Metal (ms)",
            "PLR min-maks FreeRTOS", "PLR min-maks Bare Metal"]]
    t22 = [["No", "FreeRTOS: hilang di radio", "FreeRTOS: dibuang antrean", "FreeRTOS: gagal publish",
            "Bare Metal: hilang di radio", "Bare Metal: tertimpa di mailbox", "Bare Metal: gagal publish"]]
    t23 = [["No", "FreeRTOS rata-rata (ms)", "FreeRTOS p95 (ms)", "FreeRTOS maks (ms)",
            "Bare Metal rata-rata (ms)", "Bare Metal p95 (ms)", "Bare Metal maks (ms)"]]
    t21 = [["No", "Arsitektur", "Sumber", "PLR (%)", "Capture rate (%)"]]
    kurang = []
    for no in range(1, 13):
        _, payload, nodes, send_ms = SKENARIO[no]
        konf = f"{payload} B, {nodes} node, {send_ms} ms"
        row20 = [no, konf]
        row22, row23 = [no], [no]
        cats = []
        for arch in ("RTOS", "BM"):
            rs = rows_for(no, arch)
            if no not in SAMA_DENGAN and len(rs) < target_rep:
                kurang.append(f"skenario {no} {ARCH_NAME[arch]}: {len(rs)}/{target_rep} repetisi")
            plr = [r["hasil"]["total"]["plr"] for r in rs if r["hasil"].get("total")]
            m, sd, mn, mx = mean_sd(plr)
            row20 += [len(rs), f_ms(m, sd)]
            cats.append(tiphon_category(m) if m is not None else "-")
            lat = [r["hasil"]["total"]["latensi"] for r in rs
                   if r["hasil"].get("total") and r["hasil"]["total"].get("latensi")]
            lm = mean_sd([x["mean"] for x in lat])
            lp = mean_sd([x["p95"] for x in lat])
            lx = mean_sd([x["max"] for x in lat])
            row20.append(f_ms(lm[0], lm[1], 1))
            row20.append(f"{mn:.2f} - {mx:.2f}" if mn is not None else "-")
            row23 += [f_ms(lm[0], lm[1], 1), f_ms(lp[0], lp[1], 1), f_ms(lx[0], lx[1], 1)]
            lt = [r["hasil"].get("letak_total") for r in rs if r["hasil"].get("letak_total")]
            for k in ("hilang_radio", "hilang_program", "gagal_publish"):
                mm = mean_sd([x[k] for x in lt])
                row22.append(f"{mm[0]:.1f}" if mm[0] is not None else "-")
            if no in (11, 12):
                for s in SOURCES:
                    pl = [r["hasil"]["sumber"][s]["plr"] for r in rs if s in r["hasil"]["sumber"]]
                    cr = [r["hasil"]["sumber"][s].get("radio", {}).get("capture_rate") for r in rs
                          if s in r["hasil"]["sumber"]]
                    a, b, _, _ = mean_sd(pl)
                    c, e, _, _ = mean_sd(cr)
                    if a is not None:
                        t21.append([no, ARCH_NAME[arch], s, f_ms(a, b), f_ms(c, e, 1) if c is not None else "-"])
        # susun ulang baris Tabel 20 sesuai urutan kolom
        r20 = [row20[0], row20[1], row20[2], row20[3], row20[6], row20[7], f"{cats[0]} / {cats[1]}",
               row20[4], row20[8], row20[5], row20[9]]
        if no in SAMA_DENGAN:
            r20[1] += f" (data skenario {SAMA_DENGAN[no]})"
        t20.append(r20)
        t22.append(row22)
        t23.append(row23)

    # ---- Uji gangguan jaringan (tabel terpisah; tingkat 0 = data matriks skenario yang sama) ----
    tgg = [["Gangguan", "Skenario", "Arsitektur", "n", "PLR total (%)", "Hilang saat gangguan",
            "Hilang di luar gangguan", "Terlambat >1 s", "Waktu pulih rata-rata (s)", "Latensi rata-rata (ms)",
            "Latensi maks (ms)", "Hilang di radio", "Hilang di program", "Gagal publish"]]
    kunci = sorted({(g, no) for (g, no, ar) in data_gg}, key=lambda k: (k[1], k[0]))
    sudah_tanpa = set()
    for g, no in kunci:
        for arch in ("RTOS", "BM"):
            base0 = rows_for(no, arch)
            if base0 and (no, arch) not in sudah_tanpa:
                sudah_tanpa.add((no, arch))
                pl = mean_sd([r["hasil"]["total"]["plr"] for r in base0 if r["hasil"].get("total")])
                la = mean_sd([r["hasil"]["total"]["latensi"]["mean"] for r in base0
                              if (r["hasil"].get("total") or {}).get("latensi")])
                lx = mean_sd([r["hasil"]["total"]["latensi"]["max"] for r in base0
                              if (r["hasil"].get("total") or {}).get("latensi")])
                lt = [r["hasil"].get("letak_total") or {} for r in base0]
                tgg.append(["TANPA", no, ARCH_NAME[arch], len(base0), f_ms(pl[0], pl[1]), "-", "-", "-", "-",
                            f_ms(la[0], la[1], 1), f_ms(lx[0], lx[1], 1)]
                           + [f"{mean_sd([x.get(k) for x in lt])[0]:.1f}" if mean_sd([x.get(k) for x in lt])[0]
                              is not None else "-" for k in ("hilang_radio", "hilang_program", "gagal_publish")])
            rs = data_gg.get((g, no, arch), [])
            if not rs:
                continue
            H = [r["hasil"] for r in rs]
            pl = mean_sd([h["total"]["plr"] for h in H if h.get("total")])
            gs = [h.get("gangguan") or {} for h in H]
            col = lambda k: mean_sd([(x.get("total") or {}).get(k) for x in gs])
            pu = mean_sd([x.get("pulih_rata_s") for x in gs])
            la = mean_sd([h["total"]["latensi"]["mean"] for h in H if (h.get("total") or {}).get("latensi")])
            lx = mean_sd([h["total"]["latensi"]["max"] for h in H if (h.get("total") or {}).get("latensi")])
            lt = [h.get("letak_total") or {} for h in H]
            tgg.append([g, no, ARCH_NAME[arch], len(rs), f_ms(pl[0], pl[1]), f_ms(*col("hilang_saat")[:2], 1),
                        f_ms(*col("hilang_luar")[:2], 1), f_ms(*col("terlambat")[:2], 1), f_ms(pu[0], pu[1], 2),
                        f_ms(la[0], la[1], 1), f_ms(lx[0], lx[1], 1)]
                       + [f"{mean_sd([x.get(k) for x in lt])[0]:.1f}" if mean_sd([x.get(k) for x in lt])[0]
                          is not None else "-" for k in ("hilang_radio", "hilang_program", "gagal_publish")])
    sheets = [("Tabel 20", t20), ("Tabel 21", t21), ("Tabel 22", t22), ("Tabel 23", t23)]
    if len(tgg) > 1:
        sheets.append(("Uji Gangguan", tgg))
    rekap_files = tulis_tabel(os.path.join(folder, "rekap_hasil"), sheets)
    grps = sorted({g for (no, ar) in data for g in [f"S{no:02d}_{ar}"]
                   if os.path.isdir(os.path.join(folder, g))}
                  | {f"S{no:02d}_{ar}_{g}" for (g, no, ar) in data_gg
                     if os.path.isdir(os.path.join(folder, f"S{no:02d}_{ar}_{g}"))})
    for g in grps:
        tulis_seri(folder, g)
    tulis_baca_saya(folder)

    out.append(f"=== REKAP {len(files)} file result di '{folder}' (rata-rata ± simpangan baku, "
               f"target {target_rep} repetisi) ===\n")
    out.append("Tabel 20. PLR total dan latensi rata-rata per skenario")
    for r in t20:
        out.append(f"  {str(r[0]):>3} | {r[1]:<38} | RTOS n={r[2]} {r[3]:>15} | BM n={r[4]} {r[5]:>15} | "
                   f"{r[6]:<24} | lat {r[7]} / {r[8]}" if r[0] != "No" else
                   "  No  | Konfigurasi                            | PLR FreeRTOS (%)        | PLR Bare Metal (%)    "
                   "  | TIPHON                   | latensi (ms) RTOS / BM")
    out.append("\nTabel 22. Letak paket hilang (rata-rata paket per sesi)")
    for r in t22:
        out.append("  " + " | ".join(str(x) for x in r))
    out.append("\nTabel 23. Latensi per skenario (ms)")
    for r in t23:
        out.append("  " + " | ".join(str(x) for x in r))
    if len(t21) > 1:
        out.append("\nTabel 21. PLR dan capture rate per sumber (skenario 11 dan 12)")
        for r in t21:
            out.append("  " + " | ".join(str(x) for x in r))
    if len(tgg) > 1:
        out.append("\nUji gangguan jaringan (rata-rata ± simpangan baku per repetisi; TANPA = data matriks)")
        for r in tgg:
            out.append("  " + " | ".join(str(x) for x in r))
    if kurang:
        out.append("\nBELUM LENGKAP:")
        out += ["  " + k for k in kurang]
    p12, n12 = tulis_tabel12(folder)
    out.append(f"\nFile: {', '.join(os.path.basename(x) for x in rekap_files)}, {os.path.basename(p12)} "
               f"({n12} sesi), dan seri_*.xlsx di tiap folder skenario ({len(grps)} folder), di '{folder}'.")
    if not rekap_files[0].endswith(".xlsx"):
        out.append("(File Excel .xlsx dibuat bila openpyxl terpasang: pip install openpyxl)")
    text = "\n".join(out)
    print(text)
    with open(os.path.join(folder, "rekap.txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


# ============================ MODE SESI ========================================
def cek_gateway_pendahuluan(pcfg):
    """Pastikan firmware/pengaturan radio gateway sesuai kode uji. True = cocok, False = tidak, None = tak terbaca."""
    kode = pcfg["kode"]
    if pcfg["wns"] is None:
        if ble_only_seen[0] and not s_rows:
            print("[OK] gateway khusus BLE (Gateway_BLE_Only_Test) terdeteksi.")
            return True
        print(f"!!! {kode} butuh tools/Gateway_BLE_Only_Test.ino, tetapi gateway "
              + ("mengirim baris S, (firmware gateway biasa)." if s_rows else "tidak terdeteksi (cek port/baud)."))
        if input("Tetap lanjut? (y/n) ").strip().lower() != "y":
            sys.exit(1)
        return False
    if ble_only_seen[0]:
        print(f"!!! Terdeteksi Gateway_BLE_Only_Test, padahal {kode} butuh {pcfg['gateway']}.")
        if input("Tetap lanjut? (y/n) ").strip().lower() != "y":
            sys.exit(1)
        return False
    if radio_cfg_line[0] is None:
        n_s = len(s_rows)
        print("\n>>> Tekan tombol EN/RST pada board GATEWAY sekali sekarang, agar baris pengaturan radio "
              "('Radio: WIFI_NO_SLEEP=.. COEX_PREFER_BT=..') ikut tercatat. Menunggu maks 60 s ...")
        t = time.time()
        while radio_cfg_line[0] is None and time.time() - t < 60:
            time.sleep(0.2)
        if radio_cfg_line[0] is not None:
            print("Pengaturan radio tercatat. Menunggu gateway tersambung lagi (baris S, baru, maks 40 s) ...")
            t = time.time()
            while len(s_rows) <= n_s and time.time() - t < 40:
                time.sleep(0.2)
    if radio_cfg_line[0] is None:
        print("[PERINGATAN] pengaturan radio gateway tidak terbaca -> dicatat 'tidak terverifikasi'.")
        return None
    m = RADIO_RE.search(radio_cfg_line[0])
    if not m:
        return None
    wns, coex = int(m.group(1)), int(m.group(2))
    if (wns, coex) != (pcfg["wns"], pcfg["coex"]):
        print(f"!!! Gateway melapor WIFI_NO_SLEEP={wns} COEX_PREFER_BT={coex}, padahal {kode} butuh "
              f"WIFI_NO_SLEEP={pcfg['wns']} COEX_PREFER_BT={pcfg['coex']}. Upload ulang firmware gateway yang benar.")
        if input("Tetap lanjut? (y/n) ").strip().lower() != "y":
            sys.exit(1)
        return False
    print(f"[OK] pengaturan radio gateway sesuai {kode}: WIFI_NO_SLEEP={wns} COEX_PREFER_BT={coex}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Logger MQTT+Serial untuk pengujian Multi-Protocol IoT Gateway ESP32")
    ap.add_argument("--skenario", type=int, choices=range(1, 13), metavar="1-12",
                    help="nomor skenario Tabel 14; mengisi payload/node/interval/P_sent otomatis")
    ap.add_argument("--arch", default="", help="RTOS (FreeRTOS) atau BM (Bare Metal)")
    ap.add_argument("--rep", type=int, default=1, help="nomor repetisi (awal) -- default 1")
    ap.add_argument("--runs", type=int, default=1,
                    help="jumlah putaran beruntun yang direkam sekaligus (= REPEAT_RUNS di node), mis. 10")
    ap.add_argument("--tunggu", type=int, default=300,
                    help="detik maksimum menunggu putaran berikutnya dimulai (default 300)")
    ap.add_argument("--target-rep", type=int, default=10, help="target repetisi untuk --rekap (default 10)")
    ap.add_argument("--serial", default=None, help="port USB gateway, mis. COM5 atau /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--broker", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=1883)
    ap.add_argument("--topic", default="ttu/gw/#")
    ap.add_argument("--outdir", default="hasil", help="folder hasil (default: hasil)")
    ap.add_argument("--session", default=None, help="nama sesi (default otomatis dari skenario)")
    ap.add_argument("--duration", type=int, default=None,
                    help="detik, dihitung sejak paket pertama (default 600 bila --skenario)")
    ap.add_argument("--grace", type=int, default=20, help="detik tambahan setelah durasi (default 20)")
    ap.add_argument("--sent", nargs="*", default=[], help="override P_sent, mis. BLE1=1199 LORA1=120")
    ap.add_argument("--payload", type=int, default=None)
    ap.add_argument("--nodes", type=int, default=None)
    ap.add_argument("--send-ms", type=float, default=None, help="interval kirim BLE (ms)")
    ap.add_argument("--adv-ms", type=float, default=None, help="interval iklan node (default 20 bila --skenario)")
    ap.add_argument("--kanal", default="", help="Tabel 12: kanal Wi-Fi router")
    ap.add_argument("--jaringan", default="", help="Tabel 12: jumlah jaringan Wi-Fi di sekitar")
    ap.add_argument("--ssid", default=WIFI_SSID_UJI, help="nama Wi-Fi router uji untuk pindaian kanal")
    ap.add_argument("--posisi", default="", help=f"Tabel 12: posisi node (bawaan: '{POSISI_DEFAULT}')")
    ap.add_argument("--catatan", default="", help="Tabel 12: catatan bebas (gangguan, kejadian, dll.)")
    ap.add_argument("--replay", default=None, metavar="FILE", help="analisis ulang log Serial, tanpa MQTT")
    ap.add_argument("--rekap", default=None, metavar="FOLDER", help="rekap semua result_*.json di folder")
    ap.add_argument("--gangguan", default=None, metavar="MODE:DUR:PERIODE",
                    help="uji gangguan jaringan, mis. putus:10:180 atau tahan:10:60 (detik)")
    ap.add_argument("--gangguan-mulai", type=int, default=60,
                    help="detik setelah paket pertama sebelum gangguan pertama (default 60)")
    ap.add_argument("--pendahuluan", default=None, metavar="KODE",
                    help="uji pendahuluan P1, P3, P4, atau P5 (1 node BLE, 2000 ms, 7 B); "
                         "P1 & P5 sekaligus menjadi uji A/B interval iklan")
    ap.add_argument("--buffer-lama", action="store_true",
                    help="uji gangguan dengan ukuran buffer terima perantara bawaan sistem operasi "
                         "(bukan 2048 byte)")
    ap.add_argument("--port-perantara", type=int, default=1883,
                    help="port yang dipakai gateway (= MQTT_PORT firmware), default 1883")
    ap.add_argument("--rapikan", default=None, metavar="FOLDER",
                    help="pindahkan file hasil lama (satu folder) ke struktur folder baru")
    ap.add_argument("--ulang", default=None, metavar="SESI",
                    help="hitung ulang sesi tersimpan, mis. --ulang S11_RTOS_R1 --sent BLE1=5998")
    a = ap.parse_args()

    if a.rekap:
        run_rekap(a.rekap, a.target_rep)
        return 0
    if a.rapikan:
        run_rapikan(a.rapikan)
        return 0

    pcfg = None
    if a.pendahuluan:
        kode = {k.lower(): k for k in PENDAHULUAN}.get(a.pendahuluan.strip().lower())
        if not kode:
            sys.exit(f"--pendahuluan harus salah satu dari: {', '.join(PENDAHULUAN)}")
        if a.skenario or a.gangguan:
            sys.exit("--pendahuluan tidak dapat digabung dengan --skenario atau --gangguan.")
        if not a.serial:
            sys.exit("Uji pendahuluan butuh --serial (rasio penerimaan iklan & celah terlama dari baris G, gateway).")
        pcfg = dict(PENDAHULUAN[kode], kode=kode)
        a.pendahuluan = kode
        a.payload, a.nodes, a.send_ms = 7, 1, P_SEND_MS
        a.adv_ms = a.adv_ms or pcfg["adv"]
        if a.duration is None:
            a.duration = TEST_S
        if pcfg["wns"] is None:
            SERIAL_ONLY[0] = True
        elif not a.arch:
            a.arch = "RTOS"
    a.pcfg = pcfg

    sent_arg = {}
    cfg = None
    if a.skenario:
        if a.skenario in SAMA_DENGAN:
            sys.exit(f"Skenario {a.skenario} identik dengan skenario {SAMA_DENGAN[a.skenario]} dan memakai "
                     f"datanya. Jalankan --skenario {SAMA_DENGAN[a.skenario]}.")
        cfg = skenario_config(a.skenario)
        sent_arg = dict(cfg["sent"])
        a.payload = a.payload or cfg["payload"]
        a.nodes = a.nodes or cfg["nodes"]
        a.send_ms = a.send_ms or cfg["send_ms"]
        a.adv_ms = a.adv_ms or ADV_MS_DEFAULT
        if a.duration is None:
            a.duration = TEST_S
    for item in a.sent:
        k, v = item.split("=")
        sent_arg[k.upper()] = int(v)
    for k in ("send_ms", "adv_ms"):          # 2000.0 -> 2000 agar rapi di laporan
        v = getattr(a, k)
        if v is not None and float(v).is_integer():
            setattr(a, k, int(v))

    if a.replay:
        run_replay(a.replay, a.adv_ms, a.send_ms, sent_arg)
        return 0
    if a.ulang:
        run_ulang(a.ulang, a.outdir, {k.upper(): int(v) for k, v in (i.split("=") for i in a.sent)}, a.adv_ms)
        return 0

    arch = norm_arch(a.arch)
    if a.skenario and arch not in ("RTOS", "BM"):
        sys.exit("Isi --arch RTOS (FreeRTOS) atau --arch BM (Bare Metal).")
    gg_cfg = None
    if a.gangguan:
        try:
            gm, gd, gp = a.gangguan.lower().split(":")
            gg_cfg = (gm, int(gd), int(gp))
            assert gm in ("tahan", "putus") and gg_cfg[1] > 0 and gg_cfg[2] > gg_cfg[1]
        except Exception:
            sys.exit("Format --gangguan: tahan:DUR:PERIODE atau putus:DUR:PERIODE, mis. putus:10:180")
        if not a.duration:
            sys.exit("--gangguan butuh --skenario atau --duration.")
        if a.buffer_lama:
            RCVBUF_PERANTARA[0] = 0
            print("[GANGGUAN] --buffer-lama: buffer terima perantara memakai ukuran bawaan sistem operasi.")
        if gg_cfg[0] == "tahan" and gg_cfg[1] < 10:
            print(f"[PERINGATAN] tahan {gg_cfg[1]} s kemungkinan besar habis ditampung buffer TCP gateway "
                  f"(~5,7 KB) sehingga gateway tidak ikut tertahan. Disarankan tahan:10:60 atau tahan:20:60.")
        if a.port == 1883:
            a.port = 1884                     # logger membaca Mosquitto langsung di port 1884
    gtag = f"_{gg_cfg[0].upper()}{gg_cfg[1]}" if gg_cfg else ""
    runs = max(1, a.runs)
    if runs > 1 and not a.duration:
        sys.exit("--runs butuh durasi: pakai --skenario atau --duration 600.")
    reps = list(range(a.rep, a.rep + runs))

    def sess_name(rp):
        if a.pendahuluan and not a.session:
            return f"{a.pendahuluan}_R{rp}"
        if a.session:
            return a.session if runs == 1 else f"{a.session}_R{rp}"
        return f"S{a.skenario:02d}_{arch}{gtag}_R{rp}" if a.skenario else ("SESI" + gtag)

    os.makedirs(a.outdir, exist_ok=True)
    PP = lambda sess, prefix, ext: path_sesi(a.outdir, sess, prefix, ext, buat=True)
    ada = [sess_name(r) for r in reps if os.path.exists(path_sesi(a.outdir, sess_name(r), "result", "json"))]
    if ada:
        ans = input(f"Sesi {', '.join(ada)} sudah pernah disimpan. Timpa? (y/n) ").strip().lower()
        if ans != "y":
            return 1

    if mqtt is None and not SERIAL_ONLY[0]:
        sys.exit("paho-mqtt belum terpasang: pip install paho-mqtt pyserial")

    judul = sess_name(reps[0]) + (f" s/d {sess_name(reps[-1])} ({runs} putaran)" if runs > 1 else "")
    print(f"=== SESI {judul} ===")
    isi_lokasi(a)
    if cfg:
        print(f"Skenario {a.skenario} ({cfg['jenis']}): payload {cfg['payload']} B | {cfg['nodes']} node BLE | "
              f"interval BLE {cfg['send_ms']} ms | interval LoRa {cfg['lora_s']} s | arsitektur {ARCH_NAME[arch]}")
        print("P_sent per putaran: " + ", ".join(f"{k}={v}" for k, v in sent_arg.items()))
        print("Atur SEMUA node dulu: node BLE sesi " f"{cfg['send_ms']} ms + payload {cfg['payload']} B, "
              f"node LoRa payload {cfg['payload']} B (interval {cfg['lora_s']} s). Cek OLED: adv20ms fail0.")
    if pcfg:
        print(f"UJI PENDAHULUAN {pcfg['kode']}: {pcfg['kondisi']}")
        print(f"  Gateway : {pcfg['gateway']}")
        print(f"  Node    : SATU node BLE saja (node BLE lain dan node LoRa DIMATIKAN), sesi 2000 ms, payload 7 B,")
        print(f"            ADV_INTERVAL_FIXED_MS {pcfg['adv']} -> OLED node harus menampilkan 'adv{pcfg['adv']}ms fail0'.")
        print(f"  P_sent  : {P_SENT_BLE} paket (10 menit / 2 s); sumber BLE dikenali otomatis.")
        if SERIAL_ONLY[0]:
            print("  Mode tanpa MQTT: P_receive = paket unik yang tercatat gateway (baris D,), latensi tidak diukur.")
    if gg_cfg:
        n_ev = len(range(a.gangguan_mulai, a.duration - 30 - gg_cfg[1] + 1, gg_cfg[2]))
        print(f"Uji gangguan: {gg_cfg[0].upper()} {gg_cfg[1]} s setiap {gg_cfg[2]} s, mulai detik ke-"
              f"{a.gangguan_mulai} (sekitar {n_ev} kali per putaran). Mosquitto harus memakai ttu_gangguan.conf "
              f"(port {a.port}).")
    if runs > 1:
        menit = runs * 11
        print(f"Mode seri: {runs} putaran x (10 menit kirim + 1 menit jeda) = sekitar {menit // 60} jam "
              f"{menit % 60} menit. Node harus memakai REPEAT_RUNS {runs}.")

    client = None
    if not SERIAL_ONLY[0]:
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, userdata={"topic": a.topic})
        except AttributeError:                       # paho-mqtt 1.x
            client = mqtt.Client(userdata={"topic": a.topic})
        client.on_connect = on_connect
        client.on_message = on_message
        try:
            client.connect(a.broker, a.port, keepalive=60)
        except Exception as e:
            sys.exit(f"[MQTT] GAGAL konek ke broker {a.broker}:{a.port} ({e}). Pastikan Mosquitto berjalan.")
        client.loop_start()

    if gg_cfg:
        proxy = PerantaraGangguan(a.port_perantara, "127.0.0.1", a.port)
        proxy.start()
        threading.Thread(target=jadwal_gangguan,
                         args=(proxy, gg_cfg[0], gg_cfg[1], gg_cfg[2], a.gangguan_mulai, a.duration),
                         daemon=True).start()
        a.gg_cfg = gg_cfg

    if a.serial:
        open_raw(PP(sess_name(reps[0]), "serial_raw", "log"))
        threading.Thread(target=serial_reader, args=(a.serial, a.baud), daemon=True).start()
    else:
        print("[PERINGATAN] tanpa --serial: latensi, diagnostik, dan letak paket hilang TIDAK tercatat.")

    # Tunggu broker + baris S, pertama (baseline penghitung gateway).
    t_wait = time.time()
    while client and not mqtt_ok.is_set() and time.time() - t_wait < 10:
        time.sleep(0.2)
    if SERIAL_ONLY[0]:
        print("Menunggu keluaran gateway khusus BLE (maks 25 s) ...")
        while not ble_only_seen[0] and time.time() - t_wait < 25:
            time.sleep(0.2)
    elif a.serial:
        print("Menunggu baris ringkasan S, pertama dari gateway (maks 25 s) ...")
        while not serial_ok.is_set() and time.time() - t_wait < 25:
            time.sleep(0.2)
        if not serial_ok.is_set():
            print("[PERINGATAN] belum ada baris S, dari gateway. Cek DATA_LOG_MODE 1, port, dan baud.")
    if pcfg:
        a.radio_ok = cek_gateway_pendahuluan(pcfg)
    if runs > 1:
        print(f"\n>>> SIAP: tekan BOOT pada SEMUA node sekarang (serentak), SEKALI saja. Node menjalankan {runs} "
              "putaran sendiri. Setelah ini Anda tinggal menunggu. Ctrl+C untuk berhenti. <<<\n")
    elif pcfg:
        print("\n>>> SIAP: tekan BOOT pada SATU node BLE sekarang (node lain mati). Ctrl+C untuk berhenti lebih awal. <<<\n")
    else:
        print("\n>>> SIAP: tekan BOOT pada SEMUA node sekarang (serentak). Ctrl+C untuk berhenti lebih awal. <<<\n")

    ringkas = []
    berhenti = False
    for i, rp in enumerate(reps):
        session = sess_name(rp)
        t0 = time.time()
        if i > 0:
            print(f"\n--- Menunggu putaran {i + 1}/{runs} ({session}); node sedang jeda ---")
            pindai_ulang(a)
        selesai_normal = False
        try:
            while True:
                time.sleep(10)
                with lock:
                    stat = " ".join(f"{s}:{len(received[s])}" + (f"/{sent_arg[s]}" if s in sent_arg else "")
                                    for s in SOURCES if s in sent_arg or received[s])
                    fp = first_pkt_time[0]
                tag = f"[{i + 1}/{runs}] " if runs > 1 else ""
                if fp is None:
                    w = int(time.time() - t0)
                    print(f"{tag}[{w:4d}s] menunggu paket pertama ...")
                    if i > 0 and w >= a.tunggu:
                        print(f"Putaran {i + 1} tidak dimulai dalam {a.tunggu} s -> seri dihentikan "
                              "(node sudah selesai, dibatalkan, atau REPEAT_RUNS node lebih kecil).")
                        berhenti = True
                        break
                    continue
                el = time.time() - fp
                print(f"{tag}[{int(el):4d}s] unik {'tercatat gateway' if SERIAL_ONLY[0] else 'di broker'} -> {stat}")
                if a.duration and el >= a.duration + a.grace:
                    selesai_normal = True
                    break
        except KeyboardInterrupt:
            print("\nDihentikan manual.")
            berhenti = True
        if first_pkt_time[0] is None:          # putaran ini tidak pernah dimulai
            open_raw(None)
            try:
                os.remove(PP(session, "serial_raw", "log"))
                os.rmdir(lokasi_sesi(a.outdir, session)[0])      # folder repetisi yang kosong
            except Exception:
                pass
            break
        # Tunggu sampai tidak ada pesan 5 s (paket terakhir putaran ini), maks 30 s.
        if selesai_normal:
            t_d = time.time()
            while time.time() - last_msg_time[0] < 5 and time.time() - t_d < 30:
                time.sleep(0.5)
        accepting[0] = False
        time.sleep(1.2)                        # baris Serial terakhir ikut masuk
        ringkas.append(finalize_run(a, session, rp, sent_arg, cfg, arch, PP, t0))
        if berhenti or i == len(reps) - 1:
            break
        reset_run_state()
        open_raw(PP(sess_name(reps[i + 1]), "serial_raw", "log") if a.serial else None)
        accepting[0] = True

    stop_flag.set()
    if client:
        client.loop_stop()
    open_raw(None)
    if runs > 1:
        print(f"\n=== RINGKASAN SERI ({len(ringkas)}/{runs} putaran tersimpan) ===")
        for line in ringkas:
            print("  " + line)
        _, grp = lokasi_sesi(a.outdir, sess_name(reps[0]))
        if grp:
            print(f"Tabel seri: {os.path.join(a.outdir, grp, 'seri_' + grp)}.xlsx / .txt")
        print(f"Langkah berikutnya: upload firmware gateway lain / skenario lain, atau jalankan "
              f"python logger.py --rekap {a.outdir}")
    return 0


def finalize_run(a, session, rp, sent_arg, cfg, arch, PP, t0):
    """Simpan 5 file satu putaran + perbarui Tabel 12. Mengembalikan satu baris ringkasan."""
    P = lambda prefix, ext: PP(session, prefix, ext)
    with lock:
        rows_m = list(mqtt_rows)
        rows_l = list(lat_rows)
    with open(P("mqtt", "csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pc_time", "src", "seq", "node_ts", "gw_rx_ms", "len", "json_asli"])
        w.writerows(rows_m)
    with open(P("latency", "csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["src", "seq", "t_receive_ms", "t_send_ms", "latency_ms", "payload_len"])
        w.writerows(rows_l)

    pcfg = getattr(a, "pcfg", None)
    peringatan_p = []
    if pcfg:
        sent_arg = dict(sent_arg)
        if not any(k.startswith("BLE") for k in sent_arg):
            with lock:
                ada = sorted(((len(received[s]), s) for s in BLE_SOURCES if received[s]), reverse=True)
            if ada:
                sent_arg[ada[0][1]] = P_SENT_BLE
            if len(ada) > 1:
                peringatan_p.append("!!! Lebih dari satu sumber BLE terdeteksi (" + ", ".join(
                    f"{s}={n} paket" for n, s in ada) + "). Uji pendahuluan memakai SATU node; sumber lain bisa "
                    "berasal dari node yang lupa dimatikan atau perangkat BLE lain -> pertimbangkan mengulang.")
    res = compute(sent_arg, a.adv_ms, a.send_ms)
    gg_cfg = getattr(a, "gg_cfg", None)
    with lock:
        evs = [dict(e) for e in gangguan_events]
    if gg_cfg:
        gg = analisis_gangguan(evs, sent_arg, a.send_ms, cfg["lora_s"] if cfg else None, rows_m)
        if gg:
            gg.update({"mode": gg_cfg[0], "durasi_s": gg_cfg[1], "periode_s": gg_cfg[2]})
            res["gangguan"] = gg
    meta = {"sesi": session, "skenario": a.skenario, "rep": rp, "arch": arch or None,
            "gangguan": f"{gg_cfg[0].upper()}{gg_cfg[1]}" if gg_cfg else None,
            "gangguan_cfg": {"mode": gg_cfg[0], "durasi_s": gg_cfg[1], "periode_s": gg_cfg[2],
                             "mulai_s": a.gangguan_mulai,
                             "buffer_perantara": RCVBUF_PERANTARA[0] or "bawaan OS"} if gg_cfg else None,
            "gangguan_events": evs or None,
            "jenis_uji": cfg["jenis"] if cfg else None, "payload": a.payload, "nodes": a.nodes,
            "send_ms": a.send_ms, "adv_ms": a.adv_ms, "lora_s": cfg["lora_s"] if cfg else None,
            "tanggal_jam": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first_pkt_time[0] or t0)),
            "kanal_wifi": a.kanal, "jaringan_sekitar": a.jaringan, "posisi_node": a.posisi,
            "pindaian_wifi": getattr(a, "wifi_scan", None),
            "catatan": a.catatan,
            "radio_gateway": radio_cfg_line[0], "scan_restart": ble_scan_restart_count,
            "pendahuluan": pcfg["kode"] if pcfg else None,
            "kondisi": pcfg["kondisi"] if pcfg else None,
            "gateway_uji": ("Gateway_BLE_Only_Test" if pcfg["wns"] is None else pcfg["gateway"]) if pcfg else None,
            "radio_terverifikasi": getattr(a, "radio_ok", None) if pcfg else None}
    meta_lines = [
        f"Tanggal/jam: {meta['tanggal_jam']} | Skenario: {a.skenario or '-'} | Repetisi: {rp or '-'} | "
        f"Arsitektur: {ARCH_NAME.get(arch, arch or '-')}",
        f"Payload: {a.payload or '-'} B | Node BLE: {a.nodes or '-'} | Interval BLE: {a.send_ms or '-'} ms | "
        f"Interval LoRa: {meta['lora_s'] or '-'} s | Interval iklan: {a.adv_ms or '-'} ms",
        f"Kanal Wi-Fi: {a.kanal or '-'} | Jaringan sekitar: {a.jaringan or '-'} | Posisi node: {a.posisi or '-'} | "
        f"Catatan: {a.catatan or '-'}",
    ] + ([f"UJI GANGGUAN: {gg_cfg[0].upper()} {gg_cfg[1]} s setiap {gg_cfg[2]} s ({len(evs)} kali)"] if gg_cfg else []) \
      + ([f"UJI PENDAHULUAN {pcfg['kode']}: {pcfg['kondisi']} | gateway: {meta['gateway_uji']} | pengaturan radio "
          + {True: "terverifikasi", False: "TIDAK SESUAI", None: "tidak terverifikasi"}[meta['radio_terverifikasi']]]
         if pcfg else []) + peringatan_p
    lt = res.get("letak_total") or {}
    if arch and lt.get("arch_gateway") and lt["arch_gateway"] != arch:
        meta_lines.append(f"!!! PERINGATAN: --arch {arch} tetapi gateway melapor arch={lt['arch_gateway']}. "
                          "Cek firmware yang ter-upload!")
    text = report_text(meta_lines, res, a.adv_ms, a.send_ms, session=session)
    if pcfg:
        b = baris_pendahuluan(res, pcfg["kode"])
        if b:
            f = lambda x, nd: "-" if x is None else (f"{x:.{nd}f}".replace(".", ",") if nd else f"{x:,}".replace(",", "."))
            text += ("\n\nBaris tabel uji pendahuluan (Kode | Kondisi | P_sent | P_receive | PLR BLE | "
                     "Rasio penerimaan iklan | Celah terlama):\n"
                     f"  {pcfg['kode']} | {pcfg['kondisi']} | {b['p_sent']} | {b['p_recv']} | {f(b['plr'], 2)}% | "
                     f"{f(b['rasio'], 1)}% | {f(b['celah'], 0)} ms")
    text += ("\n\nFile sesi: " + ", ".join(os.path.basename(P(p, e)) for p, e in
             (("summary", "txt"), ("result", "json"), ("mqtt", "csv"), ("latency", "csv"), ("serial_raw", "log")))
             + f" (folder '{os.path.dirname(P('result', 'json'))}')")
    print("\n" + text)
    with open(P("summary", "txt"), "w", encoding="utf-8") as f:
        f.write(text + "\n")
    with open(P("result", "json"), "w", encoding="utf-8") as f:
        json.dump({"meta": meta, "hasil": res}, f, ensure_ascii=False, indent=1)
    p12, n12 = tulis_tabel12(a.outdir)
    if pcfg:
        tp = tulis_tabel_pendahuluan(a.outdir)
        if tp:
            print(f"Tabel uji pendahuluan diperbarui: {tp[0]}")
    _, grp = lokasi_sesi(a.outdir, session)
    if grp:
        tulis_seri(a.outdir, grp)
    tulis_baca_saya(a.outdir)
    print(f"Tabel 12 diperbarui: {os.path.basename(p12)} ({n12} sesi)"
          + (f" | tabel seri diperbarui: seri_{grp}" if grp else ""))
    t = res.get("total") or {}
    lat = (t.get("latensi") or {}).get("mean")
    return (f"{session}: PLR total {t.get('plr', float('nan')):.2f}% ({t.get('tiphon', '-')})"
            + (f", latensi rata-rata {lat:.1f} ms" if lat is not None else ""))


if __name__ == "__main__":
    sys.exit(main())
