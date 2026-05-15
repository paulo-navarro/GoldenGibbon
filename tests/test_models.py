"""
Unit tests for Pydantic models.
"""

from datetime import datetime
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from core.models import (
    BearGuardConditions,
    Candle,
    ExitReason,
    MarketData,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    PositionSide,
    RiskAction,
    RiskDecision,
    Signal,
    StopCheckResult,
    StrategyConditions,
    StrategyState,
    Trade,
)


# ── Candle Tests ─────────────────────────────────────────────────────────────

class TestCandle:
    """Tests for Candle model."""
    
    def test_valid_candle(self):
        """Test creating a valid candle."""
        candle = Candle(
            open_time=datetime(2026, 2, 17, 10, 0),
            open=Decimal("50000.00"),
            high=Decimal("50100.00"),
            low=Decimal("49900.00"),
            close=Decimal("50050.00"),
            volume=Decimal("10.5"),
        )
        assert candle.open == Decimal("50000.00")
        assert candle.high == Decimal("50100.00")
        assert candle.low == Decimal("49900.00")
        assert candle.close == Decimal("50050.00")
        assert candle.volume == Decimal("10.5")
    
    def test_negative_price_raises_error(self):
        """Test that negative prices are rejected."""
        with pytest.raises(ValidationError, match="must be positive"):
            Candle(
                open_time=datetime(2026, 2, 17, 10, 0),
                open=Decimal("-50000.00"),
                high=Decimal("50100.00"),
                low=Decimal("49900.00"),
                close=Decimal("50050.00"),
                volume=Decimal("10.5"),
            )
    
    def test_high_less_than_low_raises_error(self):
        """Test that high < low is rejected."""
        with pytest.raises(ValidationError, match="High must be >= low"):
            Candle(
                open_time=datetime(2026, 2, 17, 10, 0),
                open=Decimal("50000.00"),
                high=Decimal("49000.00"),  # High is less than low!
                low=Decimal("49900.00"),
                close=Decimal("50050.00"),
                volume=Decimal("10.5"),
            )


# ── Position Tests ───────────────────────────────────────────────────────────

class TestPosition:
    """Tests for Position model."""
    
    def test_valid_position(self):
        """Test creating a valid position."""
        position = Position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50200.00"),
            trailing_stop_price=Decimal("49800.00"),
            hard_stop_price=Decimal("48500.00"),
            scale_in_count=0,
        )
        assert position.symbol == "BTCUSDT"
        assert position.size == Decimal("0.1")
        assert position.scale_in_count == 0
    
    def test_invalid_scale_in_count(self):
        """Test that invalid scale_in_count is rejected."""
        with pytest.raises(ValidationError, match="must be 0, 1, or 2"):
            Position(
                symbol="BTCUSDT",
                size=Decimal("0.1"),
                entry_price=Decimal("50000.00"),
                entry_time=datetime(2026, 2, 17, 10, 0),
                highest_close=Decimal("50200.00"),
                trailing_stop_price=Decimal("49800.00"),
                hard_stop_price=Decimal("48500.00"),
                scale_in_count=5,  # Invalid!
            )
    
    def test_calculate_unrealized_pnl(self):
        """Test unrealized PnL calculation."""
        position = Position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50200.00"),
            trailing_stop_price=Decimal("49800.00"),
            hard_stop_price=Decimal("48500.00"),
        )
        
        # Price goes up 2%
        pnl_usdt, pnl_percent = position.calculate_unrealized_pnl(Decimal("51000.00"))
        assert pnl_usdt == Decimal("100.00")  # (51000 - 50000) * 0.1
        assert pnl_percent == Decimal("2.00")  # 2% gain


# ── Trade Tests ──────────────────────────────────────────────────────────────

class TestTrade:
    """Tests for Trade model."""
    
    def test_valid_trade(self):
        """Test creating a valid trade."""
        trade = Trade(
            symbol="BTCUSDT",
            strategy="smart_hodler",
            entry_price=Decimal("50000.00"),
            exit_price=Decimal("51000.00"),
            size=Decimal("0.1"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            exit_time=datetime(2026, 2, 17, 12, 0),
            pnl_usdt=Decimal("100.00"),
            pnl_percent=Decimal("2.00"),
            duration_minutes=120,
            exit_reason=ExitReason.TRAILING_STOP,
        )
        assert trade.symbol == "BTCUSDT"
        assert trade.pnl_usdt == Decimal("100.00")
        assert trade.exit_reason == ExitReason.TRAILING_STOP


# ── Order Tests ──────────────────────────────────────────────────────────────

class TestOrder:
    """Tests for Order model."""
    
    def test_valid_market_order(self):
        """Test creating a valid market order."""
        order = Order(
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            amount=Decimal("0.1"),
        )
        assert order.symbol == "BTCUSDT"
        assert order.side == OrderSide.BUY
        assert order.order_type == OrderType.MARKET
        assert order.status == OrderStatus.PENDING
        assert order.price is None  # Market orders have no price
    
    def test_valid_limit_order(self):
        """Test creating a valid limit order."""
        order = Order(
            symbol="BTCUSDT",
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            amount=Decimal("0.1"),
            price=Decimal("51000.00"),
        )
        assert order.side == OrderSide.SELL
        assert order.order_type == OrderType.LIMIT
        assert order.price == Decimal("51000.00")


# ── Portfolio Tests ──────────────────────────────────────────────────────────

class TestPortfolio:
    """Tests for Portfolio model."""
    
    def test_empty_portfolio(self):
        """Test creating an empty portfolio."""
        portfolio = Portfolio(
            usdt_balance=Decimal("10000.00"),
            equity=Decimal("10000.00"),
        )
        assert portfolio.usdt_balance == Decimal("10000.00")
        assert portfolio.equity == Decimal("10000.00")
        assert portfolio.available_capital == Decimal("10000.00")
        assert len(portfolio.positions) == 0
    
    def test_portfolio_with_position(self):
        """Test portfolio with an open position."""
        position = Position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50200.00"),
            trailing_stop_price=Decimal("49800.00"),
            hard_stop_price=Decimal("48500.00"),
        )
        
        portfolio = Portfolio(
            usdt_balance=Decimal("5000.00"),
            positions={"BTCUSDT": position},
            equity=Decimal("10000.00"),
            open_trades_count=1,
        )
        
        assert len(portfolio.positions) == 1
        assert portfolio.open_trades_count == 1
        assert portfolio.positions_cost_basis == Decimal("5000.00")  # 0.1 * 50000
    
    def test_update_equity(self):
        """Test equity update with current prices."""
        position = Position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50200.00"),
            trailing_stop_price=Decimal("49800.00"),
            hard_stop_price=Decimal("48500.00"),
        )
        
        portfolio = Portfolio(
            usdt_balance=Decimal("5000.00"),
            positions={"BTCUSDT": position},
            equity=Decimal("10000.00"),
        )
        
        # Update with new price (price went up to 52000)
        portfolio.update_equity({"BTCUSDT": Decimal("52000.00")})
        
        # New equity = 5000 (balance) + 5200 (position value)
        assert portfolio.equity == Decimal("10200.00")


# ── StrategyConditions Tests ─────────────────────────────────────────────────

class TestStrategyConditions:
    """Tests for StrategyConditions model."""
    
    def test_all_conditions_false(self):
        """Test conditions with all false."""
        conditions = StrategyConditions()
        assert conditions.all_buy_conditions_met is False
    
    def test_all_conditions_true(self):
        """Test conditions with all true."""
        conditions = StrategyConditions(
            ema_cross_bullish=True,
            adx_above_threshold=True,
            close_above_ema_fast=True,
            volume_above_average=True,
            hourly_ema_rising=True,
            hourly_rsi_above_threshold=True,
            pullback_near_ema=True,
            session_filter_pass=True,
        )
        assert conditions.all_buy_conditions_met is True
    
    def test_partial_conditions(self):
        """Test conditions with some true."""
        conditions = StrategyConditions(
            ema_cross_bullish=True,
            adx_above_threshold=True,
            close_above_ema_fast=False,  # One condition false
            volume_above_average=True,
            hourly_ema_rising=True,
            hourly_rsi_above_threshold=True,
            session_filter_pass=True,
        )
        assert conditions.all_buy_conditions_met is False


# ── MarketData Tests ─────────────────────────────────────────────────────────

def _make_ohlcv(periods: int = 20, freq: str = '15min') -> pd.DataFrame:
    """Helper: build a minimal OHLCV DataFrame."""
    np.random.seed(99)
    dates = pd.date_range(start='2020-01-01', periods=periods, freq=freq)
    close = 100 + np.cumsum(np.random.randn(periods) * 0.5)
    return pd.DataFrame({
        'open': close + np.random.randn(periods) * 0.1,
        'high': close + np.abs(np.random.randn(periods) * 0.2),
        'low':  close - np.abs(np.random.randn(periods) * 0.2),
        'close': close,
        'volume': np.abs(np.random.randn(periods) * 1000 + 5000),
    }, index=dates)


class TestMarketData:
    """Tests for MarketData model."""

    # ── Construction ──────────────────────────────────────────────────────

    def test_primary_only(self):
        """Create MarketData with only primary fields (backward compat)."""
        df = _make_ohlcv()
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=df)
        assert md.symbol == "BTCUSDT"
        assert md.timeframe == "15m"
        assert len(md.candles) == 20
        assert md.indicators == {}
        assert md.secondary_timeframe is None
        assert md.secondary_candles is None
        assert md.secondary_indicators == {}
        assert md.has_secondary is False

    def test_primary_with_indicators(self):
        """Create MarketData with primary indicators."""
        df = _make_ohlcv()
        inds = {"ema_50": df['close'].ewm(span=10).mean()}
        md = MarketData(symbol="ETHUSDT", timeframe="1h", candles=df, indicators=inds)
        assert "ema_50" in md.indicators

    def test_full_multi_timeframe(self):
        """Create MarketData with both primary and secondary timeframes."""
        df_15m = _make_ohlcv(80, '15min')
        df_1h  = _make_ohlcv(20, '1h')
        inds_15m = {"ema_50": df_15m['close'].ewm(span=50).mean()}
        inds_1h  = {"ema_21": df_1h['close'].ewm(span=21).mean(), "rsi": df_1h['close']}

        md = MarketData(
            symbol="BTCUSDT",
            timeframe="15m",
            candles=df_15m,
            indicators=inds_15m,
            secondary_timeframe="1h",
            secondary_candles=df_1h,
            secondary_indicators=inds_1h,
        )

        assert md.has_secondary is True
        assert md.secondary_timeframe == "1h"
        assert len(md.secondary_candles) == 20
        assert "ema_21" in md.secondary_indicators
        assert "rsi" in md.secondary_indicators

    # ── Validation ────────────────────────────────────────────────────────

    def test_invalid_symbol_lowercase(self):
        """Lowercase symbol is rejected."""
        with pytest.raises(ValidationError, match="uppercase"):
            MarketData(symbol="btcusdt", timeframe="15m", candles=_make_ohlcv())

    def test_invalid_primary_timeframe(self):
        """Invalid primary timeframe is rejected."""
        with pytest.raises(ValidationError, match="Invalid timeframe"):
            MarketData(symbol="BTCUSDT", timeframe="3m", candles=_make_ohlcv())

    def test_invalid_secondary_timeframe(self):
        """Invalid secondary timeframe is rejected."""
        with pytest.raises(ValidationError, match="Invalid secondary timeframe"):
            MarketData(
                symbol="BTCUSDT",
                timeframe="15m",
                candles=_make_ohlcv(),
                secondary_timeframe="3m",
                secondary_candles=_make_ohlcv(10, '1h'),
            )

    def test_secondary_candles_without_timeframe(self):
        """secondary_candles without secondary_timeframe raises."""
        with pytest.raises(ValidationError, match="both be set or both be None"):
            MarketData(
                symbol="BTCUSDT",
                timeframe="15m",
                candles=_make_ohlcv(),
                secondary_candles=_make_ohlcv(10, '1h'),
            )

    def test_secondary_timeframe_without_candles(self):
        """secondary_timeframe without secondary_candles raises."""
        with pytest.raises(ValidationError, match="both be set or both be None"):
            MarketData(
                symbol="BTCUSDT",
                timeframe="15m",
                candles=_make_ohlcv(),
                secondary_timeframe="1h",
            )

    # ── get_indicator() ───────────────────────────────────────────────────

    def test_get_indicator_primary_default(self):
        """get_indicator without timeframe returns primary."""
        df = _make_ohlcv()
        ema = df['close'].ewm(span=50).mean()
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=df, indicators={"ema_50": ema})
        result = md.get_indicator("ema_50")
        assert (result == ema).all()

    def test_get_indicator_primary_explicit(self):
        """get_indicator with primary timeframe returns primary."""
        df = _make_ohlcv()
        ema = df['close'].ewm(span=50).mean()
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=df, indicators={"ema_50": ema})
        result = md.get_indicator("ema_50", "15m")
        assert (result == ema).all()

    def test_get_indicator_secondary(self):
        """get_indicator with secondary timeframe returns secondary."""
        df_15m = _make_ohlcv(80, '15min')
        df_1h  = _make_ohlcv(20, '1h')
        rsi_1h = df_1h['close']  # Dummy series
        md = MarketData(
            symbol="BTCUSDT", timeframe="15m", candles=df_15m,
            indicators={"ema_50": df_15m['close']},
            secondary_timeframe="1h", secondary_candles=df_1h,
            secondary_indicators={"rsi": rsi_1h},
        )
        result = md.get_indicator("rsi", "1h")
        assert (result == rsi_1h).all()

    def test_get_indicator_missing_key(self):
        """get_indicator with unknown key raises KeyError."""
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=_make_ohlcv())
        with pytest.raises(KeyError, match="not found in primary"):
            md.get_indicator("nonexistent")

    def test_get_indicator_missing_secondary_key(self):
        """get_indicator for secondary with unknown key raises."""
        df_15m = _make_ohlcv(80, '15min')
        df_1h  = _make_ohlcv(20, '1h')
        md = MarketData(
            symbol="BTCUSDT", timeframe="15m", candles=df_15m,
            secondary_timeframe="1h", secondary_candles=df_1h,
        )
        with pytest.raises(KeyError, match="not found in secondary"):
            md.get_indicator("nonexistent", "1h")

    def test_get_indicator_unknown_timeframe(self):
        """get_indicator for an unknown timeframe raises."""
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=_make_ohlcv())
        with pytest.raises(KeyError, match="not available"):
            md.get_indicator("ema_50", "4h")

    # ── has_secondary property ────────────────────────────────────────────

    def test_has_secondary_false(self):
        md = MarketData(symbol="BTCUSDT", timeframe="15m", candles=_make_ohlcv())
        assert md.has_secondary is False

    def test_has_secondary_true(self):
        df_15m = _make_ohlcv(80, '15min')
        df_1h  = _make_ohlcv(20, '1h')
        md = MarketData(
            symbol="BTCUSDT", timeframe="15m", candles=df_15m,
            secondary_timeframe="1h", secondary_candles=df_1h,
        )
        assert md.has_secondary is True


# ── Enum Tests ───────────────────────────────────────────────────────────────

class TestEnums:
    """Tests for enum values."""
    
    def test_signal_enum(self):
        """Test Signal enum values."""
        assert Signal.BUY.value == "buy"
        assert Signal.SELL_FULL.value == "sell_full"
        assert Signal.SELL_HALF.value == "sell_half"
        assert Signal.HOLD.value == "hold"
    
    def test_strategy_state_enum(self):
        """Test StrategyState enum values."""
        assert StrategyState.FLAT.value == "flat"
        assert StrategyState.POSITION.value == "position"
        assert StrategyState.REDUCED.value == "reduced"
        assert StrategyState.COOLDOWN.value == "cooldown"
    
    def test_exit_reason_enum(self):
        """Test ExitReason enum values."""
        assert ExitReason.EMA_CROSS.value == "ema_cross"
        assert ExitReason.TRAILING_STOP.value == "trailing_stop"
        assert ExitReason.HARD_STOP.value == "hard_stop"


# ── Phase 5.1: Short Support Model Tests ────────────────────────────────────


class TestPositionSide:
    """Tests for PositionSide enum."""

    def test_position_side_values(self):
        assert PositionSide.LONG.value == "long"
        assert PositionSide.SHORT.value == "short"

    def test_signal_short_exists(self):
        assert Signal.SHORT.value == "short"


class TestPositionShort:
    """Tests for Position model with short support."""

    def _make_long_position(self) -> Position:
        return Position(
            symbol="BTCUSDT",
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50200.00"),
            trailing_stop_price=Decimal("49800.00"),
            hard_stop_price=Decimal("48500.00"),
        )

    def _make_short_position(self) -> Position:
        return Position(
            symbol="BTCUSDT",
            side=PositionSide.SHORT,
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50000.00"),
            lowest_close=Decimal("49000.00"),
            trailing_stop_price=Decimal("51500.00"),
            hard_stop_price=Decimal("52500.00"),
        )

    def test_default_side_is_long(self):
        pos = self._make_long_position()
        assert pos.side == PositionSide.LONG

    def test_short_side(self):
        pos = self._make_short_position()
        assert pos.side == PositionSide.SHORT

    def test_lowest_close_optional(self):
        pos = self._make_long_position()
        assert pos.lowest_close is None

    def test_lowest_close_set(self):
        pos = self._make_short_position()
        assert pos.lowest_close == Decimal("49000.00")

    def test_calculate_unrealized_pnl_long_profit(self):
        """Existing long PnL logic unchanged — price up = profit."""
        pos = self._make_long_position()
        pnl_usdt, pnl_percent = pos.calculate_unrealized_pnl(Decimal("51000.00"))
        assert pnl_usdt == Decimal("100.00")  # (51000 - 50000) * 0.1
        assert pnl_percent == Decimal("2.00")

    def test_calculate_unrealized_pnl_long_loss(self):
        """Long — price down = loss."""
        pos = self._make_long_position()
        pnl_usdt, pnl_percent = pos.calculate_unrealized_pnl(Decimal("49000.00"))
        assert pnl_usdt == Decimal("-100.00")
        assert pnl_percent == Decimal("-2.00")

    def test_calculate_unrealized_pnl_short_profit(self):
        """Short — price down = profit."""
        pos = self._make_short_position()
        pnl_usdt, pnl_percent = pos.calculate_unrealized_pnl(Decimal("45000.00"))
        # (50000 - 45000) * 0.1 = 500
        assert pnl_usdt == Decimal("500.00")
        # (50000 - 45000) / 50000 * 100 = 10%
        assert pnl_percent == Decimal("10.00")

    def test_calculate_unrealized_pnl_short_loss(self):
        """Short — price up = loss."""
        pos = self._make_short_position()
        pnl_usdt, pnl_percent = pos.calculate_unrealized_pnl(Decimal("55000.00"))
        # (50000 - 55000) * 0.1 = -500
        assert pnl_usdt == Decimal("-500.00")
        # (50000 - 55000) / 50000 * 100 = -10%
        assert pnl_percent == Decimal("-10.00")


class TestRiskDecisionSide:
    """Tests for RiskDecision side field."""

    def test_default_side_is_long(self):
        decision = RiskDecision(action=RiskAction.OPEN)
        assert decision.side == PositionSide.LONG

    def test_short_side(self):
        decision = RiskDecision(action=RiskAction.OPEN, side=PositionSide.SHORT)
        assert decision.side == PositionSide.SHORT


class TestStopCheckResultLowestClose:
    """Tests for StopCheckResult lowest_close field."""

    def test_lowest_close_default_none(self):
        result = StopCheckResult()
        assert result.lowest_close is None

    def test_lowest_close_set(self):
        result = StopCheckResult(lowest_close=Decimal("48000.00"))
        assert result.lowest_close == Decimal("48000.00")


class TestBearGuardConditions:
    """Tests for BearGuardConditions model."""

    def test_all_false_by_default(self):
        conds = BearGuardConditions()
        assert conds.all_short_conditions_met is False

    def test_all_true(self):
        conds = BearGuardConditions(
            death_cross=True,
            close_below_ema_fast=True,
            adx_above_threshold=True,
            volume_above_average=True,
            hourly_ema_falling=True,
            hourly_rsi_bearish=True,
            session_filter_pass=True,
        )
        assert conds.all_short_conditions_met is True

    @pytest.mark.parametrize("field", [
        "death_cross",
        "close_below_ema_fast",
        "adx_above_threshold",
        "volume_above_average",
        "hourly_ema_falling",
        "hourly_rsi_bearish",
        "session_filter_pass",
    ])
    def test_one_false_blocks(self, field):
        """If any single condition is False, all_short_conditions_met is False."""
        kwargs = {
            "death_cross": True,
            "close_below_ema_fast": True,
            "adx_above_threshold": True,
            "volume_above_average": True,
            "hourly_ema_falling": True,
            "hourly_rsi_bearish": True,
            "session_filter_pass": True,
        }
        kwargs[field] = False
        conds = BearGuardConditions(**kwargs)
        assert conds.all_short_conditions_met is False
