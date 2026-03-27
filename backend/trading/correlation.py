"""Correlation & sector concentration guard — prevents stacking correlated positions."""
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("moonshotx.correlation")

# ── Sector mappings ───────────────────────────────────────────────────────────
SECTOR_MAP: Dict[str, str] = {
    # Semiconductors
    "NVDA": "semis", "AMD": "semis", "INTC": "semis", "MU": "semis",
    "AVGO": "semis", "QCOM": "semis", "AMAT": "semis", "LRCX": "semis",
    "MRVL": "semis", "ON": "semis", "KLAC": "semis", "ASML": "semis",
    "ARM": "semis", "SMCI": "semis",
    # Big tech
    "AAPL": "big_tech", "MSFT": "big_tech", "GOOGL": "big_tech",
    "META": "big_tech", "AMZN": "big_tech",
    # EV / auto
    "TSLA": "ev_auto",
    # Enterprise SaaS
    "CRM": "saas", "NOW": "saas", "ORCL": "saas", "ADBE": "saas",
    "WDAY": "saas", "TEAM": "saas", "HUBS": "saas", "VEEV": "saas",
    # Consumer / streaming
    "NFLX": "consumer", "SPOT": "consumer", "UBER": "consumer",
    "ABNB": "consumer", "DASH": "consumer", "RBLX": "consumer",
    "DIS": "consumer", "CMCSA": "consumer",
    # Fintech / crypto
    "COIN": "fintech", "PYPL": "fintech",
    "SOFI": "fintech", "HOOD": "fintech", "MSTR": "fintech",
    # Cybersecurity / cloud
    "PANW": "cyber", "CRWD": "cyber", "DDOG": "cyber", "NET": "cyber",
    "SNOW": "cyber", "ZS": "cyber", "FTNT": "cyber", "S": "cyber",
    # AI / data
    "PLTR": "ai_data", "SHOP": "ai_data", "AI": "ai_data",
    "PATH": "ai_data", "IONQ": "ai_data",
    # Biotech
    "MRNA": "biotech", "REGN": "biotech", "VRTX": "biotech",
    "ISRG": "biotech", "LLY": "biotech", "UNH": "biotech",
    "ABBV": "biotech", "PFE": "biotech", "SRPT": "biotech",
    "TERN": "biotech",
    # Energy
    "XOM": "energy", "CVX": "energy", "OXY": "energy", "SLB": "energy",
    "ENPH": "energy", "FSLR": "energy", "CEG": "energy", "VST": "energy",
    # Financials
    "GS": "financials", "JPM": "financials", "MS": "financials",
    "SCHW": "financials", "V": "financials", "MA": "financials",
    "AXP": "financials",
    # Retail
    "COST": "retail", "WMT": "retail", "TGT": "retail", "HD": "retail",
    # Telecom
    "T": "telecom", "TMUS": "telecom",
    # Defense / industrial
    "BA": "industrial", "CAT": "industrial", "DE": "industrial",
    "LMT": "industrial", "RTX": "industrial",
    # Misc
    "NOK": "telecom", "HPE": "big_tech",
    # ETFs / Index
    "SPY": "index_etf", "QQQ": "index_etf", "DIA": "index_etf", "IWM": "index_etf",
    # Energy (additional)
    "RIG": "energy", "HAL": "energy",
}

# ── Concentration limits ──────────────────────────────────────────────────────
# Max positions per sector (regime-dependent)
SECTOR_LIMITS = {
    "bull":         {"default": 4, "semis": 5, "big_tech": 4},
    "neutral":      {"default": 3, "semis": 4, "big_tech": 3},
    "fear":         {"default": 2, "semis": 3, "big_tech": 2},
    "choppy":       {"default": 2, "semis": 2, "big_tech": 2},
    "bear_mode":    {"default": 1, "semis": 1, "big_tech": 1},
    "extreme_fear": {"default": 0, "semis": 0, "big_tech": 0},
}


def get_sector(ticker: str) -> str:
    """Return sector for a ticker, or 'unknown' if not mapped."""
    return SECTOR_MAP.get(ticker, "unknown")


def sector_concentration(open_positions: List[dict]) -> Dict[str, List[str]]:
    """Return {sector: [tickers]} for all open positions."""
    sectors: Dict[str, List[str]] = {}
    for pos in open_positions:
        sym = pos.get("symbol", "")
        sector = get_sector(sym)
        sectors.setdefault(sector, []).append(sym)
    return sectors


def can_add_to_sector(ticker: str, open_positions: List[dict], regime: str) -> tuple[bool, str]:
    """Check if adding this ticker would breach sector concentration limits."""
    sector = get_sector(ticker)
    concentration = sector_concentration(open_positions)

    # Get regime-specific limits
    limits = SECTOR_LIMITS.get(regime, SECTOR_LIMITS["neutral"])
    max_in_sector = limits.get(sector, limits["default"])

    current_in_sector = len(concentration.get(sector, []))

    if current_in_sector >= max_in_sector:
        existing = concentration.get(sector, [])
        return False, f"Sector '{sector}' full: {current_in_sector}/{max_in_sector} ({existing})"

    return True, "ok"


def get_concentration_summary(open_positions: List[dict], regime: str) -> dict:
    """Return concentration summary for monitoring."""
    concentration = sector_concentration(open_positions)
    limits = SECTOR_LIMITS.get(regime, SECTOR_LIMITS["neutral"])

    summary = {}
    for sector, tickers in sorted(concentration.items()):
        max_allowed = limits.get(sector, limits["default"])
        summary[sector] = {
            "count": len(tickers),
            "max": max_allowed,
            "tickers": tickers,
            "at_limit": len(tickers) >= max_allowed,
        }
    return summary
