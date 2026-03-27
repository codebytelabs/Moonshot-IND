"""
MoonshotX-IND Multi-Agent Trading Pipeline — India/NSE Edition
12 agents: Technical, News, Sentiment, Fundamentals, Bull, Bear,
Research Manager, Trader, Aggressive, Neutral, Conservative, Portfolio Manager

India localization: all prompts use ₹, NSE, India VIX, FII/DII flows.
"""
import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from emergentintegrations.llm.chat import LlmChat, UserMessage

logger = logging.getLogger("moonshotx.pipeline")

# ── Provider selection — single switch in .env ────────────────────────────
# LLM_PROVIDER = "openrouter" | "ollama"
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openrouter").lower()

if LLM_PROVIDER == "ollama":
    QUICK_MODEL    = os.environ.get("Ollama_Quick_Primary_Model",    "gemini-3-flash-preview:cloud")
    QUICK_FALLBACK = os.environ.get("Ollama_Quick_Backup_Model",     "glm-5:cloud")
    DEEP_MODEL     = os.environ.get("Ollama_Research_Primary_Model", "kimi-k2.5:cloud")
    DEEP_FALLBACK  = os.environ.get("Ollama_Research_Backup_Model",  "glm-5:cloud")
    _LLM_API_KEY   = os.environ.get("OLLAMA_API_KEY", "")
else:
    # Default: openrouter
    LLM_PROVIDER   = "openrouter"
    QUICK_MODEL    = os.environ.get("Openrouter_Quick_Primary_Model",    "google/gemini-2.5-flash-lite-preview-09-2025")
    QUICK_FALLBACK = os.environ.get("Openrouter_Quick_Backup_Model",     "google/gemini-3.1-flash-lite-preview")
    DEEP_MODEL     = os.environ.get("Openrouter_Research_Primary_Model", "anthropic/claude-haiku-4-5")
    DEEP_FALLBACK  = os.environ.get("Openrouter_Research_Backup_Model",  "google/gemini-3.1-flash-lite-preview")
    _LLM_API_KEY   = os.environ.get("OPENROUTER_API_KEY", os.environ.get("EMERGENT_LLM_KEY", ""))

logger.info(
    f"LLM provider={LLM_PROVIDER.upper()} | "
    f"quick={QUICK_MODEL} | quick_fallback={QUICK_FALLBACK} | "
    f"deep={DEEP_MODEL} | deep_fallback={DEEP_FALLBACK}"
)

# Approximate cost per call (USD) for budget tracking
_COST_MAP = {
    # openrouter
    "google/gemini-2.5-flash-lite-preview-09-2025": 0.000040,
    "google/gemini-3.1-flash-lite-preview":         0.000040,
    "anthropic/claude-haiku-4-5":                   0.001300,
    # ollama cloud (estimated, pricing TBD)
    "gemini-3-flash-preview:cloud":                 0.000050,
    "glm-5:cloud":                                  0.000100,
    "kimi-k2.5:cloud":                              0.000200,
    "nemotron-3-nano:30b-cloud":                    0.000030,
}
_COST_QUICK = _COST_MAP.get(QUICK_MODEL, 0.00005)
_COST_DEEP  = _COST_MAP.get(DEEP_MODEL,  0.00100)


def extract_json(text: str) -> dict | list:
    """Extract JSON from LLM response. Supports both objects and arrays."""
    # Try code-block patterns first (handles ```json ... ``` wrappers)
    for pattern in [r"```json\s*(.*?)\s*```", r"```\s*(.*?)\s*```"]:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    # Try raw JSON (array or object)
    for pattern in [r"(\[.*\])", r"(\{.*\})"]:
        m = re.search(pattern, text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    try:
        return json.loads(text)
    except Exception:
        return {}


class AgentPipeline:
    DEEP_MODEL  = DEEP_MODEL
    QUICK_MODEL = QUICK_MODEL

    def __init__(self, llm_api_key: str, broadcast_fn: Optional[Callable] = None):
        self.api_key = llm_api_key
        self.broadcast = broadcast_fn or (lambda _: None)
        self.llm_cost_today = 0.0

    async def _call_llm(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str = QUICK_MODEL,
        timeout: int = 25,
        max_tokens: int = 1024,
    ) -> dict:
        # Determine fallback: deep model gets deep fallback, quick gets quick fallback
        fallback = DEEP_FALLBACK if model == DEEP_MODEL else QUICK_FALLBACK
        models_to_try = [model] if model == fallback else [model, fallback]

        for attempt, m in enumerate(models_to_try):
            try:
                chat = LlmChat(
                    api_key=self.api_key,
                    session_id=str(uuid.uuid4()),
                    system_message=system_prompt,
                    max_tokens=max_tokens,
                ).with_model(LLM_PROVIDER, m)
                resp = await asyncio.wait_for(
                    chat.send_message(UserMessage(text=user_prompt)), timeout=timeout
                )
                cost = _COST_DEEP if m in (DEEP_MODEL, DEEP_FALLBACK) else _COST_QUICK
                self.llm_cost_today += cost
                if attempt > 0:
                    logger.info(f"Fallback succeeded: {model} → {m}")
                return extract_json(resp) if isinstance(resp, str) else {}
            except asyncio.TimeoutError:
                logger.warning(f"LLM timeout ({m})" + (" — trying fallback" if attempt == 0 and len(models_to_try) > 1 else ""))
            except Exception as e:
                logger.warning(f"LLM error ({m}): {e}" + (" — trying fallback" if attempt == 0 and len(models_to_try) > 1 else ""))
        return {}

    def _emit(self, agent_name: str, ticker: str, status: str, data: dict = {}):
        self.broadcast({
            "type": "agent_activity",
            "agent": agent_name,
            "ticker": ticker,
            "status": status,
            "data": data,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

    # ─── ANALYST AGENTS ──────────────────────────────────────────────────────

    async def technical_analyst(self, ticker: str, md: dict) -> dict:
        self._emit("Technical Analyst", ticker, "running")
        sys_p = "You are MoonshotX-IND Technical Analyst. You analyze price action and indicators for NSE India equities. Return ONLY valid JSON."
        user_p = f"""Analyze {ticker} (NSE) based on this market data and return your technical assessment.

Price Data:
- Current Price: ₹{md.get('price', 0):.2f}
- 5-day Momentum: {md.get('momentum_5d', 0):.2f}%
- 20-day Momentum: {md.get('momentum_20d', 0):.2f}%

Indicators:
- RSI(14): {md.get('rsi', 50):.1f}
- EMA Trend: {'BULLISH (EMA9 > EMA21)' if md.get('ema_bullish') else 'BEARISH (EMA9 < EMA21)'}
- Volume Ratio: {md.get('volume_ratio', 1.0):.2f}x average
- ATR: ₹{md.get('atr', 0):.2f} ({md.get('atr_pct', 0):.2f}% of price)
- Avg Rupee Volume: ₹{md.get('avg_rupee_volume', 0)/1e7:.1f} Cr

Respond ONLY with this JSON:
{{
  "signal": "BULLISH" or "BEARISH" or "NEUTRAL",
  "confidence": 0.0-1.0,
  "analysis": "2-sentence assessment",
  "key_factors": ["factor1", "factor2"],
  "stop_suggestion_pct": 0.02,
  "risks": ["risk1"]
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("Technical Analyst", ticker, "complete", result)
        return result

    async def news_analyst(self, ticker: str, md: dict) -> dict:
        self._emit("News Analyst", ticker, "running")
        sys_p = "You are MoonshotX-IND News Analyst. You assess India macro, FII/DII flows, and NSE company-specific news impact. Return ONLY valid JSON."
        user_p = f"""Assess news and macro context for {ticker} (NSE India).

Stock: {ticker} (NSE)
Price: ₹{md.get('price', 0):.2f}
Recent momentum: {md.get('momentum_5d', 0):.2f}% over 5 days

India macro context:
- Current market regime: {md.get('regime', 'neutral')}
- India VIX: {md.get('india_vix', 16):.1f}
- Fear & Greed Proxy: {md.get('fear_greed', 50):.0f}/100
- FII/DII flow bias: {md.get('fii_direction', 'neutral')}

Respond ONLY with this JSON:
{{
  "signal": "BULLISH" or "BEARISH" or "NEUTRAL",
  "confidence": 0.0-1.0,
  "analysis": "2-sentence assessment of India macro/news context",
  "key_factors": ["factor1", "factor2"],
  "risks": ["risk1"]
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("News Analyst", ticker, "complete", result)
        return result

    async def sentiment_analyst(self, ticker: str, md: dict) -> dict:
        self._emit("Sentiment Analyst", ticker, "running")
        sys_p = "You are MoonshotX-IND Sentiment Analyst. You assess retail and institutional sentiment for NSE India equities, including FII activity and NSE option chain signals. Return ONLY valid JSON."
        user_p = f"""Assess market sentiment for {ticker} (NSE India).

Stock: {ticker} | Price: ₹{md.get('price', 0):.2f}
Volume surge: {md.get('volume_ratio', 1.0):.2f}x average
5d momentum: {md.get('momentum_5d', 0):.2f}%
India Fear & Greed Proxy: {md.get('fear_greed', 50):.0f}/100
FII direction: {md.get('fii_direction', 'neutral')}

Respond ONLY with this JSON:
{{
  "signal": "BULLISH" or "BEARISH" or "NEUTRAL",
  "confidence": 0.0-1.0,
  "analysis": "2-sentence NSE sentiment assessment",
  "crowd_positioning": "LONG_BIASED" or "SHORT_BIASED" or "NEUTRAL",
  "risks": ["risk1"]
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("Sentiment Analyst", ticker, "complete", result)
        return result

    async def fundamentals_analyst(self, ticker: str, md: dict) -> dict:
        self._emit("Fundamentals Analyst", ticker, "running")
        sys_p = "You are MoonshotX-IND Fundamentals Analyst. You assess NSE India company fundamentals, quarterly results, and sector catalysts. Return ONLY valid JSON."
        user_p = f"""Assess fundamental backdrop for {ticker} (NSE India) as a short-term trading catalyst.

Stock: {ticker} (NSE) | Price: ₹{md.get('price', 0):.2f}
20d Momentum: {md.get('momentum_20d', 0):.2f}%
Market regime: {md.get('regime', 'neutral')}
Results imminent: {md.get('results_imminent', False)}

For short-term NSE trading context, assess:
- Is the fundamental backdrop supportive of the current price action?
- Are there near-term India-specific catalysts (quarterly results, RBI policy, sector news)?

Respond ONLY with this JSON:
{{
  "signal": "BULLISH" or "BEARISH" or "NEUTRAL",
  "confidence": 0.0-1.0,
  "analysis": "2-sentence NSE fundamental context",
  "catalyst": "string or null",
  "risks": ["risk1"]
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("Fundamentals Analyst", ticker, "complete", result)
        return result

    # ─── RESEARCHER AGENTS ──────────────────────────────────────────────────

    async def bull_researcher(self, ticker: str, reports: dict, round_num: int, bear_case: str = "") -> dict:
        self._emit("Bull Researcher", ticker, "running")
        sys_p = "You are MoonshotX Bull Researcher. Build the strongest possible BUY case. Return ONLY valid JSON."
        user_p = f"""Build the bull case for {ticker} (Debate Round {round_num}).

Technical: {json.dumps(reports.get('technical', {}))}
News: {json.dumps(reports.get('news', {}))}
Sentiment: {json.dumps(reports.get('sentiment', {}))}

Bear's previous argument: {bear_case or 'None yet'}

Respond ONLY with this JSON:
{{
  "bull_case": "2-3 sentence bullish argument",
  "confidence": 0.0-1.0,
  "key_arguments": ["arg1", "arg2", "arg3"],
  "price_target_reason": "why price could move up"
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("Bull Researcher", ticker, "complete", result)
        return result

    async def bear_researcher(self, ticker: str, reports: dict, round_num: int, bull_case: str = "") -> dict:
        self._emit("Bear Researcher", ticker, "running")
        sys_p = "You are MoonshotX Bear Researcher. Build the strongest possible SKIP/AVOID case. Return ONLY valid JSON."
        user_p = f"""Build the bear case against buying {ticker} (Debate Round {round_num}).

Technical: {json.dumps(reports.get('technical', {}))}
News: {json.dumps(reports.get('news', {}))}

Bull's argument: {bull_case or 'None yet'}

Respond ONLY with this JSON:
{{
  "bear_case": "2-3 sentence bearish argument",
  "confidence": 0.0-1.0,
  "key_arguments": ["arg1", "arg2"],
  "downside_risks": ["risk1", "risk2"]
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("Bear Researcher", ticker, "complete", result)
        return result

    # ─── RESEARCH MANAGER ───────────────────────────────────────────────────

    async def research_manager(self, ticker: str, debate: list, reports: dict, regime: str) -> dict:
        self._emit("Research Manager", ticker, "running")
        sys_p = "You are MoonshotX Research Manager. Judge the investment debate and issue a verdict. Be rigorous. Return ONLY valid JSON."
        debate_text = "\n".join([f"Round {i+1}: Bull: {d.get('bull','')}\nBear: {d.get('bear','')}" for i, d in enumerate(debate)])
        user_p = f"""Judge this investment debate for {ticker}.

Market Regime: {regime}
Technical Signal: {reports.get('technical', {}).get('signal', 'NEUTRAL')} (confidence: {reports.get('technical', {}).get('confidence', 0.5):.0%})

Debate History:
{debate_text}

Issue a definitive verdict. If there is no strong edge, REJECT.

Respond ONLY with this JSON:
{{
  "verdict": "BULLISH" or "BEARISH" or "NEUTRAL",
  "recommended_action": "BUY" or "SKIP",
  "conviction_score": 0.0-1.0,
  "reasoning": "2-3 sentence synthesis",
  "winning_arguments": ["arg1"],
  "risk_level": "LOW" or "MEDIUM" or "HIGH"
}}"""
        result = await self._call_llm(sys_p, user_p, DEEP_MODEL, timeout=25)
        self._emit("Research Manager", ticker, "complete", result)
        return result

    # ─── TRADER AGENT ────────────────────────────────────────────────────────

    async def trader(self, ticker: str, verdict: dict, md: dict) -> dict:
        self._emit("Trader", ticker, "running")
        sys_p = "You are MoonshotX Trader. Create a precise entry plan with stop loss and take profit. Return ONLY valid JSON."
        price = md.get("price", 100)
        atr = md.get("atr", price * 0.02)
        user_p = f"""Create entry plan for {ticker}.

Research Verdict: {verdict.get('verdict', 'NEUTRAL')} | Conviction: {verdict.get('conviction_score', 0.5):.0%}
Current Price: ${price:.2f}
ATR(14): ${atr:.2f}

Plan rules:
- Stop Loss: 1.5x ATR below entry
- Take Profit: 2.5x risk above entry (R:R >= 1.67)
- Entry type: limit at current price or slightly below

Respond ONLY with this JSON:
{{
  "action": "BUY" or "SKIP",
  "entry_price": {price:.2f},
  "stop_loss": {price - 1.5 * atr:.2f},
  "take_profit": {price + 2.5 * 1.5 * atr:.2f},
  "entry_type": "limit",
  "reasoning": "1-sentence rationale"
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=15)
        self._emit("Trader", ticker, "complete", result)
        return result

    # ─── RISK DEBATE AGENTS ──────────────────────────────────────────────────

    async def aggressive_analyst(self, ticker: str, plan: dict) -> dict:
        self._emit("Aggressive Analyst", ticker, "running")
        sys_p = "You are MoonshotX Aggressive Analyst. Push for larger positions and tighter stops when setup is strong. Return ONLY valid JSON."
        user_p = f"""Review this trade plan for {ticker} from an aggressive perspective:
Plan: {json.dumps(plan)}

Respond ONLY with this JSON:
{{
  "recommendation": "INCREASE_SIZE" or "APPROVE_AS_IS" or "REJECT",
  "suggested_size_mult": 1.0-1.3,
  "reasoning": "1-sentence aggressive view",
  "concern": "main risk"
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=12)
        self._emit("Aggressive Analyst", ticker, "complete", result)
        return result

    async def neutral_analyst(self, ticker: str, plan: dict) -> dict:
        self._emit("Neutral Analyst", ticker, "running")
        sys_p = "You are MoonshotX Neutral Analyst. Provide balanced risk/reward assessment. Return ONLY valid JSON."
        user_p = f"""Assess this trade plan for {ticker} objectively:
Plan: {json.dumps(plan)}

Respond ONLY with this JSON:
{{
  "recommendation": "APPROVE" or "REDUCE_SIZE" or "REJECT",
  "risk_reward_assessment": "FAVORABLE" or "MARGINAL" or "UNFAVORABLE",
  "reasoning": "1-sentence neutral view",
  "key_risk": "primary risk factor"
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=12)
        self._emit("Neutral Analyst", ticker, "complete", result)
        return result

    async def conservative_analyst(self, ticker: str, plan: dict) -> dict:
        self._emit("Conservative Analyst", ticker, "running")
        sys_p = "You are MoonshotX Conservative Analyst. Protect capital. Question every trade. Return ONLY valid JSON."
        user_p = f"""Review this trade plan for {ticker} with capital preservation focus:
Plan: {json.dumps(plan)}

Respond ONLY with this JSON:
{{
  "recommendation": "APPROVE" or "REDUCE_SIZE" or "REJECT",
  "position_size_view": "TOO_LARGE" or "APPROPRIATE" or "ACCEPTABLE",
  "reasoning": "1-sentence conservative view",
  "rejection_threshold": "what would make you reject this"
}}"""
        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=12)
        self._emit("Conservative Analyst", ticker, "complete", result)
        return result

    # ─── PORTFOLIO MANAGER ───────────────────────────────────────────────────

    async def portfolio_manager(
        self, ticker: str, plan: dict, risk_debate: dict, portfolio_context: dict
    ) -> dict:
        self._emit("Portfolio Manager", ticker, "running")
        sys_p = "You are MoonshotX Portfolio Manager. Make the final APPROVE/REJECT decision. Be disciplined. Return ONLY valid JSON."
        user_p = f"""Make the final trading decision for {ticker}.

Proposed Plan: {json.dumps(plan)}

Risk Debate:
- Aggressive: {risk_debate.get('aggressive', {}).get('recommendation', 'N/A')} — {risk_debate.get('aggressive', {}).get('reasoning', '')}
- Neutral: {risk_debate.get('neutral', {}).get('recommendation', 'N/A')} — {risk_debate.get('neutral', {}).get('reasoning', '')}
- Conservative: {risk_debate.get('conservative', {}).get('recommendation', 'N/A')} — {risk_debate.get('conservative', {}).get('reasoning', '')}

Portfolio Context:
- Open positions: {portfolio_context.get('open_positions', 0)}
- Daily P&L: ${portfolio_context.get('daily_pnl', 0):.2f}
- Regime: {portfolio_context.get('regime', 'neutral')}

APPROVE only if: risk debate shows ≥2 positive votes AND the setup is genuinely high quality.

Respond ONLY with this JSON:
{{
  "decision": "APPROVE" or "REJECT",
  "final_size_shares": {plan.get('size', 1)},
  "final_stop_loss": {plan.get('stop_loss', 0):.2f},
  "final_take_profit": {plan.get('take_profit', 0):.2f},
  "reasoning": "2-sentence final reasoning",
  "risk_vote_summary": "how the risk debate influenced decision"
}}"""
        result = await self._call_llm(sys_p, user_p, DEEP_MODEL, timeout=25)
        self._emit("Portfolio Manager", ticker, "complete", result)
        return result

    # ─── BATCHED PIPELINE (token-optimized: 2 LLM calls for N tickers) ─────

    def _compact_ticker_data(self, ticker: str, md: dict) -> dict:
        """Compress market data to minimal token-efficient dict for batch calls."""
        return {
            "sym": ticker,
            "px": round(md.get("price", 0), 2),
            "rsi": round(md.get("rsi", 50), 1),
            "ema_bull": bool(md.get("ema_bullish")),
            "mom5": round(md.get("momentum_5d", 0), 2),
            "mom20": round(md.get("momentum_20d", 0), 2),
            "vol_r": round(md.get("volume_ratio", 1), 2),
            "atr": round(md.get("atr", 0), 2),
            "atr_pct": round(md.get("atr_pct", 0), 2),
            "bay": round(md.get("bayesian_score", 0.5), 2),
        }

    # Regime-adaptive confidence thresholds for batch screening
    _SCREEN_CONF_THRESHOLDS = {
        "bull": 0.55, "neutral": 0.65, "fear": 0.78, "choppy": 0.80,
        "bear_mode": 0.90, "extreme_fear": 1.0,
    }
    _SCREEN_MAX_PASS = {
        "bull": 5, "neutral": 4, "fear": 2, "choppy": 2,
        "bear_mode": 1, "extreme_fear": 0,
    }

    async def _batch_screen(self, candidates: list[dict], regime: str, regime_data: dict) -> list[dict]:
        """Phase A: One QUICK call screens all candidates. Returns shortlist."""
        self.broadcast({"type": "batch_screen_start", "count": len(candidates), "ts": datetime.now(timezone.utc).isoformat()})

        conf_threshold = self._SCREEN_CONF_THRESHOLDS.get(regime, 0.70)
        max_pass = self._SCREEN_MAX_PASS.get(regime, 3)

        compact = json.dumps(candidates, separators=(",", ":"))
        selectivity = {
            "bull": "moderately aggressive — favor momentum",
            "neutral": "balanced — require clear edge",
            "fear": "VERY selective — only the strongest setups with defensive characteristics",
            "choppy": "VERY selective — need extreme clarity",
        }.get(regime, "selective")

        sys_p = (
            "You are MoonshotX Batch Screener. You receive an array of stock candidates with "
            "technical indicators and return a filtered shortlist of the best trading opportunities. "
            "Be selective — only pass stocks with a clear edge. Return ONLY valid JSON."
        )
        user_p = f"""Screen these {len(candidates)} candidates for short-term long entries.

Market context: regime={regime}, VIX={regime_data.get('vix', 20):.1f}, F&G={regime_data.get('fear_greed', 50):.0f}/100

Candidates (compact):
{compact}

Key: sym=ticker, px=price, rsi=RSI14, ema_bull=EMA9>EMA21, mom5/mom20=momentum%, vol_r=volume ratio, atr=ATR$, atr_pct=ATR%, bay=bayesian score

RULES:
- PASS only stocks with genuine bullish edge (momentum, volume, trend alignment)
- REJECT overbought (RSI>75), weak momentum, low volume, bearish trend
- Regime is {regime} — be {selectivity}
- Maximum {max_pass} passes (fewer is better in this regime)
- Confidence must be very high to pass (>= {conf_threshold})

Return ONLY this JSON array (empty array if nothing passes):
[{{"sym":"TICKER","signal":"BULLISH","conf":0.0-1.0,"edge":"one-line reason"}}]"""

        result = await self._call_llm(sys_p, user_p, QUICK_MODEL, timeout=30, max_tokens=2048)
        if isinstance(result, dict):
            shortlist = result.get("shortlist", result.get("candidates", result.get("results", [])))
            if not isinstance(shortlist, list):
                shortlist = [result] if result.get("sym") else []
        elif isinstance(result, list):
            shortlist = result
        else:
            shortlist = []

        # Filter by regime-specific confidence threshold and cap
        passed = [s for s in shortlist if s.get("signal") == "BULLISH" and float(s.get("conf", 0)) >= conf_threshold]
        passed = passed[:max_pass]  # enforce max passes for this regime
        tickers_passed = [s.get("sym") for s in passed]
        logger.info(f"[BATCH SCREEN] {len(candidates)} candidates → {len(passed)} passed (regime={regime}, conf>={conf_threshold}, max={max_pass}): {tickers_passed}")
        self.broadcast({"type": "batch_screen_complete", "passed": tickers_passed, "ts": datetime.now(timezone.utc).isoformat()})
        return passed

    async def _batch_deep_analysis(
        self, shortlist: list[dict], candidates_md: dict[str, dict],
        regime: str, regime_data: dict, portfolio_context: dict,
    ) -> list[dict]:
        """Phase B: One DEEP call produces full trade plans for shortlisted tickers."""
        self.broadcast({"type": "batch_analysis_start", "tickers": [s["sym"] for s in shortlist], "ts": datetime.now(timezone.utc).isoformat()})

        # Build detailed data only for shortlisted tickers
        detailed = []
        for s in shortlist:
            sym = s["sym"]
            md = candidates_md.get(sym, {})
            detailed.append({
                "sym": sym,
                "px": round(md.get("price", 0), 2),
                "rsi": round(md.get("rsi", 50), 1),
                "ema_bull": bool(md.get("ema_bullish")),
                "mom5": round(md.get("momentum_5d", 0), 2),
                "mom20": round(md.get("momentum_20d", 0), 2),
                "vol_r": round(md.get("volume_ratio", 1), 2),
                "atr": round(md.get("atr", 0), 2),
                "atr_pct": round(md.get("atr_pct", 0), 2),
                "screen_conf": s.get("conf", 0.7),
                "screen_edge": s.get("edge", ""),
            })

        compact = json.dumps(detailed, separators=(",", ":"))
        sys_p = (
            "You are MoonshotX Portfolio Manager & Trader. For each shortlisted stock, "
            "perform bull/bear analysis, create an entry plan (entry, stop-loss, take-profit), "
            "and make a final APPROVE/REJECT decision. Be disciplined — only APPROVE high-conviction setups. "
            "Return ONLY valid JSON."
        )
        user_p = f"""Analyze and create trade plans for these {len(detailed)} shortlisted stocks.

Market: regime={regime}, VIX={regime_data.get('vix', 20):.1f}, F&G={regime_data.get('fear_greed', 50):.0f}/100
Portfolio: {portfolio_context.get('open_positions', 0)} open positions, daily_pnl=${portfolio_context.get('daily_pnl', 0):.2f}, value=${portfolio_context.get('portfolio_value', 0):,.0f}

Stocks:
{compact}

For EACH stock, analyze:
1. Bull case vs bear case (2 sentences each)
2. Entry plan: stop_loss = 1.5× ATR below price, take_profit = 2.5× risk above price
3. Risk assessment and final decision

APPROVE only if: clear directional edge + favorable risk/reward (R:R >= 1.67) + regime-appropriate.

Return ONLY this JSON array:
[{{
  "sym": "TICKER",
  "decision": "APPROVE" or "REJECT",
  "conviction": 0.0-1.0,
  "entry_price": 0.00,
  "stop_loss": 0.00,
  "take_profit": 0.00,
  "reasoning": "2-sentence rationale",
  "bull_case": "1-sentence",
  "bear_case": "1-sentence",
  "risk_level": "LOW" or "MEDIUM" or "HIGH"
}}]"""

        result = await self._call_llm(sys_p, user_p, DEEP_MODEL, timeout=45, max_tokens=4096)
        # Parse result — could be list directly or wrapped in a dict
        if isinstance(result, dict):
            decisions = result.get("decisions", result.get("trades", result.get("results", [])))
            if not isinstance(decisions, list):
                decisions = [result] if result.get("sym") else []
        elif isinstance(result, list):
            decisions = result
        else:
            decisions = []

        approved = [d for d in decisions if d.get("decision") == "APPROVE"]
        logger.info(f"[BATCH DEEP] {len(shortlist)} analyzed → {len(approved)} approved: {[d.get('sym') for d in approved]}")
        self.broadcast({"type": "batch_analysis_complete", "approved": [d.get("sym") for d in approved], "ts": datetime.now(timezone.utc).isoformat()})
        return decisions

    async def run_batch(
        self,
        candidates_with_data: list[dict],
        regime: str,
        regime_data: dict,
        portfolio_context: dict,
    ) -> list[dict]:
        """Run the full batched pipeline: 2 LLM calls for N candidates.
        
        Args:
            candidates_with_data: list of {"ticker": str, "md": dict, "bayesian_score": float}
            regime, regime_data, portfolio_context: context dicts
        Returns:
            list of decision dicts (same schema as run() output)
        """
        batch_id = str(uuid.uuid4())[:8]
        start_time = time.time()
        n = len(candidates_with_data)
        logger.info(f"[BATCH {batch_id}] Starting batched pipeline for {n} candidates (old method would use {n * 12} LLM calls)")

        self.broadcast({
            "type": "batch_pipeline_start",
            "batch_id": batch_id,
            "candidate_count": n,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        # Build compact data + keep full md for Phase B
        compact_list = []
        candidates_md = {}
        for c in candidates_with_data:
            ticker = c["ticker"]
            md = c["md"]
            compact_list.append(self._compact_ticker_data(ticker, md))
            candidates_md[ticker] = md

        # ── Phase A: Batch Screen (1 QUICK call) ─────────────────────────
        shortlist = await self._batch_screen(compact_list, regime, regime_data)
        if not shortlist:
            duration = round(time.time() - start_time, 1)
            logger.info(f"[BATCH {batch_id}] No candidates passed screening ({duration}s, 1 LLM call)")
            return []

        # ── Phase B: Batch Deep Analysis (1 DEEP call) ───────────────────
        decisions = await self._batch_deep_analysis(shortlist, candidates_md, regime, regime_data, portfolio_context)

        # ── Build result dicts matching run() output schema ──────────────
        results = []
        for d in decisions:
            sym = d.get("sym", "")
            md = candidates_md.get(sym, {})
            bay = next((c.get("bayesian_score", 0.5) for c in candidates_with_data if c["ticker"] == sym), 0.5)
            decision_id = str(uuid.uuid4())
            results.append({
                "decision_id": decision_id,
                "ticker": sym,
                "decision": d.get("decision", "REJECT"),
                "plan": {
                    "entry_price": d.get("entry_price", md.get("price", 0)),
                    "stop_loss": d.get("stop_loss", 0),
                    "take_profit": d.get("take_profit", 0),
                },
                "verdict": {
                    "verdict": "BULLISH" if d.get("decision") == "APPROVE" else "NEUTRAL",
                    "conviction_score": d.get("conviction", 0.5),
                    "reasoning": d.get("reasoning", ""),
                    "risk_level": d.get("risk_level", "MEDIUM"),
                },
                "reasoning": d.get("reasoning", ""),
                "bayesian_score": bay,
                "regime": regime,
                "agents": [
                    {"agent": "Batch Screener", "result": {"signal": "BULLISH", "confidence": d.get("conviction", 0.5)}},
                    {"agent": "Batch Analyst", "result": {"bull_case": d.get("bull_case", ""), "bear_case": d.get("bear_case", "")}},
                    {"agent": "Batch PM", "result": {"decision": d.get("decision"), "reasoning": d.get("reasoning", "")}},
                ],
                "duration_s": round(time.time() - start_time, 1),
                "llm_cost": round(self.llm_cost_today, 4),
                "batch_id": batch_id,
            })

        duration = round(time.time() - start_time, 1)
        llm_calls_saved = max(0, n * 12 - 2)
        logger.info(f"[BATCH {batch_id}] Complete: {n} candidates → {len([r for r in results if r['decision'] == 'APPROVE'])} approved in {duration}s (2 LLM calls, saved ~{llm_calls_saved})")

        self.broadcast({
            "type": "batch_pipeline_complete",
            "batch_id": batch_id,
            "candidates": n,
            "approved": [r["ticker"] for r in results if r["decision"] == "APPROVE"],
            "duration_s": duration,
            "llm_calls_saved": llm_calls_saved,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        return results

    # ─── FULL PIPELINE (single-ticker, kept for compatibility) ────────────────

    async def run(
        self,
        ticker: str,
        market_data: dict,
        regime: str,
        portfolio_context: dict,
        bayesian_score: float = 0.5,
    ) -> dict:
        """Run the full 12-agent pipeline and return a trading decision."""
        decision_id = str(uuid.uuid4())
        start_time = time.time()
        agent_log = []

        md = {**market_data, "regime": regime}

        self.broadcast({
            "type": "pipeline_start",
            "ticker": ticker,
            "decision_id": decision_id,
            "ts": datetime.now(timezone.utc).isoformat(),
        })

        try:
            # ── PHASE 1: Parallel Analysts ────────────────────────────────
            tech_task = asyncio.create_task(self.technical_analyst(ticker, md))
            news_task = asyncio.create_task(self.news_analyst(ticker, md))
            sent_task = asyncio.create_task(self.sentiment_analyst(ticker, md))
            fund_task = asyncio.create_task(self.fundamentals_analyst(ticker, md))

            done, pending = await asyncio.wait(
                [tech_task, news_task, sent_task, fund_task], timeout=18
            )
            for t in pending:
                t.cancel()

            if tech_task not in done or tech_task.exception():
                logger.warning(f"{ticker}: Technical analyst failed — aborting")
                return {
                    "decision_id": decision_id, "ticker": ticker, "decision": "REJECT",
                    "reason": "technical_failed", "bayesian_score": bayesian_score,
                    "regime": regime, "reasoning": "Technical analyst failed — no data",
                    "agents": [], "duration_s": round(time.time() - start_time, 1),
                }

            reports = {}
            for task, name in [(tech_task, "technical"), (news_task, "news"), (sent_task, "sentiment"), (fund_task, "fundamentals")]:
                if task in done and not task.exception():
                    reports[name] = task.result()
                    agent_log.append({"agent": name.title() + " Analyst", "result": task.result()})

            # ── PHASE 2: Bull / Bear Debate (2 rounds) ───────────────────
            bull_case = ""
            bear_case = ""
            debate_history = []
            for round_num in range(1, 3):
                bull_r, bear_r = await asyncio.gather(
                    self.bull_researcher(ticker, reports, round_num, bear_case),
                    self.bear_researcher(ticker, reports, round_num, bull_case),
                )
                bull_case = bull_r.get("bull_case", "")
                bear_case = bear_r.get("bear_case", "")
                debate_history.append({"bull": bull_case, "bear": bear_case})
                agent_log.extend([
                    {"agent": "Bull Researcher", "result": bull_r},
                    {"agent": "Bear Researcher", "result": bear_r},
                ])

            # ── PHASE 3: Research Manager ────────────────────────────────
            verdict = await self.research_manager(ticker, debate_history, reports, regime)
            agent_log.append({"agent": "Research Manager", "result": verdict})

            if verdict.get("recommended_action") != "BUY":
                return {
                    "decision_id": decision_id, "ticker": ticker, "decision": "REJECT",
                    "reason": "research_manager_skip",
                    "reasoning": verdict.get("reasoning", ""),
                    "bayesian_score": bayesian_score,
                    "regime": regime,
                    "verdict": verdict,
                    "agents": agent_log, "duration_s": round(time.time() - start_time, 1),
                }

            # ── PHASE 4: Trader ──────────────────────────────────────────
            plan = await self.trader(ticker, verdict, md)
            agent_log.append({"agent": "Trader", "result": plan})

            if plan.get("action") != "BUY":
                return {
                    "decision_id": decision_id, "ticker": ticker, "decision": "REJECT",
                    "reason": "trader_skip",
                    "reasoning": plan.get("reasoning", "Trader declined entry"),
                    "bayesian_score": bayesian_score,
                    "regime": regime,
                    "verdict": verdict,
                    "agents": agent_log, "duration_s": round(time.time() - start_time, 1),
                }

            # ── PHASE 5: Risk Debate (parallel) ─────────────────────────
            agg_r, neu_r, con_r = await asyncio.gather(
                self.aggressive_analyst(ticker, plan),
                self.neutral_analyst(ticker, plan),
                self.conservative_analyst(ticker, plan),
            )
            risk_debate = {"aggressive": agg_r, "neutral": neu_r, "conservative": con_r}
            agent_log.extend([
                {"agent": "Aggressive Analyst", "result": agg_r},
                {"agent": "Neutral Analyst", "result": neu_r},
                {"agent": "Conservative Analyst", "result": con_r},
            ])

            # ── PHASE 6: Portfolio Manager ───────────────────────────────
            pm_decision = await self.portfolio_manager(ticker, plan, risk_debate, portfolio_context)
            agent_log.append({"agent": "Portfolio Manager", "result": pm_decision})

            decision = pm_decision.get("decision", "REJECT")
            duration = round(time.time() - start_time, 1)

            result = {
                "decision_id": decision_id,
                "ticker": ticker,
                "decision": decision,
                "plan": {
                    "entry_price": plan.get("entry_price", md.get("price", 0)),
                    "stop_loss": pm_decision.get("final_stop_loss", plan.get("stop_loss", 0)),
                    "take_profit": pm_decision.get("final_take_profit", plan.get("take_profit", 0)),
                    "size": pm_decision.get("final_size_shares", 1),
                    "entry_type": plan.get("entry_type", "market"),
                },
                "verdict": verdict,
                "reasoning": pm_decision.get("reasoning", ""),
                "bayesian_score": bayesian_score,
                "regime": regime,
                "agents": agent_log,
                "duration_s": duration,
                "llm_cost": round(self.llm_cost_today, 4),
            }

            self.broadcast({
                "type": "pipeline_complete",
                "ticker": ticker,
                "decision": decision,
                "decision_id": decision_id,
                "duration_s": duration,
            })

            return result

        except Exception as e:
            logger.error(f"Pipeline error for {ticker}: {e}")
            return {"decision_id": decision_id, "ticker": ticker, "decision": "REJECT", "reason": str(e), "agents": agent_log}
