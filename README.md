# MacroAI — Automated US Macro Release Monitor

MacroAI watches the US macro calendar (FOMC, CPI, PPI, NFP, GDP), reads the actual release, and turns it into an **objective, rules-based position bias** — then pushes it to Telegram only when something actually changes.

Built to remove the two failure modes of trading macro headlines: reacting late, and reacting emotionally to the *headline* number instead of the deviation from expectations.

> Decision-support tooling, not investment advice. Outputs are deterministic verdicts from a rule table plus measured calibration stats, not predictions.

## What it does

1. **Calendar** — pulls the official BLS release schedule (ICS) and FOMC calendar, validates it, and falls back to compiled constants when the file is stale or missing.
2. **Actuals** — fetches the released values (FRED for series freshness and history, provider APIs for the rest).
3. **Surprise delta** — computes `Actual − Consensus` where a consensus value is available, with a scenario playbook per event.
4. **Verdict** — a deterministic rule engine (`src/rule_engine.py`, `INDICATOR_TABLE` as the single source of truth for direction) produces Bullish / Bearish / Neutral, plus a two-horizon thesis: short horizon = surprise, long horizon = FRED trend.
5. **Action** — maps the verdict to BUY / SELL / HOLD / PENDING per timeframe (15m, 1h, 4h), using measured calibration statistics instead of intuition.
6. **Notification** — Telegram delivery where every function returns a result instead of throwing, plus a coverage alarm when a high-impact indicator has no scheduled date within the next 60 days.
7. **Dashboard** — Streamlit terminal view (`dashboard.py`) that only *presents*; it holds no verdict logic of its own.

## Module map

```
src/rule_engine.py     deterministic verdict + INDICATOR_TABLE (source of truth for direction)
src/thesis.py          two-horizon thesis: short = surprise, long = FRED trend
src/action.py          BUY / SELL / HOLD / PENDING per timeframe 15m, 1h, 4h
src/backtest.py        real backtest: FRED release dates + 15m Binance candles
src/rule_engine.py     verdict rules          src/schedule.py   calendar validation
src/revision.py        data revision handling src/live_calibration_view.py
src/providers/         fmp, fred, binance clients
src/telegram_bot.py    notifications, non-throwing
dashboard.py           Streamlit presenter (no verdict logic)
data/calibration.json  measured stats consumed by action.py
scripts/               schedule fetch, calibration build, backfill, chat-id helper
prompts/               analyst + presenter system prompts
docs/                  method review, incident briefs, work orders
```

## Commands

```bash
streamlit run dashboard.py                 # dashboard (port 8501)
python scripts/fetch_schedule.py           # official BLS (ICS) + FOMC -> data/schedule.json + coverage alarm (exit 2/3)
python scripts/build_calibration.py        # rebuild data/calibration.json from backtest
python scripts/build_calibration.py --live-only   # live hit-rate vs backtest -> data/calibration_live.json
python scripts/backfill_moves.py --dry-run # backfill null 1h/4h horizons in data/live_events.jsonl
python -m unittest discover -s tests -v    # offline tests (fixtures, no network)
```

`.env` is required (see `.env.example`). `load_dotenv()` runs once at import, so changing `.env` needs a process restart.

## Honest limitations

- **Consensus is not freely available.** The FMP economic calendar returns HTTP 402 outside its paid tier, and FRED publishes no forecasts at all. Without consensus, the live surprise delta cannot be computed — so the PRD's original fallback chain (FMP → TradingEconomics → FRED) was wrong and is documented as such.
- **FRED is the best source for actuals** (fresh, decades of history), while the FMP economic-indicators endpoint lags by roughly 276 days.
- **GDP schedule** is not fetched from BEA; it always comes from constants.
- **No scheduler in this repo** — the tick/notify cadence is handled by external infrastructure.
- `www.bls.gov` rejects fake browser user agents (403) but serves a self-identifying UA; do not swap the `USER_AGENT` in `scripts/fetch_schedule.py` for a browser one.

## Stack

Python · Streamlit · FRED API · FMP API · BLS ICS calendar · Binance market data · Telegram Bot API · unittest (offline fixtures) · Claude Code for development workflow
