"""
Utility functions for converting between Pydantic and ORM models.

Provides bidirectional conversion to maintain clean separation between
business logic (Pydantic) and persistence (SQLAlchemy) layers.
"""

from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd

from core import models as pydantic_models
from db import models as orm_models


# ── Candle Conversions ───────────────────────────────────────────────────────

def candle_to_orm(candle: pydantic_models.Candle, symbol: str, timeframe: str) -> orm_models.CandleRecord:
    """
    Convert Pydantic Candle to ORM CandleRecord.
    
    Args:
        candle: Pydantic Candle model
        symbol: Trading symbol
        timeframe: Timeframe string
    
    Returns:
        ORM CandleRecord
    """
    return orm_models.CandleRecord(
        symbol=symbol,
        timeframe=timeframe,
        open_time=candle.open_time,
        open=candle.open,
        high=candle.high,
        low=candle.low,
        close=candle.close,
        volume=candle.volume,
        quote_volume=candle.quote_volume,
        trades_count=candle.trades_count,
    )


def orm_to_candle(record: orm_models.CandleRecord) -> pydantic_models.Candle:
    """
    Convert ORM CandleRecord to Pydantic Candle.
    
    Args:
        record: ORM CandleRecord
    
    Returns:
        Pydantic Candle model
    """
    return pydantic_models.Candle(
        open_time=record.open_time,
        open=record.open,
        high=record.high,
        low=record.low,
        close=record.close,
        volume=record.volume,
        quote_volume=record.quote_volume,
        trades_count=record.trades_count,
    )


def candles_to_dataframe(records: List[orm_models.CandleRecord]) -> pd.DataFrame:
    """
    Convert list of ORM CandleRecords to pandas DataFrame.
    
    Args:
        records: List of ORM CandleRecord objects
    
    Returns:
        DataFrame with columns: open_time, open, high, low, close, volume
    """
    if not records:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume"])
    
    data = []
    for record in records:
        data.append({
            "open_time": record.open_time,
            "open": float(record.open),
            "high": float(record.high),
            "low": float(record.low),
            "close": float(record.close),
            "volume": float(record.volume),
        })
    
    df = pd.DataFrame(data)
    df.set_index("open_time", inplace=True)
    return df


def indicators_to_dict(records: List[orm_models.CandleRecord]) -> Dict[str, pd.Series]:
    """
    Extract indicators from CandleRecords and convert to dictionary of Series.
    
    Args:
        records: List of ORM CandleRecord objects with indicators JSONB field
    
    Returns:
        Dict mapping indicator name to pandas Series
    """
    if not records:
        return {}
    
    # Collect all unique indicator names
    indicator_names = set()
    for record in records:
        if record.indicators:
            indicator_names.update(record.indicators.keys())
    
    # Build Series for each indicator
    indicators = {}
    for name in indicator_names:
        values = []
        timestamps = []
        for record in records:
            if record.indicators and name in record.indicators:
                values.append(float(record.indicators[name]))
                timestamps.append(record.open_time)
            else:
                values.append(None)
                timestamps.append(record.open_time)
        
        indicators[name] = pd.Series(values, index=timestamps, name=name)
    
    return indicators


# ── Position Conversions ─────────────────────────────────────────────────────

def position_to_orm(position: pydantic_models.Position) -> orm_models.PositionRecord:
    """
    Convert Pydantic Position to ORM PositionRecord.
    
    Args:
        position: Pydantic Position model
    
    Returns:
        ORM PositionRecord
    """
    return orm_models.PositionRecord(
        symbol=position.symbol,
        size=position.size,
        entry_price=position.entry_price,
        entry_time=position.entry_time,
        highest_close=position.highest_close,
        trailing_stop_price=position.trailing_stop_price,
        hard_stop_price=position.hard_stop_price,
        scale_in_count=position.scale_in_count,
        buy_signal_candles=position.buy_signal_candles,
    )


def orm_to_position(record: orm_models.PositionRecord) -> pydantic_models.Position:
    """
    Convert ORM PositionRecord to Pydantic Position.
    
    Args:
        record: ORM PositionRecord
    
    Returns:
        Pydantic Position model
    """
    return pydantic_models.Position(
        symbol=record.symbol,
        size=record.size,
        entry_price=record.entry_price,
        entry_time=record.entry_time,
        highest_close=record.highest_close,
        trailing_stop_price=record.trailing_stop_price,
        hard_stop_price=record.hard_stop_price,
        scale_in_count=record.scale_in_count,
        buy_signal_candles=record.buy_signal_candles,
    )


# ── Trade Conversions ────────────────────────────────────────────────────────

def trade_to_orm(
    trade: pydantic_models.Trade,
    run_id: str | None = None,
) -> orm_models.TradeRecord:
    """
    Convert Pydantic Trade to ORM TradeRecord.
    
    Args:
        trade: Pydantic Trade model
        run_id: Optional backtest run_id to link this trade to.
    
    Returns:
        ORM TradeRecord
    """
    return orm_models.TradeRecord(
        run_id=run_id,
        symbol=trade.symbol,
        strategy=trade.strategy,
        entry_price=trade.entry_price,
        exit_price=trade.exit_price,
        size=trade.size,
        entry_time=trade.entry_time,
        exit_time=trade.exit_time,
        pnl_usdt=trade.pnl_usdt,
        pnl_percent=trade.pnl_percent,
        duration_minutes=trade.duration_minutes,
        exit_reason=trade.exit_reason.value,  # Convert enum to string
        max_favorable_excursion=trade.max_favorable_excursion,
        max_adverse_excursion=trade.max_adverse_excursion,
    )


def orm_to_trade(record: orm_models.TradeRecord) -> pydantic_models.Trade:
    """
    Convert ORM TradeRecord to Pydantic Trade.
    
    Args:
        record: ORM TradeRecord
    
    Returns:
        Pydantic Trade model
    """
    return pydantic_models.Trade(
        symbol=record.symbol,
        strategy=record.strategy,
        entry_price=record.entry_price,
        exit_price=record.exit_price,
        size=record.size,
        entry_time=record.entry_time,
        exit_time=record.exit_time,
        pnl_usdt=record.pnl_usdt,
        pnl_percent=record.pnl_percent,
        duration_minutes=record.duration_minutes,
        exit_reason=pydantic_models.ExitReason(record.exit_reason),  # Convert string to enum
        max_favorable_excursion=record.max_favorable_excursion,
        max_adverse_excursion=record.max_adverse_excursion,
    )


# ── Order Conversions ────────────────────────────────────────────────────────

def order_to_orm(order: pydantic_models.Order) -> orm_models.OrderRecord:
    """
    Convert Pydantic Order to ORM OrderRecord.
    
    Args:
        order: Pydantic Order model
    
    Returns:
        ORM OrderRecord
    """
    return orm_models.OrderRecord(
        symbol=order.symbol,
        side=order.side.value,
        order_type=order.order_type.value,
        amount=order.amount,
        price=order.price,
        status=order.status.value,
        filled_amount=order.filled_amount,
        avg_fill_price=order.avg_fill_price,
        slippage_percent=order.slippage_percent,
        fee_usdt=order.fee_usdt,
        exchange_order_id=order.exchange_order_id,
        created_at=order.created_at,
        updated_at=order.updated_at,
        filled_at=order.filled_at,
    )


def orm_to_order(record: orm_models.OrderRecord) -> pydantic_models.Order:
    """
    Convert ORM OrderRecord to Pydantic Order.
    
    Args:
        record: ORM OrderRecord
    
    Returns:
        Pydantic Order model
    """
    return pydantic_models.Order(
        run_id=record.run_id,
        symbol=record.symbol,
        side=pydantic_models.OrderSide(record.side),
        order_type=pydantic_models.OrderType(record.order_type),
        amount=record.amount,
        price=record.price,
        status=pydantic_models.OrderStatus(record.status),
        filled_amount=record.filled_amount,
        avg_fill_price=record.avg_fill_price,
        slippage_percent=record.slippage_percent,
        fee_usdt=record.fee_usdt,
        exchange_order_id=record.exchange_order_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
        filled_at=record.filled_at,
    )


# ── Portfolio Snapshot Conversions ───────────────────────────────────────────

def portfolio_snapshot_to_orm(
    snapshot: pydantic_models.PortfolioSnapshot,
    run_id: str | None = None,
) -> orm_models.PortfolioSnapshot:
    """
    Convert Pydantic PortfolioSnapshot to ORM PortfolioSnapshot.
    
    Args:
        snapshot: Pydantic PortfolioSnapshot model
        run_id: Optional backtest run_id to link this snapshot to.
    
    Returns:
        ORM PortfolioSnapshot
    """
    return orm_models.PortfolioSnapshot(
        run_id=run_id,
        timestamp=snapshot.timestamp,
        usdt_balance=snapshot.usdt_balance,
        positions_value=snapshot.positions_value,
        total_equity=snapshot.total_equity,
        daily_pnl=snapshot.daily_pnl,
        total_pnl=snapshot.total_pnl,
        open_positions_count=snapshot.open_positions_count,
    )


def orm_to_portfolio_snapshot(record: orm_models.PortfolioSnapshot) -> pydantic_models.PortfolioSnapshot:
    """
    Convert ORM PortfolioSnapshot to Pydantic PortfolioSnapshot.
    
    Args:
        record: ORM PortfolioSnapshot
    
    Returns:
        Pydantic PortfolioSnapshot model
    """
    return pydantic_models.PortfolioSnapshot(
        timestamp=record.timestamp,
        usdt_balance=record.usdt_balance,
        positions_value=record.positions_value,
        total_equity=record.total_equity,
        daily_pnl=record.daily_pnl,
        total_pnl=record.total_pnl,
        open_positions_count=record.open_positions_count,
    )


# ── Backtest Results Conversions ─────────────────────────────────────────────

def backtest_metrics_to_orm(metrics: pydantic_models.BacktestMetrics) -> orm_models.BacktestResult:
    """
    Convert Pydantic BacktestMetrics to ORM BacktestResult.
    
    Args:
        metrics: Pydantic BacktestMetrics model
    
    Returns:
        ORM BacktestResult
    """
    return orm_models.BacktestResult(
        run_id=metrics.run_id,
        strategy=metrics.strategy,
        symbol=metrics.symbol,
        start_date=metrics.start_date,
        end_date=metrics.end_date,
        initial_capital=metrics.initial_capital,
        final_capital=metrics.final_capital,
        total_return=metrics.total_return,
        total_trades=metrics.total_trades,
        winning_trades=metrics.winning_trades,
        losing_trades=metrics.losing_trades,
        win_rate=metrics.win_rate,
        avg_win=metrics.avg_win,
        avg_loss=metrics.avg_loss,
        max_drawdown=metrics.max_drawdown,
        sharpe_ratio=metrics.sharpe_ratio,
        profit_factor=metrics.profit_factor,
        avg_trade_duration_minutes=metrics.avg_trade_duration_minutes,
        max_consecutive_wins=metrics.max_consecutive_wins,
        max_consecutive_losses=metrics.max_consecutive_losses,
        buy_hold_return=metrics.buy_hold_return,
        buy_hold_vs_strategy=metrics.buy_hold_vs_strategy,
    )


def orm_to_backtest_metrics(record: orm_models.BacktestResult) -> pydantic_models.BacktestMetrics:
    """
    Convert ORM BacktestResult to Pydantic BacktestMetrics.
    
    Args:
        record: ORM BacktestResult
    
    Returns:
        Pydantic BacktestMetrics model
    """
    return pydantic_models.BacktestMetrics(
        run_id=record.run_id,
        strategy=record.strategy,
        symbol=record.symbol,
        start_date=record.start_date,
        end_date=record.end_date,
        initial_capital=record.initial_capital,
        final_capital=record.final_capital,
        total_return=record.total_return,
        total_trades=record.total_trades,
        winning_trades=record.winning_trades,
        losing_trades=record.losing_trades,
        win_rate=record.win_rate,
        avg_win=record.avg_win,
        avg_loss=record.avg_loss,
        max_drawdown=record.max_drawdown,
        sharpe_ratio=record.sharpe_ratio,
        profit_factor=record.profit_factor,
        avg_trade_duration_minutes=record.avg_trade_duration_minutes,
        max_consecutive_wins=record.max_consecutive_wins,
        max_consecutive_losses=record.max_consecutive_losses,
        buy_hold_return=record.buy_hold_return,
        buy_hold_vs_strategy=record.buy_hold_vs_strategy,
    )


# ── Strategy State Conversions ───────────────────────────────────────────────


def orm_to_strategy_state(
    record: orm_models.StrategyStateRecord,
) -> pydantic_models.StrategyStateResponse:
    """
    Convert ORM StrategyStateRecord to Pydantic StrategyStateResponse.
    """
    return pydantic_models.StrategyStateResponse(
        symbol=record.symbol,
        strategy=record.strategy,
        state=pydantic_models.StrategyState(record.state),
        cooldown_until=record.cooldown_until,
        consecutive_buy_candles=record.consecutive_buy_candles,
        last_exit_time=record.last_exit_time,
        state_data=record.state_data,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def orm_to_signal_snapshot(
    record: orm_models.StrategyStateRecord,
) -> pydantic_models.StrategySignalSnapshot:
    """
    Derive a signal snapshot from a StrategyStateRecord.

    Signal is always HOLD until live conditions are persisted
    to ``state_data`` (Phase 3, task 3.8).
    """
    return pydantic_models.StrategySignalSnapshot(
        symbol=record.symbol,
        strategy=record.strategy,
        state=pydantic_models.StrategyState(record.state),
        signal=pydantic_models.Signal.HOLD,
        conditions=record.state_data,
        updated_at=record.updated_at,
    )


# ── Exports ──────────────────────────────────────────────────────────────────

__all__ = [
    "candle_to_orm",
    "orm_to_candle",
    "candles_to_dataframe",
    "indicators_to_dict",
    "position_to_orm",
    "orm_to_position",
    "trade_to_orm",
    "orm_to_trade",
    "order_to_orm",
    "orm_to_order",
    "portfolio_snapshot_to_orm",
    "orm_to_portfolio_snapshot",
    "backtest_metrics_to_orm",
    "orm_to_backtest_metrics",
    "orm_to_strategy_state",
    "orm_to_signal_snapshot",
]
