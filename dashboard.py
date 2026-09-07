"""
MacroAI Agent - Terminal
========================

Jalankan dengan:  streamlit run dashboard.py

Seluruh Final Bias di halaman ini dihitung oleh `src/rule_engine.py`. Dashboard
hanya menyajikan - tidak ada satu pun verdict yang ditentukan di file ini.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.rule_engine import (  # noqa: E402
    INDICATOR_TABLE,
    NO_VERDICT_DISPLAY,
    VOLATILITY_BUFFER_MINUTES,
    calculate_surprise_delta,
    get_playbook,
    get_verdict,
)
from src import calendar as macro_calendar  # noqa: E402
from src import telegram_bot  # noqa: E402
from src.providers import fmp  # noqa: E402
from src.action import TIMEFRAMES, build_action_plan, load_calibration  # noqa: E402
from src.backtest import run_intraday_backtest  # noqa: E402
from src.thesis import build_long_term, build_short_term  # noqa: E402

WIB = ZoneInfo("Asia/Jakarta")
ET = ZoneInfo("America/New_York")

# --- Palet terminal ---------------------------------------------------------
# Fosfor amber di atas hitam. Bull/bear diverifikasi terhadap simulasi buta warna
# (deuteranopia dE 11.9 pada pasangan terdekat, yaitu amber vs bear). Hijau/merah
# konvensional hanya mencapai dE 6.0 dan tidak terbedakan oleh pembaca deutan.
GROUND = "#0A0A0B"
PANEL = "#101013"
RULE = "#26262B"
TEXT = "#E8E4DA"
DIM = "#8A857A"
AMBER = "#FFA92B"
CYAN = "#4FC3E8"
BULL = "#2EE6A0"
BEAR = "#FF5A4A"
NEUTRAL_C = "#8A857A"
STALE = "#B57BFF"

VERDICT_COLORS = {"BULLISH": BULL, "BEARISH": BEAR, "NEUTRAL": NEUTRAL_C}
GLYPH = {"BULLISH": "▲", "BEARISH": "▼", "NEUTRAL": "■"}

st.set_page_config(page_title="MacroAI Terminal", page_icon="■", layout="wide")


# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

st.markdown(
    f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600;700&display=swap');

.stApp, body {{
  background:{GROUND};
  color:{TEXT};
  font-family:'IBM Plex Mono',ui-monospace,Consolas,monospace;
  font-variant-numeric:tabular-nums;
}}
.block-container {{ padding:3.6rem 1.1rem 3rem; max-width:1600px; }}
header[data-testid="stHeader"] {{ background:{GROUND}; height:2.2rem; }}
[data-testid="stToolbarActions"] {{ display:none; }}
#MainMenu, footer {{ visibility:hidden; }}

h1,h2,h3,h4,h5 {{
  font-family:'IBM Plex Mono',monospace !important;
  color:{TEXT} !important; font-weight:600 !important; letter-spacing:.02em;
}}
p, span, div, label, li {{ font-family:'IBM Plex Mono',monospace; }}

/* --- Command bar ------------------------------------------------------- */
.cmdbar {{
  display:flex; align-items:baseline; gap:14px; flex-wrap:wrap;
  border-bottom:2px solid {AMBER}; padding:0 0 6px;
}}
.cmd-name {{ color:{AMBER}; font-weight:700; font-size:1.05rem; letter-spacing:.14em; }}
.cmd-sub  {{ color:{DIM}; font-size:.68rem; letter-spacing:.10em; }}
.cmd-clock {{ margin-left:auto; color:{DIM}; font-size:.68rem; letter-spacing:.06em;
              white-space:nowrap; }}
.cmd-clock b {{ color:{TEXT}; font-weight:500; }}

/* --- Ticker strip ------------------------------------------------------- */
.tick {{
  display:flex; gap:0; overflow-x:auto; border-bottom:1px solid {RULE};
  margin-bottom:14px; -webkit-overflow-scrolling:touch;
}}
.tick::-webkit-scrollbar {{ height:3px; }}
.tick::-webkit-scrollbar-thumb {{ background:{RULE}; }}
.tick-i {{
  flex:0 0 auto; padding:7px 16px 7px 0; margin-right:16px;
  border-right:1px solid {RULE}; white-space:nowrap;
}}
.tick-i:last-child {{ border-right:none; margin-right:0; }}
.tick-k {{ color:{DIM}; font-size:.6rem; letter-spacing:.14em; display:block; }}
.tick-v {{ color:{TEXT}; font-size:.9rem; font-weight:600; }}
.tick-d {{ font-size:.68rem; margin-left:5px; }}

/* --- Panel header (inverse video, ala terminal) ------------------------- */
.sec {{
  display:flex; align-items:center; gap:10px; background:{RULE};
  padding:3px 9px; margin:18px 0 9px; border-left:3px solid {AMBER};
}}
.sec-code {{ color:{AMBER}; font-size:.62rem; font-weight:700; letter-spacing:.14em; }}
.sec-title {{ color:{TEXT}; font-size:.72rem; font-weight:600; letter-spacing:.14em;
              text-transform:uppercase; }}
.sec-meta {{ margin-left:auto; color:{DIM}; font-size:.62rem; letter-spacing:.08em;
             white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}

/* --- Tile (sudut tajam, tanpa bayangan) --------------------------------- */
.tile {{ background:{PANEL}; border:1px solid {RULE}; padding:10px 12px; height:100%; }}
.tile-k {{ color:{DIM}; font-size:.6rem; letter-spacing:.14em; text-transform:uppercase; }}
.tile-v {{ color:{TEXT}; font-size:1.32rem; font-weight:600; margin-top:3px;
           line-height:1.15; word-break:break-word; }}
.tile-f {{ color:{DIM}; font-size:.64rem; margin-top:4px; line-height:1.45; }}

/* --- Verdict ------------------------------------------------------------ */
.vd {{ border:1px solid; padding:13px 15px; background:{PANEL}; }}
.vd-k {{ font-size:.6rem; letter-spacing:.16em; text-transform:uppercase; opacity:.8; }}
.vd-v {{ font-size:1.85rem; font-weight:700; line-height:1.1; margin:3px 0 7px;
         word-break:break-word; }}
.vd-w {{ font-size:.72rem; line-height:1.55; color:{TEXT}; opacity:.9; }}
.vd-c {{ font-size:.62rem; color:{DIM}; margin-top:7px; letter-spacing:.06em; }}
/* Engine gagal: arsir diagonal, sengaja tidak menyerupai verdict mana pun. */
.vd-stale {{
  border-style:dashed; border-color:{STALE}; color:{STALE};
  background:repeating-linear-gradient(45deg,
    rgba(181,123,255,.09) 0 9px, rgba(181,123,255,.02) 9px 18px);
}}

/* --- Trigger ------------------------------------------------------------ */
.trg {{ background:{PANEL}; border:1px solid {RULE}; border-left:3px solid; padding:9px 11px;
        height:100%; }}
.trg-k {{ font-size:.6rem; letter-spacing:.14em; text-transform:uppercase; font-weight:700; }}
.trg-c {{ color:{TEXT}; font-size:.76rem; margin:5px 0 4px; line-height:1.45;
          word-break:break-word; }}
.trg-r {{ color:{DIM}; font-size:.62rem; line-height:1.45; }}

/* --- Notice ------------------------------------------------------------- */
.note {{ border:1px solid {RULE}; border-left:3px solid {CYAN}; background:{PANEL};
         padding:9px 12px; font-size:.7rem; line-height:1.6; margin-bottom:12px; }}
.note-w {{ border-left-color:{AMBER}; }}
.note-e {{ border-left-color:{BEAR}; }}

/* --- LED ---------------------------------------------------------------- */
.led {{ display:flex; justify-content:space-between; align-items:center; gap:8px;
        padding:5px 0; border-bottom:1px solid {RULE}; font-size:.68rem; }}
.led-s {{ font-size:.58rem; letter-spacing:.10em; color:{DIM}; white-space:nowrap; }}

/* --- Streamlit widget overrides ---------------------------------------- */
.stTabs [data-baseweb="tab-list"] {{ gap:0; border-bottom:1px solid {RULE}; }}
.stTabs [data-baseweb="tab"] {{
  color:{DIM}; font-size:.68rem; letter-spacing:.13em; text-transform:uppercase;
  border-radius:0 !important; padding:7px 15px; font-weight:600;
}}
.stTabs [aria-selected="true"] {{ color:{GROUND} !important; background:{AMBER} !important; }}
.stTabs [data-baseweb="tab-highlight"] {{ display:none; }}

section[data-testid="stSidebar"] {{ background:{PANEL}; border-right:1px solid {RULE}; }}
section[data-testid="stSidebar"] * {{ font-family:'IBM Plex Mono',monospace; }}

div[data-testid="stDataFrame"] {{ border:1px solid {RULE}; }}
.stButton button {{
  background:{RULE}; color:{AMBER}; border:1px solid {AMBER}; border-radius:0;
  font-family:'IBM Plex Mono',monospace; font-size:.66rem; letter-spacing:.13em;
  text-transform:uppercase; font-weight:600;
}}
.stButton button:hover {{ background:{AMBER}; color:{GROUND}; border-color:{AMBER}; }}
div[data-baseweb="select"] > div, .stNumberInput input {{
  background:{PANEL} !important; border-radius:0 !important; border-color:{RULE} !important;
  font-family:'IBM Plex Mono',monospace !important; font-size:.76rem !important;
}}
hr {{ border-color:{RULE}; }}
[data-testid="stCaptionContainer"] {{ color:{DIM} !important; font-size:.63rem !important;
                                      line-height:1.55 !important; }}

/* --- MOBILE ------------------------------------------------------------- */
@media (max-width:820px) {{
  .block-container {{ padding:3.2rem 0.6rem 2rem; }}
  /* Kolom Streamlit ditumpuk penuh. Tanpa ini kolom menyusut sampai tak terbaca
     di layar sempit - tile tiga-kolom jadi pita selebar 90px. */
  [data-testid="stHorizontalBlock"] {{ flex-wrap:wrap !important; gap:7px !important; }}
  [data-testid="stColumn"] {{
    flex:1 1 100% !important; min-width:100% !important; width:100% !important;
  }}
  .cmd-name {{ font-size:.9rem; letter-spacing:.10em; }}
  .cmd-sub {{ font-size:.6rem; }}
  .cmd-clock {{ margin-left:0; width:100%; font-size:.6rem; white-space:normal; }}
  .tile-v {{ font-size:1.12rem; }}
  .vd-v {{ font-size:1.45rem; }}
  .sec-meta {{ display:none; }}
  .stTabs [data-baseweb="tab"] {{ padding:6px 9px; font-size:.6rem; letter-spacing:.07em; }}
  /* Tabel lebar menggulir di dalam wadahnya; halaman tidak pernah geser horizontal. */
  div[data-testid="stDataFrame"] {{ overflow-x:auto; }}
}}
@media (max-width:420px) {{
  .tile-v {{ font-size:1rem; }}
  .vd-v {{ font-size:1.18rem; }}
  .tick-v {{ font-size:.82rem; }}
}}
</style>
""",
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

NOW_UTC = datetime.now(timezone.utc)


@st.cache_data
def load_calendar() -> pd.DataFrame:
    """
    Kalender contoh. Waktu disimpan UTC lalu diturunkan ke ET dan WIB.

    Rilis makro AS dijadwalkan dalam zona Eastern yang mengikuti DST, WIB tidak.
    Offset WIB karena itu bergeser sepanjang tahun - konversi selalu lewat UTC.
    """
    base = NOW_UTC.replace(hour=12, minute=30, second=0, microsecond=0)
    rows = [
        ("CPI", "US CPI YOY", base + timedelta(days=3), 2.9, 3.1, "HIGH"),
        ("PPI", "US PPI YOY", base + timedelta(days=4), 2.4, 2.6, "HIGH"),
        ("FOMC", "FOMC RATE DECISION", base + timedelta(days=9, hours=5, minutes=30), 3.75, 4.00, "HIGH"),
        ("GDP", "US GDP GROWTH Q2 FINAL", base + timedelta(days=17), 2.1, 2.3, "MED"),
        ("NFP", "US NON-FARM PAYROLLS", base + timedelta(days=25), 165.0, 142.0, "HIGH"),
        ("UNEMPLOYMENT", "US UNEMPLOYMENT RATE", base + timedelta(days=25), 4.3, 4.3, "HIGH"),
    ]
    df = pd.DataFrame(
        rows, columns=["event_type", "event_name", "scheduled_utc", "consensus", "previous", "impact"]
    )
    df["scheduled_wib"] = df["scheduled_utc"].apply(lambda d: d.astimezone(WIB))
    df["scheduled_et"] = df["scheduled_utc"].apply(lambda d: d.astimezone(ET))
    df["days_out"] = df["scheduled_utc"].apply(lambda d: (d - NOW_UTC).total_seconds() / 86400)
    return df.sort_values("scheduled_utc").reset_index(drop=True)


@st.cache_data(ttl=1800, show_spinner="Kalender FRED...")
def load_real_releases() -> pd.DataFrame:
    """
    Rilis NYATA dari FRED (bukan simulasi): terakhir per indikator + berikutnya
    yang sudah dikonfirmasi. FRED mengunci jadwal ~2-4 minggu sebelum H-1, jadi
    kolom berikutnya sering kosong - itu normal dan ditampilkan apa adanya.
    """
    last_by = {r["indicator"]: r for r in macro_calendar.last_releases()}
    up_by = {r.indicator: r for r in macro_calendar.upcoming()}

    rows = []
    for ind in macro_calendar.RELEASE_IDS:
        last = last_by.get(ind)
        up = up_by.get(ind)
        rows.append(
            {
                "IND": ind,
                "EVENT": macro_calendar.EVENT_LABEL.get(ind, ind),
                "RILIS TERAKHIR": last["release_date"].strftime("%a %d %b %Y")
                if last and last["release_date"]
                else "—",
                "BERIKUTNYA (WIB)": up.release_utc.astimezone(WIB).strftime("%a %d %b %H:%M")
                if up
                else "belum dikonfirmasi FRED",
                "IMP": macro_calendar.IMPACT.get(ind, ""),
            }
        )
    return pd.DataFrame(rows)


def load_event_log(limit: int = 8) -> pd.DataFrame:
    """Log hasil rilis nyata (data/live_events.jsonl) - feed belajar engine."""
    f = Path(__file__).parent / "data" / "live_events.jsonl"
    if not f.exists():
        return pd.DataFrame()
    rows = []
    for line in f.read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        moves = (rec.get("moves") or {}).get("BTC/USD") or {}
        rows.append(
            {
                "RILIS": rec.get("release_utc", "")[:10],
                "EVENT": rec.get("label", rec.get("indicator", "")),
                "ACTUAL": rec.get("actual"),
                "Δ": rec.get("naive_delta"),
                "VERDICT": rec.get("verdict", ""),
                "BTC 15m %": moves.get("15m"),
                "BTC 4h %": moves.get("4h"),
            }
        )
    return pd.DataFrame(rows)


@st.cache_data(ttl=3600, show_spinner="Menarik backtest nyata...")
def load_real_backtest(indicator: str, asset: str) -> pd.DataFrame:
    """
    Backtest INTRADAY SUNGGUHAN - tanggal rilis FRED, harga candle 15m Binance.

    Menggantikan dataset sintetis yang sebelumnya menempati tab ini. Candle
    Binance sudah ter-cache di data/cache, sehingga pemanggilan berikutnya
    tidak menyentuh jaringan.
    """
    result = run_intraday_backtest(indicator, asset, "2019-01-01", "2026-09-07")
    if not result.ok:
        return pd.DataFrame()
    df = pd.DataFrame(result.rows)
    df["year"] = pd.to_datetime(df["release_date"]).dt.year
    return df


@st.cache_data(ttl=3600)
def calibration() -> dict:
    return load_calibration()


@st.cache_data(ttl=1800, show_spinner=False)
def cached_long_term(indicator: str) -> dict:
    """
    Tesis jangka panjang di-cache per indikator.

    Bagian ini menarik histori FRED, sementara jangka pendek murni kalkulasi
    lokal. Tanpa pemisahan ini, setiap geseran slider Simulated Actual akan
    memicu permintaan jaringan baru.
    """
    view = build_long_term(indicator)
    return {
        "display": view.display,
        "rationale": view.rationale,
        "basis": view.basis,
        "evidence": view.evidence,
    }


@st.cache_data(ttl=60, show_spinner=False)
def live_ticker() -> dict:
    """Harga live dari FMP. Kegagalan mengembalikan None, tidak pernah angka palsu."""
    out: dict = {}
    for label, symbol in (("BTC/USD", "BTCUSD"), ("ETH/USD", "ETHUSD"), ("NASDAQ", "^IXIC")):
        try:
            res = fmp.get_quote(symbol)
            if res.ok and res.data:
                row = res.data[0]
                out[label] = (row.get("price"), row.get("changePercentage"))
            else:
                out[label] = (None, None)
        except Exception:
            out[label] = (None, None)
    try:
        rates = fmp.get_treasury_rates(
            (NOW_UTC - timedelta(days=20)).strftime("%Y-%m-%d"),
            NOW_UTC.strftime("%Y-%m-%d"),
        )
        if rates.ok and rates.data:
            rows = sorted(rates.data, key=lambda r: r.get("date", ""))
            out["US 10Y"] = (rows[-1].get("year10"), None)
        else:
            out["US 10Y"] = (None, None)
    except Exception:
        out["US 10Y"] = (None, None)
    return out


def init_audit_log() -> None:
    if "audit_log" in st.session_state:
        return
    seed = []
    for offset, (etype, actual, consensus) in enumerate(
        [("NFP", 142.0, 165.0), ("CPI", 2.8, 2.9), ("PPI", 2.7, 2.4)]
    ):
        delta = calculate_surprise_delta(actual, consensus)
        result = get_verdict(etype, delta)
        seed.append(
            {
                "timestamp": (NOW_UTC - timedelta(days=6 + offset * 7)).astimezone(WIB).strftime("%Y-%m-%d %H:%M"),
                "event": INDICATOR_TABLE[etype].label.upper(),
                "actual": actual,
                "consensus": consensus,
                "surprise_delta": delta,
                "verdict": result.display_bias,
                "delivery": "SENT",
            }
        )
    failed = get_verdict("CPI", None)
    seed.append(
        {
            "timestamp": (NOW_UTC - timedelta(days=27)).astimezone(WIB).strftime("%Y-%m-%d %H:%M"),
            "event": "US CPI YOY",
            "actual": None,
            "consensus": 3.0,
            "surprise_delta": None,
            "verdict": failed.display_bias,
            "delivery": "HELD",
        }
    )
    st.session_state.audit_log = sorted(seed, key=lambda r: r["timestamp"], reverse=True)


# ---------------------------------------------------------------------------
# Komponen
# ---------------------------------------------------------------------------

def section(code: str, title: str, meta: str = "") -> None:
    st.markdown(
        f'<div class="sec"><span class="sec-code">{code}</span>'
        f'<span class="sec-title">{title}</span>'
        f'<span class="sec-meta">{meta}</span></div>',
        unsafe_allow_html=True,
    )


def tile(key: str, value: str, foot: str = "", color: str = TEXT) -> str:
    return (
        f'<div class="tile"><div class="tile-k">{key}</div>'
        f'<div class="tile-v" style="color:{color}">{value}</div>'
        f'<div class="tile-f">{foot}</div></div>'
    )


def verdict_block(display: str, rationale: str, confirmation: str = "") -> str:
    stale = display == NO_VERDICT_DISPLAY
    color = STALE if stale else VERDICT_COLORS.get(display, DIM)
    cls = "vd vd-stale" if stale else "vd"
    style = "" if stale else f"border-color:{color};color:{color};"
    tail = f'<div class="vd-c">CROSS-ASSET: {confirmation}</div>' if confirmation else ""
    return (
        f'<div class="{cls}" style="{style}">'
        f'<div class="vd-k">Final Bias &middot; Rule Engine</div>'
        f'<div class="vd-v">{GLYPH.get(display, "⊘")} {display}</div>'
        f'<div class="vd-w">{rationale}</div>{tail}</div>'
    )


def style_plot(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        paper_bgcolor=PANEL,
        plot_bgcolor=PANEL,
        font=dict(color=TEXT, family="IBM Plex Mono, monospace", size=11),
        margin=dict(l=8, r=8, t=34, b=8),
        legend=dict(bgcolor="rgba(0,0,0,0)", bordercolor=RULE, borderwidth=1, font=dict(size=10)),
        title=dict(font=dict(size=12, color=DIM)),
    )
    fig.update_xaxes(gridcolor=RULE, zerolinecolor=DIM, linecolor=RULE)
    fig.update_yaxes(gridcolor=RULE, zerolinecolor=DIM, linecolor=RULE)
    return fig


init_audit_log()
calendar = load_calendar()


# ---------------------------------------------------------------------------
# Command bar + ticker
# ---------------------------------------------------------------------------

now_wib = NOW_UTC.astimezone(WIB)
now_et = NOW_UTC.astimezone(ET)
st.markdown(
    f'<div class="cmdbar"><span class="cmd-name">MACROAI</span>'
    f'<span class="cmd-sub">MACRO INTELLIGENCE &middot; CRYPTO / TECH EQUITIES</span>'
    f'<span class="cmd-clock">WIB <b>{now_wib.strftime("%H:%M")}</b> &nbsp;|&nbsp; '
    f'ET <b>{now_et.strftime("%H:%M %Z")}</b> &nbsp;|&nbsp; '
    f'UTC <b>{NOW_UTC.strftime("%H:%M")}</b> &nbsp;|&nbsp; '
    f'{now_wib.strftime("%a %d %b %Y")}</span></div>',
    unsafe_allow_html=True,
)

nxt = calendar.iloc[0]
secs = max(int((nxt["scheduled_utc"] - NOW_UTC).total_seconds()), 0)
countdown = f"{secs // 86400}d {(secs % 86400) // 3600:02d}h {(secs % 3600) // 60:02d}m"

ticks = [
    f'<div class="tick-i"><span class="tick-k">NEXT RELEASE</span>'
    f'<span class="tick-v" style="color:{AMBER}">{countdown}</span>'
    f'<span class="tick-d" style="color:{DIM}">{nxt["event_name"]}</span></div>'
]
for label, (price, change) in live_ticker().items():
    if price is None:
        ticks.append(
            f'<div class="tick-i"><span class="tick-k">{label}</span>'
            f'<span class="tick-v" style="color:{DIM}">&mdash;</span>'
            f'<span class="tick-d" style="color:{DIM}">n/a</span></div>'
        )
    elif change is None:
        ticks.append(
            f'<div class="tick-i"><span class="tick-k">{label}</span>'
            f'<span class="tick-v">{price:,.2f}</span></div>'
        )
    else:
        col = BULL if change >= 0 else BEAR
        arrow = "▲" if change >= 0 else "▼"
        ticks.append(
            f'<div class="tick-i"><span class="tick-k">{label}</span>'
            f'<span class="tick-v">{price:,.2f}</span>'
            f'<span class="tick-d" style="color:{col}">{arrow} {change:+.2f}%</span></div>'
        )
st.markdown(f'<div class="tick">{"".join(ticks)}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

with st.sidebar:
    st.markdown(
        f'<div style="color:{AMBER};font-weight:700;letter-spacing:.14em;font-size:.8rem;'
        f'border-bottom:1px solid {RULE};padding-bottom:6px">SYSTEM</div>',
        unsafe_allow_html=True,
    )
    st.write("")

    for name, up in (
        ("FMP", fmp.is_configured()),
        ("FRED", bool(os.getenv("FRED_API_KEY"))),
        ("TELEGRAM", telegram_bot.is_configured()),
    ):
        col = BULL if up else DIM
        st.markdown(
            f'<div class="led"><span style="color:{col}">● {name}</span>'
            f'<span class="led-s">{"READY" if up else "NO KEY"}</span></div>',
            unsafe_allow_html=True,
        )

    st.write("")
    st.markdown(
        f'<div style="color:{DIM};font-size:.6rem;letter-spacing:.12em;'
        f'border-bottom:1px solid {RULE};padding-bottom:4px">ALERT TEST</div>',
        unsafe_allow_html=True,
    )
    dry = st.checkbox("Dry-run", value=True)
    if st.button("SEND TEST ALERT", width="stretch"):
        res = telegram_bot.send_test_alert(dry_run=dry)
        (st.success if res.ok else st.error)(f"{res.status} — {res.detail}")
        if res.message_preview:
            st.code(res.message_preview, language="html")
        st.session_state.audit_log.insert(
            0,
            {
                "timestamp": datetime.now(WIB).strftime("%Y-%m-%d %H:%M"),
                "event": "TEST ALERT",
                "actual": None,
                "consensus": None,
                "surprise_delta": None,
                "verdict": "—",
                "delivery": res.status,
            },
        )

    st.write("")
    st.caption(
        f"VOL BUFFER {VOLATILITY_BUFFER_MINUTES}M · verdict deterministik, bukan LLM "
        f"· threshold belum terkalibrasi"
    )


tab1, tab2, tab3 = st.tabs(["CALENDAR / SIM", "BACKTEST", "SYSTEM"])


# ---------------------------------------------------------------------------
# TAB 1
# ---------------------------------------------------------------------------

with tab1:
    section("F0", "Real Release Calendar (FRED)", "tanggal publikasi asli - bukan simulasi")
    real = load_real_releases()
    st.dataframe(
        real,
        width="stretch",
        hide_index=True,
    )
    st.caption(
        "Sumber: FRED /release/dates (endpoint singular). FRED mengonfirmasi rilis "
        "~2-4 minggu sebelum H-1 - kolom BERIKUTNYA berisi 'belum dikonfirmasi FRED' "
        "sampai jadwal BLS/BEA dikunci. Telegram hanya mengirim HASIL rilis, jadwal "
        "cukup di dashboard ini."
    )

    event_log = load_event_log()
    if not event_log.empty:
        section("F0b", "Event Log (feed belajar engine)", "data/live_events.jsonl")
        st.dataframe(event_log, width="stretch", hide_index=True)
        st.caption(
            "Tiap rilis yang dikirim ke Telegram otomatis tercatat di sini: actual, "
            "delta naive, verdict, dan reaksi BTC - bahan kalibrasi ulang engine."
        )

    horizon = st.selectbox("HORIZON", [7, 14, 30], index=0, format_func=lambda d: f"{d} HARI")
    window = calendar[calendar["days_out"].between(0, horizon)]

    section("F1", "Sim Calendar (sandbox)", f"{len(window)} event / {horizon} hari — simulasi")
    if window.empty:
        st.markdown(
            '<div class="note">Tidak ada rilis high-impact pada horizon ini.</div>',
            unsafe_allow_html=True,
        )
    else:
        st.dataframe(
            pd.DataFrame(
                {
                    "EVENT": window["event_name"],
                    "WIB": window["scheduled_wib"].dt.strftime("%a %d %b %H:%M"),
                    "ET": window["scheduled_et"].dt.strftime("%H:%M %Z"),
                    "CONS": window["consensus"],
                    "PREV": window["previous"],
                    "IMP": window["impact"],
                    "T-MINUS": window["days_out"].apply(lambda d: f"{d:.1f}d"),
                }
            ),
            width="stretch",
            hide_index=True,
        )
        st.caption(
            "Waktu disimpan UTC, diturunkan ke ET dan WIB. Offset WIB bergeser mengikuti DST "
            "di zona Eastern — jam WIB satu event tidak tetap sepanjang tahun."
        )

    section("F2", "Scenario Simulator", "pre-event playbook")
    st.markdown(
        '<div class="note">Geser <b>SIMULATED ACTUAL</b> untuk melihat verdict yang akan '
        "dikeluarkan Rule Engine. Perhitungannya identik dengan jalur produksi — halaman "
        "ini tidak punya logika verdict sendiri.</div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3 = st.columns([1.2, 1, 1])
    with c1:
        event_key = st.selectbox(
            "INDIKATOR", list(INDICATOR_TABLE.keys()),
            format_func=lambda k: INDICATOR_TABLE[k].label.upper(),
        )
    spec = INDICATOR_TABLE[event_key]
    row = calendar[calendar["event_type"] == event_key]
    default_consensus = float(row.iloc[0]["consensus"]) if not row.empty else 0.0
    with c2:
        consensus = st.number_input(
            f"CONSENSUS ({spec.unit})",
            value=default_consensus,
            step=1.0 if spec.unit == "K" else 0.1,
            format="%.0f" if spec.unit == "K" else "%.2f",
        )
    with c3:
        asset = st.selectbox("TARGET ASSET", ["BTC/USD", "ETH/USD", "NASDAQ (IXIC)"])

    span = 300.0 if spec.unit == "K" else 1.5
    actual = st.slider(
        f"SIMULATED ACTUAL ({spec.unit})",
        min_value=float(consensus - span),
        max_value=float(consensus + span),
        value=float(consensus),
        step=1.0 if spec.unit == "K" else 0.01,
    )

    delta = calculate_surprise_delta(actual, consensus)
    result = get_verdict(event_key, delta)
    dcol = TEXT if delta is None else (BULL if delta < 0 else BEAR if delta > 0 else DIM)

    m1, m2, m3 = st.columns(3)
    m1.markdown(
        tile("SIMULATED ACTUAL", spec.format_level(actual), f"CONS {spec.format_level(consensus)}"),
        unsafe_allow_html=True,
    )
    m2.markdown(
        tile(
            "SURPRISE DELTA",
            spec.format_delta(delta) if delta is not None else "[MISSING]",
            f"BUFFER &plusmn;{spec.format_level(spec.neutral_buffer)}",
            dcol,
        ),
        unsafe_allow_html=True,
    )
    m3.markdown(
        tile(
            "MAPPING",
            spec.shape.value.replace("_", " "),
            spec.note[:78] + ("…" if len(spec.note) > 78 else ""),
        ),
        unsafe_allow_html=True,
    )

    st.write("")
    st.markdown(verdict_block(result.display_bias, result.rationale), unsafe_allow_html=True)

    impact = {
        "BULLISH": f"DXY diperkirakan melemah, US 10Y turun. Likuiditas mengalir ke {asset}.",
        "BEARISH": f"DXY diperkirakan menguat, US 10Y naik. {asset} menghadapi tekanan risk-off.",
        "NEUTRAL": f"Tidak ada edge arah pada {asset}. Risiko whipsaw tinggi.",
    }.get(result.display_bias, "Rule Engine tidak menghasilkan verdict — tidak ada anjuran posisi.")
    st.markdown(f'<div class="tile-f" style="margin-top:9px">{impact}</div>', unsafe_allow_html=True)
    if result.notes:
        st.caption(" · ".join(result.notes))

    # ----- Dual-horizon thesis -----------------------------------------
    section("F5", "Dual-Horizon Thesis", "pendek: surprise · panjang: tren FRED")
    short_view = build_short_term(event_key, actual=actual, consensus=consensus)
    long_view = cached_long_term(event_key)

    th1, th2 = st.columns(2)
    for col, horizon, label, view in (
        (th1, "JANGKA PENDEK", "jam &ndash; hari", {
            "display": short_view.display,
            "rationale": short_view.rationale,
            "basis": short_view.basis,
        }),
        (th2, "JANGKA PANJANG", "minggu &ndash; bulan", long_view),
    ):
        disp = view["display"]
        color = STALE if disp == NO_VERDICT_DISPLAY else VERDICT_COLORS.get(disp, DIM)
        col.markdown(
            f'<div class="trg" style="border-left-color:{color}">'
            f'<div class="trg-k" style="color:{color}">{horizon} &middot; {label}</div>'
            f'<div class="trg-c" style="font-size:1rem;font-weight:700;color:{color}">'
            f'{GLYPH.get(disp, "&#8856;")} {disp}</div>'
            f'<div class="trg-r">{view["rationale"]}</div>'
            f'<div class="trg-r" style="margin-top:6px;opacity:.75">Basis: {view["basis"]}</div>'
            f"</div>",
            unsafe_allow_html=True,
        )
    if short_view.display != long_view["display"] and NO_VERDICT_DISPLAY not in (
        short_view.display, long_view["display"]
    ):
        st.caption(
            "Kedua horizon berbeda arah. Ini normal — satu rilis bisa melawan tren — "
            "tetapi jangan dipakai membenarkan posisi yang ditahan melewati horizonnya."
        )

    # ----- Action plan --------------------------------------------------
    section("F4", "Action Plan", "BUY / SELL / HOLD per timeframe")
    ac1, ac2, ac3 = st.columns([1, 1.4, 1.2])
    with ac1:
        act_asset = st.selectbox("ASET EKSEKUSI", ["BTC/USD", "ETH/USD"], key="act_asset")
    with ac2:
        mins = st.slider("MENIT SEJAK RILIS", 0, 240, 45, key="act_mins")
    with ac3:
        strict = st.checkbox("Strict mode", value=False,
                             help="Turunkan ke HOLD bila tidak ada edge terukur.")

    # Konteks dinamis: rilis lain yang akan datang bisa mengubah aksi. Jendela
    # 4h yang melewati FOMC, misalnya, menjadi PENDING - hasil posisinya akan
    # ditentukan event itu, bukan oleh tesis yang sedang dijalankan.
    upcoming_ctx = [
        {
            "event_type": row["event_type"],
            "event_name": row["event_name"],
            "scheduled_utc": row["scheduled_utc"],
        }
        for _, row in calendar.iterrows()
        if row["scheduled_utc"] > NOW_UTC
    ]
    plan = build_action_plan(
        event_key,
        act_asset,
        result.display_bias,
        minutes_since_release=mins,
        strict_mode=strict,
        missing_fields=result.missing_fields,
        upcoming_events=upcoming_ctx,
        now=NOW_UTC,
    )

    acols = st.columns(3)
    for col, tf in zip(acols, plan.timeframes):
        act = tf.action.value
        color = {"BUY": BULL, "SELL": BEAR, "PENDING": AMBER}.get(act, NEUTRAL_C)
        arrow = {"BUY": "▲", "SELL": "▼", "PENDING": "—"}.get(act, "■")
        exp = f"{tf.expected_move_pct}%" if tf.expected_move_pct is not None else "&mdash;"
        inv = f"{tf.invalidation_pct}%" if tf.invalidation_pct is not None else "&mdash;"
        acc = (
            f"{tf.measured_alignment}% (n={tf.sample_size})"
            if tf.measured_alignment is not None else "belum dikalibrasi"
        )
        acc_color = BULL if tf.edge_status == "EDGE_DETECTED" else STALE
        col.markdown(
            f'<div class="trg" style="border-left-color:{color}">'
            f'<div class="trg-k" style="color:{DIM}">T+{tf.timeframe}</div>'
            f'<div style="color:{color};font-size:1.5rem;font-weight:700;line-height:1.1;'
            f'margin:2px 0 7px">{arrow} {act}</div>'
            f'<div class="trg-r">Pergerakan rata-rata <b style="color:{TEXT}">{exp}</b><br>'
            f'Invalidasi di bawah <b style="color:{TEXT}">{inv}</b><br>'
            f'Akurasi arah <b style="color:{acc_color}">{acc}</b></div>'
            + (
                f'<div class="trg-r" style="margin-top:7px;color:{AMBER}">{tf.reason}</div>'
                if act == "PENDING" else ""
            )
            + f"</div>",
            unsafe_allow_html=True,
        )

    for warning in plan.global_warnings:
        st.caption(f"! {warning}")

    section("F2", "Trigger Map", spec.shape.value)
    pb = get_playbook(event_key, consensus)
    p1, p2, p3 = st.columns(3)
    for col, key, color in ((p1, "bullish", BULL), (p2, "bearish", BEAR), (p3, "neutral", NEUTRAL_C)):
        col.markdown(
            f'<div class="trg" style="border-left-color:{color}">'
            f'<div class="trg-k" style="color:{color}">'
            f'{GLYPH[key.upper()]} {key.upper()} TRIGGER</div>'
            f'<div class="trg-c">{pb.get(key + "_condition", "[MISSING]")}</div>'
            f'<div class="trg-r">{pb.get(key + "_rationale", "")}</div></div>',
            unsafe_allow_html=True,
        )
    st.caption(
        "Kondisi dibaca dari indicator interpretation table di Rule Engine. Operator dan arahnya "
        "tidak boleh disusun lapisan penyaji — pemetaan berbeda per indikator dan tidak "
        "selalu monotonik."
    )


# ---------------------------------------------------------------------------
# TAB 2
# ---------------------------------------------------------------------------

with tab2:
    st.markdown(
        '<div class="note"><b>DATA NYATA.</b> Tanggal rilis dari FRED (tanggal publikasi '
        "sesungguhnya, bukan periode acuan), harga dari candle 15m Binance. "
        "<b>Dua batasan metodologis:</b> surprise diukur terhadap forecast naif "
        "(nilai rilis sebelumnya), BUKAN consensus pasar — consensus tidak tersedia di "
        "sumber mana pun yang kita punya. Karena itu angka di bawah bisa berbeda jauh dari "
        "edge terhadap ekspektasi pasar sesungguhnya.</div>",
        unsafe_allow_html=True,
    )

    b1, b2, b3 = st.columns([1, 1, 1])
    with b1:
        bt_ind = st.selectbox("INDIKATOR", ["CPI", "PPI", "NFP", "UNEMPLOYMENT"], key="bt_ind")
    with b2:
        bt_asset = st.selectbox("ASET", ["BTC/USD", "ETH/USD"], key="bt_asset")
    with b3:
        bt_tf = st.selectbox("HORIZON", TIMEFRAMES, key="bt_tf")

    df = load_real_backtest(bt_ind, bt_asset)
    cal = calibration().get("pairs", {}).get(f"{bt_ind}|{bt_asset}", {})
    stats = cal.get("horizons", {}).get(bt_tf, {})

    section("F3", "Backtest Analytics", f"{len(df)} rilis · data nyata")

    if df.empty:
        st.markdown(
            '<div class="note note-e">Backtest tidak menghasilkan baris. Cek koneksi '
            "FRED/Binance di tab SYSTEM.</div>",
            unsafe_allow_html=True,
        )
    else:
        rcol, acol = f"ret_{bt_tf}", f"aligned_{bt_tf}"
        rate = stats.get("alignment_rate")
        n_dir = stats.get("n_directional")
        edge = stats.get("edge_status", "UNCALIBRATED")
        z = stats.get("z_score")

        edge_color = BULL if edge == "EDGE_DETECTED" else STALE
        k1, k2, k3 = st.columns(3)
        k1.markdown(
            tile("AKURASI ARAH", f"{rate}%" if rate is not None else "—",
                 (f"{n_dir} sinyal berarah · z={z}" if n_dir is not None
                  else "belum dikalibrasi"), edge_color),
            unsafe_allow_html=True,
        )
        k2.markdown(
            tile("PERGERAKAN RATA-RATA",
                 (f"{stats['avg_abs_move_pct']}%" if stats.get("avg_abs_move_pct") is not None
                  else "—"),
                 f"|return| pada {bt_tf} · {len(df)} rilis"),
            unsafe_allow_html=True,
        )
        k3.markdown(
            tile("STATUS EDGE", edge.replace("_", " "),
                 "tidak berbeda signifikan dari 50%" if edge == "NO_EDGE_DETECTED"
                 else "berbeda signifikan dari 50%", edge_color),
            unsafe_allow_html=True,
        )

        if edge == "NO_EDGE_DETECTED":
            st.markdown(
                f'<div class="note note-w"><b>Tidak ada edge arah yang terdeteksi.</b> '
                f"Akurasi {rate}% dari {n_dir} rilis, z={z} — di dalam rentang kebetulan "
                f"(butuh |z| &gt; 1,96). Kolom PERGERAKAN RATA-RATA tetap bermakna: sistem "
                f"ini mengukur <i>seberapa besar</i> pasar bergerak dengan andal, meski "
                f"belum bisa mengatakan <i>ke arah mana</i>.</div>",
                unsafe_allow_html=True,
            )

        plot_df = df[df[rcol].notna()].copy()
        plot_df["hasil"] = plot_df[acol].map({True: "SEJALAN", False: "MELESET"})
        plot_df["hasil"] = plot_df["hasil"].fillna("NEUTRAL")

        fig = px.scatter(
            plot_df, x="surprise", y=rcol, color="hasil",
            color_discrete_map={"SEJALAN": BULL, "MELESET": BEAR, "NEUTRAL": NEUTRAL_C},
            hover_data={"release_date": True, "actual": True, "naive_forecast": True,
                        "verdict": True},
            labels={"surprise": "SURPRISE (ACTUAL − FORECAST NAIF)",
                    rcol: f"{bt_asset} T+{bt_tf} RETURN (%)", "hasil": "HASIL"},
            title=f"{bt_ind} — SURPRISE vs {bt_asset} T+{bt_tf} RETURN",
        )
        fig.update_traces(marker=dict(size=7, opacity=0.78, line=dict(width=1, color=PANEL)))
        fig.add_hline(y=0, line_color=DIM, line_width=1, opacity=0.5)
        fig.add_vline(x=0, line_color=DIM, line_width=1, opacity=0.5)
        st.plotly_chart(style_plot(fig), width="stretch")
        st.caption(
            "Hijau = arah verdict sejalan dengan return, merah = meleset, abu = NEUTRAL "
            "(tidak dinilai). Sebaran yang merata di kedua warna itulah wujud visual dari "
            "'tidak ada edge'."
        )

        with st.expander("TABEL RILIS"):
            show = df[["release_date", "release_utc", "period", "actual", "naive_forecast",
                       "surprise", "verdict"] + [f"ret_{t}" for t in TIMEFRAMES]]
            st.dataframe(show.sort_values("release_date", ascending=False),
                         width="stretch", hide_index=True)


# ---------------------------------------------------------------------------
# TAB 3
# ---------------------------------------------------------------------------

with tab3:
    section("SYS", "Provider Status", "diverifikasi terhadap API sungguhan")
    fmp_health = fmp.health_check()

    h1, h2, h3 = st.columns(3)
    h1.markdown(
        tile(
            "FMP",
            fmp_health["status"],
            (
                f'{fmp_health["latency_ms"]} ms · kalender DIBLOKIR (402)'
                if fmp_health["ok"] and not fmp_health["calendar_available"]
                else f'{fmp_health["latency_ms"]} ms · kalender OK'
                if fmp_health["ok"]
                else fmp_health["detail"]
            ),
            BULL if fmp_health["ok"] else DIM,
        ),
        unsafe_allow_html=True,
    )
    h2.markdown(tile("FRED", "NO KEY", "Set FRED_API_KEY di .env", DIM), unsafe_allow_html=True)
    tg = telegram_bot.check_connection() if telegram_bot.is_configured() else None
    h3.markdown(
        tile(
            "TELEGRAM",
            tg.status if tg else "NO KEY",
            f"{tg.latency_ms} ms · {tg.detail}" if tg and tg.latency_ms else "Set TELEGRAM_BOT_TOKEN",
            BULL if tg and tg.ok else DIM,
        ),
        unsafe_allow_html=True,
    )

    if fmp_health["ok"] and not fmp_health["calendar_available"]:
        st.write("")
        st.markdown(
            '<div class="note note-w"><b>FMP tersambung, tetapi economic-calendar diblokir '
            "(HTTP 402 — di luar paket langganan).</b> Endpoint itu satu-satunya sumber "
            "angka <i>consensus</i> di FMP. Tanpa consensus, Surprise Delta tidak dapat "
            "dihitung, sehingga Rule Engine tidak bisa menghasilkan verdict dari data live. "
            "Harga pasar dan Treasury rates tetap terambil — lihat ticker di atas.</div>",
            unsafe_allow_html=True,
        )
    st.caption(
        "Rantai fallback PRD: FMP → TradingEconomics → FRED. Adapter FMP sudah ditulis "
        "dan terverifikasi. Catatan: FRED tidak menerbitkan consensus sama sekali — hanya "
        "angka aktual — sehingga ia bukan fallback yang sah untuk consensus."
    )

    section("SYS", "Audit Log", f"{len(st.session_state.audit_log)} baris")
    log = pd.DataFrame(st.session_state.audit_log).rename(
        columns={
            "timestamp": "TIMESTAMP (WIB)",
            "event": "EVENT",
            "actual": "ACTUAL",
            "consensus": "CONSENSUS",
            "surprise_delta": "DELTA",
            "verdict": "VERDICT",
            "delivery": "DELIVERY",
        }
    )
    # Kolom numerik bercampur penanda [MISSING], jadi tabel dirender sebagai teks.
    # Nilai kosong TIDAK diisi 0 atau "-": data hilang harus terbaca sebagai hilang.
    log = log.astype(object).where(log.notna(), "[MISSING]").astype(str)
    st.dataframe(log, width="stretch", hide_index=True)

    st.caption(
        f"Baris ber-verdict '{NO_VERDICT_DISPLAY}' menandakan Rule Engine tidak menghasilkan "
        f"keputusan, dan notifikasinya DITAHAN (HELD). Ini sengaja dibedakan dari NEUTRAL — "
        f"NEUTRAL adalah hasil analisis yang sah, yang ini kegagalan sistem."
    )
