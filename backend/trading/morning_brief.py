"""India Morning Intelligence Brief — runs at 08:55 IST, 20 min before NSE open.

Gathers overnight global market data, India VIX, FII/DII flows, BSE results
calendar, and NSE-specific news, then sends everything to a single LLM call
to produce a structured pre-session briefing.

The brief answers:
  - What happened overnight in Asia / US / Europe?
  - What do India VIX, FII flows, SGX Nifty say about today's open?
  - Which companies report results today (binary event risk)?
  - What sectors / themes are hot or risky today on NSE?
  - Which NSE tickers from our watchlist are most likely to move?
  - What trading stance should we take this NSE session?

Output feeds directly into the batch pipeline to bias candidate ranking.
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger("moonshotx.morning_brief")

MAX_HEADLINES = 20


# ── Data gathering ────────────────────────────────────────────────────────

async def gather_overnight_intel(kite_client=None) -> dict:
    """
    Fetch all India morning intel concurrently.
    Returns structured intel dict for the LLM brief.
    """
    from data.india_macro import get_india_macro_summary
    from data.india_news_feed import get_morning_headlines
    from data.bse_results_calendar import get_results_today, get_results_calendar

    macro_task    = get_india_macro_summary()
    news_task     = get_morning_headlines(limit=MAX_HEADLINES)
    results_today = get_results_today()
    results_week  = get_results_calendar(days_ahead=5)

    macro, news, results_today_data, results_week_data = await asyncio.gather(
        macro_task,
        news_task,
        results_today,
        results_week,
        return_exceptions=True,
    )

    return {
        "macro": macro if isinstance(macro, dict) else {},
        "headlines": news if isinstance(news, list) else [],
        "results_today": results_today_data if isinstance(results_today_data, list) else [],
        "results_this_week": results_week_data if isinstance(results_week_data, list) else [],
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ── LLM analysis ─────────────────────────────────────────────────────────────────────────────

def _build_brief_prompt(intel: dict, regime_data: dict, watchlist: list) -> str:
    """Build the India-specific user prompt for the morning brief LLM call."""
    macro = intel.get("macro", {})
    headlines = intel.get("headlines", [])
    results_today = intel.get("results_today", [])
    results_week = intel.get("results_this_week", [])

    current_reg  = regime_data.get("regime", "neutral")
    india_vix    = macro.get("india_vix", regime_data.get("india_vix", "N/A"))
    fg           = macro.get("fear_greed", regime_data.get("fear_greed", "N/A"))

    news_block = "\n".join(f"  • {h.get('title', h) if isinstance(h, dict) else h}" for h in headlines[:12]) if headlines else "  No headlines available"
    watchlist_str = ", ".join(watchlist[:30]) if watchlist else "N/A"

    results_today_str = ", ".join(
        f"{r.get('symbol')} ({r.get('result_type', 'Results')})" for r in results_today
    ) if results_today else "None"
    results_week_str = ", ".join(
        f"{r.get('symbol')} on {r.get('result_date', '?')}" for r in results_week[:8]
    ) if results_week else "None"

    fii_net = macro.get("fii_net_cr", 0)
    dii_net = macro.get("dii_net_cr", 0)
    fii_str = f"FII: {'BUYING' if fii_net > 0 else 'SELLING'} (₹{abs(fii_net):.0f} Cr)  |  DII: {'BUYING' if dii_net > 0 else 'SELLING'} (₹{abs(dii_net):.0f} Cr)"

    return f"""You are the pre-market intelligence analyst for MoonshotX-IND, an intraday momentum trading bot on NSE India.
NSE opens at 09:15 IST in ~20 minutes. Produce a crisp morning brief from the data below.

=== CURRENT REGIME ===
Regime: {current_reg}  |  India VIX: {india_vix}  |  Fear & Greed Proxy: {fg}

=== INDIA MARKET SNAPSHOT ===
NIFTY50: {macro.get('nifty50_price', 'N/A')} ({macro.get('nifty50_pct', 0):+.2f}%)
Bank Nifty: {macro.get('nifty_bank_pct', 0):+.2f}%  |  Nifty IT: {macro.get('nifty_it_pct', 0):+.2f}%  |  Nifty Midcap: {macro.get('nifty_midcap_pct', 0):+.2f}%

=== INSTITUTIONAL FLOWS (previous session) ===
{fii_str}

=== GLOBAL OVERNIGHT CUES ===
US Futures: {macro.get('us_futures_pct', 0):+.2f}% ({macro.get('us_futures_direction', 'flat')})
Nikkei: {macro.get('nikkei_pct', 0):+.2f}%  |  Hang Seng: {macro.get('hsi_pct', 0):+.2f}%
Brent Crude: ${macro.get('brent_price', 0):.1f} ({macro.get('brent_pct', 0):+.2f}%)
Gold: ${macro.get('gold_price', 0):.0f} ({macro.get('gold_pct', 0):+.2f}%)
USD/INR: {macro.get('usdinr', 84.0):.2f} ({macro.get('usdinr_pct', 0):+.2f}%)
RBI Repo Rate: {macro.get('rbi_repo_rate_pct', 6.5)}%

=== QUARTERLY RESULTS TODAY (AVOID THESE) ===
{results_today_str}

=== RESULTS EXPECTED THIS WEEK ===
{results_week_str}

=== MORNING NEWS HEADLINES ===
{news_block}

=== OUR NSE WATCHLIST (evaluate these for today) ===
{watchlist_str}

Analyze everything above and respond ONLY with this JSON (no other text):
{{
  "expected_regime": "bull" | "neutral" | "fear" | "choppy" | "bear_mode",
  "trading_stance": "aggressive" | "normal" | "cautious" | "sit_out",
  "session_sentiment": "brief 1-sentence overall NSE market mood",
  "hot_sectors": ["sector1", "sector2"],
  "avoid_sectors": ["sector3"],
  "key_themes": ["theme1 (e.g. FII inflows)", "theme2"],
  "macro_risks": ["risk1", "risk2"],
  "results_risk_symbols": ["SYM1"],
  "top_picks": [
    {{"symbol": "NSE_TICKER", "thesis": "1-sentence why it moves today on NSE", "confidence": 0.0-1.0}},
    {{"symbol": "NSE_TICKER2", "thesis": "...", "confidence": 0.0-1.0}}
  ],
  "avoid_picks": ["SYM1", "SYM2"],
  "brief_summary": "2-3 sentence executive summary of what to expect today on NSE"
}}"""


async def run_morning_brief(
    kite_client,
    pipeline,
    regime_data: dict,
    watchlist: list,
) -> dict:
    """
    Run full India morning brief: gather overnight data → one LLM call → structured intel.
    Returns the brief dict (also logged). Falls back to empty dict on any failure.
    """
    logger.info("[MORNING BRIEF] Gathering India overnight intel...")
    try:
        intel = await gather_overnight_intel(kite_client)
    except Exception as e:
        logger.error(f"[MORNING BRIEF] Data gather failed: {e}")
        return {}

    macro = intel.get("macro", {})
    logger.info(
        f"[MORNING BRIEF] IndiaVIX={macro.get('india_vix', '?')} "
        f"NIFTY={macro.get('nifty50_pct', 0):+.2f}% "
        f"FII={macro.get('fii_direction', '?')} "
        f"Headlines={len(intel.get('headlines', []))}"
    )

    # One DEEP LLM call for macro analysis
    sys_p = (
        "You are MoonshotX-IND pre-market intelligence analyst specializing in NSE India. "
        "Analyze India VIX, FII/DII flows, global overnight cues, and produce a trading brief for the upcoming NSE session. "
        "Return ONLY valid JSON, no markdown, no code blocks."
    )
    user_p = _build_brief_prompt(intel, regime_data, watchlist)

    try:
        brief = await pipeline._call_llm(sys_p, user_p, model=pipeline.DEEP_MODEL, timeout=30)
    except Exception as e:
        logger.error(f"[MORNING BRIEF] LLM call failed: {e}")
        return {"raw_intel": intel}

    if not brief:
        logger.warning("[MORNING BRIEF] LLM returned empty response")
        return {"raw_intel": intel}

    brief["raw_intel"]  = intel
    brief["created_at"] = datetime.now(timezone.utc).isoformat()

    # Log the summary
    stance  = brief.get("trading_stance", "?")
    regime  = brief.get("expected_regime", "?")
    summary = brief.get("brief_summary", "")
    themes  = brief.get("key_themes", [])
    hot     = brief.get("hot_sectors", [])
    picks   = [p.get("symbol") for p in brief.get("top_picks", [])]

    logger.info(f"[MORNING BRIEF] ═══════════════════════════════════════")
    logger.info(f"[MORNING BRIEF] Stance={stance}  Regime={regime}")
    logger.info(f"[MORNING BRIEF] Themes: {themes}")
    logger.info(f"[MORNING BRIEF] Hot sectors: {hot}")
    logger.info(f"[MORNING BRIEF] Top picks: {picks}")
    logger.info(f"[MORNING BRIEF] Summary: {summary}")
    logger.info(f"[MORNING BRIEF] ═══════════════════════════════════════")

    return brief
