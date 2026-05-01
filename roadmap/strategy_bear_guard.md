# BearGuard – Strategy Specification

> **Type:** Trend-following (short side)
> **Timeframe:** 15m (primary) + 1H (confirmation)
> **Goal:** Profit from confirmed bear trends via spot margin short — no leverage multiplier
> **Philosophy:** Mirror Smart Hodler's discipline on the short side. Only short a real trend. Exit the moment the trend ends.

---

## 1. Signal Logic

### 1.1 Bear Trend Detection (Primary – 15m)

| Indicator | Condition | Role |
|---|---|---|
| `EMA 50` vs `EMA 200` | `EMA 50 < EMA 200` | Death cross — bear structure confirmed |
| `Close` vs `EMA 50` | `Close < EMA 50` | Price is below the fast MA — confirming direction |
| `ADX(14)` | `> 25` | Real trend, not noise — same threshold as Smart Hodler |
| `Volume` | `>= 70% of SMA(20) of volume` | Selling pressure softly confirmed — configurable threshold (`volume_filter_pct`) |
| `Borrow Rate` | `<= max_borrow_rate_pct / day` | Margin cost gate — avoids entries when borrowing cost is elevated |

**SHORT signal** requires the structural conditions (EMA cross, ADX, close vs EMA50) plus hourly and session filters. The volume threshold is configurable (default: 70% of SMA20, set to 0 to disable). The borrow rate gate is checked live before entry.

**COVER signal** — tiered exit system:

| Priority | Condition | Action | Rationale |
|---|---|---|---|
| 1 — Hard cover | `EMA 50 > EMA 200` | Cover 100% immediately | Trend structure is reversed — golden cross |
| 2 — Confirmed reversal | `Close > EMA 200` for **3 consecutive candles** | Cover 100% | Price reclaimed the long-term MA (avoids wick fakeouts) |
| 3 — Momentum exhaustion | `RSI(1H) > 70` AND `ADX falling` (current < 3 bars ago) | Cover 50% of position | Downtrend losing steam but not yet reversed |

> **Why tiered?** A single candle closing above EMA 200 on 15m is often a wick in a still-bearish market. Bear market bounces on 15m routinely last 1–2 candles before resuming — requiring 3-candle confirmation (45 minutes) provides a more reliable signal without waiting too long. The partial cover on momentum exhaustion locks in profit without abandoning a trend that may resume its descent.

**HOLD** in all other cases.

### 1.2 Hourly Confirmation (1H)

Before entering a short, check the 1H chart:

- `EMA 21` (1H) must be **falling** (current < 3 bars ago)
- `RSI(14)` (1H) must be **below 55** (momentum is bearish or neutral, not bullish)

If hourly confirmation fails → **HOLD** (do not enter, but manage open position normally).

> **Why?** Even when 15m shows a death cross, the 1H trend may still be neutral or turning bullish. Shorting into a higher-timeframe upswing is dangerous. The hourly EMA falling confirms the macro direction; RSI < 55 avoids entering a short when the hourly momentum is still constructively bullish.

### 1.3 Session Filter

Same dead zones as Smart Hodler — low liquidity makes short entries unpredictable:

**Rules:**
- **No new SHORT entries** during dead zones: `Saturday 21:00 – Sunday 20:00 UTC` and `Weekdays 21:00 – 01:00 UTC`
- Existing short positions are **not affected** — stops and covers execute normally during these windows

> **Why?** Thin order books during low-liquidity windows can produce violent short squeezes with no real buying pressure behind them, triggering hard stops unnecessarily. Covers remain active because protecting capital is always a priority.

### 1.4 Signal Summary

```
SHORT     = (EMA50 < EMA200) AND (ADX > 25) AND (Close < EMA50)
            AND (Volume >= volume_filter_pct × SMA20_Volume) AND (1H EMA21 falling)
            AND (1H RSI < 55) AND (Session OK) AND (borrow_rate <= max_borrow_rate)
COVER 100% = (EMA50 > EMA200) OR (Close > EMA200 for 3 consecutive candles)
COVER  50% = (1H RSI > 70) AND (ADX falling)
HOLD       = everything else
```

> **Note on signal names:** The strategy emits `Signal.SHORT`, `Signal.SELL_FULL` (cover 100%), and `Signal.SELL_HALF` (cover 50%). `SELL_FULL` and `SELL_HALF` are position-side-agnostic — the executor reads `position.side` to know whether to BUY (to cover a short) or SELL (to exit a long).

---

## 2. Position Sizing

No scale-in on shorts — entering incrementally into a short position adds counterparty risk and complexity that is not justified at this stage:

| Condition | Action | Size |
|---|---|---|
| First SHORT signal | Enter short position | 50% of available capital |
| COVER 100% signal | Close full short | 100% of position |
| COVER 50% signal | Partial cover (momentum exhaustion) | 50% of current position |

### Why 50%? Why no scale-in?

- 50% (not 75%) reflects the asymmetric risk of shorts: losses are theoretically unlimited while gains are capped at entry price. The smaller base size is a structural safety buffer on top of the hard stop.
- Capital is preserved for concurrent long positions — BearGuard at 50% and Smart Hodler at 75% can both run simultaneously without over-exposing the portfolio.
- Scale-in is excluded because short trends are faster and more violent than bull trends — waiting for 8–16 candles to scale in typically misses the best part of the move.

---

## 3. Stop-Loss

### Hard Stop (inverted)

- **Max adverse move per trade:** `+5%` above entry price
- For a short, the stop fires when price **rises** above entry, not falls below it
- `hard_stop_price = entry_price × (1 + 0.05)`
- If triggered → cover immediately, enter **cooldown period** (16 candles / ~4 hours)

> **Why 5%?** Bear markets routinely produce 3–4% counter-trend bounces within 15m candles before resuming the downtrend. A tighter stop would be triggered by normal volatility rather than genuine trend reversal. 5% gives the position room to breathe while the break-even ratchet (+2% / +4% milestones) quickly moves the stop toward entry once the trade is profitable, limiting actual risk exposure on winners.

### Trailing Stop (inverted)

- Tracks the **lowest close** since entry (instead of highest close used for longs)
- Stop price ratchets **downward** as price falls: `trailing_stop = lowest_close + (ATR × 2.5)`
- Stop never moves up (only downward ratchet)
- If `close > trailing_stop_price` → cover immediately with `ExitReason.TRAILING_STOP`

### Break-Even Ratchet (inverted)

The hard stop ratchets **downward** (toward greater profit) as the trade moves in our favour:

| Profit Milestone | Action |
|---|---|
| `+2% unrealized gain` | Move hard stop to `entry_price` (break-even) |
| `+4% unrealized gain` | Move hard stop to `entry_price × 0.99` (lock in 1%) |

The ratcheted hard stop never moves back up once triggered.

### No Time Stop

- BearGuard does not use a time stop
- Bear trends can persist for many candles; exiting by time would prematurely close winners
- The trailing stop and golden-cross cover handle all exits

---

## 4. Re-Entry After Exit

After a hard-stop exit:

- **Cooldown period:** 16 candles (~4 hours, no new SHORT entries allowed)
- Matches Smart Hodler's cooldown — hard stops indicate the thesis was wrong; wait for the market to stabilize

After profit exits (COVER 50% or COVER 100%):

- **No cooldown** — re-entry is immediate if all conditions realign
- A covered position means the trend ended cleanly; watching for a new setup immediately is correct

---

## 5. Indicators Required

| Indicator | Period | Timeframe | Purpose |
|---|---|---|---|
| EMA | 50 | 15m | Fast MA — bear structure filter |
| EMA | 200 | 15m | Slow MA — death cross / golden cross |
| ADX | 14 | 15m | Trend strength — only short real trends |
| Volume SMA | 20 | 15m | Selling pressure confirmation |
| EMA | 21 | 1H | Higher-TF direction filter |
| RSI | 14 | 1H | Higher-TF momentum filter |
| ATR | 14 | 15m | Trailing stop distance calculation |

All indicators already exist in `core/indicators/technical.py`. No new indicators needed.

---

## 6. State Machine

Reuses existing `StrategyState` enum: `FLAT`, `POSITION`, `REDUCED`, `COOLDOWN`.

```
                    ┌──────────────┐
         ┌─────────│   COOLDOWN   │◄──── hard stop only
         │         │  (~4 hours)  │
         │         └──────────────┘
         │ cooldown expires
         ▼
   ┌───────────┐  SHORT signal   ┌──────────────┐
   │   FLAT    │────────────────►│  POSITION    │
   │ (no pos)  │                 │  (short on)  │──── COVER 50% ──►┌────────────┐
   └───────────┘                 └──────────────┘                  │  REDUCED   │
         ▲                         │                               │  (partial) │
         │                         │ COVER 100%                    └────────────┘
         │                         │ (golden cross | 3 closes above EMA200)       │ COVER 100%
         │                         ▼                                               ▼
         └───────────────────── FLAT ◄──────────────────────────────────────── FLAT
         (no cooldown on                                           (no cooldown on
          profit exits)                                            profit exits)
```

### States

| State | Description |
|---|---|
| `FLAT` | No position. Watching for death cross + all confirmations. |
| `POSITION` | Short position open. Watching for golden cross or consecutive closes above EMA200 (full cover) or momentum exhaustion (partial cover). |
| `REDUCED` | Partial short remaining after 50% cover. Watching for golden cross or confirmed reversal. |
| `COOLDOWN` | Exited via hard stop. Waiting 16 candles (~4 hours) before re-evaluating. |

### Key difference from Smart Hodler

- **No scale-in** — single entry at 50% capital
- **Inverted stop logic** — hard stop is above entry, trailing stop ratchets downward
- **No cooldown on profit exits** — bear setups can re-emerge quickly; pausing unnecessarily sacrifices opportunity

---

## 7. Decision Interface

```python
class BearGuard(Strategy):
    def decide(self, market_data: MarketData, portfolio: Portfolio) -> Signal:
        """
        Pure decision function.

        Reads:
          - market_data.indicators (EMA50, EMA200, ADX, volume_sma)
          - market_data.secondary_indicators (EMA21, RSI — 1H)
          - market_data.candles (close prices)
          - portfolio.positions (current exposure)
          - self._state (FLAT / POSITION / REDUCED / COOLDOWN)

        Returns:
          - Signal.SHORT      → risk engine sizes the short (50% of capital)
          - Signal.SELL_FULL  → cover 100% of position (golden cross / confirmed reversal)
          - Signal.SELL_HALF  → cover 50% of position (momentum exhaustion)
          - Signal.HOLD       → no action
        """
```

---

## 8. Compatibility with Existing Strategies

BearGuard is **regime-complementary** to Smart Hodler:

| Condition | Smart Hodler | BearGuard |
|---|---|---|
| `EMA50 > EMA200`, ADX > 25 | BUY | HOLD (no short during uptrend) |
| `EMA50 < EMA200`, ADX > 25 | HOLD (no long during downtrend) | SHORT |
| ADX < 25 (ranging) | HOLD | HOLD |
| Mean Reversion territory (ADX < 25, oversold) | HOLD | HOLD |

Both strategies can run on the same or different symbols simultaneously without conflicting — BearGuard at 50% and Smart Hodler at 75% means total exposure stays well-managed even when both are active. The regime gating system (`Phase 3`) already blocks Smart Hodler from entering during downtrends; BearGuard adds the mirror image.

---

## 9. Execution — Spot Margin (Binance)

BearGuard uses **Binance Cross Margin** at **1x** (borrow factor = 1, no leverage multiplier):

| Action | Binance API | Notes |
|---|---|---|
| Open short | `POST /sapi/v1/margin/order` `side=SELL` `sideEffectType=MARGIN_BUY` | Borrows asset, sells immediately |
| Cover (close) | `POST /sapi/v1/margin/order` `side=BUY` `sideEffectType=AUTO_REPAY` | Buys back asset, repays borrow |
| Stop order | `POST /sapi/v1/margin/order` `side=BUY` `type=STOP_LOSS_LIMIT` | Exchange-side stop for disconnection safety |

> **Why Spot Margin (not Futures)?** Spot margin at 1x is the safest non-leveraged short mechanism. There is no funding rate, no mark price divergence, no liquidation engine separate from the stop — the position can be held as long as the borrow cost (typically < 0.1%/day for BTC/ETH) is acceptable. This is consistent with the platform's no-leverage philosophy.

A global kill switch (`shorts_enabled: false` in config) instantly disables all new SHORT entries and cancels pending margin stop orders without affecting existing long positions.

---

## 10. Risk Parameters (Default Config)

```yaml
bear_guard:
  # Entry
  adx_threshold: 25
  hourly_rsi_bear_threshold: 55
  hourly_ema_lookback: 4           # bars to look back for EMA21 direction
  volume_filter_pct: 0.70          # volume must be >= 70% of SMA20 (0.0 = disabled)
  max_borrow_rate_pct: 0.003       # block entry if daily borrow rate > 0.3%/day

  # Exit
  rsi_overbought_threshold: 70
  adx_falling_lookback: 3          # bars to look back for ADX direction
  exit_confirmation_candles: 3     # consecutive candles above EMA200 to trigger full cover

  # Sizing
  position_size_pct: 0.50          # 50% of available capital

  # Stops
  hard_stop_pct: 0.05              # 5% adverse move above entry
  trailing_stop_atr_multiplier: 2.5
  trailing_stop_enabled: true
  breakeven_trigger_pct: 0.02      # ratchet hard stop to entry at +2% profit
  lockin_trigger_pct: 0.04         # ratchet hard stop to +1% profit at +4% profit
  lockin_stop_pct: 0.01

  # Cooldown
  cooldown_candles: 16             # 16 × 15m = ~4 hours

  # Margin
  margin_type: "cross"             # "cross" or "isolated"
```
