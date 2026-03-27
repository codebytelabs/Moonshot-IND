"""
Login fallback — browser-based OAuth + manual token injection.

Primary workflow (browser login):
  1. Call generate_login_url() → prints the Kite Connect URL
  2. User clicks it in their already-logged-in browser
  3. Kite redirects to callback URL with ?request_token=XXX
  4. Call complete_browser_login(request_token) → generates + saves access_token

OR use the server endpoints:
  GET  /api/kite/login-url   → returns the click link
  GET  /api/kite/callback    → Zerodha redirects here automatically with request_token

CLI fallback:
  python -m broker.login_fallback --browser   # print login URL, then prompt for callback URL
  python -m broker.login_fallback --manual    # paste raw access_token
  python -m broker.login_fallback --test      # validate current token
"""
import asyncio
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger("moonshotx.broker.login_fallback")

PROJECT_ROOT = Path(__file__).parent.parent.parent


def generate_login_url(env_path: Path = None) -> str:
    """
    Return the Kite Connect login URL.

    User clicks this in their already-logged-in Zerodha browser session.
    Kite redirects to the configured callback URL with ?request_token=XXX.
    """
    load_dotenv(env_path or (PROJECT_ROOT / ".env"), override=True)
    api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
    if not api_key:
        raise ValueError("Zerodha_KITE_PAID_API_KEY not set in .env")
    return f"https://kite.trade/connect/login?api_key={api_key}&v=3"


def complete_browser_login(request_token: str, env_path: Path = None) -> str:
    """
    Exchange a request_token (from Kite's callback redirect) for an access_token.

    Steps:
      1. User clicked generate_login_url() and authenticated in browser
      2. Kite redirected to callback URL with ?request_token=XXX
      3. Caller passes that request_token here
      4. We call KiteConnect.generate_session() → access_token → write to .env

    Returns the new access_token.
    """
    from urllib.parse import urlparse, parse_qs
    env_path = env_path or (PROJECT_ROOT / ".env")
    load_dotenv(env_path, override=True)

    api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
    api_secret = os.getenv("Zerodha_KITE_PAID_Secret_KEY")

    if not api_key or not api_secret:
        raise ValueError("Zerodha_KITE_PAID_API_KEY / Zerodha_KITE_PAID_Secret_KEY not set in .env")

    # Accept either a raw token or a full callback URL
    if request_token.startswith("http"):
        params = parse_qs(urlparse(request_token).query)
        request_token = params.get("request_token", [None])[0]
        if not request_token:
            raise ValueError("Could not extract request_token from URL")

    request_token = request_token.strip()
    logger.info(f"Exchanging request_token={request_token[:10]}... for access_token")

    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    logger.info(f"Access token generated for {data.get('user_name')} ({data.get('user_id')})")
    inject_manual_token(access_token, env_path=env_path)
    return access_token


def inject_manual_token(access_token: str, env_path: Path = None) -> bool:
    """
    Write a manually obtained access_token to .env.
    Returns True on success.
    """
    env_path = env_path or (PROJECT_ROOT / ".env")
    access_token = access_token.strip()

    if not access_token:
        logger.error("inject_manual_token: empty token provided")
        return False

    try:
        with open(env_path, "r") as f:
            content = f.read()
        if "Zerodha_KITE_PAID_ACCESS_TOKEN=" in content:
            content = re.sub(
                r"Zerodha_KITE_PAID_ACCESS_TOKEN=.*",
                f"Zerodha_KITE_PAID_ACCESS_TOKEN={access_token}",
                content,
            )
        else:
            content += f"\nZerodha_KITE_PAID_ACCESS_TOKEN={access_token}\n"
        with open(env_path, "w") as f:
            f.write(content)
        os.environ["Zerodha_KITE_PAID_ACCESS_TOKEN"] = access_token
        logger.info(f"Manual token injected: {access_token[:10]}...")
        return True
    except Exception as e:
        logger.error(f"inject_manual_token error: {e}")
        return False


def test_current_token(env_path: Path = None) -> dict:
    """
    Validate the current token in .env against Kite API.
    Returns dict with 'valid', 'user_name', 'balance'.
    """
    env_path = env_path or (PROJECT_ROOT / ".env")
    load_dotenv(env_path, override=True)
    api_key = os.getenv("Zerodha_KITE_PAID_API_KEY")
    access_token = os.getenv("Zerodha_KITE_PAID_ACCESS_TOKEN", "")

    if not access_token:
        return {"valid": False, "error": "No token in .env"}

    try:
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=api_key)
        kite.set_access_token(access_token)
        profile = kite.profile()
        margins = kite.margins()
        equity = margins.get("equity", {})
        balance = equity.get("available", {}).get("live_balance", 0) or equity.get("net", 0)
        return {
            "valid": True,
            "user_name": profile.get("user_name"),
            "user_id": profile.get("user_id"),
            "balance": balance,
        }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def cli_main():
    """CLI entry point for manual token operations."""
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    parser = argparse.ArgumentParser(description="MoonshotX-IND Zerodha Login Fallback")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true", help="Test current token validity")
    group.add_argument("--browser", action="store_true", help="Browser OAuth: print login URL, paste callback URL back")
    group.add_argument("--manual", action="store_true", help="Manually inject a raw access token")
    group.add_argument("--refresh", action="store_true", help="Run automated TOTP token refresh")
    args = parser.parse_args()

    if args.test:
        result = test_current_token()
        if result["valid"]:
            print(f"✅ Token valid for {result['user_name']} — Balance: ₹{result.get('balance', 0):,.2f}")
        else:
            print(f"❌ Token invalid: {result.get('error')}")

    elif args.browser:
        url = generate_login_url()
        print("\n── Zerodha Browser Login ──────────────────────────────────")
        print(f"  1. Open this URL in your already-logged-in Zerodha browser:")
        print(f"\n     {url}\n")
        print(f"  2. After redirect, paste the FULL callback URL below")
        print(f"     (or just the request_token value from it)")
        print("───────────────────────────────────────────────────────────\n")
        raw = input("Paste callback URL or request_token: ").strip()
        try:
            token = complete_browser_login(raw)
            result = test_current_token()
            if result["valid"]:
                print(f"\n✅ Access token saved! Logged in as {result['user_name']} — Balance: ₹{result.get('balance', 0):,.2f}")
            else:
                print(f"\n⚠️  Token saved but validation failed: {result.get('error')}")
        except Exception as e:
            print(f"\n❌ Login failed: {e}")
            sys.exit(1)

    elif args.manual:
        token = input("Paste your Zerodha access token: ").strip()
        if inject_manual_token(token):
            result = test_current_token()
            if result["valid"]:
                print(f"✅ Token injected and validated for {result['user_name']}")
            else:
                print(f"⚠️ Token written but validation failed: {result.get('error')}")
        else:
            print("❌ Failed to write token to .env")
            sys.exit(1)

    elif args.refresh:
        from broker.token_refresh import refresh_kite_token
        try:
            new_token = refresh_kite_token(restart_bot=False)
            print(f"✅ Token refreshed: {new_token[:10]}...")
        except Exception as e:
            print(f"❌ Refresh failed: {e}")
            sys.exit(1)


if __name__ == "__main__":
    cli_main()
