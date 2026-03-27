"""
India news feed — aggregates financial news headlines for morning_brief.py.

Sources:
  1. NSE announcements API (corporate actions, results, AGM notices)
  2. Economic Times / Moneycontrol RSS feeds via feedparser
  3. Fallback: Google News RSS for "NSE India stock market"
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Dict

logger = logging.getLogger("moonshotx.data.india_news_feed")

MAX_HEADLINES_PER_SOURCE = 10
TOTAL_HEADLINES_CAP = 20


async def get_morning_headlines(limit: int = TOTAL_HEADLINES_CAP) -> List[Dict]:
    """
    Gather top financial news headlines for morning brief.
    Returns list of {title, source, published, url}.
    """
    tasks = [
        _fetch_et_rss(),
        _fetch_moneycontrol_rss(),
        _fetch_nse_announcements(),
    ]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    headlines = []
    for r in results:
        if isinstance(r, list):
            headlines.extend(r)
    headlines.sort(key=lambda x: x.get("published", ""), reverse=True)
    return headlines[:limit]


async def _fetch_et_rss() -> List[Dict]:
    """Economic Times Markets RSS."""
    url = "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms"
    return await _parse_rss(url, source="Economic Times")


async def _fetch_moneycontrol_rss() -> List[Dict]:
    """Moneycontrol Markets RSS."""
    url = "https://www.moneycontrol.com/rss/marketreports.xml"
    return await _parse_rss(url, source="Moneycontrol")


async def _parse_rss(url: str, source: str) -> List[Dict]:
    """Parse an RSS feed and return headline dicts."""
    try:
        result = await asyncio.to_thread(_parse_rss_sync, url, source)
        return result
    except Exception as e:
        logger.warning(f"[NEWS] RSS fetch failed for {source}: {e}")
        return []


def _parse_rss_sync(url: str, source: str) -> List[Dict]:
    try:
        import feedparser
        feed = feedparser.parse(url)
        headlines = []
        for entry in feed.entries[:MAX_HEADLINES_PER_SOURCE]:
            headlines.append({
                "title": entry.get("title", ""),
                "source": source,
                "published": entry.get("published", ""),
                "url": entry.get("link", ""),
                "summary": entry.get("summary", "")[:300],
            })
        return headlines
    except Exception as e:
        logger.warning(f"[NEWS] _parse_rss_sync {source} error: {e}")
        return []


async def _fetch_nse_announcements() -> List[Dict]:
    """NSE corporate announcements (results, dividends, AGM)."""
    try:
        result = await asyncio.to_thread(_fetch_nse_announcements_sync)
        return result
    except Exception as e:
        logger.warning(f"[NEWS] NSE announcements fetch error: {e}")
        return []


def _fetch_nse_announcements_sync() -> List[Dict]:
    """Synchronous NSE announcements fetch."""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }
    session = requests.Session()
    session.get("https://www.nseindia.com", headers=headers, timeout=10)
    url = "https://www.nseindia.com/api/corp-announcements"
    resp = session.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    announcements = []
    for item in (data if isinstance(data, list) else data.get("data", []))[:MAX_HEADLINES_PER_SOURCE]:
        announcements.append({
            "title": f"[{item.get('symbol', '')}] {item.get('subject', item.get('desc', ''))}",
            "source": "NSE Corporate",
            "published": item.get("exchdisstime", ""),
            "url": "",
            "summary": item.get("attachmentid", ""),
        })
    return announcements


async def get_stock_headlines(symbol: str, limit: int = 5) -> List[Dict]:
    """
    Fetch news headlines specific to a given NSE symbol.
    Uses Google News RSS as a quick source.
    """
    try:
        url = f"https://news.google.com/rss/search?q={symbol}+NSE+India+stock&hl=en-IN&gl=IN&ceid=IN:en"
        return await _parse_rss(url, source=f"Google News ({symbol})")
    except Exception as e:
        logger.warning(f"[NEWS] get_stock_headlines {symbol} error: {e}")
        return []
