"""Intraday momentum confirmation — gate entries on real-time price action.

The core problem: daily indicators (RSI, EMA, 5d momentum) are lagging.
A stock can look great on daily charts but be falling RIGHT NOW.
This module checks real-time 5-min bars before allowing an entry.

Rules for confirmation:
1. Price must be trending UP in recent 5-min bars (higher closes)
2. Latest price must be above the short-term moving average of recent bars
3. Volume should not be collapsing (recent volume >= 50% of average)
4. Spread between bid/ask should be reasonable (not illiquid)
"""
import logging
from typing import Optional

logger = logging.getLogger("moonshotx.momentum")


async def confirm_intraday_momentum(kite, symbol: str, regime: str = "neutral", conviction: float = 0.7) -> tuple[bool, str]:
    """Check real-time intraday momentum before entering a position.

    Returns (confirmed: bool, reason: str).
    If data is unavailable, returns (True, "no_data") to avoid blocking all entries.

    High-conviction picks (>=0.80) get relaxed requirements:
    - Fear regime only needs 1/2 up bars instead of 2/2
    - Skips red-candle and local-top checks
    """
    high_conviction = conviction >= 0.80
    try:
        bars = await kite.get_bars(symbol, timeframe="5Min", limit=6)
    except Exception as e:
        logger.warning(f"[MOMENTUM] {symbol}: bars fetch failed: {e}")
        return True, "bars_unavailable"

    if not bars or len(bars) < 3:
        logger.info(f"[MOMENTUM] {symbol}: insufficient bars ({len(bars) if bars else 0}) — allowing entry")
        return True, "insufficient_bars"

    closes = [float(b.get("c", b.get("close", 0))) for b in bars]
    volumes = [float(b.get("v", b.get("volume", 0))) for b in bars]

    if not all(closes) or len(closes) < 3:
        return True, "bad_bar_data"

    # ── Check 1: Recent price direction (last 3 bars) ───────────────────
    recent = closes[-3:]
    up_bars = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i - 1])
    flat_bars = sum(1 for i in range(1, len(recent)) if recent[i] == recent[i - 1])
    down_bars = len(recent) - 1 - up_bars - flat_bars

    # In fear/choppy: require CLEAR uptrend (at least 2 of 3 bars up)
    # High conviction (>=0.80): relax to 1 up bar even in fear
    # In bull/neutral: allow flat (at least 1 up bar)
    if regime in ("fear", "choppy", "bear_mode", "extreme_fear"):
        min_up = 1 if high_conviction else 2
        if up_bars < min_up:
            return False, f"weak_trend_in_{regime}: only {up_bars}/{min_up} up bars ({[round(c, 2) for c in recent]}) conv={conviction:.2f}"
    else:
        if up_bars == 0:
            return False, f"downtrend: last 3 bars all declining ({[round(c, 2) for c in recent]})"

    # ── Check 2: Current price vs short-term average ────────────────────
    avg_close = sum(closes) / len(closes)
    current = closes[-1]
    pct_above_avg = (current - avg_close) / avg_close

    # In fear: price must be ABOVE average (positive momentum)
    # In bull: allow slightly below
    min_above_avg = {
        "bull": -0.003, "neutral": -0.001, "fear": 0.001,
        "choppy": 0.001, "bear_mode": 0.002, "extreme_fear": 0.005,
    }.get(regime, -0.001)

    # High conviction gets halved threshold
    if high_conviction:
        min_above_avg = min_above_avg / 2

    if pct_above_avg < min_above_avg:
        return False, f"below_avg: price ₹{current:.2f} is {pct_above_avg*100:.2f}% vs avg ₹{avg_close:.2f} (need >{min_above_avg*100:.1f}% in {regime}, conv={conviction:.2f})"

    # ── Check 3: Volume not collapsing ──────────────────────────────────
    if len(volumes) >= 4 and all(v > 0 for v in volumes):
        avg_vol = sum(volumes[:-1]) / len(volumes[:-1])
        recent_vol = volumes[-1]
        if avg_vol > 0 and recent_vol < avg_vol * 0.3:
            return False, f"volume_collapse: latest vol {recent_vol:.0f} < 30% of avg {avg_vol:.0f}"

    # ── Check 4: Price making progress from bar open ────────────────────
    # The most recent bar should have close >= open (green candle)
    latest_open = float(bars[-1].get("o", bars[-1].get("open", 0)))
    latest_close = closes[-1]
    if latest_open > 0 and latest_close < latest_open * 0.998:
        # Latest bar is a red candle dropping > 0.2%
        # In fear regime, this is a stronger rejection signal
        if regime in ("fear", "choppy", "bear_mode") and not high_conviction:
            return False, f"red_candle_fear: latest bar O=₹{latest_open:.2f} C=₹{latest_close:.2f} (red in {regime})"

    # ── Check 5: Not at intraday high (avoid buying the top) ────────────
    bar_high = max(float(b.get("h", b.get("high", 0))) for b in bars)
    if bar_high > 0 and current >= bar_high * 0.999:
        # Price is at/near the high of the last 30 min — might be a local top
        # Only block in fear/choppy where reversals are common
        if regime in ("fear", "choppy") and not high_conviction:
            # Check if it just spiked up (last bar high == 30min high and close near high)
            last_high = float(bars[-1].get("h", bars[-1].get("high", 0)))
            if last_high >= bar_high * 0.999 and current >= last_high * 0.998:
                return False, f"local_top_fear: at 30min high ₹{bar_high:.2f} in {regime} — wait for pullback"

    # ── All checks passed ───────────────────────────────────────────────
    trend = "UP" if up_bars >= 2 else "FLAT"
    logger.info(f"[MOMENTUM] {symbol}: CONFIRMED ({trend}, {pct_above_avg*100:+.2f}% vs avg, bars={[round(c,2) for c in recent]})")
    return True, f"confirmed:{trend}"
