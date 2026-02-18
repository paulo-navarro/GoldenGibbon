"""
Database seed script for testing.

Inserts sample data for development and testing purposes.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from db import get_session
from db.models import CandleRecord, PositionRecord, TradeRecord, PortfolioSnapshot


def seed_candles() -> None:
    """Seed sample BTCUSDT candles with indicators."""
    print("Seeding candles...")
    
    with get_session() as session:
        # Generate 50 candles for BTCUSDT 15m
        base_time = datetime(2026, 2, 1, 0, 0, 0)
        base_price = Decimal("50000.00")
        
        candles = []
        for i in range(50):
            open_time = base_time + timedelta(minutes=15 * i)
            
            # Simulate price movement
            price_change = Decimal(str((i % 10 - 5) * 50))  # Oscillate between -250 and +250
            current_price = base_price + price_change
            
            # OHLCV data
            open_price = current_price
            close_price = current_price + Decimal(str(((i + 5) % 10 - 5) * 20))
            high_price = max(open_price, close_price) + Decimal("100.00")
            low_price = min(open_price, close_price) - Decimal("100.00")
            volume = Decimal("10.5") + Decimal(str(i % 5))
            
            # Calculate simple indicators (simplified for demo)
            ema_50 = float(base_price + price_change)  # Simplified
            ema_200 = float(base_price)  # Simplified
            adx = 28.5 + (i % 10) * 0.5  # Range from 28.5 to 33.0
            atr = 150.0 + (i % 5) * 10  # Range from 150 to 190
            rsi = 50 + (i % 20) - 10  # Range from 40 to 60
            volume_sma = float(volume) * 0.9
            
            candle = CandleRecord(
                symbol="BTCUSDT",
                timeframe="15m",
                open_time=open_time,
                open=open_price,
                high=high_price,
                low=low_price,
                close=close_price,
                volume=volume,
                quote_volume=volume * close_price,
                trades_count=1000 + i * 10,
                indicators={
                    "ema_50": ema_50,
                    "ema_200": ema_200,
                    "adx": adx,
                    "atr": atr,
                    "rsi": rsi,
                    "volume_sma": volume_sma,
                }
            )
            candles.append(candle)
        
        session.add_all(candles)
        session.commit()
        print(f"✓ Seeded {len(candles)} BTCUSDT 15m candles")


def seed_trades() -> None:
    """Seed sample trade records."""
    print("Seeding trades...")
    
    with get_session() as session:
        trades = [
            TradeRecord(
                symbol="BTCUSDT",
                strategy="smart_hodler",
                side="long",
                entry_price=Decimal("49800.00"),
                exit_price=Decimal("50200.00"),
                size=Decimal("0.1"),
                entry_time=datetime(2026, 2, 1, 10, 0, 0),
                exit_time=datetime(2026, 2, 1, 14, 30, 0),
                pnl_usdt=Decimal("40.00"),
                pnl_percent=Decimal("0.8032"),
                duration_minutes=270,
                exit_reason="trailing_stop",
                max_favorable_excursion=Decimal("450.00"),
                max_adverse_excursion=Decimal("-150.00"),
            ),
            TradeRecord(
                symbol="BTCUSDT",
                strategy="smart_hodler",
                side="long",
                entry_price=Decimal("50100.00"),
                exit_price=Decimal("49900.00"),
                size=Decimal("0.15"),
                entry_time=datetime(2026, 2, 2, 8, 0, 0),
                exit_time=datetime(2026, 2, 2, 10, 0, 0),
                pnl_usdt=Decimal("-30.00"),
                pnl_percent=Decimal("-0.3992"),
                duration_minutes=120,
                exit_reason="hard_stop",
                max_favorable_excursion=Decimal("100.00"),
                max_adverse_excursion=Decimal("-300.00"),
            ),
            TradeRecord(
                symbol="BTCUSDT",
                strategy="smart_hodler",
                side="long",
                entry_price=Decimal("49950.00"),
                exit_price=Decimal("51000.00"),
                size=Decimal("0.2"),
                entry_time=datetime(2026, 2, 3, 12, 0, 0),
                exit_time=datetime(2026, 2, 3, 18, 0, 0),
                pnl_usdt=Decimal("210.00"),
                pnl_percent=Decimal("2.1021"),
                duration_minutes=360,
                exit_reason="ema_cross",
                max_favorable_excursion=Decimal("1200.00"),
                max_adverse_excursion=Decimal("-50.00"),
            ),
        ]
        
        session.add_all(trades)
        session.commit()
        print(f"✓ Seeded {len(trades)} trade records")


def seed_portfolio_snapshots() -> None:
    """Seed sample portfolio snapshots."""
    print("Seeding portfolio snapshots...")
    
    with get_session() as session:
        base_time = datetime(2026, 2, 1, 0, 0, 0)
        initial_capital = Decimal("10000.00")
        
        snapshots = []
        for i in range(10):
            timestamp = base_time + timedelta(days=i)
            
            # Simulate growing equity
            pnl = Decimal(str(i * 50 - 100))  # Starts at -100, grows to +350
            total_equity = initial_capital + pnl
            
            snapshot = PortfolioSnapshot(
                timestamp=timestamp,
                usdt_balance=total_equity * Decimal("0.8"),  # 80% cash
                positions_value=total_equity * Decimal("0.2"),  # 20% in position
                total_equity=total_equity,
                daily_pnl=Decimal(str((i % 3 - 1) * 20)),  # -20, 0, or +20
                total_pnl=pnl,
                open_positions_count=1 if i % 3 != 0 else 0,
            )
            snapshots.append(snapshot)
        
        session.add_all(snapshots)
        session.commit()
        print(f"✓ Seeded {len(snapshots)} portfolio snapshots")


def seed_all() -> None:
    """Run all seed functions."""
    print("=" * 60)
    print("DATABASE SEEDING")
    print("=" * 60)
    
    try:
        seed_candles()
        seed_trades()
        seed_portfolio_snapshots()
        
        print("\n" + "=" * 60)
        print("✅ Database seeding completed successfully!")
        print("=" * 60)
    except Exception as e:
        print(f"\n❌ Error during seeding: {e}")
        raise


if __name__ == "__main__":
    seed_all()
