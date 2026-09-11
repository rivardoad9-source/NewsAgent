# Review metode analisa @hoteliercrypto — apa yang kita ikutin, apa yang nggak
**Tanggal:** 11 Sep 2026 · **Oleh:** Hermes (operator: Zemiz)

## Kenapa dokumen ini ada

Operator minta: *"pelajari dan amati, kalau emang relevan dan cara dia analisa itu menarik
dan banyak yang bener kita ikutin, yang jelek dan ga relevan ga ush diikutin."*

Jadi ini bukan review orang. Ini keputusan **apa yang diadopsi ke sistem kita** dan
apa yang dibuang, plus angkanya.

## Cara kerjanya (ringkas, dari 63 kolom mingguan)

Dia nulis kolom mingguan gratis "Tren Bitcoin <tanggal>" di Tokocrypto (Sep 2024 –
Nov 2025, 63 kolom, ~491rb karakter). Metodenya:

1. **Kalender data sebagai unit analisa**, bukan candle. Tiap rilis di minggu depan
   dibahas hari-per-hari, jam WITA, lengkap dengan dugaan arah BTC.
2. **Eksplisit nolak TA**: *"saya tak akan berhasil menganalisa panjang pendek body dan
   sumbu candle... tetapi mood Bitcoin hari itu atas referensi data."*
3. **Rantai sebab**: data → DXY / Yen / likuiditas Tiongkok → BTC.
4. **Review mingguan + publish miss**: tiap kolom dibuka dengan penilaian minggu lalu,
   termasuk yang salah (*"minggu ini garis saya salah telak"*).
5. **Angka spesifik** (mis. "angka 217 dirubah naik ke 218K", "Yen 156/dollar").
6. Disclaimer epistemik kuat: *"ini semua hanyalah cocoklogi, saya tidak boleh
   dijadikan acuan, hanya boleh dijadikan hiburan."*

Beban katanya: DXY 495 · inflasi 469 · Tiongkok 440 · Fed 280 · claim 259 · suku bunga
200 · retail 161 · liquidity 151 · CPI 146 · NFP 124 · housing 123 · PPI 112 · JOLTS 101
· yen 45 · BlackRock 28 · PBOC 27 · carry 19 · **yield 7 · bond 5 · ETF 4**.

## Hasil pengukuran (script, bukan opini)

`~/.hermes/scripts/hotelier_weekly.py` — 58 minggu bisa diskor:

| Ukuran | Angka |
| --- | --- |
| Distribusi call-nya | bullish 42 · bearish 13 · netral 6 (bias long 69%) |
| BTC mingguan rata-rata di periode itu | 1.96% (cuma 4 minggu gerak >5%) |
| Minggu flat (±2%) | 36 dari 58 |
| Hit-rate minggu itu sendiri | 22 HIT / 7 MISS / 29 flat → 76% dari 29 minggu decisive |
| **Baseline "selalu bullish"** | **55%** |
| Hit-rate klaim lag-nya (+1m / +2m / +3m) | **54% / 56% / 53%** |

Dia sendiri klaim *"artikelnya selalu kecepetan seminggu, toleransinya lagging 1-2
minggu"*. Diukur: **lag-nya nggak menyelamatkan apa-apa** — 53-56%, alias koin.

## Temuan yang lebih penting: arah itu koin, volatilitas itu bukan

`~/.hermes/scripts/macro_direction_test.py` — tabel arah kita sendiri
(`src/rule_engine.py` INDICATOR_TABLE) diuji ke 74 rilis nyata (CPI 24, PPI 25, NFP 25,
26 bulan) lawan candle BTC 15m Binance, baseline `naive-previous` (actual − previous):

| Indikator | n | avg \|15m\| | avg \|1h\| | avg \|4h\| | max \|1h\| | hit 1h |
| --- | --- | --- | --- | --- | --- | --- |
| CPI | 24 | 0.78% | 0.83% | 1.46% | 2.86% | 8/16 |
| PPI | 25 | 0.62% | 0.77% | 1.25% | 2.37% | 9/20 |
| NFP | 25 | 0.85% | 0.83% | 1.36% | 2.38% | 8/19 |
| **TOTAL** | 74 | | | | | **25/55 = 45%** |

Dan komposisi verdict-nya: **BEARISH 55 · BULLISH 14 · NEUTRAL 5**. Artinya baseline
`naive-previous` bikin verdict kita nyaris selalu BEARISH (delta level hampir selalu
positif), dan arahnya benar **45% — di bawah koin**.

**Dua kesimpulan yang layak dipercaya:**
1. **Volatilitas saat rilis itu nyata dan terukur** (~0.8% dalam 15 menit, sampai ~2.9%
   dalam 1 jam). Klaim dia bahwa kalender data itu penting: **BENAR** — tapi untuk
   *besar gerakan*, bukan arah.
2. **Arah pasca-rilis tanpa consensus itu tidak punya edge.** Ini mengkonfirmasi
   blocker yang sudah ditulis di `CLAUDE.md` (FMP consensus 402, FRED nggak punya
   forecast) dan menjustifikasi kebijakan engine yang **blackout**, bukan trading, di
   window news.

## YANG KITA IKUTI

| # | Yang diadopsi | Status |
| --- | --- | --- |
| 1 | **Loop akuntabilitas mingguan: publish hit-rate sendiri, bukan cuma review naratif.** Dia review tiap minggu tapi TIDAK PERNAH menghitung hit-rate-nya — itu kelemahan terbesarnya, dan kita ambil bentuknya tanpa kelemahan itu. | ✅ deployed: cron `044117c09516` (harian 08:05 WIB) + `~/.hermes/scripts/na_scorecard.py` — skor verdict kita vs gerak BTC nyata, per indikator, silent kalau ga ada rilis baru. Skor sekarang: **2 HIT / 0 MISS** (NFP 4 Sep, PPI 10 Sep) |
| 2 | **Kalender rilis sebagai kerangka harian** (bukan candle) | sudah ada: `news_blackout_write.py` + engine gate + briefing pre-news `09073e521734` |
| 3 | **Angka yang bisa disalahkan + label kejujuran.** Dia pakai "cocoklogi"; kita sudah punya `measured_alignment` + `edge_status` di CLAUDE.md — sekarang ada angkanya: **45% pada 1h untuk baseline naive-previous**. | ✅ dokumen ini + test di atas |
| 4 | **Pisahkan volatilitas dari arah.** Yang bisa diprediksi: besar gerakan (pakai blackout). Yang nggak: arah (jangan dipakai entry). | ✅ ini persis kebijakan engine sekarang |
| 5 | **Skor pihak ketiga sebagai pembanding** | ✅ cron `a3998b3691d6` (Senin 08:15 WIB) — skor kolom dia otomatis |

## YANG TIDAK KITA IKUTI

| # | Yang dibuang | Alasan (terukur) |
| --- | --- | --- |
| 1 | **Prediksi arah naratif sebagai sinyal** | 53-56% di jendela 1-3 minggu; 45% untuk mapping arah kita sendiri. Dia sendiri nulis *"menjadikan saya acuan adalah salah"* |
| 2 | **Toleransi "lagging 1-2 minggu"** | Diuji, nggak nyata (53-56%). Klaim telat = tameng, bukan temuan |
| 3 | **Rantai sebab yang nggak bisa difalsifikasi** (housing starts → utang AS → DXY) | nggak ada cara bikin pernyataan itu salah → nggak bisa dipakai, cuma bisa diceritakan |
| 4 | **Split gratis/bayar: framework gratis, entry berbayar** | yang bisa diverifikasi ≠ yang dipakai buat duit. Kita nggak boleh niru model yang nggak bisa diaudit |
| 5 | **Ceramah moneter jangka panjang** (utang USD, tokenisasi, jangan resign) | bikin semua call kelihatan benar jangka panjang, nggak memberi timing apa pun |
| 6 | **Nyebut arah tanpa angka akurasi** | berlawanan langsung dengan NFR Objectivity di PRD kita |

## Adopsi bersyarat (butuh keputusan operator / task Claude)

1. **Tambah USDJPY (carry) + likuiditas Tiongkok (M1/PBOC) sebagai KONTEKS, bukan verdict.**
   Dua-duanya lensa yang dia pakai dan TIDAK kita punya (kita cuma DXY + 10Y). Aturan:
   masuk ke briefing sebagai observasi + ikut di-log di `live_events.jsonl`, TIDAK boleh
   mengubah verdict sampai terukur punya edge.
2. **`live_events.jsonl` baru punya 2 entri.** Loop skor ini butuh ~6-12 bulan rilis
   (CPI/PPI/NFP/FOMC ≈ 40-50/tahun) sebelum hit-rate-nya boleh dipakai buat apa pun
   selain laporan. Sampai itu: **jangan pakai angka ini buat naikin/nurunin gate.**
3. **Kalau mau ngejar arah beneran**, satu-satunya jalur yang jujur adalah mendapat
   consensus asli (bukan naive-previous). Sekarang blocker-nya biaya/akses provider.

## Catatan reproduksi

```bash
python3 ~/.hermes/scripts/hotelier_weekly.py      # skor kolom dia (silent kalau ga ada baru)
python3 ~/.hermes/scripts/na_scorecard.py         # skor verdict kita (silent kalau ga ada baru)
python3 ~/.hermes/scripts/macro_direction_test.py # uji tabel arah kita, 74 rilis, ~2 menit
```
⚠️ Dua hal yang bikin script ini awalnya gagal: (1) `api.binance.vision` kosong dari host
ini — pakai **`data-api.binance.vision`**; (2) Python urllib **hang** ke
`api.stlouisfed.org` (curl cuma 0.3 s) — FRED diakses lewat `curl` subprocess. Bukan
masalah IPv6 (nama resolve A-only, sudah diuji).
