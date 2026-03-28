"""NSE Options Chain fetcher.

Primary source : NSE public JSON API (no auth required, needs browser headers)
Fallback source: Zerodha Kite quote API (requires valid access_token)

Returns a normalised OptionChain dataclass with CE/PE data per strike.
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

logger = logging.getLogger("moonshotx.derivatives.chain")

# ── NSE public API ────────────────────────────────────────────────────────────
NSE_CHAIN_URL = "https://www.nseindia.com/api/option-chain-indices"
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
    "Connection": "keep-alive",
}

# Lot sizes (shares per lot) — update as NSE changes them
LOT_SIZES = {
    "NIFTY":     25,
    "BANKNIFTY": 15,
    "FINNIFTY":  40,
    "MIDCPNIFTY": 75,
}

# Strike intervals
STRIKE_INTERVALS = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
    "FINNIFTY":  50,
    "MIDCPNIFTY": 25,
}


@dataclass
class OptionLeg:
    strike: float
    option_type: str          # "CE" or "PE"
    ltp: float                # last traded price
    oi: int                   # open interest (contracts)
    oi_change: int            # OI change from prev day
    volume: int
    iv: float                 # implied volatility (%)
    bid: float
    ask: float
    delta: float = 0.0
    gamma: float = 0.0
    theta: float = 0.0
    vega: float = 0.0
    instrument_token: Optional[int] = None
    tradingsymbol: Optional[str] = None


@dataclass
class OptionChain:
    symbol: str
    spot: float
    expiry: str               # "DDMMMYY"
    atm_strike: float
    lot_size: int
    strike_interval: int
    calls: list[OptionLeg] = field(default_factory=list)
    puts: list[OptionLeg] = field(default_factory=list)
    pcr: float = 0.0          # put-call ratio by OI
    timestamp: float = field(default_factory=time.time)

    def strikes_near_atm(self, n: int = 5) -> list[float]:
        """Return n strikes on each side of ATM."""
        strikes = sorted({leg.strike for leg in self.calls + self.puts})
        if not strikes:
            return []
        atm_idx = min(range(len(strikes)), key=lambda i: abs(strikes[i] - self.atm_strike))
        lo = max(0, atm_idx - n)
        hi = min(len(strikes), atm_idx + n + 1)
        return strikes[lo:hi]

    def get_leg(self, strike: float, option_type: str) -> Optional[OptionLeg]:
        lst = self.calls if option_type == "CE" else self.puts
        return next((l for l in lst if l.strike == strike), None)


# ── NSE chain fetch ───────────────────────────────────────────────────────────

async def _get_nse_cookies(client: httpx.AsyncClient) -> None:
    """Hit the NSE homepage first to get session cookies."""
    try:
        await client.get("https://www.nseindia.com", headers=NSE_HEADERS, timeout=10)
    except Exception:
        pass


async def fetch_chain_nse(symbol: str = "NIFTY", expiry_date: str = "") -> Optional[OptionChain]:
    """Fetch option chain from NSE public API.

    `expiry_date` format: 'DD-Mon-YYYY' e.g. '27-Mar-2025'
    If empty, returns the nearest expiry chain.
    """
    params = {"symbol": symbol.upper()}
    if expiry_date:
        params["expiryDate"] = expiry_date

    async with httpx.AsyncClient(follow_redirects=True) as client:
        await _get_nse_cookies(client)
        try:
            resp = await client.get(
                NSE_CHAIN_URL, params=params, headers=NSE_HEADERS, timeout=15
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.warning(f"[CHAIN] NSE fetch failed for {symbol}: {e}")
            return None

    records = data.get("records", {})
    filtered = data.get("filtered", {})
    spot = float(records.get("underlyingValue", 0))

    if spot <= 0:
        logger.warning(f"[CHAIN] NSE returned spot=0 for {symbol}")
        return None

    # Pick expiry: first in list if not specified
    expiry_list = records.get("expiryDates", [])
    if not expiry_list:
        return None
    chosen_expiry = expiry_list[0]   # nearest weekly expiry

    # Parse strikes
    strike_interval = STRIKE_INTERVALS.get(symbol.upper(), 50)
    atm_strike = round(spot / strike_interval) * strike_interval

    calls: list[OptionLeg] = []
    puts: list[OptionLeg] = []

    for row in filtered.get("data", []):
        strike = float(row.get("strikePrice", 0))
        if strike <= 0:
            continue

        ce_data = row.get("CE", {})
        pe_data = row.get("PE", {})

        if ce_data:
            calls.append(OptionLeg(
                strike=strike,
                option_type="CE",
                ltp=float(ce_data.get("lastPrice", 0)),
                oi=int(ce_data.get("openInterest", 0)),
                oi_change=int(ce_data.get("changeinOpenInterest", 0)),
                volume=int(ce_data.get("totalTradedVolume", 0)),
                iv=float(ce_data.get("impliedVolatility", 0)),
                bid=float(ce_data.get("bidprice", 0)),
                ask=float(ce_data.get("askPrice", 0)),
            ))

        if pe_data:
            puts.append(OptionLeg(
                strike=strike,
                option_type="PE",
                ltp=float(pe_data.get("lastPrice", 0)),
                oi=int(pe_data.get("openInterest", 0)),
                oi_change=int(pe_data.get("changeinOpenInterest", 0)),
                volume=int(pe_data.get("totalTradedVolume", 0)),
                iv=float(pe_data.get("impliedVolatility", 0)),
                bid=float(pe_data.get("bidprice", 0)),
                ask=float(pe_data.get("askPrice", 0)),
            ))

    # PCR by total OI
    total_call_oi = sum(l.oi for l in calls)
    total_put_oi  = sum(l.oi for l in puts)
    pcr = round(total_put_oi / total_call_oi, 3) if total_call_oi > 0 else 0.0

    lot_size = LOT_SIZES.get(symbol.upper(), 25)

    logger.info(
        f"[CHAIN] {symbol} spot={spot:.0f} atm={atm_strike:.0f} "
        f"expiry={chosen_expiry} calls={len(calls)} puts={len(puts)} PCR={pcr}"
    )

    return OptionChain(
        symbol=symbol.upper(),
        spot=spot,
        expiry=chosen_expiry,
        atm_strike=atm_strike,
        lot_size=lot_size,
        strike_interval=strike_interval,
        calls=calls,
        puts=puts,
        pcr=pcr,
    )


# ── Kite instrument lookup ────────────────────────────────────────────────────

async def lookup_kite_instruments(kite, symbol: str, expiry_str: str) -> dict[tuple, int]:
    """Return {(strike, option_type): instrument_token} from Kite NFO instruments.

    `expiry_str`: 'DDMMMYY' e.g. '27MAR25'
    """
    try:
        instruments = await asyncio.to_thread(kite._kite.instruments, "NFO")
    except Exception as e:
        logger.warning(f"[CHAIN] Kite instruments fetch failed: {e}")
        return {}

    mapping = {}
    sym_upper = symbol.upper()
    for inst in instruments:
        ts = inst.get("tradingsymbol", "")
        if not ts.startswith(sym_upper):
            continue
        # tradingsymbol format: NIFTY25MAR2522500CE
        otype = ts[-2:]   # "CE" or "PE"
        if otype not in ("CE", "PE"):
            continue
        try:
            strike = float(inst.get("strike", 0))
        except (ValueError, TypeError):
            continue
        expiry = inst.get("expiry", "")
        if isinstance(expiry, str) and expiry.upper() != expiry_str.upper():
            continue
        mapping[(strike, otype)] = inst.get("instrument_token")

    logger.info(f"[CHAIN] Kite instruments mapped: {len(mapping)} legs for {symbol} {expiry_str}")
    return mapping


async def enrich_chain_with_kite_tokens(
    chain: OptionChain, kite, expiry_str: str
) -> OptionChain:
    """Add Zerodha instrument_token and tradingsymbol to each leg."""
    token_map = await lookup_kite_instruments(kite, chain.symbol, expiry_str)
    for leg in chain.calls + chain.puts:
        token = token_map.get((leg.strike, leg.option_type))
        if token:
            leg.instrument_token = token
    return chain
