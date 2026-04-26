# Mean Reversion – Strategy Specification

> **Type:** Mean-reversion / fade  
> **Timeframe:** 15m (primary) + 1H (confirmation)  
> **Goal:** Profit from price snapping back to its mean after overextension  
> **Philosophy:** Be contrarian when the crowd panics. Take profit when normality returns.

---

## 1. Signal Logic

### 1.1 Overextension Detection (Primary – 15m)

| Indicator | Condition | Role |
|---|---|---|
| `Bollinger Band (20, 2σ)` | `Close ≤ Lower Band` | Price is statistically overextended to the downside |
| `RSI(14)` | `< 30` | Oversold momentum confirmation |
| `ADX(14)` | `< 25` | Market is range-bound (not trending) |
| `Volume` | `> 1.5 × SMA(20) of volume` | Capitulation / exhaustion spike |

**BUY signal** requires **all four conditions** to be true simultaneously.

> **Why ADX < 25?** This is the inverse of Smart Hodler's `ADX > 25` filter. Mean reversion only works in range-bound markets. If ADX is high, the market is trending — and fading a trend is how accounts blow up. The two strategies are regime-complementary: they never generate BUY signals at the same time.

**SELL signal** — tiered exit system (profit targets):

| Priority | Condition | Action | Rationale |
|---|---|---|---|
| 1 — Middle reversion | `Close ≥ SMA(20)` (middle Bollinger Band) | Exit 50% | Price reached the mean — lock in partial profit |
| 2 — Full reversion | `Close ≥ Upper Band` OR `RSI > 70` | Exit remaining 100% | Overextended to the upside — reversion complete |
| 3 — Regime shift | `ADX rises > 30` while in position | Exit 100% | Market shifted to trending — abort reversion thesis |

> **Why tiered?** The middle band is the most probable target (mean reversion to the mean). Taking 50% there locks in profit. Holding the remainder for the upper band or RSI 70 captures the occasional overshoot. The ADX > 30 safety valve exits if the ranging market suddenly trends — our thesis is invalidated.

**HOLD** in all other cases (e.g., RSI between 30–70 with no sell trigger).

### 1.2 Hourly Confirmation (1H)

Before entering, check the 1H chart:

- `RSI(14)` (1H) must be **above 35** — avoids buying dips in a macro downtrend (falling knife filter)
- `Close` (1H) must be **above EMA(50)** — price still holds above long-term support on higher timeframe

If hourly confirmation fails → **HOLD** (do not enter, but do not exit existing positions either).

> **Why?** On 15m, price can dip below Bollinger Bands during a legitimate macro sell-off. The hourly filter prevents buying the dip when the dip is actually a cliff. If 1H RSI is below 35 or price is below EMA 50, the "mean" itself is likely shifting lower — reversion to a falling mean is not a trade we want.

### 1.3 Session Filter

Same dead zones as Smart Hodler — low liquidity produces unreliable mean reversion:

**Rules:**
- **No new entries** during dead zones: `Saturday 21:00 – Sunday 20:00 UTC` and `Weekdays 21:00 – 01:00 UTC`
- Existing positions are **not affected** — stops and exits operate normally during these windows

> **Why?** Mean reversion relies on the market having enough participants to push price back to the mean. During low-liquidity windows, the "snap back" may not happen at all — price can stay overextended for hours, eventually hitting our hard stop. Exits remain active because protecting capital is always a priority.

### 1.4 Signal Summary

```
BUY       = (Close ≤ Lower BB) AND (RSI < 30) AND (ADX < 25)
            AND (Volume > 1.5 × SMA20_Volume) AND (1H OK) AND (Session OK)
SELL  50% = Close ≥ SMA(20)  (middle Bollinger Band)
SELL 100% = (Close ≥ Upper BB) OR (RSI > 70) OR (ADX > 30)
HOLD      = everything else
```

---

## 2. Position Sizing

Simpler than Smart Hodler — no scaled entries because mean reversion trades are shorter duration with defined targets:

| Condition | Action | Size |
|---|---|---|
| First BUY signal | Enter position | 75% of available capital |
| SELL 50% signal (middle band hit) | Partial exit | 50% of current position |
| SELL 100% signal (upper band / RSI / ADX) | Full exit | Remaining position |

### Why no scale-in?

- Mean reversion trades are expected to resolve within ~1–6 hours
- The entry itself is the highest-conviction moment (extreme overextension)
- Adding capital later increases risk without the trend-persistence logic that justifies it in Smart Hodler
- 75% (not 100%) leaves a buffer for a second setup on a different symbol

---

## 3. Stop-Loss

### Hard Stop

- **Max drawdown per trade:** `-2%` from entry price
- Tighter than Smart Hodler's 3% because mean reversion trades have smaller expected moves
- If triggered → exit immediately, enter **cooldown period** (8 candles / ~2 hours)

### Time Stop

- If position hasn't reached the middle Bollinger Band within **16 candles (~4 hours)** → exit at market
- The reversion thesis has a time limit — if price stays overextended for 4 hours, the "mean" is likely shifting
- This is a new concept not present in Smart Hodler

### No Trailing Stop

- Mean reversion targets a fixed level (the mean), not open-ended upside
- A trailing stop would prematurely exit on the choppy path back to the mean
- Risk is managed via hard stop + time stop instead

---

## 4. Re-Entry After Exit

After a stop-loss exit:

- **Cooldown period:** 8 candles (~2 hours, no BUY signals allowed)
- Shorter than Smart Hodler's 16-candle cooldown because mean reversion setups appear more frequently
- After normal profit exits (SELL 50%/100%), **no cooldown** — re-entry is immediate if conditions align
- After time stop, **4-candle cooldown** (~1 hour) — brief pause to let the setup develop

---

## 5. Indicators Required

| Indicator | Period | Timeframe | Purpose |
|---|---|---|---|
| SMA | 20 | 15m | Bollinger Band center / reversion target |
| Bollinger Bands | 20, 2.0σ | 15m | Overextension envelope |
| RSI | 14 | 15m | Oversold/overbought detection |
| ADX | 14 | 15m | Regime filter (range vs trend) |
| Volume SMA | 20 | 15m | Exhaustion volume confirmation |
| RSI | 14 | 1H | Macro momentum filter (falling knife guard) |
| EMA | 50 | 1H | Higher-TF support level |

### New Indicators (not yet in indicator engine)

| Indicator | Function Needed | Status |
|---|---|---|
| **Bollinger Bands** | `calculate_bollinger_bands(close, period=20, std_dev=2.0)` → upper, middle, lower Series | ❌ Not implemented |

> SMA and RSI already exist. Only Bollinger Bands must be added to `core/indicators/technical.py`.

---

## 6. State Machine

Reuses existing `StrategyState` enum: `FLAT`, `POSITION`, `REDUCED`, `COOLDOWN`.

```
                    ┌──────────────┐
         ┌─────────│   COOLDOWN   │◄──── hard stop or time stop
         │         │  (~2 hours)  │
         │         └──────────────┘
         │ cooldown expires
         ▼
   ┌───────────┐   BUY signal    ┌──────────────┐
   │   FLAT    │────────────────►│  POSITION    │
   │ (no pos)  │                 │  (watching)  │──── SELL 50% ───►┌────────────┐
   └───────────┘                 └──────────────┘                  │  REDUCED   │
         ▲                         │                               │  (partial) │
         │                         │ SELL 100%                     └────────────┘
         │                         │ (upper BB / RSI / ADX)          │ SELL 100%
         │                         ▼                                 ▼
         └───────────────────── FLAT ◄─────────────────────────── FLAT
         (no cooldown on                              (no cooldown on
          profit exits)                                profit exits)
```

### States

| State | Description |
|---|---|
| `FLAT` | No position. Watching for oversold dip. |
| `POSITION` | Holding full position. Watching for middle band (partial exit) or upper band (full exit). |
| `REDUCED` | Partial position after middle band hit. Watching for upper band / RSI / ADX shift. |
| `COOLDOWN` | Exited via stop-loss or time stop. Waiting 8 candles (~2h) before re-evaluating. |

### Key difference from Smart Hodler

- **No cooldown on profit exits** — mean reversion setups can cluster (multiple dips in a ranging market)
- **Cooldown only on stops** — stops indicate the mean is shifting, so we pause

---

## 7. Decision Interface

```python
class MeanReversion(Strategy):
    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Pure decision function.
        
        Reads:
          - market_data.indicators (Bollinger Bands, RSI, ADX, Volume SMA)
          - market_data.candles (close prices)
          - portfolio.positions (current exposure)
          - self.state (FLAT / POSITION / REDUCED / COOLDOWN)
        
        Returns:
          - Signal.BUY       → risk engine decides size (75% of capital)
          - Signal.SELL_FULL  → exit 100% of position
          - Signal.SELL_HALF  → exit 50% of position (middle band hit)
          - Signal.HOLD       → do nothing
        """
```

The strategy **never**:
- Places orders
- Calls APIs
- Modifies portfolio directly
- Accesses execution details

---

## 8. Backtest Expectations

For BTC/USDT on 15m (2023–2025), this strategy should:

| Metric | Target |
|---|---|
| Win rate | ~55–65% (higher than Smart Hodler — defined targets are easier to hit) |
| Avg trade duration | ~1–6 hours (shorter than Smart Hodler) |
| Avg win size | Smaller per trade (bounded by Bollinger Band width) |
| Avg loss size | ~2% max (tighter hard stop) |
| Performance: ranging markets | ✅ Outperforms Smart Hodler |
| Performance: trending markets | ≈ Flat / small loss (ADX filter keeps it out) |
| Total trades | ~30–80 per month (fewer than Smart Hodler) |
| Max drawdown | Lower than buy & hold |

> **Note:** Mean reversion and Smart Hodler are designed to be complementary. In multi-strategy mode (Phase 5), they alternate activity based on market regime — one is always "off" when the other is "on".

---

## 9. Regime Complementarity with Smart Hodler

The two strategies are explicitly designed to not overlap:

| Market Regime | ADX Level | Smart Hodler | Mean Reversion |
|---|---|---|---|
| Strong trend | > 25 | **Active** — riding the trend | Inactive — ADX filter blocks entry |
| Range-bound | < 25 | Inactive — ADX filter blocks entry | **Active** — buying dips |
| Transition (25 ± 3) | ~22–28 | May generate exit signals | May generate exit signals |

> **Future (Phase 5):** A regime allocator can direct capital to the appropriate strategy based on ADX, reducing idle capital. See task 5.4 (Portfolio allocation engine).

---

## 10. Future Improvements (Post-MVP)

- **Double bottom detection:** Require a second touch of the lower BB before entry for higher-conviction setups
- **Dynamic band width:** Use Bollinger Band Width (BBW) to skip entries when bands are too narrow (low-vol squeeze → breakout, not reversion)
- **Multi-asset mode:** Run Mean Reversion on top 10 alts — altcoins tend to mean-revert more aggressively than BTC
- **Adaptive volume threshold:** Replace fixed 1.5× multiplier with percentile-based threshold (e.g., > 90th percentile of recent volume)
- **Pair with VIX/crypto fear index:** Enter only when market-wide fear is elevated for higher-probability setups
