# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status Proyek

Implementasi berjalan. Mesin inti sudah ada dan terverifikasi terhadap API sungguhan;
belum ada scheduler (hermes yang akan menanganinya) dan belum ada test suite.

Baca `macroai_agent_prd.md` untuk spesifikasi asli. Perlu diketahui: beberapa asumsi
PRD sudah terbukti tidak berlaku, dan yang berlaku adalah temuan di bawah.

### Perintah

```
streamlit run dashboard.py              # dashboard terminal (port 8501)
python scripts/build_calibration.py     # bangun ulang data/calibration.json dari backtest
python scripts/find_chat_id.py          # cari TELEGRAM_CHAT_ID
```

`.env` wajib diisi (lihat `.env.example`). **`load_dotenv()` hanya jalan sekali saat
import**, jadi setiap perubahan `.env` menuntut restart Streamlit — ini pernah membuat
FRED terbaca `NO KEY` padahal key-nya sudah ada.

### Peta modul

```
src/rule_engine.py     verdict deterministik + INDICATOR_TABLE (sumber kebenaran arah)
src/thesis.py          tesis dua horizon: pendek = surprise, panjang = tren FRED
src/action.py          BUY / SELL / HOLD / PENDING per timeframe 15m, 1h, 4h
src/backtest.py        backtest nyata: tanggal rilis FRED + candle 15m Binance
src/telegram_bot.py    notifikasi, semua fungsi mengembalikan hasil, tidak melempar
src/providers/         fmp, fred, binance
dashboard.py           penyaji; TIDAK punya logika verdict sendiri
data/calibration.json  statistik terukur yang dibaca action.py
```

### Temuan yang mengubah rencana PRD

**Consensus tidak tersedia di mana pun.** `economic-calendar` FMP mengembalikan HTTP 402
(di luar paket), dan FRED tidak menerbitkan forecast sama sekali. Tanpa consensus,
Surprise Delta tidak dapat dihitung dari data live. Ini blocker utama Feature 1 dan 4.

**Rantai fallback PRD keliru.** PRD menetapkan FMP -> TradingEconomics -> FRED untuk
kalender. FRED bukan fallback yang sah untuk consensus karena memang tidak memilikinya.
Untuk angka *aktual*, FRED justru sumber terbaik: segar dan dalam puluhan tahun,
sementara `economic-indicators` FMP tertinggal ~276 hari.

**Intraday hanya dari Binance.** Seluruh endpoint intraday FMP terkunci (402).
`data-api.binance.vision` menyediakan candle 15m sejak 2019 tanpa API key.
Catatan: `api.binance.com` tidak dapat dijangkau dari mesin ini.

**Backtest 2019-2026 tidak menemukan edge arah.** Dari 24 kombinasi indikator x aset x
timeframe, tidak satu pun berbeda signifikan dari 50% (alignment 45,8%-53,9%, semua
|z| < 1,0). Yang terukur andal adalah *besar* pergerakan, bukan arahnya. Angka ini
diukur terhadap forecast naif, bukan consensus pasar — satu-satunya variabel yang
belum pernah diuji, dan berpotensi mengubah seluruh kesimpulan.

Karena itu setiap sinyal BUY/SELL WAJIB membawa `measured_alignment` dan `edge_status`.
Jangan pernah menampilkan arah tanpa angka itu.

## Apa yang Sedang Dibangun

MacroAI Agent: pipeline otomatis yang memantau rilis data makro AS berdampak tinggi (FOMC, CPI, PPI, NFP, GDP), mengkuantifikasi seberapa jauh rilis menyimpang dari consensus, lalu mengeluarkan bias posisi objektif — **BULLISH / BEARISH / NEUTRAL** — untuk aset crypto dan tech equities. Playbook pre-event di T-24h dan T-1h, sinyal post-event di bawah 60 detik.

## Arsitektur yang Direncanakan

Empat modul yang membentuk satu pipeline. Di PRD keempatnya dijelaskan terpisah (Feature 1–4), padahal hanya masuk akal jika dibaca sebagai satu rantai:

```
Calendar/Scheduler ──> Pre-Event Playbook (T-24h, T-1h) ──┐
   (F1: apa yang rilis,           (F2: peta skenario)      │
    dan kapan)                                             ├──> Pipeline notifikasi
                                                           │     (Telegram/Discord/Calendar)
Feed rilis ────> Surprise Delta ──> Signal Engine ─────────┘
                 (F3: Δ + profil          (F4: verdict <60d)
                  volatilitas historis)
                        ▲
                  Kalibrasi backtest
                  (5–10 thn, menetapkan threshold)
```

Backtesting engine (F3) bukan sekadar fitur pelaporan — modul inilah yang **mengkalibrasi threshold** yang nantinya dibandingkan oleh signal engine live (F4). Mengubah metodologi backtest berarti mengubah perilaku sinyal live; perlakukan keduanya sebagai komponen yang terkopel.

## Aturan Domain yang Membatasi Implementasi

Bagian-bagian berikut mudah salah diimplementasikan, dan mahal konsekuensinya kalau salah:

- **Surprise Delta adalah `Actual − Consensus`, tetapi tandanya tidak bisa dipetakan ke sinyal secara global.** Pemetaannya berbeda per indikator dan bisa terbalik: untuk CPI/PPI, Δ *negatif* (inflasi mendingin) justru BULLISH bagi risk assets. Jangan pernah menulis satu aturan tanda→sinyal yang dipakai bersama; buat tabel interpretasi per indikator.
- **NEUTRAL adalah fail-safe, bukan keranjang sisa.** Status ini harus dikeluarkan secara aktif setiap kali metrik saling bertentangan (contoh di PRD: NFP kuat tetapi angka pengangguran naik) atau divergensi multi-metrik melewati batas aman. Ambiguitas berujung ke NEUTRAL, tidak pernah ke sinyal directional yang lemah.
- **Sinyal wajib 100% berbasis aturan dan deterministik.** Keterlibatan LLM dibatasi hanya untuk mem-parsing teks *unstructured* — FOMC statement dan pidato Fed Chair. LLM tidak boleh menghasilkan atau menyetel verdict BULLISH/BEARISH/NEUTRAL; itu melanggar NFR objectivity.
- **Konfirmasi lintas aset adalah bagian dari sinyal, bukan pelengkap.** DXY dan U.S. 10Y yields bergerak berlawanan arah terhadap panggilan risk-on. Verdict directional tanpa konfirmasi tersebut berarti belum tervalidasi.
- **15 menit pertama setelah rilis adalah no-entry buffer.** Mitigasi whipsaw: sistem boleh langsung mempublikasikan bias, tetapi tidak boleh menyarankan entri trend-following di dalam jendela itu. Perhatikan bahwa jendela ini berimpit dengan interval pengukuran backtest `T+15m` — jam yang sama, tujuan berbeda; jangan digabung jadi satu konsep.
- **Budget latency adalah < 60 detik end-to-end**, dihitung sejak rilis muncul di feed utama sampai notifikasi terkirim — plafon itu mencakup fetch, parsing, perhitungan delta, lookup threshold, dan pengiriman sekaligus.
- **Urutan fallback provider sudah ditetapkan: FMP → TradingEconomics → FRED.** Routing multi-sumber adalah mitigasi untuk risiko berperingkat tertinggi di PRD; implementasi satu provider saja tidak dapat diterima, bahkan sebagai versi pertama.
- **Target uptime (99,9%) hanya berlaku untuk jendela waktu rilis terjadwal**, bukan operasi terus-menerus. Ini mengubah sizing infrastruktur — yang dibutuhkan adalah ketersediaan terjamin pada jendela yang sudah diketahui, bukan HA always-on.

### Penanganan Waktu

Rilis data makro AS dijadwalkan dalam zona U.S. Eastern yang mengikuti DST, sementara contoh notifikasi di PRD ditulis dalam WIB (UTC+7) yang tidak mengenal DST. Contoh `19:30 WIB` untuk US CPI setara 08:30 EDT — pada periode EST, rilis yang sama jatuh pukul 20:30 WIB. Simpan dan jadwalkan dalam UTC, konversi hanya untuk tampilan, dan turunkan waktu rilis dari jadwal ET, bukan dari offset WIB yang dipatok tetap.

## Stack yang Direncanakan (sesuai PRD — belum ada yang terpasang)

| Lapisan | Pilihan |
| :--- | :--- |
| Core engine | Python 3.11+, Pandas, NumPy, scikit-learn, XGBoost |
| Data makro | FRED, TradingEconomics, Financial Modeling Prep |
| Data harga | YFinance, CoinGecko, Binance WebSocket |
| NLP unstructured | Claude / OpenAI API (khusus parsing statement + pidato) |
| Pengiriman | Telegram Bot API, Discord Webhook, Google Calendar API |

**Catatan interpreter di mesin ini:** `python` mengarah ke 3.12.10, sedangkan `python3` dan `py` mengarah ke 3.14.6. Keduanya memenuhi syarat minimum 3.11+, tapi keduanya instalasi yang berbeda — tentukan secara eksplisit saat membuat virtualenv, karena `pip` terikat ke instalasi 3.12.

## Konvensi

PRD ditulis dalam bahasa Indonesia dengan terminologi finansial berbahasa Inggris, dan template notifikasinya mencampur keduanya. Pertahankan gaya itu untuk dokumentasi baru maupun teks notifikasi yang dilihat pengguna; jangan dialihkan seluruhnya ke bahasa Inggris.
