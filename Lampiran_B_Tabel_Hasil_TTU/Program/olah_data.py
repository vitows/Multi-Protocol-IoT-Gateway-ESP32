# -*- coding: utf-8 -*-
"""
Program pengolahan data
Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32 dengan Implementasi FreeRTOS

Program ini membaca data mentah pada folder Data_Mentah (Lampiran A), lalu:
  1. menghitung PLR dan latensi setiap sesi,
  2. menjalankan seluruh uji statistik pada subbab 6.8 proposal (alur A, B, dan C pada Gambar 10), dan
  3. menulis Tabel B.1 sampai B.12 ke Tabel_Hasil_Pengujian.xlsx (Lampiran B).
Contoh perhitungan setiap langkah dijelaskan pada Lampiran C dan Lampiran D proposal.

Cara menjalankan (Python 3.10 atau lebih baru):
    pip install numpy scipy openpyxl
    python olah_data.py
Folder Lampiran_A_Data_Mentah_TTU dan Lampiran_B_Tabel_Hasil_TTU diletakkan berdampingan dalam satu folder.
Bila letaknya berbeda, sebutkan folder Data_Mentah saat menjalankan program:
    python olah_data.py <folder Data_Mentah>
"""
import csv, glob, itertools, json, math, os, re, sys
import numpy as np
from scipy import stats

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
CALON = [sys.argv[1]] if len(sys.argv) > 1 else [os.path.join(ROOT, '..', 'Lampiran_A_Data_Mentah_TTU', 'Data_Mentah'),
                                                  os.path.join(ROOT, 'Data_Mentah')]
DATA = next((os.path.abspath(c) for c in CALON if os.path.isdir(c)), None)
if DATA is None:
    sys.exit('Folder Data_Mentah tidak ditemukan. Letakkan folder Lampiran_A_Data_Mentah_TTU di samping folder '
             'Lampiran_B_Tabel_Hasil_TTU, atau jalankan: python olah_data.py <folder Data_Mentah>')
XLSX = os.path.join(ROOT, 'Tabel_Hasil_Pengujian.xlsx')

ARS = {'RTOS': 'FreeRTOS', 'BM': 'Bare Metal'}
# skenario utama yang diuji (skenario 4 memakai data skenario 1, skenario 8 memakai data skenario 7)
SC = [1, 2, 3, 5, 6, 7, 9, 10, 11, 12]
PENGATURAN = {1: '7 B, 1 node, 2000 ms', 2: '16 B, 1 node, 2000 ms', 3: '32 B, 1 node, 2000 ms', 4: '7 B, 1 node, 2000 ms (data skenario 1)',
              5: '7 B, 2 node, 2000 ms', 6: '7 B, 3 node, 2000 ms', 7: '7 B, 4 node, 2000 ms', 8: '7 B, 4 node, 2000 ms (data skenario 7)',
              9: '7 B, 4 node, 1000 ms', 10: '7 B, 4 node, 500 ms', 11: '7 B, 4 node, 100 ms', 12: '32 B, 4 node, 100 ms',
              13: '7 B, 4 node, 500 ms, tersendat 2 s', 14: '7 B, 4 node, 500 ms, putus 10 s', 15: '7 B, 4 node, 500 ms, putus 30 s'}
GANGGUAN = {'TAHAN2': (13, 'tersendat 2 s'), 'PUTUS10': (14, 'putus 10 s'), 'PUTUS30': (15, 'putus 30 s')}
LOADS = {'payload': ('Ukuran payload', [1, 2, 3], [7, 16, 32], ['7 byte', '16 byte', '32 byte']),
         'node': ('Jumlah node BLE', [1, 5, 6, 7], [1, 2, 3, 4], ['1 node', '2 node', '3 node', '4 node']),
         'interval': ('Interval kirim BLE', [7, 9, 10, 11], [2000, 1000, 500, 100], ['2000 ms', '1000 ms', '500 ms', '100 ms'])}
TARGET_PLR, TARGET_LAT = 5.0, 1000.0


# ============================================================ 1. membaca data satu sesi
def latensi_sesi(folder, p_sent):
    """Latensi setiap paket (persamaan 8). Hanya paket milik sesi ini yang dihitung:
    paket diterima paling cepat 5 detik sebelum paket BLE bernomor urut 0, dan nomor urutnya lebih kecil dari P_send."""
    f = glob.glob(os.path.join(folder, 'latency_*.csv'))
    if not f:
        return None
    rows = list(csv.DictReader(open(f[0], encoding='utf-8')))
    t0s = [int(r['t_receive_ms']) for r in rows if r['src'].startswith('BLE') and int(r['seq']) == 0]
    t0 = np.median(t0s) if t0s else min(int(r['t_receive_ms']) for r in rows)
    keep = [r for r in rows if int(r['t_receive_ms']) >= t0 - 5000 and int(r['seq']) < p_sent.get(r['src'], 10 ** 9)]
    return np.array([float(r['latency_ms']) for r in keep])


def baca_sesi(json_path):
    d = json.load(open(json_path, encoding='utf-8'))
    m, h = d['meta'], d['hasil']
    folder = os.path.dirname(json_path)
    log = glob.glob(os.path.join(folder, 'serial_raw_*.log'))
    t = open(log[0], errors='ignore').read() if log else ''
    i0 = t.find('TERSAMBUNG')
    putus = len(re.findall(r'Publish GAGAL', t)) + (len(re.findall(r'gagal connect', t[i0:])) if i0 >= 0 else 0)
    p_sent = {k: v['p_sent'] for k, v in h['sumber'].items()}
    lat = latensi_sesi(folder, p_sent)
    lt = h['letak_total']
    base = lt.get('baseline')
    if base:
        radio, prog, pub = lt['hilang_radio'], lt['hilang_program'], lt['gagal_publish']
    else:  # data awal gateway tidak lengkap: seluruh paket hilang dianggap hilang di radio
        radio, prog, pub = h['total']['p_sent'] - h['total']['p_recv'], 0, 0
    ble = [v for k, v in h['sumber'].items() if k.startswith('BLE') and v.get('radio')]
    return dict(nama=m['sesi'], meta=m, sesi=m['rep'], tanggal=m['tanggal_jam'],
                p_sent=h['total']['p_sent'], p_recv=h['total']['p_recv'], plr=h['total']['plr'], tiphon=h['total'].get('tiphon'),
                plr_ble=h['protokol']['BLE']['plr'], plr_lora=h['protokol']['LoRa']['plr'],
                lat=lat.mean(), p50=np.percentile(lat, 50), p95=np.percentile(lat, 95), lmax=lat.max(),
                nlat=len(lat), nol=float((lat == 0).mean() * 100), latraw=lat,
                radio=radio, prog=prog, pub=pub, base=bool(base), putus=putus,
                cr=float(np.mean([v['radio']['capture_rate'] for v in ble])) if ble else None,
                src=h['sumber'], g=h.get('gangguan'))


def baca_folder(nama):
    return [baca_sesi(f) for f in sorted(glob.glob(os.path.join(DATA, nama, 'R*', 'result_*.json')))]


# ============================================================ 2. fungsi statistik
def holm(ps):
    """Koreksi Holm: p diurutkan dari terkecil, p ke-k dikali (m - k + 1), lalu dibuat tidak pernah turun."""
    idx = sorted(range(len(ps)), key=lambda i: ps[i])
    m, adj, run = len(ps), [0.0] * len(ps), 0.0
    for k, i in enumerate(idx):
        run = max(run, min(1.0, (m - k) * ps[i]))
        adj[i] = run
    return adj


def shapiro(x):
    x = np.asarray(x, float)
    if np.ptp(x) == 0:
        return None, None
    r = stats.shapiro(x)
    return r.statistic, r.pvalue


def uji_beda(a, b):
    """Alur A: Shapiro-Wilk -> uji t Welch (keduanya normal) atau Mann-Whitney U (minimal satu tidak normal)."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    wa, pa = shapiro(a)
    wb, pb = shapiro(b)
    if np.ptp(np.concatenate([a, b])) == 0:
        return dict(uji='tidak diuji (semua nilai sama)', stat=None, p=1.0, wa=wa, pa=pa, wb=wb, pb=pb, df=None)
    if pa is not None and pb is not None and pa > 0.05 and pb > 0.05:
        t = stats.ttest_ind(a, b, equal_var=False)
        va, vb = np.var(a, ddof=1) / len(a), np.var(b, ddof=1) / len(b)
        df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
        return dict(uji='t Welch', stat=t.statistic, p=t.pvalue, wa=wa, pa=pa, wb=wb, pb=pb, df=df)
    u = stats.mannwhitneyu(a, b, alternative='two-sided')
    return dict(uji='Mann-Whitney U', stat=u.statistic, p=u.pvalue, wa=wa, pa=pa, wb=wb, pb=pb, df=None)


def sk95_bootstrap(a, b, n=5000, seed=1):
    """Selang kepercayaan 95% selisih rata-rata (a - b): 5.000 kali ambil ulang acak, lalu persentil 2,5 dan 97,5."""
    rng = np.random.default_rng(seed)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = [rng.choice(a, len(a)).mean() - rng.choice(b, len(b)).mean() for _ in range(n)]
    return np.percentile(d, [2.5, 97.5])


def batas_atas(x):
    """Batas atas 95% satu sisi rata-rata: rata-rata + t(0,95; n-1) x s / akar(n)."""
    x = np.asarray(x, float)
    t = stats.t.ppf(0.95, len(x) - 1)
    return x.mean() + t * x.std(ddof=1) / math.sqrt(len(x)), t


def dunn(groups):
    """Uji post hoc Dunn (1964) dengan koreksi ties dan koreksi Holm."""
    allv = np.concatenate(groups)
    N = len(allv)
    ranks = stats.rankdata(allv)
    _, cnt = np.unique(allv, return_counts=True)
    ties = np.sum(cnt ** 3 - cnt)
    s2 = N * (N + 1) / 12 - ties / (12 * (N - 1))
    idx = np.cumsum([0] + [len(g) for g in groups])
    mr = [ranks[idx[i]:idx[i + 1]].mean() for i in range(len(groups))]
    out = []
    for i, j in itertools.combinations(range(len(groups)), 2):
        se = math.sqrt(s2 * (1 / len(groups[i]) + 1 / len(groups[j])))
        z = (mr[i] - mr[j]) / se
        out.append([i, j, mr[i], mr[j], se, z, 2 * stats.norm.sf(abs(z))])
    for o, ph in zip(out, holm([o[6] for o in out])):
        o.append(ph)
    return mr, out, ties, s2


def bintang(p):
    return '***' if p < 0.001 else '**' if p < 0.01 else '*' if p < 0.05 else 'tidak signifikan'


# ============================================================ 3. pengolahan
print('membaca data ...')
data = {(s, a): baca_folder('S%02d_%s' % (s, a)) for s in SC for a in ARS}
gang = {(lv, a): baca_folder('S10_%s_%s' % (a, lv)) for lv in GANGGUAN for a in ARS}
pend = {}
for kode in ['P1', 'P3', 'P4', 'P5']:
    for f in sorted(glob.glob(os.path.join(DATA, 'PENDAHULUAN', kode, 'R*', 'result_*.json'))):
        pend.setdefault(kode, []).append(f)
assert sum(len(v) for v in data.values()) + sum(len(v) for v in gang.values()) == 230

# alur A: uji beda FreeRTOS dan Bare Metal pada skenario 1-12
cmp = {}
for metr in ['plr', 'lat']:
    rows = []
    for s in SC:
        a = [r[metr] for r in data[(s, 'RTOS')]]
        b = [r[metr] for r in data[(s, 'BM')]]
        c = uji_beda(a, b)
        c.update(s=s, ma=np.mean(a), mb=np.mean(b), sa=np.std(a, ddof=1), sb=np.std(b, ddof=1), ci=sk95_bootstrap(a, b))
        rows.append(c)
    for row, ph in zip(rows, holm([x['p'] for x in rows])):
        row['ph'] = ph
    cmp[metr] = rows

# alur A pada uji gangguan (skenario 13-15)
G, GC = {}, {}
for lv in ['PUTUS10', 'PUTUS30', 'TAHAN2']:
    G[lv] = {}
    for a in ARS:
        v = gang[(lv, a)]
        ok = [r for r in v if r['base']]
        G[lv][a] = dict(plr=[r['plr'] for r in v], hs=[r['g']['total']['hilang_saat'] for r in v],
                        late=[r['g']['total']['terlambat'] for r in v], lmax=[r['lmax'] / 1000 for r in ok],
                        lat=[r['lat'] for r in ok])
for metr in ['plr', 'hs', 'late', 'lmax', 'lat']:
    rows = [uji_beda(G[lv]['RTOS'][metr], G[lv]['BM'][metr]) for lv in G]
    for row, ph, lv in zip(rows, holm([x['p'] for x in rows]), G):
        row['ph'] = ph
        row['lv'] = lv
    GC[metr] = rows

# alur B: Kruskal-Wallis, Spearman, dan uji post hoc Dunn
KW, DUNN = {}, {}
for k, (judul, ss, xs, lab) in LOADS.items():
    for a in ARS:
        groups = [np.array([r['plr'] for r in data[(s, a)]]) for s in ss]
        kw = stats.kruskal(*groups)
        xv = np.concatenate([[x] * len(g) for x, g in zip(xs, groups)])
        sp = stats.spearmanr(xv, np.concatenate(groups))
        KW[(k, a)] = dict(H=kw.statistic, kw=kw.pvalue, rho=sp.correlation, psp=sp.pvalue,
                          means=[g.mean() for g in groups], sds=[g.std(ddof=1) for g in groups])
        mr, pairs, ties, s2 = dunn(groups)
        DUNN[(k, a)] = dict(H=kw.statistic, kw=kw.pvalue, labels=lab, means=[g.mean() for g in groups], sds=[g.std(ddof=1) for g in groups],
                            mr=mr, pairs=[(o[0], o[1], o[5], o[6], o[7]) for o in pairs], rinci=pairs, ties=ties, s2=s2)

# alur C: batas atas 95%
UB = {}
for s in SC:
    for a in ARS:
        for metr in ['plr', 'lat']:
            x = [r[metr] for r in data[(s, a)]]
            ub, t = batas_atas(x)
            UB[(s, a, metr)] = dict(n=len(x), mean=np.mean(x), sd=np.std(x, ddof=1), t=t, ub=ub)


# ============================================================ 4. menulis tabel hasil (Excel)
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

wb = Workbook()
HFILL = PatternFill('solid', fgColor='DCE6F1')
THIN = Side(style='thin', color='A6A6A6')
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


FONT = 'Times New Roman'


def _teks(v, f=None):
    """Perkiraan teks yang tampil pada sel, untuk menghitung lebar kolom dan tinggi baris."""
    if v is None:
        return ''
    if isinstance(v, float):
        d = len(f.split('.')[1]) if f and '.' in f and 'E' not in f else 4
        return '%.*f' % (d, v)
    return str(v)


def _baris(teks, lebar):
    """Jumlah baris teks bila dibungkus pada kolom selebar lebar karakter."""
    n, isi = 1, 0
    for kata in str(teks).split():
        if isi == 0:
            isi = len(kata)
        elif isi + 1 + len(kata) <= lebar:
            isi += 1 + len(kata)
        else:
            n, isi = n + 1, len(kata)
    return n


def lembar(kode, nama, judul, catatan, header, rows, fmt=None, lebar=None):
    """Satu lembar Excel = satu tabel: judul, catatan, lalu tabel dengan judul kolom berlatar biru muda.
    Angka dan teks pendek rata tengah, teks panjang rata kiri, dan semua sel rata tengah secara vertikal."""
    ws = wb.create_sheet(kode + ' ' + nama)
    fmt = fmt or {}
    nk = len(header)
    rows = [[v.item() if isinstance(v, (np.floating, np.integer)) else v for v in row] for row in rows]
    # lebar kolom: cukup untuk kata terpanjang pada judul kolom dan isi kolom, paling lebar 32 karakter
    lb = {}
    for j in range(1, nk + 1):
        h = str(header[j - 1])
        isi = [len(_teks(r[j - 1], fmt.get(j))) for r in rows]
        lb[j] = max(10, min(46, max([int(max(len(k) for k in h.split()) * 1.2) + 3, len(h) // 3 + 3] + [int(x * 1.2) + 3 for x in isi])))
    kiri = {j: any(isinstance(r[j - 1], str) and len(r[j - 1]) > 16 for r in rows) for j in range(1, nk + 1)}
    total = sum(lb.values())
    # judul dan catatan digabung selebar tabel
    teks_atas = [('Tabel %s. %s' % (kode, judul), Font(name=FONT, bold=True, size=12))]
    teks_atas += [(t, Font(name=FONT, italic=True, size=10, color='404040')) for t in catatan]
    for r, (t, f) in enumerate(teks_atas, 1):
        c = ws.cell(row=r, column=1, value=t)
        c.font = f
        c.alignment = Alignment(wrap_text=True, vertical='center', horizontal='left')
        if nk > 1:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=nk)
        ws.row_dimensions[r].height = 15 * _baris(t, int(total * 1.1)) + 5
    r0 = len(teks_atas) + 2
    # judul kolom
    for j, h in enumerate(header, 1):
        c = ws.cell(row=r0, column=j, value=h)
        c.font = Font(name=FONT, bold=True, size=11)
        c.fill = HFILL
        c.border = BOX
        c.alignment = Alignment(wrap_text=True, vertical='center', horizontal='center')
    ws.row_dimensions[r0].height = 15 * max(_baris(h, lb[j] - 2) for j, h in enumerate(header, 1)) + 8
    # isi tabel; semua baris isi diberi tinggi yang sama agar tabel terlihat rata
    tinggi = []
    for i, row in enumerate(rows, r0 + 1):
        nbaris = 1
        for j, v in enumerate(row, 1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = BOX
            c.font = Font(name=FONT, size=11)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                f = fmt.get(j)
                if f and isinstance(v, float) and f.startswith('0.000') and v != 0 and abs(v) < 0.001:
                    f = '0.00E+00'      # nilai p yang sangat kecil tidak ditampilkan sebagai 0,0000
                if f == '0.000' and isinstance(v, int):
                    f = '0'             # nilai U uji Mann-Whitney berupa bilangan bulat
                if f:
                    c.number_format = f
                c.alignment = Alignment(vertical='center', horizontal='center')
            else:
                c.alignment = Alignment(wrap_text=True, vertical='center', horizontal='left' if kiri[j] else 'center')
                nbaris = max(nbaris, _baris(_teks(v), lb[j] - 2))
        tinggi.append(nbaris)
    for i in range(r0 + 1, r0 + 1 + len(rows)):
        ws.row_dimensions[i].height = 15 * max(tinggi + [1]) + 8
    for j in range(1, nk + 1):
        ws.column_dimensions[get_column_letter(j)].width = lb[j]
    ws.freeze_panes = ws.cell(row=r0 + 1, column=1)
    ws.sheet_view.showGridLines = False
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_title_rows = '%d:%d' % (r0, r0)
    return ws


def nkat(p):
    if p is None:
        return 'semua nilai sama (tidak dapat diuji)'
    return 'terdistribusi normal' if p > 0.05 else 'tidak terdistribusi normal'


# ---------------------------------------------------------------- Tabel B.1 catatan seluruh sesi
rows = []


def catat(skenario, versi, files):
    for f in files:
        m = json.load(open(f, encoding='utf-8'))['meta']
        rows.append([m['sesi'], skenario, versi, m['rep'], m['tanggal_jam'], m.get('payload'), m.get('nodes'), m.get('send_ms'),
                     m.get('adv_ms'), m['lora_s'] if m.get('lora_s') is not None else 'tidak dipakai', GANGGUAN[m['gangguan']][1] if m.get('gangguan') in GANGGUAN else 'tidak ada',
                     m.get('kanal_wifi'), m.get('jaringan_sekitar'), m.get('posisi_node')])


for kode, files in pend.items():
    catat(kode, 'program tanpa Wi-Fi' if kode == 'P3' else 'FreeRTOS', files)
for s in SC:
    for a in ARS:
        catat(s, ARS[a], sorted(glob.glob(os.path.join(DATA, 'S%02d_%s' % (s, a), 'R*', 'result_*.json'))))
for lv, (no, nm) in sorted(GANGGUAN.items(), key=lambda x: x[1][0]):
    for a in ARS:
        catat(no, ARS[a], sorted(glob.glob(os.path.join(DATA, 'S10_%s_%s' % (a, lv), 'R*', 'result_*.json'))))
lembar('B.1', 'Catatan sesi', 'Catatan seluruh sesi pengujian (12 sesi uji pendahuluan dan 230 sesi pengujian utama)',
       ['Nama sesi menunjukkan foldernya, misalnya S01_RTOS_R1 tersimpan pada folder S01_RTOS/R01 dan P1_R1 pada folder PENDAHULUAN/P1/R01.',
        'Kanal Wi-Fi router dan jumlah jaringan Wi-Fi di sekitar diambil otomatis oleh logger.py dari pemindaian Wi-Fi laptop. '
        'Uji pendahuluan hanya memakai 1 node BLE, sehingga node LoRa tidak dipakai. P3 memakai program gateway khusus yang hanya menjalankan scan BLE tanpa Wi-Fi.'],
       ['Nama sesi', 'Skenario', 'Versi', 'Sesi ke-', 'Tanggal dan jam', 'Ukuran payload (byte)', 'Jumlah node BLE',
        'Interval kirim BLE (ms)', 'Advertising interval BLE (ms)', 'Interval kirim LoRa (s)', 'Gangguan', 'Kanal Wi-Fi router',
        'Jumlah jaringan Wi-Fi di sekitar', 'Posisi node'], rows, lebar={1: 22, 3: 14, 5: 19})

# ---------------------------------------------------------------- Tabel B.2 uji pendahuluan
rows = []
NAMA_P = {'P1': 'Advertising 100 ms, Wi-Fi aktif', 'P3': 'Advertising 100 ms, tanpa Wi-Fi', 'P4': 'Advertising 100 ms, Bluetooth diprioritaskan',
          'P5': 'Advertising 20 ms, Wi-Fi aktif'}
for kode, files in pend.items():
    for f in files:
        d = json.load(open(f, encoding='utf-8'))
        b = d['hasil']['sumber']['BLE1']
        rd = b.get('radio') or {}
        rows.append([kode, NAMA_P[kode], d['meta']['rep'], b['p_sent'], b['p_recv'], b['plr'], rd.get('iklan_tertangkap'),
                     rd.get('capture_rate'), rd.get('celah_terlama_ms')])
lembar('B.2', 'Uji pendahuluan', 'Hasil uji pendahuluan setiap sesi (1 node BLE, interval kirim 2000 ms, payload 7 byte)',
       ['Tabel ini menjadi dasar Tabel 11 dan Gambar 11 proposal. P2 berupa pemeriksaan pengaturan scan gateway, sehingga tidak memiliki data sesi.',
        'Capture rate = advertising tertangkap / perkiraan advertising yang dipancarkan (lama node tercatat aktif / advertising interval rata-rata) x 100% (subbab 5.3.8 dan Lampiran C.4 proposal).'],
       ['Skenario', 'Pengaturan', 'Sesi ke-', 'P_send', 'P_receive', 'PLR BLE (%)', 'Advertising tertangkap', 'Capture rate (%)',
        'Jeda terpanjang tanpa advertising (ms)'], rows,
       fmt={6: '0.00', 8: '0.0'}, lebar={2: 36})

# ---------------------------------------------------------------- Tabel B.3 hasil setiap sesi
rows = []
for s in SC:
    for a in ARS:
        for r in data[(s, a)]:
            rows.append([s, ARS[a], r['sesi'], r['p_sent'], r['p_recv'], r['plr'], r['plr_ble'], r['plr_lora'], r['tiphon'],
                         r['lat'], r['p50'], r['p95'], r['lmax'], r['nol'], r['radio'], r['prog'], r['pub'], r['cr'], r['nlat'], r['nama']])
lembar('B.3', 'Hasil per sesi', 'PLR, latensi, dan jumlah paket yang hilang di radio dan di dalam program pada setiap sesi (skenario 1 sampai 12)',
       ['Tabel ini menjadi dasar Tabel 13 serta Gambar 12 sampai 16 proposal. PLR dihitung dengan persamaan (7) dan latensi dengan persamaan (8).',
        'Paket tercatat 0 ms adalah persentase paket yang diterima dan di-publish pada milidetik yang sama (resolusi millis() 1 ms).'],
       ['Skenario', 'Versi', 'Sesi ke-', 'P_send', 'P_receive', 'PLR total (%)', 'PLR BLE (%)', 'PLR LoRa (%)', 'Kategori TIPHON',
        'Latensi rata-rata (ms)', 'Latensi median (ms)', 'Latensi persentil ke-95 (ms)', 'Latensi maksimum (ms)',
        'Paket tercatat 0 ms (%)', 'Hilang di radio (paket)', 'Hilang di program (paket)', 'Gagal publish (paket)',
        'Capture rate BLE (%)', 'Jumlah paket yang dihitung latensinya', 'Nama sesi'], rows,
       fmt={6: '0.00', 7: '0.00', 8: '0.00', 10: '0.00', 11: '0.0', 12: '0.0', 13: '0', 14: '0.0', 18: '0.0'}, lebar={2: 13, 20: 18})

# ---------------------------------------------------------------- Tabel B.4 rekapitulasi
# Median, nilai terkecil, dan nilai terbesar ditambahkan karena sebagian data tidak tersebar seimbang di sekitar
# rata-ratanya (lihat CATATAN SIMPANGAN BAKU pada README.txt).
rows = []
for s in range(1, 16):
    for a in ARS:
        if s <= 12:
            v = data[({4: 1, 8: 7}.get(s, s), a)]
            lat = [r['lat'] for r in v]
        else:
            lv = [k for k, (no, _) in GANGGUAN.items() if no == s][0]
            v = gang[(lv, a)]
            lat = [r['lat'] for r in v if r['base']]
        plr = [r['plr'] for r in v]
        rows.append([s, PENGATURAN[s], ARS[a], len(v), np.mean(plr), np.std(plr, ddof=1), np.median(plr), np.min(plr), np.max(plr),
                     np.mean([r['plr_ble'] for r in v]), np.mean([r['plr_lora'] for r in v]),
                     len(lat), np.mean(lat), np.std(lat, ddof=1), np.median(lat), np.min(lat), np.max(lat)])
lembar('B.4', 'Rekapitulasi', 'Rekapitulasi PLR dan latensi rata-rata setiap skenario beserta median dan rentangnya',
       ['Tabel ini menjadi dasar Tabel 13 proposal. Skenario 4 dan 8 memakai data skenario 1 dan 7. Latensi skenario 13 sampai 15 dihitung dari sesi yang log Serial gateway-nya lengkap sejak awal sesi.',
        'Rata-rata, simpangan baku, median, terkecil, dan terbesar dihitung dari nilai setiap sesi pada Tabel B.3 dan B.8. '
        'Bila simpangan baku lebih besar daripada rata-rata, gunakan median dan rentangnya (penjelasan pada README.txt).'],
       ['Skenario', 'Pengaturan (payload, node BLE, interval kirim BLE)', 'Versi', 'Jumlah sesi', 'PLR total rata-rata (%)',
        'PLR total simpangan baku (%)', 'PLR total median (%)', 'PLR total terkecil (%)', 'PLR total terbesar (%)',
        'PLR BLE rata-rata (%)', 'PLR LoRa rata-rata (%)', 'Jumlah sesi latensi', 'Latensi rata-rata (ms)',
        'Latensi simpangan baku (ms)', 'Latensi median (ms)', 'Latensi terkecil (ms)', 'Latensi terbesar (ms)'], rows,
       fmt={5: '0.00', 6: '0.00', 7: '0.00', 8: '0.00', 9: '0.00', 10: '0.00', 11: '0.00', 13: '0.0', 14: '0.0', 15: '0.0', 16: '0.0', 17: '0.0'},
       lebar={2: 40})

# ---------------------------------------------------------------- Tabel B.5 Kruskal-Wallis dan Spearman
rows = []
for k, (judul, ss, xs, lab) in LOADS.items():
    for a in ARS:
        r = KW[(k, a)]
        rows.append([judul, ARS[a], ', '.join('S%d' % s for s in ss), ', '.join(lab), '; '.join(('%.2f' % m).replace('.', ',') for m in r['means']),
                     r['H'], len(ss) - 1, r['kw'], 'berbeda signifikan' if r['kw'] < 0.05 else 'tidak berbeda signifikan', r['rho'], r['psp']])
lembar('B.5', 'Kruskal-Wallis', 'Uji Kruskal-Wallis dan korelasi Spearman antartingkat beban dalam satu versi (alur B)',
       ['Tabel ini menjadi dasar subbab 7.4 sampai 7.6 serta Gambar 12 sampai 14 proposal. Setiap tingkat beban berisi 10 sesi.',
        'ρ negatif pada interval kirim berarti PLR naik ketika interval kirim diperkecil (beban bertambah).'],
       ['Jenis uji', 'Versi', 'Skenario', 'Tingkat beban', 'PLR rata-rata setiap tingkat (%)', 'H Kruskal-Wallis', 'Derajat bebas',
        'Nilai p', 'Kesimpulan', 'ρ Spearman', 'Nilai p untuk ρ'], rows,
       fmt={6: '0.00', 8: '0.0000', 10: '0.00', 11: '0.0000'}, lebar={4: 32, 5: 28, 9: 24})

# ---------------------------------------------------------------- Tabel B.6 uji post hoc Dunn
rows = []
for k, (judul, ss, xs, lab) in LOADS.items():
    for a in ARS:
        r = DUNN[(k, a)]
        dilakukan = r['kw'] < 0.05
        for i, j, mi, mj, se, z, p, ph in r['rinci']:
            rows.append([judul, ARS[a], lab[i] + ' dan ' + lab[j], mi, mj, mi - mj, se, z, p, ph, bintang(ph),
                         'ya' if dilakukan else 'tidak (Kruskal-Wallis tidak signifikan)'])
lembar('B.6', 'Uji post hoc Dunn', 'Uji post hoc Dunn untuk semua pasangan tingkat beban (alur B)',
       ['Tabel ini menjadi dasar kurung siku dan tanda bintang pada Gambar 13 dan 14 proposal. z = (rata-rata peringkat 1 - rata-rata peringkat 2) / SE, dengan standard error (SE) yang memakai koreksi nilai sama (ties).',
        'p Holm dikoreksi dalam satu jenis uji dan satu versi. Uji post hoc hanya dipakai bila uji Kruskal-Wallis signifikan; baris lain hanya sebagai informasi.'],
       ['Jenis uji', 'Versi', 'Pasangan', 'Rata-rata peringkat 1', 'Rata-rata peringkat 2', 'Selisih peringkat', 'SE', 'z', 'Nilai p',
        'p Holm', 'Tanda', 'Uji post hoc dipakai'], rows,
       fmt={4: '0.00', 5: '0.00', 6: '0.00', 7: '0.00', 8: '0.00', 9: '0.0000', 10: '0.0000'}, lebar={3: 22, 12: 34})

# ---------------------------------------------------------------- Tabel B.7 paket hilang di radio dan di dalam program
rows = []
for s in SC:
    for a in ARS:
        v = data[(s, a)]
        hil = [r['p_sent'] - r['p_recv'] for r in v]
        rows.append([s, PENGATURAN[s], ARS[a], np.mean([r['p_sent'] for r in v]), np.mean(hil), np.mean([r['radio'] for r in v]),
                     np.mean([r['prog'] for r in v]), np.mean([r['pub'] for r in v]), min(r['prog'] for r in v), max(r['prog'] for r in v),
                     'dibuang queue' if a == 'RTOS' else 'tertimpa di mailbox'])
lembar('B.7', 'Paket hilang radio-program', 'Jumlah paket yang hilang di radio dan di dalam program (rata-rata paket per sesi dari 10 sesi)',
       ['Tabel ini menjadi dasar Gambar 15 proposal. Hilang di radio = paket tidak tertangkap gateway. Hilang di dalam program = paket sudah tertangkap radio, tetapi dibuang queue (FreeRTOS) atau tertimpa di mailbox (Bare Metal).'],
       ['Skenario', 'Pengaturan', 'Versi', 'Paket dikirim per sesi', 'Paket hilang per sesi', 'Hilang di radio', 'Hilang di program',
        'Gagal publish', 'Hilang di program terkecil', 'Hilang di program terbesar', 'Jenis kehilangan di program'], rows,
       fmt={4: '0.0', 5: '0.0', 6: '0.0', 7: '0.0', 8: '0.0'}, lebar={2: 24, 11: 22})

# ---------------------------------------------------------------- Tabel B.8 uji gangguan setiap sesi
rows = []
for lv, (no, nm) in sorted(GANGGUAN.items(), key=lambda x: x[1][0]):
    for a in ARS:
        for r in gang[(lv, a)]:
            gt = r['g']['total']
            rows.append([no, nm, ARS[a], r['sesi'], r['p_sent'], r['p_recv'], r['plr'], gt['hilang_saat'], gt['hilang_luar'], gt['terlambat'],
                         'ya' if r['base'] else 'tidak', r['lat'] if r['base'] else 'tidak dihitung', r['lmax'] / 1000 if r['base'] else 'tidak dihitung',
                         r['g'].get('pulih_rata_s'), r['nama']])
lembar('B.8', 'Uji gangguan per sesi', 'Hasil uji gangguan jaringan pada setiap sesi (skenario 13 sampai 15)',
       ['Tabel ini menjadi dasar Tabel 14 proposal. Latensi hanya dihitung dari sesi yang log Serial gateway-nya lengkap sejak awal sesi.'],
       ['Skenario', 'Gangguan', 'Versi', 'Sesi ke-', 'P_send', 'P_receive', 'PLR total (%)', 'Paket hilang saat gangguan',
        'Paket hilang di luar gangguan', 'Paket terlambat > 1 s', 'Log Serial lengkap sejak awal sesi', 'Latensi rata-rata (ms)', 'Latensi maksimum (s)',
        'Waktu pulih rata-rata (s)', 'Nama sesi'], rows, fmt={7: '0.00', 12: '0.0', 13: '0.00', 14: '0.00'}, lebar={15: 22})

# ---------------------------------------------------------------- Tabel B.9 uji beda pada uji gangguan
rows = []
for metr, nm in [('plr', 'PLR total (%)'), ('hs', 'Paket hilang saat gangguan'), ('late', 'Paket terlambat > 1 s'),
                 ('lmax', 'Latensi maksimum (s)'), ('lat', 'Latensi rata-rata (ms)')]:
    for c in sorted(GC[metr], key=lambda c: GANGGUAN[c['lv']][0]):
        A_, B_ = G[c['lv']]['RTOS'][metr], G[c['lv']]['BM'][metr]
        rows.append([nm, GANGGUAN[c['lv']][1], np.mean(A_), np.std(A_, ddof=1), np.mean(B_), np.std(B_, ddof=1), c['uji'], c['p'], c['ph'],
                     'berbeda signifikan' if c['ph'] < 0.05 else 'tidak berbeda signifikan'])
lembar('B.9', 'Uji beda gangguan', 'Uji beda FreeRTOS dan Bare Metal pada uji gangguan jaringan (alur A)',
       ['Tabel ini menjadi dasar baris p Holm pada Tabel 14 proposal. Koreksi Holm dilakukan untuk 3 tingkat gangguan pada setiap ukuran.',
        'Bila simpangan baku lebih besar daripada rata-rata, nilai setiap sesi dapat dilihat pada Tabel B.8 (penjelasan pada README.txt).'],
       ['Ukuran', 'Gangguan', 'Rata-rata FreeRTOS', 'Simpangan baku FreeRTOS', 'Rata-rata Bare Metal', 'Simpangan baku Bare Metal', 'Uji',
        'Nilai p', 'p Holm', 'Kesimpulan'], rows, fmt={3: '0.00', 4: '0.00', 5: '0.00', 6: '0.00', 8: '0.0000', 9: '0.0000'}, lebar={1: 26, 7: 17, 10: 24})

# ---------------------------------------------------------------- Tabel B.10 uji normalitas
rows = []
for metr, nm in [('plr', 'PLR total'), ('lat', 'Latensi rata-rata')]:
    for c in cmp[metr]:
        for a, w, p in [('FreeRTOS', c['wa'], c['pa']), ('Bare Metal', c['wb'], c['pb'])]:
            rows.append([nm, c['s'], PENGATURAN[c['s']], a, w if w is not None else 'tidak dapat dihitung', p if p is not None else 'tidak dapat dihitung', nkat(p)])
lembar('B.10', 'Uji normalitas', 'Uji normalitas Shapiro-Wilk untuk setiap skenario dan versi',
       ['Tabel ini menjadi dasar subbab 7.9 proposal. p > 0,05 berarti data terdistribusi normal; p ≤ 0,05 berarti data tidak terdistribusi normal. Setiap uji memakai 10 sesi.'],
       ['Ukuran', 'Skenario', 'Pengaturan', 'Versi', 'W Shapiro-Wilk', 'Nilai p', 'Kesimpulan'], rows,
       fmt={5: '0.000', 6: '0.0000'}, lebar={3: 24, 7: 34})

# ---------------------------------------------------------------- Tabel B.11 uji beda FreeRTOS dan Bare Metal
rows = []
for metr, nm, sat in [('plr', 'PLR total', '%'), ('lat', 'Latensi rata-rata', 'ms')]:
    for c in cmp[metr]:
        d = c['ma'] - c['mb']
        kes = 'tidak berbeda signifikan' if c['ph'] >= 0.05 else ('FreeRTOS lebih kecil' if d < 0 else 'Bare Metal lebih kecil')
        rows.append([nm + ' (' + sat + ')', c['s'], c['ma'], c['mb'], d, c['ci'][0], c['ci'][1], c['uji'],
                     c['stat'], c['df'] if c['df'] is not None else 'bukan uji t', c['p'], c['ph'], kes])
lembar('B.11', 'Uji beda', 'Uji beda FreeRTOS dan Bare Metal pada setiap skenario (alur A)',
       ['Tabel ini menjadi dasar Tabel 15 proposal. Selisih = rata-rata FreeRTOS − rata-rata Bare Metal. Selang kepercayaan 95% (SK 95%) dihitung dengan bootstrap 5.000 kali (seed 1).',
        'Koreksi Holm dilakukan untuk 10 skenario pada setiap ukuran. Statistik uji adalah nilai t untuk uji t Welch dan nilai U untuk versi FreeRTOS untuk uji Mann-Whitney U.'],
       ['Ukuran', 'Skenario', 'Rata-rata FreeRTOS', 'Rata-rata Bare Metal', 'Selisih', 'SK 95% batas bawah', 'SK 95% batas atas',
        'Uji', 'Statistik uji', 'Derajat bebas Welch', 'Nilai p', 'p Holm', 'Kesimpulan'], rows,
       fmt={3: '0.00', 4: '0.00', 5: '0.00', 6: '0.00', 7: '0.00', 9: '0.000', 10: '0.0', 11: '0.0000', 12: '0.0000'},
       lebar={1: 22, 8: 17, 13: 24})

# ---------------------------------------------------------------- Tabel B.12 batas atas 95%
rows = []
for s in SC:
    for a in ARS:
        p, l = UB[(s, a, 'plr')], UB[(s, a, 'lat')]
        rows.append([s, ARS[a], p['n'], p['mean'], p['sd'], p['ub'], 'ya' if s in (7, 9, 10) else 'tidak',
                     'tercapai' if p['ub'] < TARGET_PLR else 'tidak tercapai', l['mean'], l['sd'], l['ub'], 'tercapai' if l['ub'] < TARGET_LAT else 'tidak tercapai'])
lembar('B.12', 'Batas atas 95%', 'Batas atas 95% PLR dan latensi rata-rata untuk menilai ketercapaian target spesifikasi nomor 6 dan 7 (alur C)',
       ['Tabel ini menjadi dasar Tabel 17 proposal. Batas atas 95% = rata-rata + t(0,95; n − 1) × simpangan baku / √n, dengan n = 10 sesi dan t(0,95; 9) = 1,833.',
        'Spesifikasi nomor 6 (PLR < 5%) berlaku pada 4 node BLE dengan interval kirim 500 ms atau lebih lambat. Target berlaku untuk FreeRTOS; Bare Metal sebagai pembanding.'],
       ['Skenario', 'Versi', 'Jumlah sesi', 'PLR rata-rata (%)', 'PLR simpangan baku (%)', 'PLR batas atas 95% (%)',
        'Termasuk cakupan spesifikasi 6', 'Target PLR 5%', 'Latensi rata-rata (ms)', 'Latensi simpangan baku (ms)', 'Latensi batas atas 95% (ms)',
        'Target latensi 1 detik'], rows, fmt={4: '0.00', 5: '0.00', 6: '0.00', 9: '0.0', 10: '0.0', 11: '0.0'})

# ---------------------------------------------------------------- daftar isi
ws = wb['Sheet']
ws.title = 'Daftar isi'
ws.sheet_view.showGridLines = False
ws.column_dimensions['A'].width = 10
ws.column_dimensions['B'].width = 110
atas = [('Tabel Hasil Pengujian', Font(name=FONT, bold=True, size=13)),
        ('Rancang Bangun Multi-Protocol IoT Gateway Berbasis ESP32 dengan Implementasi FreeRTOS', Font(name=FONT, bold=True, size=11)),
        ('Seluruh tabel dibuat oleh program olah_data.py (folder Program) dari data mentah pada Lampiran A (folder Data_Mentah). Nomor '
         'tabel sama dengan nomor yang dirujuk pada proposal (Lampiran B), dan contoh perhitungannya dijelaskan pada Lampiran D proposal.',
         Font(name=FONT, italic=True, size=10, color='404040'))]
for r, (t, f) in enumerate(atas, 1):
    c = ws.cell(row=r, column=1, value=t)
    c.font = f
    c.alignment = Alignment(wrap_text=True, vertical='center', horizontal='left')
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
    ws.row_dimensions[r].height = 15 * _baris(t, 125) + 5
for j, h in enumerate(['Tabel', 'Isi tabel'], 1):
    c = ws.cell(row=5, column=j, value=h)
    c.font = Font(name=FONT, bold=True, size=11)
    c.fill = HFILL
    c.border = BOX
    c.alignment = Alignment(vertical='center', horizontal='center')
ws.row_dimensions[5].height = 22
for i, n in enumerate([n for n in wb.sheetnames if n != 'Daftar isi'], 6):
    a1 = ws.cell(row=i, column=1, value=n.split(' ')[0])
    b1 = ws.cell(row=i, column=2, value=wb[n]['A1'].value.split('. ', 1)[1])
    a1.font = Font(name=FONT, bold=True, size=11)
    b1.font = Font(name=FONT, size=11)
    a1.alignment = Alignment(vertical='center', horizontal='center')
    b1.alignment = Alignment(wrap_text=True, vertical='center', horizontal='left')
    a1.border = b1.border = BOX
    ws.row_dimensions[i].height = 21
ws.page_setup.orientation = 'landscape'
ws.page_setup.paperSize = ws.PAPERSIZE_A4
ws.page_setup.fitToWidth = 1
ws.page_setup.fitToHeight = 0
ws.sheet_properties.pageSetUpPr.fitToPage = True
wb.save(XLSX)
print('tabel hasil ditulis:', XLSX)
