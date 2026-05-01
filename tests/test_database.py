"""
Integration tests for database operations.
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import Float
from sqlalchemy.exc import IntegrityError

from core.models import Candle, Position, Trade, ExitReason
from db import check_connection, get_session
from db.models import CandleRecord, PositionRecord, TradeRecord
from db.utils import (
    candle_to_orm,
    candles_to_dataframe,
    indicators_to_dict,
    orm_to_candle,
    orm_to_position,
    orm_to_trade,
    position_to_orm,
    trade_to_orm,
)


# ── Connection Tests ─────────────────────────────────────────────────────────

class TestDatabaseConnection:
    """Tests for database connectivity."""
    
    def test_check_connection(self):
        """Test that database connection works."""
        assert check_connection() is True
    
    def test_get_session(self):
        """Test getting a database session."""
        with get_session() as session:
            assert session is not None


# ── Candle CRUD Tests ────────────────────────────────────────────────────────

class TestCandleOperations:
    """Tests for candle CRUD operations."""
    
    def test_insert_candle(self):
        """Test inserting a candle record."""
        with get_session() as session:
            candle = CandleRecord(
                symbol="TESTUSDT",
                timeframe="15m",
                open_time=datetime(2026, 2, 17, 10, 0),
                open=Decimal("50000.00"),
                high=Decimal("50100.00"),
                low=Decimal("49900.00"),
                close=Decimal("50050.00"),
                volume=Decimal("10.5"),
                indicators={"ema_50": 50000.0, "ema_200": 49000.0, "adx": 28.5},
            )
            session.add(candle)
            session.commit()
            
            assert candle.id is not None
    
    def test_query_candles(self):
        """Test querying candle records."""
        with get_session() as session:
            # Insert a candle first
            candle = CandleRecord(
                symbol="TESTUSDT2",
                timeframe="15m",
                open_time=datetime(2026, 2, 17, 11, 0),
                open=Decimal("50000.00"),
                high=Decimal("50100.00"),
                low=Decimal("49900.00"),
                close=Decimal("50050.00"),
                volume=Decimal("10.5"),
            )
            session.add(candle)
            session.commit()
            
            # Query it back
            result = session.query(CandleRecord).filter_by(
                symbol="TESTUSDT2",
                timeframe="15m"
            ).first()
            
            assert result is not None
            assert result.symbol == "TESTUSDT2"
            assert result.close == Decimal("50050.00")
    
    def test_unique_constraint(self):
        """Test that duplicate candles are rejected."""
        with get_session() as session:
            open_time = datetime(2026, 2, 17, 12, 0)
            
            # Insert first candle
            candle1 = CandleRecord(
                symbol="TESTUSDT3",
                timeframe="15m",
                open_time=open_time,
                open=Decimal("50000.00"),
                high=Decimal("50100.00"),
                low=Decimal("49900.00"),
                close=Decimal("50050.00"),
                volume=Decimal("10.5"),
            )
            session.add(candle1)
            session.commit()
        
        # Try to insert duplicate - should fail
        with pytest.raises(IntegrityError):
            with get_session() as session2:
                candle2 = CandleRecord(
                    symbol="TESTUSDT3",
                    timeframe="15m",
                    open_time=open_time,  # Same time!
                    open=Decimal("50100.00"),
                    high=Decimal("50200.00"),
                    low=Decimal("50000.00"),
                    close=Decimal("50150.00"),
                    volume=Decimal("11.5"),
                )
                session2.add(candle2)
                session2.commit()
    
    def test_jsonb_query(self):
        """Test querying JSONB indicators column."""
        with get_session() as session:
            # Insert candle with indicators
            candle = CandleRecord(
                symbol="TESTUSDT4",
                timeframe="15m",
                open_time=datetime(2026, 2, 17, 13, 0),
                open=Decimal("50000.00"),
                high=Decimal("50100.00"),
                low=Decimal("49900.00"),
                close=Decimal("50050.00"),
                volume=Decimal("10.5"),
                indicators={"ema_50": 50000.0, "adx": 30.0},
            )
            session.add(candle)
            session.commit()
            
            # Query by JSONB field
            result = session.query(CandleRecord).filter(
                CandleRecord.symbol == "TESTUSDT4",
                CandleRecord.indicators["adx"].astext.cast(Float) > 25
            ).first()
            
            assert result is not None
            assert result.indicators["adx"] == 30.0


# ── Position CRUD Tests ──────────────────────────────────────────────────────

class TestPositionOperations:
    """Tests for position CRUD operations."""
    
    def test_insert_position(self):
        """Test inserting a position record."""
        with get_session() as session:
            position = PositionRecord(
                symbol="TESTUSDT5",
                size=Decimal("0.1"),
                entry_price=Decimal("50000.00"),
                entry_time=datetime(2026, 2, 17, 10, 0),
                highest_close=Decimal("50200.00"),
                trailing_stop_price=Decimal("49800.00"),
                hard_stop_price=Decimal("48500.00"),
            )
            session.add(position)
            session.commit()
            
            assert position.id is not None


# ── Trade CRUD Tests ─────────────────────────────────────────────────────────

class TestTradeOperations:
    """Tests for trade CRUD operations."""
    
    def test_insert_trade(self):
        """Test inserting a trade record."""
        with get_session() as session:
            trade = TradeRecord(
                symbol="TESTUSDT6",
                strategy="smart_hodler",
                entry_price=Decimal("50000.00"),
                exit_price=Decimal("51000.00"),
                size=Decimal("0.1"),
                entry_time=datetime(2026, 2, 17, 10, 0),
                exit_time=datetime(2026, 2, 17, 12, 0),
                pnl_usdt=Decimal("100.00"),
                pnl_percent=Decimal("2.00"),
                duration_minutes=120,
                exit_reason="trailing_stop",
            )
            session.add(trade)
            session.commit()
            
            assert trade.id is not None


# ── Conversion Tests ─────────────────────────────────────────────────────────

class TestConversions:
    """Tests for Pydantic ↔ ORM conversions."""
    
    def test_candle_roundtrip(self):
        """Test Pydantic Candle ↔ ORM CandleRecord conversion."""
        # Pydantic model
        pydantic_candle = Candle(
            open_time=datetime(2026, 2, 17, 10, 0),
            open=Decimal("50000.00"),
            high=Decimal("50100.00"),
            low=Decimal("49900.00"),
            close=Decimal("50050.00"),
            volume=Decimal("10.5"),
        )
        
        # Convert to ORM
        orm_candle = candle_to_orm(pydantic_candle, "TESTUSDT7", "15m")
        assert orm_candle.symbol == "TESTUSDT7"
        assert orm_candle.open == pydantic_candle.open
        
        # Convert back to Pydantic
        pydantic_candle2 = orm_to_candle(orm_candle)
        assert pydantic_candle2.open == pydantic_candle.open
        assert pydantic_candle2.close == pydantic_candle.close
    
    def test_position_roundtrip(self):
        """Test Pydantic Position ↔ ORM PositionRecord conversion."""
        pydantic_position = Position(
            symbol="TESTUSDT8",
            size=Decimal("0.1"),
            entry_price=Decimal("50000.00"),
            entry_time=datetime(2026, 2, 17, 10, 0),
            highest_close=Decimal("50200.00"),
            trailing_stop_price=Decimal("49800.00"),
            hard_stop_price=Decimal("48500.00"),
        )
        
        # Convert to ORM
        orm_position = position_to_orm(pydantic_position)
        assert orm_position.symbol == "TESTUSDT8"
        assert orm_position.size == pydantic_position.size
        
        # Convert back to Pydantic
        pydantic_position2 = orm_to_position(orm_position)
        assert pydantic_position2.symbol == pydantic_position.symbol
        assert pydantic_position2.size == pydantic_position.size
    
    def test_trade_roundtrip(self):
        """Test Pydantic Trade ↔ ORM TradeRecord conversion."""
        pydantic_trade = Trade(
            symbol="TESTUSDT9",
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
        
        # Convert to ORM
        orm_trade = trade_to_orm(pydantic_trade)
        assert orm_trade.symbol == "TESTUSDT9"
        assert orm_trade.exit_reason == "trailing_stop"
        
        # Convert back to Pydantic
        pydantic_trade2 = orm_to_trade(orm_trade)
        assert pydantic_trade2.symbol == pydantic_trade.symbol
        assert pydantic_trade2.exit_reason == ExitReason.TRAILING_STOP
    
    def test_candles_to_dataframe(self):
        """Test converting candle records to pandas DataFrame."""
        with get_session() as session:
            # Insert some candles
            candles = []
            for i in range(3):
                candle = CandleRecord(
                    symbol="TESTUSDT10",
                    timeframe="15m",
                    open_time=datetime(2026, 2, 17, 10 + i, 0),
                    open=Decimal(f"{50000 + i * 100}.00"),
                    high=Decimal(f"{50100 + i * 100}.00"),
                    low=Decimal(f"{49900 + i * 100}.00"),
                    close=Decimal(f"{50050 + i * 100}.00"),
                    volume=Decimal("10.5"),
                )
                candles.append(candle)
            session.add_all(candles)
            session.commit()
            
            # Convert to DataFrame
            df = candles_to_dataframe(candles)
            assert len(df) == 3
            assert "open" in df.columns
            assert "close" in df.columns
    
    def test_indicators_to_dict(self):
        """Test converting indicators to dictionary of Series."""
        with get_session() as session:
            # Insert candles with indicators
            candles = []
            for i in range(3):
                candle = CandleRecord(
                    symbol="TESTUSDT11",
                    timeframe="15m",
                    open_time=datetime(2026, 2, 17, 10 + i, 0),
                    open=Decimal("50000.00"),
                    high=Decimal("50100.00"),
                    low=Decimal("49900.00"),
                    close=Decimal("50050.00"),
                    volume=Decimal("10.5"),
                    indicators={"ema_50": 50000.0 + i * 10, "adx": 25.0 + i},
                )
                candles.append(candle)
            session.add_all(candles)
            session.commit()
            
            # Convert indicators
            indicators = indicators_to_dict(candles)
            assert "ema_50" in indicators
            assert "adx" in indicators
            assert len(indicators["ema_50"]) == 3
