"""Derivative strategy engine for NSE F&O.

Strategies implemented (regime-aware):
  FEAR   (VIX > 20) : Short Strangle with wide wings → collect premium in high IV
                      Bull Put Spread                → directional + defined risk
  NEUTRAL(VIX 14-20): Iron Condor                   → range-bound theta decay
                      Short Strangle                 → IV collection
  GREED  (VIX < 14) : Bull Call Spread              → directional, low cost
                      Calendar Spread                → IV arbitrage

All strategies work on NIFTY weekly options (lot size 25).
"""
import logging
from dataclasses import dataclass, field
from typing import Optional

from derivatives.chain import OptionChain, OptionLeg
from derivatives.greeks import calculate_greeks, Greeks

logger = logging.getLogger("moonshotx.derivatives.strategies")

# ── Strategy parameters ───────────────────────────────────────────────────────
IRON_CONDOR_WING_WIDTH   = 3     # strikes away from short for long protection
STRANGLE_DELTA_TARGET    = 0.20  # sell ~20-delta options for short strangle
MIN_PREMIUM_COLLECTED    = 50    # ₹ minimum net premium per lot to enter
MIN_IV                   = 0.12  # don't sell premium when IV < 12%
MAX_IV_FOR_BUY_STRATEGY  = 0.25  # don't buy spreads when IV > 25% (expensive)
MIN_OI                   = 500   # minimum open interest for liquidity check
MIN_VOLUME               = 100   # minimum daily volume


@dataclass
class StrategyLeg:
    option_type: str      # "CE" or "PE"
    strike: float
    side: str             # "buy" or "sell"
    ltp: float
    iv: float
    delta: float
    oi: int
    lot_size: int
    tradingsymbol: str = ""
    instrument_token: Optional[int] = None


@dataclass
class StrategySignal:
    name: str                          # e.g. "iron_condor", "bull_put_spread"
    legs: list[StrategyLeg] = field(default_factory=list)
    net_premium: float = 0.0          # positive = credit received, negative = debit paid
    max_profit: float = 0.0           # ₹ per lot
    max_loss: float = 0.0             # ₹ per lot (positive = limited loss)
    breakeven_upper: float = 0.0
    breakeven_lower: float = 0.0
    net_delta: float = 0.0
    net_theta: float = 0.0            # ₹/day
    net_vega: float = 0.0
    confidence: float = 0.0           # 0-1
    rationale: str = ""
    regime: str = ""


def _liquid(leg: Optional[OptionLeg]) -> bool:
    if leg is None:
        return False
    return leg.oi >= MIN_OI and leg.volume >= MIN_VOLUME and leg.ltp > 0


def _nearest_strike(chain: OptionChain, spot: float, offset_strikes: int, option_type: str) -> Optional[OptionLeg]:
    """Find leg at ATM ± offset_strikes intervals."""
    interval = chain.strike_interval
    target = round(spot / interval) * interval + offset_strikes * interval
    lst = chain.calls if option_type == "CE" else chain.puts
    return next((l for l in lst if l.strike == target), None)


def _find_delta_strike(chain: OptionChain, target_delta: float, option_type: str, spot: float, T: float) -> Optional[OptionLeg]:
    """Find option leg closest to target_delta."""
    lst = chain.calls if option_type == "CE" else chain.puts
    best = None
    best_diff = float("inf")
    for leg in lst:
        if not _liquid(leg):
            continue
        iv = leg.iv / 100.0 if leg.iv > 1 else leg.iv
        if iv <= 0:
            continue
        g = calculate_greeks(option_type, spot, leg.strike, T, iv, lot_size=1)
        d = abs(abs(g.delta) - target_delta)
        if d < best_diff:
            best_diff = d
            best = leg
    return best


# ── Strategy builders ─────────────────────────────────────────────────────────

def build_iron_condor(
    chain: OptionChain, T: float, regime: str
) -> Optional[StrategySignal]:
    """Iron Condor: Sell OTM call + OTM put, buy further OTM wings for protection.

    Best in NEUTRAL regime, VIX 14-20, range-bound market.
    """
    spot = chain.spot
    interval = chain.strike_interval
    lot = chain.lot_size

    # Short legs: ~20-delta (2-3 intervals OTM)
    short_call = _find_delta_strike(chain, STRANGLE_DELTA_TARGET, "CE", spot, T)
    short_put  = _find_delta_strike(chain, STRANGLE_DELTA_TARGET, "PE", spot, T)

    if not short_call or not short_put:
        logger.debug("[STRATEGY] Iron condor: no liquid short legs found")
        return None

    # Long wings: IRON_CONDOR_WING_WIDTH strikes further OTM
    long_call_strike = short_call.strike + IRON_CONDOR_WING_WIDTH * interval
    long_put_strike  = short_put.strike  - IRON_CONDOR_WING_WIDTH * interval

    long_call = next((l for l in chain.calls if l.strike == long_call_strike), None)
    long_put  = next((l for l in chain.puts  if l.strike == long_put_strike),  None)

    if not _liquid(long_call) or not _liquid(long_put):
        logger.debug("[STRATEGY] Iron condor: no liquid long wings")
        return None

    # P&L calculation (per lot)
    net_credit = (short_call.ltp + short_put.ltp - long_call.ltp - long_put.ltp) * lot
    if net_credit < MIN_PREMIUM_COLLECTED:
        logger.debug(f"[STRATEGY] Iron condor: net credit ₹{net_credit:.0f} < min ₹{MIN_PREMIUM_COLLECTED}")
        return None

    wing_width = IRON_CONDOR_WING_WIDTH * interval * lot
    max_loss = wing_width - net_credit

    # Greeks (net = sell short, buy long)
    def g(leg: OptionLeg, otype: str):
        iv = leg.iv / 100.0 if leg.iv > 1 else leg.iv
        return calculate_greeks(otype, spot, leg.strike, T, iv or 0.18, lot_size=lot)

    gsc = g(short_call, "CE"); glc = g(long_call, "CE")
    gsp = g(short_put,  "PE"); glp = g(long_put,  "PE")

    net_delta = -gsc.delta + glc.delta - gsp.delta + glp.delta
    net_theta = -gsc.theta + glc.theta - gsp.theta + glp.theta
    net_vega  = -gsc.vega  + glc.vega  - gsp.vega  + glp.vega

    confidence = min(0.85, 0.5 + (net_credit / wing_width) * 0.5)

    legs = [
        StrategyLeg("CE", short_call.strike, "sell", short_call.ltp, short_call.iv, gsc.delta, short_call.oi, lot,
                    instrument_token=short_call.instrument_token),
        StrategyLeg("CE", long_call.strike,  "buy",  long_call.ltp,  long_call.iv,  glc.delta, long_call.oi,  lot,
                    instrument_token=long_call.instrument_token),
        StrategyLeg("PE", short_put.strike,  "sell", short_put.ltp,  short_put.iv,  gsp.delta, short_put.oi,  lot,
                    instrument_token=short_put.instrument_token),
        StrategyLeg("PE", long_put.strike,   "buy",  long_put.ltp,   long_put.iv,   glp.delta, long_put.oi,   lot,
                    instrument_token=long_put.instrument_token),
    ]

    return StrategySignal(
        name="iron_condor",
        legs=legs,
        net_premium=round(net_credit, 2),
        max_profit=round(net_credit, 2),
        max_loss=round(max_loss, 2),
        breakeven_upper=short_call.strike + (net_credit / lot),
        breakeven_lower=short_put.strike  - (net_credit / lot),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 2),
        net_vega=round(net_vega, 2),
        confidence=round(confidence, 2),
        rationale=(
            f"Iron condor: sell {short_put.strike}PE + {short_call.strike}CE, "
            f"buy {long_put.strike}PE + {long_call.strike}CE. "
            f"Net credit ₹{net_credit:.0f}/lot. "
            f"Max loss ₹{max_loss:.0f}. Theta +₹{net_theta:.1f}/day."
        ),
        regime=regime,
    )


def build_short_strangle(
    chain: OptionChain, T: float, regime: str
) -> Optional[StrategySignal]:
    """Short Strangle: Sell OTM call + OTM put. No protection wings.

    Suits FEAR regime (high IV = fat premium) but unlimited risk — use small size.
    """
    spot = chain.spot
    lot  = chain.lot_size

    short_call = _find_delta_strike(chain, STRANGLE_DELTA_TARGET, "CE", spot, T)
    short_put  = _find_delta_strike(chain, STRANGLE_DELTA_TARGET, "PE", spot, T)

    if not short_call or not short_put:
        return None

    net_credit = (short_call.ltp + short_put.ltp) * lot
    if net_credit < MIN_PREMIUM_COLLECTED * 2:
        return None

    iv_ce = short_call.iv / 100.0 if short_call.iv > 1 else short_call.iv
    iv_pe = short_put.iv  / 100.0 if short_put.iv  > 1 else short_put.iv
    gsc = calculate_greeks("CE", spot, short_call.strike, T, iv_ce or 0.20, lot_size=lot)
    gsp = calculate_greeks("PE", spot, short_put.strike,  T, iv_pe or 0.20, lot_size=lot)

    net_theta = -(gsc.theta + gsp.theta)
    net_vega  = -(gsc.vega  + gsp.vega)
    net_delta = -(gsc.delta + gsp.delta)

    legs = [
        StrategyLeg("CE", short_call.strike, "sell", short_call.ltp, short_call.iv, gsc.delta, short_call.oi, lot,
                    instrument_token=short_call.instrument_token),
        StrategyLeg("PE", short_put.strike,  "sell", short_put.ltp,  short_put.iv,  gsp.delta, short_put.oi,  lot,
                    instrument_token=short_put.instrument_token),
    ]

    return StrategySignal(
        name="short_strangle",
        legs=legs,
        net_premium=round(net_credit, 2),
        max_profit=round(net_credit, 2),
        max_loss=float("inf"),
        breakeven_upper=short_call.strike + (net_credit / lot),
        breakeven_lower=short_put.strike  - (net_credit / lot),
        net_delta=round(net_delta, 4),
        net_theta=round(net_theta, 2),
        net_vega=round(net_vega, 2),
        confidence=0.55,
        rationale=(
            f"Short strangle: sell {short_put.strike}PE + {short_call.strike}CE. "
            f"Net credit ₹{net_credit:.0f}/lot. High IV = fat premium. "
            f"Theta +₹{net_theta:.1f}/day. Unlimited risk — size carefully."
        ),
        regime=regime,
    )


def build_bull_put_spread(
    chain: OptionChain, T: float, regime: str
) -> Optional[StrategySignal]:
    """Bull Put Spread: Sell ATM put, buy OTM put. Bullish + defined risk credit spread."""
    spot = chain.spot
    interval = chain.strike_interval
    lot  = chain.lot_size

    # Sell ATM or slightly OTM put
    short_put_strike = round(spot / interval) * interval
    long_put_strike  = short_put_strike - 2 * interval

    short_put = next((l for l in chain.puts if l.strike == short_put_strike), None)
    long_put  = next((l for l in chain.puts if l.strike == long_put_strike),  None)

    if not _liquid(short_put) or not _liquid(long_put):
        return None

    net_credit  = (short_put.ltp - long_put.ltp) * lot
    spread_width = 2 * interval * lot
    max_loss    = spread_width - net_credit

    if net_credit < MIN_PREMIUM_COLLECTED:
        return None

    confidence = min(0.75, 0.5 + (net_credit / spread_width) * 0.4)

    legs = [
        StrategyLeg("PE", short_put.strike, "sell", short_put.ltp, short_put.iv,
                    0.0, short_put.oi, lot, instrument_token=short_put.instrument_token),
        StrategyLeg("PE", long_put.strike,  "buy",  long_put.ltp,  long_put.iv,
                    0.0, long_put.oi,  lot, instrument_token=long_put.instrument_token),
    ]

    return StrategySignal(
        name="bull_put_spread",
        legs=legs,
        net_premium=round(net_credit, 2),
        max_profit=round(net_credit, 2),
        max_loss=round(max_loss, 2),
        breakeven_upper=0.0,
        breakeven_lower=short_put_strike - (net_credit / lot),
        net_delta=0.0,
        net_theta=0.0,
        net_vega=0.0,
        confidence=round(confidence, 2),
        rationale=(
            f"Bull put spread: sell {short_put_strike}PE, buy {long_put_strike}PE. "
            f"Credit ₹{net_credit:.0f}/lot. Max loss ₹{max_loss:.0f}. "
            f"Profitable if NIFTY stays above {short_put_strike}."
        ),
        regime=regime,
    )


def build_bull_call_spread(
    chain: OptionChain, T: float, regime: str
) -> Optional[StrategySignal]:
    """Bull Call Spread: Buy ATM call, sell OTM call. Low-cost bullish debit spread."""
    spot = chain.spot
    interval = chain.strike_interval
    lot  = chain.lot_size

    atm_call_strike  = round(spot / interval) * interval
    otm_call_strike  = atm_call_strike + 2 * interval

    atm_call = next((l for l in chain.calls if l.strike == atm_call_strike), None)
    otm_call = next((l for l in chain.calls if l.strike == otm_call_strike),  None)

    if not _liquid(atm_call) or not _liquid(otm_call):
        return None

    net_debit    = (atm_call.ltp - otm_call.ltp) * lot
    spread_width = 2 * interval * lot
    max_profit   = spread_width - net_debit

    if net_debit <= 0 or max_profit <= 0:
        return None

    rr = max_profit / net_debit   # reward/risk ratio
    confidence = min(0.70, 0.4 + rr * 0.1)

    legs = [
        StrategyLeg("CE", atm_call.strike, "buy",  atm_call.ltp, atm_call.iv,
                    0.0, atm_call.oi, lot, instrument_token=atm_call.instrument_token),
        StrategyLeg("CE", otm_call.strike, "sell", otm_call.ltp, otm_call.iv,
                    0.0, otm_call.oi, lot, instrument_token=otm_call.instrument_token),
    ]

    return StrategySignal(
        name="bull_call_spread",
        legs=legs,
        net_premium=-round(net_debit, 2),   # negative = debit paid
        max_profit=round(max_profit, 2),
        max_loss=round(net_debit, 2),
        breakeven_upper=atm_call_strike + (net_debit / lot),
        breakeven_lower=0.0,
        net_delta=0.0,
        net_theta=0.0,
        net_vega=0.0,
        confidence=round(confidence, 2),
        rationale=(
            f"Bull call spread: buy {atm_call_strike}CE, sell {otm_call_strike}CE. "
            f"Debit ₹{net_debit:.0f}/lot. Max profit ₹{max_profit:.0f} "
            f"(R:R {rr:.1f}x). Profitable if NIFTY > {atm_call_strike + net_debit/lot:.0f}."
        ),
        regime=regime,
    )


# ── Regime-aware strategy selector ───────────────────────────────────────────

def select_strategy(
    chain: OptionChain,
    T: float,              # years to expiry
    regime: str,           # "fear" | "neutral" | "greed" | "extreme_fear"
    india_vix: float,
    pcr: float,
) -> Optional[StrategySignal]:
    """Select and build the best strategy for current regime.

    Returns the highest-confidence StrategySignal or None if nothing qualifies.
    """
    regime_lower = regime.lower()
    candidates: list[StrategySignal] = []

    # --- FEAR / EXTREME_FEAR (VIX > 20): sell premium (high IV = fat premium)
    if "fear" in regime_lower or india_vix > 20:
        # Iron condor preferred (defined risk) over naked strangle
        ic = build_iron_condor(chain, T, regime)
        if ic:
            ic.confidence *= 0.95   # slightly less confident in fear
            candidates.append(ic)
        # Bull put spread if PCR > 1 (more puts = market expecting recovery)
        if pcr >= 1.0:
            bps = build_bull_put_spread(chain, T, regime)
            if bps:
                candidates.append(bps)
        # Short strangle only in extreme_fear (IV is highest = most premium)
        if "extreme" in regime_lower or india_vix > 28:
            ss = build_short_strangle(chain, T, regime)
            if ss:
                ss.confidence *= 0.70  # riskier, discount confidence
                candidates.append(ss)

    # --- NEUTRAL (VIX 14-20): iron condor is king, theta decay
    elif "neutral" in regime_lower or 14 <= india_vix <= 20:
        ic = build_iron_condor(chain, T, regime)
        if ic:
            ic.confidence = min(0.88, ic.confidence * 1.05)
            candidates.append(ic)
        ss = build_short_strangle(chain, T, regime)
        if ss:
            candidates.append(ss)

    # --- GREED (VIX < 14): directional spreads, buy gamma
    elif "greed" in regime_lower or india_vix < 14:
        bcs = build_bull_call_spread(chain, T, regime)
        if bcs:
            candidates.append(bcs)
        bps = build_bull_put_spread(chain, T, regime)
        if bps:
            candidates.append(bps)

    # Fallback: always try iron condor
    if not candidates:
        ic = build_iron_condor(chain, T, regime)
        if ic:
            candidates.append(ic)

    if not candidates:
        logger.info(f"[STRATEGY] No qualifying strategy for regime={regime} VIX={india_vix:.1f}")
        return None

    best = max(candidates, key=lambda s: s.confidence)
    logger.info(
        f"[STRATEGY] Selected: {best.name} | credit=₹{best.net_premium:.0f} "
        f"| confidence={best.confidence:.2f} | regime={regime}"
    )
    return best
