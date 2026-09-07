# Product Requirement Document (PRD)

## **MacroAI Agent**
*Macro-Economic Intelligence & Automated Signal System for High-Risk Assets*

---

| Attribute | Details |
| :--- | :--- |
| **Document Version** | 1.0 |
| **Target Market** | Crypto & Tech Equities |
| **Created Date** | September 2026 |
| **Author** | Rivardo |

---

## **1. Executive Summary**
**MacroAI Agent** adalah sistem pemantauan, analisis, dan notifikasi makroekonomi terotomatisasi yang dirancang khusus untuk trader di kelas aset berisiko tinggi (*Cryptocurrency* dan *Tech Equities*). Dengan melacak rilis data *high-impact* kalender ekonomi AS, mengkuantifikasi deviasi data historis (*Surprise Delta*), dan memproses data rilis secara instan, sistem ini memberikan saran posisi objektif dan bebas emosi (**Bullish**, **Bearish**, atau **Neutral**) yang dilengkapi dengan panduan skenario pre-event.

---

## **2. Problem Statement & Core Value Proposition**

### **Problem Statement**
Aset berisiko tinggi mengalami volatilitas harga ekstrem selama rilis data makroekonomi utama AS (seperti Keputusan Suku Bunga FOMC, CPI, PPI, NFP). Trader *retail* dan *crypto* sering menghadapi tiga kendala utama:
* **Awareness & Timing Gaps:** Terlambat atau salah memperhitungkan waktu rilis laporan makro penting.
* **Execution Paralysis / Emotional Reactivity:** Kesulitan menerjemahkan data *Actual vs Consensus* secara cepat menjadi *bias* pasar yang objektif, memicu *FOMO* atau *panic trading*.
* **Lack of Historical Context:** Bereaksi pada angka *headline* tanpa memperhitungkan *Surprise Delta* ($\Delta = 	ext{Actual} - 	ext{Consensus}$) serta korelasi lintas aset (DXY dan U.S. 10Y Yields).

### **Core Value Proposition**
MacroAI Agent menjembatani kendala ini dengan menyediakan pipeline intelijen otomatis 24/7 yang menyajikan **T-24h & T-1h Pre-Event Playbooks**, pemodelan volatilitas historis ter-backtest, dan **Post-Event Position Suggestions** dalam waktu di bawah 60 detik.

---

## **3. User Persona**

| Atribut | Deskripsi |
| :--- | :--- |
| **Target User** | Crypto Day/Swing Traders, Tech Stock Traders (Nasdaq / S&P 500), Multi-asset Derivatives Traders. |
| **Tujuan Utama** | Mendapatkan *informational edge*, menyiapkan rencana skenario sebelum event makro, dan menghindari jebakan *whipsaw*. |
| **Pain Points** | *Information overload*, bias emosional, kesulitan analisis korelasi lintas aset secara real-time (BTC vs DXY vs Yields). |

---

## **4. System Architecture & Feature Specifications**

### **Feature 1: Macro Event Calendar & Automated Scheduler Module**
Mengambil dan memperbarui data kalender ekonomi secara otomatis dari penyedia data makro (TradingEconomics, FRED API, FMP API) dengan fokus pada indikator dampak tinggi AS:
* **FOMC Rate Decision, Statement & Dot Plot** (8x / Tahun)
* **CPI (Consumer Price Index) & PPI (Producer Price Index)** (Bulanan)
* **NFP (Non-Farm Payrolls) & Unemployment Rate** (Bulanan)
* **U.S. GDP Growth Rate** (Triwulanan)

---

### **Feature 2: Pre-Event Notification & Scenario Playbook (T-24h & T-1h)**
Mengirimkan peringatan otomatis via Telegram/Discord/Calendar dengan pemetaan skenario:

> 🔔 **[EVENT REMINDER] US CPI YoY Report Tomorrow at 19:30 WIB**
> * **Consensus:** 2.9% \| **Previous:** 3.1%
> 
> 📈 **Bullish Scenario (Crypto/Stocks):** Actual < 2.9% $ightarrow$ Inflasi dingin $ightarrow$ DXY melemah, ekspansi Risk-On.
> 📉 **Bearish Scenario (Crypto/Stocks):** Actual > 3.0% $ightarrow$ Inflasi tinggi $ightarrow$ Ekspektasi suku bunga Hawkish.
> ➖ **Neutral Scenario:** Actual = 2.9% $\pm$ 0.05% $ightarrow$ Sesuai ekspektasi pasar.

---

### **Feature 3: Historical Backtesting & Surprise Delta Engine**
Mengevaluasi profil reaksi pasar historis selama 5 hingga 10 tahun untuk mengkalibrasi ambang batas volatilitas:

$$	ext{Surprise Delta } (\Delta) = 	ext{Actual} - 	ext{Consensus}$$

* **Metrik yang Diukur:** Persentase perubahan harga pada BTC/USD, ETH/USD, S&P 500, dan Nasdaq di interval `T+15m`, `T+1h`, dan `T+24h` pasca-rilis.
* **Validasi Lintas Aset:** Verifikasi arah pergerakan secara *real-time* pada DXY (U.S. Dollar Index) dan U.S. 10Y Treasury Yields.

---

### **Feature 4: Post-Event Instant Signal Engine (< 60 Detik)**

| Signal Output | Trigger Conditions | Market Interpretation & Execution Bias |
| :--- | :--- | :--- |
| **BULLISH** | Kejutan makro mendukung pelonggaran moneter (misal: CPI/PPI < Consensus, Rate Cuts). | Influx likuiditas positif diperkirakan untuk BTC, ETH, dan Growth Stocks. Bias ekspansi likuiditas. |
| **BEARISH** | Kejutan makro mengindikasikan inflasi persisten / kebijakan ketat (misal: CPI/PPI > Consensus). | Tekanan Hawkish, DXY melonjak, kontraksi Risk-Off. Bias risiko penurunan. |
| **NEUTRAL** | Actual sesuai Consensus dalam batas ambang OR sinyal data bertentangan (misal: NFP tinggi tapi Pengangguran naik). | Potensi pasar *whipsaw / fake-out*. Lingkungan risiko tinggi; disarankan *stay cash* / tunggu konsolidasi. |

---

## **5. Tech Stack & Infrastructure Requirements**

| Komponen | Teknologi |
| :--- | :--- |
| **Macro Data Feeds** | FRED API, TradingEconomics API, Financial Modeling Prep (FMP) |
| **Market Price Feeds** | Yahoo Finance API (YFinance), CoinGecko API, Binance WebSocket API |
| **Core Engine & Analytics** | Python 3.11+, Pandas, NumPy, Scikit-learn, XGBoost |
| **NLP Unstructured Analysis** | Claude / OpenAI API (Parsing FOMC Statements & Fed Chair Speeches) |
| **Notification Pipeline** | Telegram Bot API, Discord Webhook, Google Calendar API |

---

## **6. Non-Functional Requirements (NFR)**
* **Latency:** Eksekusi sinyal dan notifikasi harus selesai dalam waktu `< 60 detik` sejak data dirilis pada *feed* utama.
* **Reliability & Uptime:** `99.9% uptime` selama jendela waktu rilis data ekonomi yang dijadwalkan.
* **Objectivity:** Matriks penilaian 100% berbasis aturan dan data. Tanpa bias opini subjektif.

---

## **7. Risk Assessment & Mitigation Plan**

| Risiko yang Teridentifikasi | Tingkat Risiko | Strategi Mitigasi |
| :--- | :---: | :--- |
| **Data Provider API Latency** | **HIGH** | Implementasi *fallback routing* API multi-sumber (misal: FMP $ightarrow$ TradingEconomics $ightarrow$ FRED). |
| **Market Whipsaw / Fake-Outs** | **HIGH** | Menerapkan peringatan *volatility buffer* 15 menit pertama sebelum menyarankan entri ikuti tren. |
| **Conflicting Macro Metrics** | **MEDIUM** | Secara otomatis menetapkan klasifikasi **NEUTRAL** ketika divergensi multi-metrik melebihi batas aman. |
