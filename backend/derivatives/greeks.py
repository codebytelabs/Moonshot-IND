"""Black-Scholes Greeks calculator for NSE index options.

All prices in ₹. Risk-free rate defaults to RBI repo rate (~6.5%).
Volatility is annualised (e.g., 0.15 = 15% IV).
"""
import math
from dataclasses import dataclass
from typing import Literal


RF_RATE = 0.065   # RBI repo rate approximation


def _norm_cdf(x: float) -> float:
    return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class Greeks:
    delta: float
    gamma: float
    theta: float   # ₹ per day decay
    vega: float    # ₹ per 1% IV change
    rho: float
    iv: float      # implied volatility used
    price_bs: float  # theoretical price from BS


def bs_price(
    option_type: Literal["CE", "PE"],
    S: float,    # spot price
    K: float,    # strike
    T: float,    # time to expiry in years
    r: float,    # risk-free rate
    sigma: float # IV (annualised)
) -> float:
    """Black-Scholes option price."""
    if T <= 0 or sigma <= 0:
        intrinsic = max(0.0, S - K) if option_type == "CE" else max(0.0, K - S)
        return intrinsic
    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    if option_type == "CE":
        return S * _norm_cdf(d1) - K * math.exp(-r * T) * _norm_cdf(d2)
    else:
        return K * math.exp(-r * T) * _norm_cdf(-d2) - S * _norm_cdf(-d1)


def calculate_greeks(
    option_type: Literal["CE", "PE"],
    S: float,
    K: float,
    T: float,           # years to expiry
    sigma: float,       # IV annualised
    r: float = RF_RATE,
    lot_size: int = 1,
) -> Greeks:
    """Calculate all Greeks for a single option.

    Returns per-lot values for theta and vega when lot_size > 1.
    """
    if T <= 1e-6:
        T = 1e-6
    if sigma <= 1e-6:
        sigma = 1e-6

    d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    price = bs_price(option_type, S, K, T, r, sigma)

    # Delta
    if option_type == "CE":
        delta = _norm_cdf(d1)
    else:
        delta = _norm_cdf(d1) - 1.0

    # Gamma (same for CE and PE)
    gamma = _norm_pdf(d1) / (S * sigma * math.sqrt(T))

    # Theta (₹/day) — divide by 365 for daily decay
    if option_type == "CE":
        theta_annual = (
            -S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T))
            - r * K * math.exp(-r * T) * _norm_cdf(d2)
        )
    else:
        theta_annual = (
            -S * _norm_pdf(d1) * sigma / (2 * math.sqrt(T))
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
        )
    theta_daily = theta_annual / 365.0

    # Vega (₹ per 1% move in IV)
    vega_pct = S * _norm_pdf(d1) * math.sqrt(T) / 100.0

    # Rho (₹ per 1% move in r)
    if option_type == "CE":
        rho = K * T * math.exp(-r * T) * _norm_cdf(d2) / 100.0
    else:
        rho = -K * T * math.exp(-r * T) * _norm_cdf(-d2) / 100.0

    return Greeks(
        delta=round(delta * lot_size, 4),
        gamma=round(gamma * lot_size, 6),
        theta=round(theta_daily * lot_size, 2),
        vega=round(vega_pct * lot_size, 2),
        rho=round(rho * lot_size, 4),
        iv=round(sigma, 4),
        price_bs=round(price, 2),
    )


def implied_volatility(
    option_type: Literal["CE", "PE"],
    S: float,
    K: float,
    T: float,
    market_price: float,
    r: float = RF_RATE,
    max_iter: int = 100,
    tol: float = 1e-5,
) -> float:
    """Newton-Raphson IV solver. Returns annualised IV (0.15 = 15%)."""
    if T <= 0 or market_price <= 0:
        return 0.0

    sigma = 0.25   # initial guess
    for _ in range(max_iter):
        price = bs_price(option_type, S, K, T, r, sigma)
        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        vega = S * _norm_pdf(d1) * math.sqrt(T)
        if abs(vega) < 1e-10:
            break
        diff = price - market_price
        if abs(diff) < tol:
            break
        sigma -= diff / vega
        sigma = max(0.001, min(sigma, 5.0))   # keep IV in [0.1%, 500%]
    return round(sigma, 4)


@dataclass
class PortfolioGreeks:
    net_delta: float
    net_gamma: float
    net_theta: float
    net_vega: float
    positions: list   # list of dicts with per-leg Greeks


def aggregate_portfolio_greeks(legs: list[dict]) -> PortfolioGreeks:
    """Aggregate Greeks across all open derivative legs.

    Each leg dict: {option_type, S, K, T, sigma, qty, side, lot_size}
    side: 'buy' → +1, 'sell' → -1
    """
    total_delta = total_gamma = total_theta = total_vega = 0.0
    positions = []
    for leg in legs:
        sign = 1 if leg.get("side", "buy") == "buy" else -1
        g = calculate_greeks(
            option_type=leg["option_type"],
            S=leg["S"], K=leg["K"], T=leg["T"], sigma=leg["sigma"],
            lot_size=leg.get("lot_size", 1) * leg.get("qty", 1),
        )
        total_delta += sign * g.delta
        total_gamma += sign * g.gamma
        total_theta += sign * g.theta
        total_vega  += sign * g.vega
        positions.append({**leg, "greeks": g})

    return PortfolioGreeks(
        net_delta=round(total_delta, 4),
        net_gamma=round(total_gamma, 6),
        net_theta=round(total_theta, 2),
        net_vega=round(total_vega, 2),
        positions=positions,
    )
