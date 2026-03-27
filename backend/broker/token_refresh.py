"""
Zerodha token refresh — ported from DayTraderAI-IND auto_token_refresh.py.

Wraps the full login → TOTP 2FA → request_token → access_token → .env rewrite
pipeline as a callable function: refresh_kite_token() -> str

The ZerodhaAutoToken class is the core engine. Use refresh_kite_token() as the
public API from KiteSessionManager.
"""
import os
import re
import time
import logging
import hashlib
import subprocess
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import requests
import pyotp
from dotenv import load_dotenv

logger = logging.getLogger("moonshotx.broker.token_refresh")

PROJECT_ROOT = Path(__file__).parent.parent.parent


class ZerodhaAutoToken:
    """
    Automated Zerodha token generation using HTTP requests only.
    No Selenium or browser automation required.

    Flow:
      1. POST /api/login  → request_id
      2. POST /api/twofa  → enctoken (TOTP)
      3. GET  Kite Connect login URL → request_token
      4. KiteConnect.generate_session() → access_token
      5. Rewrite .env with new token
    """

    LOGIN_URL = "https://kite.zerodha.com/api/login"
    TWOFA_URL = "https://kite.zerodha.com/api/twofa"
    KITE_LOGIN_URL = "https://kite.trade/connect/login"

    def __init__(self, env_path: Path = None):
        self._env_path = env_path or (PROJECT_ROOT / ".env")
        load_dotenv(self._env_path)

        self.api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
        self.api_secret = os.getenv("Zerodha_KITE_PAID_Secret_KEY")
        self.user_id = os.getenv("ZERODHA_USER_ID", "")
        self.password = os.getenv("ZERODHA_PASSWORD", "")
        self.totp_secret = os.getenv("ZERODHA_TOTP_SECRET", "")

        if not all([self.api_key, self.api_secret]):
            raise ValueError("Missing Zerodha_KITE_PAID_API_KEY / Zerodha_KITE_PAID_Secret_KEY in .env")
        if not all([self.user_id, self.password, self.totp_secret]):
            raise ValueError(
                "Missing login credentials in .env. Required:\n"
                "  ZERODHA_USER_ID=your_client_id\n"
                "  ZERODHA_PASSWORD=your_password\n"
                "  ZERODHA_TOTP_SECRET=your_totp_secret"
            )

        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded",
            "Origin": "https://kite.zerodha.com",
            "Referer": "https://kite.zerodha.com/",
        })

    def generate_totp(self) -> str:
        """Generate current TOTP code from secret."""
        totp = pyotp.TOTP(self.totp_secret)
        code = totp.now()
        logger.info(f"TOTP generated: {code}")
        return code

    def step1_login(self) -> str:
        """Submit credentials → request_id."""
        logger.info("Step 1: submitting login credentials")
        resp = self.session.post(self.LOGIN_URL, data={"user_id": self.user_id, "password": self.password})
        if resp.status_code != 200:
            raise RuntimeError(f"Login HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"Login failed: {data.get('message', 'unknown')}")
        request_id = data["data"]["request_id"]
        logger.info(f"Login OK — request_id={request_id}")
        return request_id

    def step2_twofa(self, request_id: str) -> str:
        """Submit TOTP → enctoken in cookies."""
        logger.info("Step 2: submitting TOTP 2FA")
        totp_code = self.generate_totp()
        payload = {
            "user_id": self.user_id,
            "request_id": request_id,
            "twofa_value": totp_code,
            "twofa_type": "totp",
            "skip_session": "",
        }
        resp = self.session.post(self.TWOFA_URL, data=payload)
        if resp.status_code != 200:
            raise RuntimeError(f"2FA HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        if data.get("status") != "success":
            raise RuntimeError(f"2FA failed: {data.get('message', 'unknown')}")
        enctoken = self.session.cookies.get("enctoken") or data.get("data", {}).get("enctoken", "")
        if enctoken:
            logger.info(f"2FA OK — enctoken={enctoken[:20]}...")
        else:
            logger.info("2FA OK (session cookies carry enctoken)")
        return enctoken

    def step3_get_request_token(self) -> str:
        """Follow Kite Connect login redirect → request_token."""
        logger.info("Step 3: fetching request_token from Kite Connect")
        login_url = f"{self.KITE_LOGIN_URL}?api_key={self.api_key}&v=3"
        resp = self.session.get(login_url, allow_redirects=True)
        final_url = resp.url
        logger.info(f"Final redirect URL: {final_url}")
        params = parse_qs(urlparse(final_url).query)
        request_token = params.get("request_token", [None])[0]
        if not request_token:
            match = re.search(r"request_token=([A-Za-z0-9]+)", resp.text)
            if match:
                request_token = match.group(1)
        if not request_token:
            raise RuntimeError(f"request_token not found in redirect URL: {final_url}")
        logger.info(f"request_token obtained: {request_token}")
        return request_token

    def step4_generate_access_token(self, request_token: str) -> str:
        """Exchange request_token for access_token via KiteConnect SDK."""
        logger.info("Step 4: generating access token")
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self.api_key)
        data = kite.generate_session(request_token, api_secret=self.api_secret)
        access_token = data["access_token"]
        logger.info(f"Access token generated for {data.get('user_name')} ({data.get('user_id')})")
        return access_token

    def update_env_file(self, access_token: str):
        """Rewrite Zerodha_KITE_PAID_ACCESS_TOKEN in .env."""
        with open(self._env_path, "r") as f:
            content = f.read()
        if "Zerodha_KITE_PAID_ACCESS_TOKEN=" in content:
            content = re.sub(
                r"Zerodha_KITE_PAID_ACCESS_TOKEN=.*",
                f"Zerodha_KITE_PAID_ACCESS_TOKEN={access_token}",
                content,
            )
        else:
            content += f"\nZerodha_KITE_PAID_ACCESS_TOKEN={access_token}\n"
        with open(self._env_path, "w") as f:
            f.write(content)
        os.environ["Zerodha_KITE_PAID_ACCESS_TOKEN"] = access_token
        logger.info(f".env updated: Zerodha_KITE_PAID_ACCESS_TOKEN={access_token[:10]}...")

    def verify_token(self, access_token: str) -> dict:
        """Verify token against Kite API and return account info."""
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self.api_key)
        kite.set_access_token(access_token)
        profile = kite.profile()
        margins = kite.margins()
        equity = margins.get("equity", {})
        available = (
            equity.get("available", {}).get("live_balance", 0)
            or equity.get("net", 0)
        )
        logger.info(f"Token verified — user={profile.get('user_name')} balance=₹{available:,.2f}")
        return {
            "user_name": profile.get("user_name"),
            "user_id": profile.get("user_id"),
            "balance": available,
        }

    def run(self, restart_bot: bool = False) -> str:
        """
        Run the full refresh pipeline.
        Returns the new access_token on success, raises on failure.
        """
        logger.info("=" * 55)
        logger.info(f"ZERODHA TOKEN REFRESH — {datetime.now().strftime('%Y-%m-%d %H:%M:%S IST')}")
        logger.info("=" * 55)

        request_id = self.step1_login()
        self.step2_twofa(request_id)
        request_token = self.step3_get_request_token()
        access_token = self.step4_generate_access_token(request_token)
        self.update_env_file(access_token)
        self.verify_token(access_token)

        if restart_bot:
            self._restart_bot()

        logger.info("TOKEN REFRESH COMPLETE")
        return access_token

    def _restart_bot(self):
        """Restart the MoonshotX-IND server process."""
        logger.info("Restarting MoonshotX-IND bot...")
        try:
            subprocess.run(["pkill", "-f", "server.py"], capture_output=True)
            time.sleep(2)
        except Exception as e:
            logger.warning(f"pkill error: {e}")
        bot_script = PROJECT_ROOT / "start_backend.sh"
        if bot_script.exists():
            subprocess.Popen(
                ["bash", str(bot_script)],
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            logger.info("Bot restart signal sent")
        else:
            logger.warning(f"start_backend.sh not found at {bot_script}")


def refresh_kite_token(restart_bot: bool = False, env_path: Path = None) -> str:
    """
    Public API: run the full Zerodha token refresh.
    Returns the new access_token string on success.
    Raises RuntimeError on failure.
    """
    refresher = ZerodhaAutoToken(env_path=env_path)
    return refresher.run(restart_bot=restart_bot)
