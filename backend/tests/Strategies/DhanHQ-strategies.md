<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# on dhanhq.co there are strategies to be deployed and the best are these

strategies :
Zen Credit Spread Overnight
Curvature Credit Spread Overnight

research and find out what these strategies are and if I can replicate them, get the logic of these strategies and summarize me and give the strategies scripts files.

Here’s the distilled logic for both strategies and how you can approximate/replicate them, plus you now have two Python strategy skeletons you can plug into your own infra.

***

## What Zen Credit Spread Overnight does

Zen is a NIFTY 50 options credit-spread algo that uses volatility- and volume-based mean-reversion signals to sell hedged premium during a mid-day window and hold positions overnight.[^1][^2][^3]

Core characteristics (from the Dhan/Stratzy description):[^2][^1]

- Instrument and style
    - Underlying: NIFTY 50 index options.
    - Style: “NiftyHedgedDirectional” – always trades hedged vertical credit spreads (not naked).[^1]
- Signal construction
    - Works on 5‑minute bars.
    - Alpha 1 (`alpha`): time-series rank of the 5‑minute forward price change normalized by opening price, using an 800‑minute (≈160 bars) lookback.[^1]
    - Alpha 2 (`alpha2`):
        - Starts with price change.
        - Multiplies by the average volume ratio of ATM put vs ATM call options (volume confirmation).
        - Divides by an ATM volatility measure (rolling vol of ATM CE + PE).[^1]
        - Then applies a 300‑minute time-series rank.[^1]
- Trade logic
    - When both `alpha` and `alpha2` are high (e.g., > 0.8), it treats this as strong bullish conviction and sells a credit put spread:
        - Sell ATM put.
        - Buy ITM put 400 points lower (hedge).
    - When both are low (e.g., < 0.2), it treats this as strong bearish conviction and sells a credit call spread:
        - Sell ATM call.
        - Buy OTM call 400 points higher.[^1]
- Risk and timing
    - Operates only between 10:15 and 14:15 to avoid open/close volatility.[^3][^2][^1]
    - Uses stop-loss “based on margin requirements” and holds as a hedged spread; in their other “variant of Zen” strategies like V‑Score and Drifting, this is explicitly implemented as `SL% = (3000 / margin_required) * 100`, i.e., per-trade max loss ≈ ₹3,000 regardless of margin.[^4][^5][^1]

So high-level: it’s an intraday signal engine on 5‑minute bars that converts volatility + volume mean-reversion signals into one hedged NIFTY credit spread per signal and carries that overnight with a margin-normalized stop.

***

## What Curvature Credit Spread Overnight does

Curvature is explicitly labeled as a **variant of Zen Credit Spread Overnight**, still “NiftyHedgedDirectional,” but with a different way of generating the directional signal based on option-chain “liquidity flow,” “viscosity,” and “curvature” across strikes.[^6][^2][^3]

From Dhan/Stratzy:[^6][^2][^3]

- Still a hedged NIFTY options credit spread algo (same general template: sell ATM, hedge with OTM/ITM; overnight credit spreads).
- Conceptual engine:
    - Thinks of the market as a fluid:
        - “Viscosity” ≈ how thick or thin trading flow is around certain strikes.
        - “Curvature” ≈ how the flow/IV profile bends across strikes (i.e., shape of IV smile/skew and liquidity distribution).
    - When the combination of viscosity and curvature goes out of balance, it bets on **rebalancing** by selling credit spreads in the direction that benefits from a return to a smoother, more “efficient” flow.[^7][^8][^6]
- It is explicitly said to be a variant of Zen, so structure is almost certainly:
    - Same NIFTY credit spread templates (ATM ± 400).
    - Same hedged, overnight layout.
    - Similar SEBI‑conscious risk framework and uncorrelated behavior vs other Stratzy algos.[^5][^6]

The exact math (how they compute curvature, viscosity, thresholds) is **not disclosed** in the public docs; we only get the qualitative description and the statement that it’s a Zen variant.[^8][^7][^6]

***

## How close you can get to replicating them

### Zen Credit Spread Overnight

For Zen, the public description is detailed enough that you can build a pretty faithful approximation:

To replicate, you’d need:

- Data
    - 5‑minute OHLC for NIFTY 50.
    - 5‑minute ATM CE/PE volumes and implied vols (or at least some realized-vol proxy) for the near expiry you’re trading.[^1]
- Steps (approximate implementation)

1. Build a 5‑minute DataFrame for NIFTY with `open`, `close`, indexed by timestamp.
2. Compute `alpha`:
        - Forward 5‑minute return: $r_{t+5} = \frac{\text{close}_{t+1}}{\text{open}_t} - 1$.
        - Use a rolling window of 800 minutes (≈160 bars).
        - For each window, rank the last value within the window and scale to  (time-series rank). Values near 1 are strong positive forward returns; near 0 are strong negative.[^1]
3. Compute `alpha2`:
        - Forward return on close/close.
        - Volume ratio: $(\text{ATM\_PE\_vol} + \epsilon) / (\text{ATM\_CE\_vol} + \epsilon)$.
        - ATM vol: sum of rolling vol of ATM CE and PE (e.g., std dev of IV or realized vol over 300 minutes).[^1]
        - Raw signal = forward return × volume ratio ÷ ATM vol.
        - Apply a 300‑minute time-series rank to get `alpha2` in.[^1]
4. Signal rules:
        - If `alpha > 0.8` and `alpha2 > 0.8` in the 10:15–14:15 window → bullish.
        - If `alpha < 0.2` and `alpha2 < 0.2` in the same window → bearish.[^1]
5. Position construction (NIFTY options):
        - Round spot to nearest 50 for ATM strike.
        - Bullish:
            - Sell ATM PE.
            - Buy ITM PE 400 points lower.
        - Bearish:
            - Sell ATM CE.
            - Buy OTM CE 400 points higher.[^4][^5][^1]
6. Risk:
        - Calculate margin required per spread from your broker.
        - Per-trade SL% ≈ `3000 / margin_required * 100` is consistent with how their other overnight variants define it, but Zen’s exact SL formula is not explicitly stated; treat this as a reasonable approximation.[^5][^4]
        - Enforce “only one active trade” per symbol at a time and exit on SL or at expiry/morning next day, depending on your execution design.[^4][^5]

So yes — Zen is realistically replicable from the public description, with only minor guesswork on the exact constants and SL formula.

### Curvature Credit Spread Overnight

For Curvature, the **execution template is straightforward** (same hedged NIFTY credit spreads, ATM ± 400, overnight) but the **curvature/viscosity math is proprietary**.[^2][^7][^8][^6]

You can still build a very similar idea:

- Build an IV smile + liquidity view at each timestamp: strike, IV, volume, OI.
- Define metrics:
    - Curvature: fit a quadratic to IV vs moneyness (strike/spot − 1); the coefficient of $x^2$ (or its magnitude) is your curvature score.
    - Viscosity: ratio of liquidity near ATM to liquidity in the wings (volume + OI density near ATM).
- Turn those into an “alpha” like this (what I coded for you):
    - Compute a rolling mean and std of curvature; convert today’s curvature into a z‑score.
    - Weight the z‑score by log(viscosity) so high‑liquidity regimes matter more.
    - Squash that into  (e.g., via tanh) and treat:[^1]
        - Alpha > 0.7 → strong “over‑curved” state likely to mean‑revert → one side of spread.
        - Alpha < 0.3 → opposite direction.
- Map bullish/bearish alpha to the same credit spread templates as Zen.

This will not be *the* Stratzy algo, but it adheres to the same conceptual description and structure and is suitable for your own experimentation.

***

## Provided Python strategy skeletons

I’ve given you two Python files as starting points: one implementing a Zen-style approximation and one implementing a Curvature-style approximation. Both are designed as reusable modules you can integrate with your own data and Dhan/other broker APIs.

### 1. `zen_credit_spread_overnight.py`

What it contains (high level):

- `compute_alpha(df, lookback_minutes=800)`
    - Computes the time-series rank of 5‑minute forward return over an 800‑minute window.
- `compute_alpha2(df, atm_ce_symbol, atm_pe_symbol, vol_lookback_minutes=300)`
    - Implements the volume + volatility scaled signal as described above: forward return × vol_ratio ÷ ATM vol, then time-series rank.[^1]
- `generate_signals(...) -> list[CreditSpreadSignal]`
    - Filters to 10:15–14:15.
    - Emits `CreditSpreadSignal` objects when both alphas are > 0.8 or < 0.2.
- `construct_credit_spread(signal, spot_price, lot_size, margin_required_per_lot, max_rupee_loss_per_trade=3000)`
    - Converts a bullish/bearish signal into a concrete NIFTY vertical credit spread:
        - Bullish: sell ATM PE, buy ITM PE −400.
        - Bearish: sell ATM CE, buy OTM CE +400.
    - Computes `stop_loss_pct = max_rupee_loss_per_trade / margin_required_per_lot * 100`.

You need to:

- Build a 5‑minute DataFrame with:
    - NIFTY `open`, `close`.
    - `ATM_CE_vol`, `ATM_PE_vol`, `ATM_CE_iv`, `ATM_PE_iv` (column names must match what you pass to the functions).
- Plug your own data source and order routing.


### 2. `curvature_credit_spread_overnight.py`

What it contains (high level):

- `compute_iv_curvature(option_chain, underlying_price)`
    - Takes a single-timestamp option chain (strike, iv, type, volume, oi).
    - Converts strikes to moneyness and fits a quadratic IV = a x² + b x + c.
    - Uses |a|, normalized by mean IV, as a curvature score.
- `compute_liquidity_viscosity(option_chain, underlying_price)`
    - Splits strikes into near-ATM vs wings.
    - Computes a simple measure of how much more volume+OI density there is around ATM versus in the wings (liquidity “viscosity”).
- `curvature_alpha(curvature_score, viscosity_score, rolling_mean_curv, rolling_std_curv)`
    - Z‑scores curvature vs its recent history, weights by viscosity, and maps to.[^1]
- `generate_curvature_signals(chain_history, spot_history, ...) -> list[CurvatureSignal]`
    - Iterates over timestamps, computes curvature, viscosity, and alpha.
    - Filters to 10:15–14:15.
    - Emits bullish signals when alpha > 0.7, bearish when alpha < 0.3.
- `construct_spread_from_curvature(signal, spot_price, lot_size, margin_required_per_lot, max_rupee_loss_per_trade=3000)`
    - Same spread template and SL logic as the Zen skeleton.

You need to:

- Build `chain_history`: `dict[timestamp → DataFrame]`, each DataFrame containing at least `strike`, `iv`, `volume`, `oi`.
- Build `spot_history`: a `pd.Series` of NIFTY spot indexed by timestamp.
- Then call `generate_curvature_signals`, map each `CurvatureSignal` into `SpreadOrder`, and route via your broker.

***

## How to practically deploy / experiment

Given your background, a plausible workflow:

1. **Data ingestion layer**
    - Use Dhan APIs or NSE historical data to build:
        - NIFTY 5‑minute OHLC.
        - 5‑minute ATM options chain for near expiry (or your chosen expiry).
2. **Backtest first**
    - Run Zen skeleton on 1–2 years of NIFTY intraday + options data.
    - Validate win‑rate, average P\&L per trade, drawdown profile vs what Dhan shows in their backtest snapshot to see how close you are.[^5][^1]
3. **Tweak risk \& parameters**
    - Experiment with:
        - Thresholds (0.75/0.25 etc.).
        - Different hedge distances (e.g., 300 or 500 points).
        - Different max loss per trade (₹1,000 – ₹5,000) depending on your capital.
4. **Paper/live via Dhan API**
    - Wrap `SpreadOrder` into actual Dhan order payloads.
    - Ensure kill-switch / no overlapping trades per strategy.
    - Start with 1 lot until the behavior matches your expectations.

If you want, next step I can help you:

- Map these directly to Dhan’s API (Python client with actual order calls).
- Or convert the logic into something PineScript-like (for TradingView signal generation only).
<span style="display:none">[^10][^11][^12][^13][^14][^15][^9]</span>

<div align="center">⁂</div>

[^1]: https://dhanhq.co/algos/managers/stratzy/zen-credit-spread-overnight/68596cd26aa2cba24bbb67da

[^2]: https://dhanhq.co/algos/by-trading-style/option-hedging-algo

[^3]: https://dhanhq.co/algos/by-risk-tolerance/low-risk-algo

[^4]: https://dhanhq.co/algos/managers/stratzy/v-score-credit-spread-overnight/68599411ce6680fc5ff7b224

[^5]: https://dhanhq.co/algos/managers/stratzy/drifting-credit-spread-overnight/685995f2ce6680fc5ff7b226

[^6]: https://dhanhq.co/algos/managers/stratzy/curvature-credit-spread-overnight/687d08f09107a80e07401e57

[^7]: https://www.stratzy.in/algo-trading-strategies/curvature-credit-spread-overnight

[^8]: https://stratzy.in/algo-trading-strategies/curvature-credit-spread-overnight

[^9]: https://www.reddit.com/r/NSEbets/comments/1or046q/algo_testing_and_comparison_d4/

[^10]: https://www.reddit.com/r/thetagang/comments/12h4902/credit_spreads_theta_strategy/

[^11]: https://dhanhq.co/algos/popular-algo/most-deployed-algo

[^12]: https://www.youtube.com/watch?v=cqnL6-44ZOk

[^13]: https://dhanhq.co/algos/by-instrument/equity-trading-algo

[^14]: https://www.bajajbroking.in/blog/what-is-a-credit-spread-strategy

[^15]: https://www.youtube.com/watch?v=RzFeJPXVeFI

