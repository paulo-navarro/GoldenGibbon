"""
Unit tests for Pydantic models.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from core.models import (
    Candle,
    ExitReason,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Portfolio,
    Position,
    Signal,
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
        assert portfolio.positions_value == Decimal("5000.00")  # 0.1 * 50000
    
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
