"""
KiteSessionManager — owns the daily Zerodha session lifecycle.

Called once at bot startup; assert_valid() is called at the top of every
loop tick to guarantee the session is live before any trading operation.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("moonshotx.broker.kite_session")

PROJECT_ROOT = Path(__file__).parent.parent.parent
SESSION_MAX_AGE_HOURS = 20       # Zerodha tokens are valid ~24h; refresh before 20h
SESSION_CHECK_INTERVAL_S = 300   # Re-validate every 5 min inside assert_valid cache


class SessionExpiredError(Exception):
    """Raised when the Kite session is expired and could not be auto-refreshed."""


class KiteSessionManager:
    """
    Owns the daily Kite token lifecycle.

    Usage:
        session_mgr = KiteSessionManager()
        await session_mgr.startup()           # call at bot startup
        await session_mgr.assert_valid()      # call at top of every loop tick
    """

    def __init__(self, env_path: Path = None):
        self._env_path = env_path or (PROJECT_ROOT / ".env")
        self._token_set_at: datetime | None = None
        self._last_check: datetime | None = None
        self._kite = None   # live KiteConnect instance, set after token load
        self._refreshing = False

    # ── Startup ───────────────────────────────────────────────────────────

    async def startup(self):
        """Load current token from .env and validate it. Refresh if stale."""
        load_dotenv(self._env_path, override=True)
        access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        if not access_token:
            logger.warning("[SESSION] No access token in .env — triggering refresh")
            await self._do_refresh()
            return

        ok = await asyncio.to_thread(self._validate_token, access_token)
        if ok:
            self._token_set_at = datetime.now(timezone.utc)
            logger.info("[SESSION] Existing token valid — session ready")
        else:
            logger.warning("[SESSION] Existing token invalid — triggering refresh")
            await self._do_refresh()

    # ── Loop gate ─────────────────────────────────────────────────────────

    async def assert_valid(self) -> None:
        """
        Call at the top of every loop tick.
        Raises SessionExpiredError if the session cannot be recovered.
        Skips full re-validation if last check was < SESSION_CHECK_INTERVAL_S ago.
        """
        now = datetime.now(timezone.utc)

        # Rate-limit re-checks
        if (
            self._last_check
            and (now - self._last_check).total_seconds() < SESSION_CHECK_INTERVAL_S
        ):
            return

        self._last_check = now

        # Age gate: if token is older than SESSION_MAX_AGE_HOURS, refresh now
        if self._token_set_at:
            age_h = (now - self._token_set_at).total_seconds() / 3600
            if age_h >= SESSION_MAX_AGE_HOURS:
                logger.warning(f"[SESSION] Token age {age_h:.1f}h >= {SESSION_MAX_AGE_HOURS}h — refreshing")
                refreshed = await self.refresh_if_stale()
                if not refreshed:
                    raise SessionExpiredError("Token refresh failed after age expiry")
                return

        # Quick live validation
        access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")
        ok = await asyncio.to_thread(self._validate_token, access_token)
        if not ok:
            logger.warning("[SESSION] Token validation failed — triggering refresh")
            refreshed = await self.refresh_if_stale()
            if not refreshed:
                logger.warning(
                    "[SESSION] Token expired — visit GET /api/kite/login-url to refresh via browser"
                )
                raise SessionExpiredError(
                    "Token expired. Visit GET http://localhost:8001/api/kite/login-url to refresh."
                )

    # ── Refresh ───────────────────────────────────────────────────────────

    async def refresh_if_stale(self) -> bool:
        """
        Trigger a token refresh. Returns True if refresh succeeded.
        Guards against concurrent refreshes.
        """
        if self._refreshing:
            logger.info("[SESSION] Refresh already in progress — waiting")
            for _ in range(30):
                await asyncio.sleep(2)
                if not self._refreshing:
                    break
            return bool(os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN"))

        self._refreshing = True
        try:
            new_token = await asyncio.to_thread(self._run_refresh)
            if new_token:
                self._token_set_at = datetime.now(timezone.utc)
                self._last_check = None   # force re-check on next tick
                return True
            return False
        except Exception as e:
            logger.error(f"[SESSION] refresh_if_stale error: {e}")
            return False
        finally:
            self._refreshing = False

    def _run_refresh(self) -> str:
        """Synchronous token refresh (runs in thread). Returns new token or ''."""
        try:
            from broker.token_refresh import refresh_kite_token
            token = refresh_kite_token(restart_bot=False, env_path=self._env_path)
            return token
        except Exception as e:
            logger.error(f"[SESSION] Token refresh failed: {e}")
            return ""

    async def _do_refresh(self):
        """Startup refresh with fatal error on failure."""
        ok = await self.refresh_if_stale()
        if not ok:
            raise SessionExpiredError(
                "Cannot start MoonshotX-IND: Zerodha token is expired and auto-refresh failed. "
                "To get a fresh token, visit: GET http://localhost:8001/api/kite/login-url "
                "then click the link in your logged-in Zerodha browser."
            )

    # ── Validation ────────────────────────────────────────────────────────

    def _validate_token(self, access_token: str) -> bool:
        """Synchronous live validation via kite.profile()."""
        if not access_token:
            return False
        try:
            from kiteconnect import KiteConnect
            api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
            kite = KiteConnect(api_key=api_key)
            kite.set_access_token(access_token)
            profile = kite.profile()
            return bool(profile.get("user_id"))
        except Exception as e:
            logger.warning(f"[SESSION] Token validation error: {e}")
            return False

    # ── Status ────────────────────────────────────────────────────────────

    def get_status(self) -> dict:
        """Return current session status dict for /api/kite/session-status."""
        now = datetime.now(timezone.utc)
        age_h = (
            round((now - self._token_set_at).total_seconds() / 3600, 2)
            if self._token_set_at else None
        )
        next_refresh_h = (
            round(SESSION_MAX_AGE_HOURS - age_h, 2) if age_h is not None else None
        )
        return {
            "token_set_at": self._token_set_at.isoformat() if self._token_set_at else None,
            "token_age_hours": age_h,
            "next_refresh_in_hours": next_refresh_h,
            "max_age_hours": SESSION_MAX_AGE_HOURS,
            "refreshing": self._refreshing,
        }
