"""NSE F&O expiry calendar helpers.

Weekly expiry schedule (as of 2024-2026):
  NIFTY     → every Thursday
  BANKNIFTY → every Wednesday
  FINNIFTY  → every Tuesday

Monthly expiry = last Thursday of the month (for index futures and stock F&O).
"""
from datetime import date, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

# Known NSE trading holidays (add each year as needed)
NSE_HOLIDAYS_2026 = {
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 17),   # Holi
    date(2026, 4, 2),    # Ram Navami
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti / Good Friday
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 8, 15),   # Independence Day
    date(2026, 10, 2),   # Gandhi Jayanti
    date(2026, 10, 26),  # Diwali Laxmi Pujan (tentative)
    date(2026, 11, 5),   # Diwali Balipratipada (tentative)
    date(2026, 12, 25),  # Christmas
}

NSE_HOLIDAYS_2025 = {
    date(2025, 1, 26),
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 4, 14),   # Dr. Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 10, 2),   # Gandhi Jayanti
    date(2025, 10, 20),  # Diwali Laxmi Pujan
    date(2025, 10, 21),  # Diwali Balipratipada
    date(2025, 11, 5),   # Gurunanak Jayanti
    date(2025, 12, 25),  # Christmas
}

_ALL_HOLIDAYS = NSE_HOLIDAYS_2025 | NSE_HOLIDAYS_2026


def is_trading_day(d: date) -> bool:
    """Return True if `d` is a weekday and not an NSE holiday."""
    return d.weekday() < 5 and d not in _ALL_HOLIDAYS


def _prev_trading_day(d: date, weekday: int) -> date:
    """Find the nearest preceding trading day for a given weekday (0=Mon … 6=Sun)."""
    # find the most recent `weekday` on or before `d`
    days_back = (d.weekday() - weekday) % 7
    candidate = d - timedelta(days=days_back)
    # if it's a holiday, walk back to previous trading day
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def next_expiry(symbol: str = "NIFTY", ref: date | None = None) -> date:
    """Return the next/current weekly expiry date for a given index symbol.

    For NIFTY: Thursday (weekday=3)
    For BANKNIFTY: Wednesday (weekday=2)
    For FINNIFTY: Tuesday (weekday=1)
    """
    if ref is None:
        from datetime import datetime
        from zoneinfo import ZoneInfo
        ref = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    expiry_weekday = {
        "NIFTY": 3,       # Thursday
        "BANKNIFTY": 2,   # Wednesday
        "FINNIFTY": 1,    # Tuesday
    }.get(symbol.upper(), 3)

    # Find next occurrence of that weekday >= today
    days_ahead = (expiry_weekday - ref.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7   # already on expiry day → next week's expiry
    candidate = ref + timedelta(days=days_ahead)
    # If candidate is a holiday, shift to preceding trading day
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def current_expiry(symbol: str = "NIFTY", ref: date | None = None) -> date:
    """Return the current week's expiry. If today IS expiry day, returns today."""
    if ref is None:
        from datetime import datetime
        ref = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    expiry_weekday = {
        "NIFTY": 3,
        "BANKNIFTY": 2,
        "FINNIFTY": 1,
    }.get(symbol.upper(), 3)

    days_ahead = (expiry_weekday - ref.weekday()) % 7
    candidate = ref + timedelta(days=days_ahead)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def monthly_expiry(symbol: str = "NIFTY", ref: date | None = None) -> date:
    """Return the monthly expiry (last Thursday of current month for NIFTY)."""
    if ref is None:
        from datetime import datetime
        ref = datetime.now(ZoneInfo("Asia/Kolkata")).date()

    # Last day of the month
    if ref.month == 12:
        last_day = date(ref.year + 1, 1, 1) - timedelta(days=1)
    else:
        last_day = date(ref.year, ref.month + 1, 1) - timedelta(days=1)

    expiry_weekday = {"NIFTY": 3, "BANKNIFTY": 3}.get(symbol.upper(), 3)
    # Walk back from last_day to find the last occurrence of expiry_weekday
    candidate = last_day
    while candidate.weekday() != expiry_weekday:
        candidate -= timedelta(days=1)
    while not is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def expiry_date_str(symbol: str = "NIFTY", monthly: bool = False) -> str:
    """Return expiry as 'DDMMMYY' string used in Kite instrument symbols.
    e.g., '27MAR25'
    """
    d = monthly_expiry(symbol) if monthly else current_expiry(symbol)
    return d.strftime("%d%b%y").upper()


def days_to_expiry(symbol: str = "NIFTY") -> int:
    """Calendar days remaining to current weekly expiry."""
    from datetime import datetime
    ref = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    exp = current_expiry(symbol, ref)
    return (exp - ref).days


def trading_days_to_expiry(symbol: str = "NIFTY") -> int:
    """Trading days remaining to current weekly expiry (excluding holidays/weekends)."""
    from datetime import datetime
    ref = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    exp = current_expiry(symbol, ref)
    count = 0
    cur = ref
    while cur < exp:
        cur += timedelta(days=1)
        if is_trading_day(cur):
            count += 1
    return count
