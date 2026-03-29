<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# I have DhanHQ developer access and API, sandbox etc.,

how to develop the above discussed strategies using dhan api and backtest them , how to use sandbox

and finally once the strategy is figured out how to deploy it on live trading?

and can I deploy multiple strategies by virutally allocating some money to each strategy like if I have 1,00,000Rs  and I like 2 strategies, can I tell my automation to use 50-50 bteween both the strategies? how to implement it..

full step by step

You’ll build and test these strategies in three layers: (1) data + backtest, (2) sandbox execution, (3) live deployment with capital allocation logic across multiple strategies. Below is a concrete step‑by‑step plan tailored to Zen/Curvature and DhanHQ.

***

## 1. Set up DhanHQ API and Sandbox

1. Create DevPortal + Sandbox access
    - Go to DhanHQ DevPortal (from dhanhq.co → “Dev Portal / Trading APIs”).[^1][^2]
    - In DevPortal, generate:
        - Sandbox API key / access token.
        - Live API key / access token (later, for real trading).[^3][^4]
2. Understand the two environments
    - Base URLs:
        - Sandbox: `https://sandbox.dhan.co/...`
        - Live: `https://api.dhan.co/...` or as per v2 docs.[^4][^5][^3]
    - API paths and request structure are the same; to move from sandbox to live, you essentially switch the base URL and tokens.[^5][^3][^4]
3. Install the Python SDK
    - Use their official Python client (`dhanhq` or the GitHub client), which wraps the REST API.[^6][^7]
    - Example (from PyPI/GitHub):[^7][^6]

```python
from dhanhq import dhanhq

# for sandbox, pass sandbox base URL if client exposes it, or patch requests session
dhan = dhanhq("CLIENT_ID", "ACCESS_TOKEN")  
```

    - You’ll use:
        - `place_order` for F\&O orders.
        - `intraday_minute_data` / `intraday_daily_minute_charts` or `historical_*` for backfill.
        - `option_chain` / `expiry_list` for IV, strikes, etc.[^6][^5][^7]

***

## 2. Backtesting architecture (offline, no sandbox)

The sandbox is for API behavior testing, not for real historical backtests. For serious backtests you do it locally: pull historical data, run your Zen/Curvature logic on it, and simulate orders.

### 2.1. Historical data retrieval

1. Get historical NIFTY spot and options
    - Use Dhan historical/intraday APIs for spot:
        - `intraday_minute_data` or `intraday_daily_minute_charts`.[^7][^6]
    - For options, you’ll either:
        - Use Dhan’s historical minute charts for each NIFTY option you care about (ATM ± 400, near expiry).
        - Or, if not available in bulk, use NSE data or a third‑party vendor for a better quality options history.
2. Build your backtest dataframes
    - For Zen approximation:
        - `df_nifty_5m`: index = timestamp, columns = `open`, `close`.
        - Add columns for ATM CE/PE volumes and IVs at each bar (e.g., `atm_ce_vol`, `atm_pe_vol`, `atm_ce_iv`, `atm_pe_iv`).
    - For Curvature approximation:
        - `chain_history[timestamp]` = DataFrame of that timestamp’s option chain for NIFTY (strike, iv, volume, oi, type).

### 2.2. Integrate the strategy logic

You already have the two skeletons I gave you:

- `zen_credit_spread_overnight.py` – builds `CreditSpreadSignal` and `SpreadOrder` objects.
- `curvature_credit_spread_overnight.py` – builds `CurvatureSignal` and corresponding `SpreadOrder`.

Backtest loop (Zen example):

1. Load `df_nifty_5m` with the required columns.
2. Call `generate_signals(df, atm_ce_symbol, atm_pe_symbol)` to get a list of signals for the historical period.
3. For each signal:
    - Pick the simulated spot at `signal.timestamp`.
    - Compute margin for the spread (approximate using fixed margin per lot or more precise margin model).
    - Call `construct_credit_spread(signal, spot, lot_size, margin_required_per_lot, max_rupee_loss_per_trade)`.
    - Simulate order fills:
        - Get the option mid-prices at that timestamp from your options data.
        - Compute entry cost / premium received.
    - Simulate exits:
        - Either exit at next day open, or
        - Apply dynamic SL in backtest by tracking P\&L vs `stop_loss_pct` on mark‑to‑market prices each bar.
4. Aggregate backtest metrics
    - P\&L curve, drawdown, win‑rate, average trade size, etc.
    - Compare approximate performance vs Dhan’s backtest snapshot to see if you’re in the same ballpark.

You repeat the same architecture for Curvature, with `generate_curvature_signals` and `construct_spread_from_curvature`.

***

## 3. Using Dhan Sandbox effectively

Sandbox is for: order flow, error handling, position lifecycle, not financial performance (prices are simulated / static).[^3][^4][^1]

1. Configure client to hit sandbox
    - Depending on the Python SDK, either:
        - Pass sandbox flag / base URL, or
        - Override `.session.base_url` to `https://sandbox.dhan.co`.
    - Use sandbox tokens from DevPortal.[^4][^3]
2. Wire up your strategy engine → sandbox orders

Pseudo‑architecture:

```python
from dhanhq import dhanhq
from zen_credit_spread_overnight import generate_signals, construct_credit_spread

dhan = dhanhq(SANDBOX_CLIENT_ID, SANDBOX_ACCESS_TOKEN)

def place_spread(order, instrument_mapper):
    # instrument_mapper: your mapping from (strike, type, expiry) → security_id (NIFTY options)
    short_id = instrument_mapper(order.short_strike, order.short_opt_type)
    long_id  = instrument_mapper(order.long_strike, order.long_opt_type)

    # short leg
    dhan.place_order(
        security_id=short_id,
        exchange_segment=dhan.NSE_FNO,
        transaction_type=dhan.SELL,
        quantity=order.quantity,
        order_type=dhan.MARKET,
        product_type=dhan.INTRA,
        price=0,
    )

    # long leg
    dhan.place_order(
        security_id=long_id,
        exchange_segment=dhan.NSE_FNO,
        transaction_type=dhan.BUY,
        quantity=order.quantity,
        order_type=dhan.MARKET,
        product_type=dhan.INTRA,
        price=0,
    )
```

3. Test key flows in sandbox
    - Place orders (single‑leg and both spread legs).
    - Modify / cancel orders.
    - Fetch positions (`get_positions`), holdings, trade history.[^6][^7]
    - Fetch fund limits and validate your margin assumptions.
4. Validate automation behavior
    - Run your bot in “dry‑run” mode:
        - Strategy logic runs on real‑time/simulated data you feed it.
        - Orders go to sandbox.
    - Check logs, error handling (rate limits, rejections, etc.).

***

## 4. Going live with real capital

Once backtest + sandbox behavior look solid, you switch to live.

1. Hardening + configuration
    - Put all environment‑specific variables in config or env:
        - Base URL (`sandbox.dhan.co` vs `api.dhan.co`).[^5]
        - API keys / access tokens.
        - Capital allocation per strategy.
    - Use a central risk module that can:
        - Read current fund limits via `get_fund_limits`.
        - Enforce max daily loss, per‑trade loss, and no. of open positions.[^7][^6]
2. Connect to live DhanHQ
    - Instantiate client with live keys and live base URL.
    - Disable any sandbox‑specific test flags.
3. Live strategy loop (high‑level)

Per strategy (e.g., Zen):

- A scheduler (cron, systemd timer, k8s job) runs every 5 minutes during 10:15–14:15 IST.
- On each tick:

1. Pull latest 5‑minute bar data + ATM option data (or from your data feed if you prefer).
2. Update in‑memory DataFrame and compute signals.
3. If new signal occurs and you have no existing open trade in this strategy for NIFTY:
        - Query `get_positions()` to ensure no overlapping positions.
        - Construct spread via `construct_credit_spread`.
        - Map to `security_id`s and call `place_order` for both legs.
        - Store a record in your DB with correlation ID / strategy ID for later tracking.
- A separate risk/exit loop monitors positions:
    - Periodically check MTM P\&L vs per‑spread SL.
    - Place exit orders when SL or time‑based exit condition hits.

***

## 5. Capital allocation across multiple strategies (virtual 50–50)

You absolutely can do a virtual capital allocation like ₹1,00,000 split 50–50 between Zen and Curvature. Implementation is fully in your code; Dhan just sees orders.

### 5.1. Define a portfolio manager layer

Create a small “portfolio allocator” module that sits above individual strategies:

```python
class StrategyAccount:
    def __init__(self, name, capital_allocated):
        self.name = name
        self.capital_allocated = capital_allocated
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0

    @property
    def equity(self):
        return self.capital_allocated + self.realized_pnl + self.unrealized_pnl
```

At startup (with ₹1,00,000 total):

- Strategy Zen: `capital_allocated = 50000`
- Strategy Curvature: `capital_allocated = 50000`


### 5.2. Position sizing per strategy

When a strategy wants to place a spread, it asks the portfolio manager for allowed size:

1. Estimate per‑spread margin from Dhan in live:
    - Place a dry‑run or use approximate margin table based on NIFTY spread width and lot size.
2. Compute max allowed lots:

$$
\text{lots} = \left\lfloor \frac{\alpha \times \text{strategy\_equity}}{\text{margin\_per\_lot}} \right\rfloor
$$

where `α` is your per‑trade risk fraction (e.g., 0.1 for 10% of that strategy’s equity).
3. If `lots == 0`, skip trade (insufficient capital).

This way:

- Zen and Curvature each make independent decisions.
- Lot size is constrained by that strategy’s virtual equity, not total broker funds.


### 5.3. Tracking P\&L per strategy

Since the broker only knows your consolidated account, you track strategy‑level P\&L in your own DB:

- Every order you send carries a `strategy_id` and `correlation_id` in your app’s DB (not necessarily in Dhan itself).
- When you fetch `trade_book` / `positions` from Dhan:[^6][^7]
    - Map each trade back to the strategy using your correlation mapping.
    - Recalculate realized and unrealized P\&L per strategy using your own prices / position records.

You can then:

- Update each `StrategyAccount.realized_pnl` and `unrealized_pnl`.
- Automatically rebalance (e.g., keep 50–50 or let them drift).


### 5.4. Enforcing global and per‑strategy risk

Implement simple guards:

- Global: stop all trading if total realized P\&L for the day < −X% of total capital.
- Per‑strategy: stop a strategy if its equity drawdown exceeds Y%.
- Per‑trade: use the same `max_rupee_loss_per_trade` SL the strategy logic uses (e.g., ₹3,000).

All of this is handled by your own code before placing orders, not by Dhan directly.

***

## 6. Suggested concrete roadmap for you

Given your stack:

1. **Week 1** – Backtest offline
    - Use the two Python strategy files.
    - Build NIFTY + options historical datasets.
    - Run full backtests and tune parameters.
2. **Week 2** – Sandbox integration
    - Wrap strategy outputs into Dhan sandbox orders (using `place_order`, `get_positions`, etc.).[^7][^6]
    - Test all lifecycle operations and failure cases.
3. **Week 3** – Portfolio manager + live dry‑run
    - Implement the `StrategyAccount` abstraction and per‑strategy sizing.
    - Run live logic but:
        - Either send to sandbox in parallel, or
        - Use a “paper” mode that logs orders instead of placing real ones.
4. **Week 4** – Small‑size live deployment
    - Point to live API, use 1 lot per strategy.
    - Gradually scale lot sizes as you gain confidence.

If you share your preferred stack (FastAPI/Flask daemon, cron + pure Python, or n8n orchestration), I can next help you with a minimal “strategy runner” service skeleton wired to DhanHQ, including multi‑strategy allocation and configuration.
<span style="display:none">[^10][^11][^12][^13][^14][^15][^8][^9]</span>

<div align="center">⁂</div>

[^1]: https://dhanhq.co

[^2]: https://dhanhq.co/trading-apis/individuals-geeks

[^3]: https://www.linkedin.com/posts/dhanhq_introducing-dhanhq-sandbox-devportal-activity-7329015456743211011-VJl3

[^4]: https://www.youtube.com/watch?v=Mzue9J4Z_0U

[^5]: https://dhanhq.co/docs/v2/

[^6]: https://pypi.org/project/dhanhq/

[^7]: https://github.com/bluenton/DhanHQ

[^8]: https://www.youtube.com/watch?v=lZBP8_UPQZQ

[^9]: https://abhipandey.com/2023/01/mastering-automated-trading-with-python-and-the-dhan-api/

[^10]: https://docs.openalgo.in/connect-brokers/brokers/dhan

[^11]: https://pypi.org/project/pydhan/

[^12]: https://www.youtube.com/watch?v=WWasB5sN63k

[^13]: https://madefortrade.in/t/dhan-hq-api-algo-trading/28274

[^14]: https://dhanhq.co/algos

[^15]: https://www.youtube.com/watch?v=3uE1gkn4Xno

