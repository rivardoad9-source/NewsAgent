# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status Proyek

**Pra-implementasi.** Direktori ini hanya berisi satu file — `macroai_agent_prd.md` — tanpa source code, tanpa dependency manifest, tanpa test, dan bukan git repository. Belum ada perintah build, lint, maupun test; semuanya baru ditetapkan bersamaan dengan scaffolding pertama, bukan diasumsikan lebih dulu.

Baca `macroai_agent_prd.md` sebelum mengerjakan apa pun yang substansial. PRD itu satu-satunya spesifikasi yang ada, dan seluruh isi dokumen ini adalah ringkasannya — bukan penggantinya.

File spesifikasi lain yang sudah ada:
- `prompts/presenter_system_prompt.md` — **spec aktif (v4)** untuk lapisan LLM, tiga mode: Pre-Event Playbook (Format 1), Post-Event Signal Report (Format 2), dan Text Parser untuk FOMC statement/pidato (Format 3). Seluruh angka masuk sebagai slot input dari Rule Engine dan backtest engine; LLM dilarang menentukan Final Bias atau mengarang statistik. Ini yang menjaga NFR *Objectivity* tetap terpenuhi.
- `prompts/analyst_system_prompt.md` — **superseded**, jangan dipakai. Versi awal yang menempatkan LLM sebagai pengambil keputusan bias.

Konsekuensi desain: seluruh field pada format output presenter adalah *slot data*, bukan hasil penalaran model. Kalau Rule Engine dan backtest engine belum mengisi slot itu, lapisan LLM tidak punya sesuatu untuk disajikan — jadi F3 dan F4 adalah prasyarat, bukan pekerjaan paralel.

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
