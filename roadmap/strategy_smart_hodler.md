# Smart Hodler – Strategy Specification

> **Type:** Trend-following  
> **Timeframe:** 15m (primary) + 1H (confirmation)  
> **Goal:** Capture intraday and multi-hour trends, stay flat during chop  
> **Philosophy:** Be in the market when the trend is strong. Be out when it isn't.

---

## 1. Signal Logic

### 1.1 Trend Detection (Primary – 15m)

| Indicator | Condition | Role |
|---|---|---|
| `EMA 50` vs `EMA 200` | `EMA 50 > EMA 200` | Bullish bias (~12.5h vs ~50h of data) |
| `ADX(14)` | `> 25` | Trend is strong (higher threshold for noisy 15m) |
| `Close` vs `EMA 50` | `Close > EMA 50` | Price respects the trend |
| `Volume` | `> SMA(20) of volume` | Confirms participation (filters low-volume fakeouts) |

**BUY signal** requires **all four conditions** to be true simultaneously.

**SELL signal** — tiered exit system:

| Priority | Condition | Action | Rationale |
|---|---|---|---|
| 1 — Hard exit | `EMA 50 < EMA 200` | Exit 100% immediately | Trend structure is broken |
| 2 — Confirmed break | `Close < EMA 200` for **2 consecutive candles** | Exit 100% | Structure break confirmed (avoids wick fakeouts) |
| 3 — Momentum fade | `Close < EMA 50` AND `ADX falling` (current < 3 bars ago) | Exit 50% of position | Trend weakening but not yet reversed |

> **Why tiered?** A single candle closing below EMA 200 on 15m is often a wick/fakeout — exiting immediately on that alone causes unnecessary losses. Requiring 2-candle confirmation filters out noise while still exiting fast when the break is real. The partial exit on momentum fade locks in profits without abandoning a trend that may resume.

**HOLD** in all other cases (e.g., ADX < 25 but no sell trigger).

### 1.2 Hourly Confirmation (1H)

Before entering, check the 1H chart:

- `EMA 21` (1H) must be **rising** (current > 3 bars ago)
- `RSI(14)` (1H) must be **above 45** (not bearish momentum)

If hourly confirmation fails → **HOLD** (do not enter, but do not exit existing positions either).

### 1.3 Session Filter

Not all hours are equal. Low-liquidity sessions produce erratic price action that triggers false signals on 15m.

**Rules:**
- **No new entries** during dead zones: `Saturday 21:00 – Sunday 20:00 UTC` and `Weekdays 21:00 – 01:00 UTC`
- Existing positions are **not affected** — stops and exits operate normally during these windows
- Scale-in events (Section 2) are also **blocked** during dead zones — candle counter continues, but execution waits for the session to reopen

> **Why?** Crypto trades 24/7 but liquidity is not uniform. Weekend sessions and the late-UTC gap between US close and Asia open have wider spreads, thinner order books, and more erratic candles. Entering during these periods invites slippage and false breakouts. Exits remain active because protecting capital is always a priority.

### 1.4 Signal Summary

```
BUY       = (EMA50 > EMA200) AND (ADX > 25) AND (Close > EMA50)
            AND (Volume > SMA20_Volume) AND (1H OK) AND (Session OK)
SELL 100% = (EMA50 < EMA200) OR (Close < EMA200 for 2 consecutive candles)
SELL  50% = (Close < EMA50) AND (ADX falling)
HOLD      = everything else
```

---

## 2. Position Sizing

No more all-in / all-out. Smart Hodler uses **scaled entries**:

| Condition | Action | Size |
|---|---|---|
| First BUY signal | Enter initial position | 50% of available capital |
| BUY holds for 8 consecutive candles (~2h) | Scale in | +25% of available capital |
| BUY holds for 16 consecutive candles (~4h) | Scale in (final) | +25% of available capital |
| SELL 100% signal | Exit full position | 100% of position |
| SELL 50% signal | Partial exit (momentum fade) | 50% of current position |

### Why?

- Reduces impact of entering at a local spike
- Confirms trend persistence over a few hours before going full size
- Full exit on sell — downtrends don't deserve partial exposure

---

## 3. Stop-Loss

### Trailing Stop (ATR-based)

- **Stop distance:** `2 × ATR(14)` below the highest close since entry
- Recalculated every candle (15m)
- If `Close < trailing stop` → **exit 100%** regardless of other signals
- Tighter multiplier (2×) vs daily strategies because 15m ATR is smaller in absolute terms

### Hard Stop

- **Max drawdown per trade:** `-3%` from average entry price
- If triggered → exit immediately, enter **cooldown period** (16 candles / ~4 hours, no re-entry)
- Tighter than daily strategies because intraday moves are faster

---

## 4. Re-Entry After Exit

After a SELL or stop-loss exit:

- **Cooldown period:** 16 candles (~4 hours, no BUY signals allowed)
- After cooldown, normal signal logic resumes
- This prevents whipsaw trades around the crossover zone
- On 15m, whipsaws happen much faster — the cooldown is short but critical

---

## 5. Indicators Required

| Indicator | Period | Timeframe | Purpose |
|---|---|---|---|
| EMA | 50 | 15m | Fast trend line (~12.5 hours) |
| EMA | 200 | 15m | Slow trend line (~50 hours) |
| ADX | 14 | 15m | Trend strength filter |
| ATR | 14 | 15m | Stop-loss calculation |
| Volume SMA | 20 | 15m | Volume confirmation for entries |
| EMA | 21 | 1H | Hourly trend confirmation |
| RSI | 14 | 1H | Hourly momentum filter |

---

## 6. State Machine

```
                    ┌──────────────┐
         ┌─────────│   COOLDOWN   │◄──── stop-loss or SELL
         │         │  (~4 hours)  │
         │         └──────────────┘
         │ cooldown expires
         ▼
   ┌───────────┐   BUY signal    ┌──────────────┐
   │   FLAT    │────────────────►│  POSITION    │
   │ (no pos)  │◄────────────────│  (scaling)   │──── SELL 50% ───►│  REDUCED   │
   └───────────┘  SELL 100%      └──────────────┘◄── BUY resumes ──│  (partial) │
                    signal                                          └────────────┘
                                                                     │ SELL 100%
                                                                     ▼
                                                              ┌──────────────┐
                                                              │   COOLDOWN   │
                                                              └──────────────┘
```

### States

| State | Description |
|---|---|
| `FLAT` | No position. Watching for BUY signal. |
| `POSITION` | Holding. Scaling in on confirmation. Trailing stop active. |
| `REDUCED` | Partial position after momentum fade exit. Can scale back to POSITION if BUY conditions resume, or exit fully on SELL 100%. |
| `COOLDOWN` | Exited. Waiting 16 candles (~4h) before re-evaluating. |

---

## 7. Decision Interface

```python
class SmartHodler(Strategy):
    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Pure decision function.
        
        Reads:
          - market_data.indicators (EMA, ADX, ATR, RSI)
          - market_data.candles (close prices)
          - portfolio.positions (current exposure)
          - self.state (FLAT / POSITION / COOLDOWN)
        
        Returns:
          - Signal.BUY       → risk engine decides size
          - Signal.SELL_FULL  → exit 100% of position
          - Signal.SELL_HALF  → exit 50% of position (momentum fade)
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
| Beat Buy & Hold drawdown | ✅ Significantly lower max drawdown |
| Capture intraday trends | ✅ Entry within ~30–60 min of trend start |
| Avoid choppy ranges | ✅ ADX + cooldown filter out noise |
| Win rate | ~40–50% (lower than daily, but winners >> losers) |
| Total trades | ~80–200 per month |
| Avg trade duration | ~2–12 hours |

> **Note:** On 15-min, trade frequency is much higher than daily strategies.  
> The edge comes from **cutting losers fast** (tight stops) and **riding winners** (trailing stop).  
> Risk-adjusted return (Sharpe) should be superior to buy & hold.

---

## 9. Future Improvements (Post-MVP)

- **Multi-asset mode:** Run Smart Hodler on top 10 coins, allocate capital to strongest trends
- **Regime detection:** Use volatility clustering to adjust ADX threshold dynamically
- **Multi-timeframe cascade:** Add 4H as a third confirmation layer for higher-conviction entries
- **Dynamic ATR multiplier:** Widen stops during high-volatility events, tighten during calm periods
- **Adaptive session windows:** Auto-detect low-liquidity hours from rolling volume data instead of hardcoded UTC windows
