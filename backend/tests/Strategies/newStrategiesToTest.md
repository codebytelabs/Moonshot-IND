Short answer: among Dhan‑integrated, *publicly documented* algos, the strongest combo of (a) live usage, (b) 1‑year returns, and (c) clearly described logic comes from the **Stratzy NIFTY credit‑spread family**: Curvature, Zen, and Drifting Credit Spread Overnight. Below I’ll summarize each and give you implementable Python skeletons approximating their logic. [dhanhq](https://dhanhq.co/algos/popular-algo/most-deployed-algo)

> Important: these are **approximations**, not Stratzy’s proprietary code. Use them as a base to build/backtest your own versions, not as identical clones.

***

## 1. Curvature Credit Spread Overnight

### Why it qualifies as “top”

- Listed by Dhan as one of the **Most Deployed Algos**. [dhanhq](https://dhanhq.co/algos/popular-algo/most-deployed-algo)
- Stratzy’s own stats show **~140.96% 1‑year returns on ₹1,00,000** capital for Curvature Credit Spread Overnight (defined‑risk credit spreads). [stratzy](https://stratzy.in/algo-trading-strategies?filterBy=all)
- Same “NiftyHedgedDirectional” template as Zen (hedged spreads, capped loss, systematic risk controls). [dhanhq](https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57)

### Intuition

Curvature treats the options market like a **fluid dynamics** problem: [dhanhq](https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57)

- Build the IV smile and volume/OI distribution across NIFTY option strikes.
- Compute:
  - **Curvature**: how “bent” the IV curve is around ATM (second‑derivative of IV vs moneyness).
  - **Viscosity**: how concentrated liquidity (volume+OI) is near ATM vs wings (how “thick” price is around current level).
- When curvature + viscosity are abnormally skewed, the strategy assumes the smile/liquidity structure will **rebalance**, and sells a **hedged credit spread** in the direction that benefits from that rebalancing. [dhanhq](https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57)

Execution template (from docs): [dhanhq](https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57)

- Underlying: NIFTY index options.
- Trade type: Credit spread (sell ATM, buy ±400 hedge).
- Direction:
  - Bullish regime → sell ATM PE, buy ITM PE 400 points lower (credit put spread).
  - Bearish regime → sell ATM CE, buy OTM CE 400 points higher (credit call spread). [dhanhq](https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57)
- Window: 10:15–14:15 IST only. [dhanhq](https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57)
- Stop‑loss: dynamic, approx `(3000 / margin_required) * 100` → limits max loss ≈ ₹3,000 per trade. [dhanhq](https://dhanhq.co/algos/managers/stratzy/v-score-credit-spread-overnight/68599411ce6680fc5ff7b224)

### Approximate Python skeleton

This is a compact version of the curvature logic you can wire to DhanHQ. You’ll need an option‑chain DataFrame per timestamp.

```python
import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum

class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"

@dataclass
class CurvatureSignal:
    ts: pd.Timestamp
    direction: Direction
    curvature: float
    viscosity: float
    alpha: float

def compute_iv_curvature(chain: pd.DataFrame, spot: float) -> float:
    """
    chain: columns ['strike', 'iv', 'volume', 'oi', 'type'] for a single expiry/timestamp.
    """
    df = chain.copy()
    df['moneyness'] = (df['strike'] - spot) / spot
    df = df.sort_values('moneyness')
    if len(df) < 5:
        return 0.0

    x = df['moneyness'].values
    y = df['iv'].values
    A = np.vstack([x**2, x, np.ones_like(x)]).T
    a, b, c = np.linalg.lstsq(A, y, rcond=None)[0]
    mean_iv = np.mean(y) or 1.0
    return float(abs(a) * (np.mean(np.abs(x)) ** 2) / mean_iv)

def compute_viscosity(chain: pd.DataFrame, spot: float) -> float:
    df = chain.copy()
    df['moneyness'] = (df['strike'] - spot) / spot
    near = df[df['moneyness'].abs() <= 0.01]
    wings = df[df['moneyness'].abs() > 0.01]

    def liq(x):
        return float((x['volume'] + x['oi']).sum()) if not x.empty else 0.0

    liq_near = liq(near)
    liq_wings = liq(wings) + 1e-9

    return (liq_near / max(len(near), 1)) / (liq_wings / max(len(wings), 1))

def curvature_alpha(curv, visc, mean_curv, std_curv) -> float:
    if std_curv <= 0:
        return 0.5
    z = np.clip((curv - mean_curv) / std_curv, -3, 3)
    v = max(np.log1p(visc), 0.0)
    raw = z * (1 + v)
    return 0.5 + 0.5 * np.tanh(raw / 6.0)

def generate_curvature_signals(chain_hist: dict, spot_series: pd.Series,
                               lookback=50, hi=0.7, lo=0.3):
    """
    chain_hist: {timestamp: option_chain_df}
    spot_series: NIFTY spot series indexed by timestamp
    """
    ts_list = sorted(chain_hist.keys())
    curv_list = []
    signals = []

    for ts in ts_list:
        if ts not in spot_series.index:
            continue
        t = ts.time()
        if not (t >= pd.to_datetime("10:15").time() and t <= pd.to_datetime("14:15").time()):
            continue

        spot = float(spot_series.loc[ts])
        chain = chain_hist[ts]
        curv = compute_iv_curvature(chain, spot)
        visc = compute_viscosity(chain, spot)
        curv_list.append(curv)

        if len(curv_list) < lookback:
            continue

        roll = pd.Series(curv_list[-lookback:])
        alpha = curvature_alpha(curv, visc, roll.mean(), roll.std(ddof=0))

        if alpha > hi:
            direction = Direction.BULLISH
        elif alpha < lo:
            direction = Direction.BEARISH
        else:
            continue

        signals.append(CurvatureSignal(ts, direction, curv, visc, alpha))

    return signals

@dataclass
class SpreadOrder:
    ts: pd.Timestamp
    direction: Direction
    short_strike: float
    long_strike: float
    short_type: str
    long_type: str
    qty: int
    sl_pct: float

def build_spread_from_curvature(sig: CurvatureSignal, spot: float,
                                lot_size: int, margin_per_lot: float,
                                max_loss_rupees: float = 3000.0) -> SpreadOrder:
    atm = round(spot / 50) * 50
    if sig.direction == Direction.BULLISH:
        short_strike, long_strike = atm, atm - 400
        opt_type = "PE"
    else:
        short_strike, long_strike = atm, atm + 400
        opt_type = "CE"

    sl_pct = max_loss_rupees / margin_per_lot * 100.0
    return SpreadOrder(sig.ts, sig.direction, short_strike, long_strike,
                       opt_type, opt_type, lot_size, sl_pct)
```

***

## 2. Zen Credit Spread Overnight

### Why it qualifies as “top”

- Also listed by Dhan as **Most Deployed Algo**. [dhanhq](https://dhanhq.co/algos/popular-algo/most-deployed-algo)
- Stratzy shows **~119.17% 1‑year returns** on ₹1,00,000 for Zen Credit Spread Overnight. [stratzy](https://stratzy.in/algo-trading-strategies?filterBy=all)
- It’s the **base template** many other algos (Curvature, V‑Score, Drifting, etc.) are built on. [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)

### Intuition

Zen is a **NIFTY index credit‑spread mean‑reversion strategy** based on two intraday alpha signals: [dhanhq](https://dhanhq.co/algos/managers/stratzy/zen-credit-spread-overnight/68596cd26aa2cba24bbb67da)

- **Alpha 1**: Time‑series rank (TSRank) of 5‑minute forward return over an 800‑minute window.
- **Alpha 2**: Forward return × ATM PE/CE volume ratio ÷ ATM volatility (IV or realized) — then TSRank over 300 minutes. [dhanhq](https://dhanhq.co/algos/managers/stratzy/zen-credit-spread-overnight/68596cd26aa2cba24bbb67da)

Both alphas must say the same thing (strongly bullish or strongly bearish).  

Execution: [dhanhq](https://dhanhq.co/algos/managers/stratzy/zen-credit-spread-overnight/68596cd26aa2cba24bbb67da)

- If both alphas > 0.8 → bullish:
  - Sell ATM PE, buy ITM PE 400 points lower (credit put spread).
- If both < 0.2 → bearish:
  - Sell ATM CE, buy OTM CE 400 points higher (credit call spread).
- Trading window: 10:15–14:15 IST.
- Risk: SL% ≈ `(3000 / margin_required) * 100`, max loss ≈ ₹3,000 per trade. [dhanhq](https://dhanhq.co/algos/managers/stratzy/v-score-credit-spread-overnight/68599411ce6680fc5ff7b224)

### Approximate Python skeleton

This assumes you have a 5‑minute NIFTY DataFrame with ATM CE/PE volume and IV columns.

```python
import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum

class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"

@dataclass
class ZenSignal:
    ts: pd.Timestamp
    direction: Direction
    alpha: float
    alpha2: float

def ts_rank(series: pd.Series) -> float:
    r = series.rank(method="average")
    return float(r.iloc[-1] / len(series))

def compute_zen_alpha(df: pd.DataFrame, lookback_minutes=800) -> pd.Series:
    # Forward 5‑min return on open→close_next
    fwd = df["close"].shift(-1) / df["open"] - 1.0
    window = lookback_minutes // 5
    return fwd.rolling(window=window, min_periods=window).apply(ts_rank, raw=False)

def compute_zen_alpha2(df: pd.DataFrame,
                       atm_ce_col="atm_ce",
                       atm_pe_col="atm_pe",
                       atm_ce_iv_col="atm_ce_iv",
                       atm_pe_iv_col="atm_pe_iv",
                       lookback_minutes=300) -> pd.Series:
    fwd = df["close"].shift(-1) / df["close"] - 1.0
    vol_ce = df[f"{atm_ce_col}_vol"]
    vol_pe = df[f"{atm_pe_col}_vol"]
    vol_ratio = (vol_pe + 1e-9) / (vol_ce + 1e-9)

    iv_ce = df[atm_ce_iv_col]
    iv_pe = df[atm_pe_iv_col]
    atm_vol = iv_ce.rolling(lookback_minutes // 5).std() + \
              iv_pe.rolling(lookback_minutes // 5).std()

    raw = fwd * vol_ratio / atm_vol.replace(0, np.nan)
    raw = raw.fillna(0.0)
    window = lookback_minutes // 5
    return raw.rolling(window=window, min_periods=window).apply(ts_rank, raw=False)

def generate_zen_signals(df: pd.DataFrame,
                         hi=0.8, lo=0.2) -> list[ZenSignal]:
    a1 = compute_zen_alpha(df)
    a2 = compute_zen_alpha2(df)
    signals = []

    for ts, x, y in zip(df.index, a1, a2):
        if pd.isna(x) or pd.isna(y):
            continue
        t = ts.time()
        if not (t >= pd.to_datetime("10:15").time() and t <= pd.to_datetime("14:15").time()):
            continue

        if x > hi and y > hi:
            direction = Direction.BULLISH
        elif x < lo and y < lo:
            direction = Direction.BEARISH
        else:
            continue
        signals.append(ZenSignal(ts, direction, float(x), float(y)))

    return signals

@dataclass
class SpreadOrder:
    ts: pd.Timestamp
    direction: Direction
    short_strike: float
    long_strike: float
    short_type: str
    long_type: str
    qty: int
    sl_pct: float

def build_spread_from_zen(sig: ZenSignal, spot: float,
                          lot_size: int, margin_per_lot: float,
                          max_loss_rupees=3000.0) -> SpreadOrder:
    atm = round(spot / 50) * 50
    if sig.direction == Direction.BULLISH:
        short_strike, long_strike = atm, atm - 400
        opt_type = "PE"
    else:
        short_strike, long_strike = atm, atm + 400
        opt_type = "CE"
    sl_pct = max_loss_rupees / margin_per_lot * 100.0
    return SpreadOrder(sig.ts, sig.direction,
                       short_strike, long_strike,
                       opt_type, opt_type,
                       lot_size, sl_pct)
```

***

## 3. Drifting Credit Spread Overnight

### Why it qualifies as “top”

- Stratzy page shows **~98.74% 1‑year returns** on ₹1,00,000 for Drifting Credit Spread Overnight (credit spread). [stratzy](https://stratzy.in/algo-trading-strategies?filterBy=all)
- Marked as a **variant of Zen** using **Geometric Brownian Motion (GBM) scoring** to detect range‑bound vs breakout probabilities. [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)
- Still NiftyHedgedDirectional, same dynamic SL formula `(3000 / margin_required) * 100` and same ATM±400 spread pattern, so win rate is typically high (many small gains, fewer capped losses). [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)

### Intuition

Drifting focuses on **range‑bound days** by modeling NIFTY price as a GBM and asking: “What is the probability it will stay between the ITM and OTM hedge strikes?” [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)

- Compute a **GBM score** based on historical volatility and drift that estimates how likely price is to stay within ±400 points.
- Combine this with:
  - **IV skew ratios** (how IV differs between calls and puts / different wings).
  - **Volume dynamics** (whether flow suggests a breakout or mean‑reversion). [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)
- If GBM says “high chance of staying in the band” AND skew/volume agree, sell a credit spread that profits if NIFTY does *not* move much.

Execution template: [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)

- Underlying: NIFTY index options.
- Trade:
  - Bullish (range‑bound with mild upside drift): sell ATM PE, buy ITM PE 400 lower.
  - Bearish (range‑bound with mild downside drift): sell ATM CE, buy OTM CE 400 higher.
- Window: 10:15–14:15 IST.
- SL: same `(3000 / margin_required) * 100` dynamic cap. [dhanhq](https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226)

### Approximate Python skeleton

This is a simplified GBM‑style scoring; you can refine it once you have data.

```python
import numpy as np
import pandas as pd
from dataclasses import dataclass
from enum import Enum

class Direction(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"

@dataclass
class DriftSignal:
    ts: pd.Timestamp
    direction: Direction
    gbm_score: float
    skew_score: float
    alpha: float

def gbm_band_probability(spot: float, mu: float, sigma: float,
                         T: float, lower: float, upper: float) -> float:
    """
    Very rough GBM band-probability proxy over horizon T (in years).
    In practice you'd use a more exact barrier probability; here we
    approximate via normal CDF on log-returns at T.
    """
    if sigma <= 0 or T <= 0:
        return 0.5
    log_l = np.log(lower / spot)
    log_u = np.log(upper / spot)
    mean = (mu - 0.5 * sigma**2) * T
    std = sigma * np.sqrt(T)
    from math import erf, sqrt
    def cdf(x): return 0.5 * (1 + erf(x / (std * np.sqrt(2))))
    return float(cdf(log_u - mean) - cdf(log_l - mean))

def compute_drift_metrics(spot_series: pd.Series,
                          window_bars=48) -> pd.DataFrame:
    """
    spot_series: 5-min NIFTY close prices.
    window_bars: rolling window (~4h if 5-min bars).
    Returns DF with rolling drift (mu) and vol (sigma).
    """
    log_ret = np.log(spot_series / spot_series.shift(1))
    mu = log_ret.rolling(window_bars).mean() * (12 * 48)   # approx to yearly
    sigma = log_ret.rolling(window_bars).std() * np.sqrt(12 * 48)
    return pd.DataFrame({"mu": mu, "sigma": sigma})

def generate_drifting_signals(df_nifty: pd.DataFrame,
                              chain_hist: dict,
                              horizon_minutes=60,
                              hi=0.7, lo=0.3) -> list[DriftSignal]:
    """
    df_nifty: 5-min NIFTY OHLC with 'close' column.
    chain_hist: {ts: option_chain_df} with 'iv', 'volume', 'oi', 'type', 'strike'.
    """
    drift_df = compute_drift_metrics(df_nifty["close"])
    signals = []

    for ts, row in drift_df.dropna().iterrows():
        t = ts.time()
        if not (t >= pd.to_datetime("10:15").time() and t <= pd.to_datetime("14:15").time()):
            continue
        if ts not in chain_hist:
            continue

        spot = float(df_nifty.at[ts, "close"])
        mu, sigma = row["mu"], row["sigma"]
        T = horizon_minutes / (60 * 252)  # 1-day ~ 252 days/year

        lower, upper = spot - 400, spot + 400
        p_band = gbm_band_probability(spot, mu, sigma, T, lower, upper)

        # crude IV skew measure: call IV - put IV at ATM
        chain = chain_hist[ts]
        chain["moneyness"] = (chain["strike"] - spot).abs()
        atm = chain.sort_values("moneyness").groupby("type").head(1)
        try:
            iv_call = float(atm[atm["type"] == "CE"]["iv"].iloc[0])
            iv_put = float(atm[atm["type"] == "PE"]["iv"].iloc[0])
            skew = iv_call - iv_put
        except IndexError:
            skew = 0.0

        # normalize roughly to [-1, 1]
        skew_score = float(np.tanh(skew / 0.05))
        # combine:
        # high p_band (range-bound) → favor selling spreads
        # sign of skew nudges direction
        alpha = p_band * (1 + 0.3 * np.sign(skew_score))

        if alpha > hi:
            direction = Direction.BULLISH
        elif alpha < lo:
            direction = Direction.BEARISH
        else:
            continue

        signals.append(DriftSignal(ts, direction, p_band, skew_score, alpha))

    return signals

@dataclass
class SpreadOrder:
    ts: pd.Timestamp
    direction: Direction
    short_strike: float
    long_strike: float
    short_type: str
    long_type: str
    qty: int
    sl_pct: float

def build_spread_from_drifting(sig: DriftSignal, spot: float,
                               lot_size: int, margin_per_lot: float,
                               max_loss_rupees=3000.0) -> SpreadOrder:
    atm = round(spot / 50) * 50
    if sig.direction == Direction.BULLISH:
        short_strike, long_strike = atm, atm - 400
        opt_type = "PE"
    else:
        short_strike, long_strike = atm, atm + 400
        opt_type = "CE"
    sl_pct = max_loss_rupees / margin_per_lot * 100.0
    return SpreadOrder(sig.ts, sig.direction,
                       short_strike, long_strike,
                       opt_type, opt_type,
                       lot_size, sl_pct)
```

***

## How I’d choose between them

For your own deployment with DhanHQ:

- **Start with Zen**  
  - Simplest to implement and backtest; well documented; strong 1‑yr returns. [dhanhq](https://dhanhq.co/algos/managers/stratzy/zen-credit-spread-overnight/68596cd26aa2cba24bbb67da)
- **Add Curvature** once you have solid option‑chain intraday data  
  - Captures smile/liquidity distortions; more complex infra requirement. [stratzy](https://stratzy.in/algo-trading-strategies?filterBy=all)
- **Use Drifting** as a *regime‑filter*  
  - Favor Drifting on historically range‑bound days, Zen/Curvature when directional or skewed.

If you want, next step I can help you wire one of these skeletons directly to DhanHQ (sandbox + live) with a clean portfolio allocator so you can, say, put ₹50k into Zen and ₹50k into Curvature, as you described earlier.