#!/usr/bin/env python3
"""
CLI script to download historical candle data for all configured symbol/timeframe pairs.

Downloads up to --days of history from Binance and caches to Postgres.
Skips already-cached candles (idempotent via upsert deduplication).

Usage:
    python scripts/pull_historical_data.py
    python scripts/pull_historical_data.py --days 30
    python scripts/pull_historical_data.py --days 30 --symbol BTCUSDT --timeframe 15m
"""

import argparse
import logging
import sys
from typing import Dict, List, Optional, Tuple

from core.config import get_settings
from core.data import BinanceClient, DataLoader
from db import get_session

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download historical candle data for all configured symbols."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=730,
        help="Number of days of history to download (default: 730)",
    )
    parser.add_argument(
        "--symbol",
        type=str,
        default=None,
        help="Filter to a single symbol (e.g. BTCUSDT). Defaults to all enabled symbols.",
    )
    parser.add_argument(
        "--timeframe",
        type=str,
        default=None,
        help="Filter to a single timeframe (e.g. 15m). Defaults to all timeframes for each symbol.",
    )
    return parser.parse_args()


def build_pairs(
    settings,
    symbol_filter: Optional[str],
    timeframe_filter: Optional[str],
) -> List[Tuple[str, str]]:
    """Return (symbol, timeframe) pairs from config, with optional filters."""
    pairs = []
    for sym_cfg in settings.enabled_symbols:
        if symbol_filter and sym_cfg.symbol != symbol_filter:
            continue
        for tf in sym_cfg.timeframes:
            if timeframe_filter and tf != timeframe_filter:
                continue
            pairs.append((sym_cfg.symbol, tf))
    return pairs


def run(
    pairs: List[Tuple[str, str]],
    days: int,
    binance_base_url: str,
    max_candles: int,
) -> Dict[Tuple[str, str], Dict]:
    """
    Download historical data for all pairs.

    Returns a results dict keyed by (symbol, timeframe):
        {"new": int, "status": "OK" | "FAILED", "error": str | None}
    """
    client = BinanceClient(base_url=binance_base_url)
    results = {}
    total = len(pairs)

    for idx, (symbol, timeframe) in enumerate(pairs, start=1):
        prefix = f"[{idx}/{total}] {symbol} {timeframe}"
        logger.info(f"{prefix} — starting ({days} days)")
        try:
            with get_session() as session:
                loader = DataLoader(
                    session=session,
                    binance_client=client,
                    max_candles_per_request=max_candles,
                )
                new_count = loader.load_historical(
                    symbol=symbol,
                    timeframe=timeframe,
                    lookback_days=days,
                )
            logger.info(f"{prefix} — {new_count} new candles")
            results[(symbol, timeframe)] = {"new": new_count, "status": "OK", "error": None}
        except Exception as exc:
            logger.error(f"{prefix} — FAILED: {exc}")
            results[(symbol, timeframe)] = {"new": 0, "status": "FAILED", "error": str(exc)}

    return results


def print_summary(results: Dict[Tuple[str, str], Dict], days: int) -> None:
    col_w = [10, 6, 8, 8, 50]
    header = f"{'Symbol':<{col_w[0]}}  {'TF':<{col_w[1]}}  {'New':>{col_w[2]}}  {'Status':<{col_w[3]}}"
    separator = "-" * (sum(col_w[:4]) + 6)

    print(f"\n{'=' * len(separator)}")
    print(f"  Historical data pull complete  ({days} days)")
    print(f"{'=' * len(separator)}")
    print(header)
    print(separator)

    total_new = 0
    failed = 0
    for (symbol, timeframe), info in results.items():
        total_new += info["new"]
        if info["status"] == "FAILED":
            failed += 1
        error_note = f"  {info['error'][:48]}" if info["error"] else ""
        print(
            f"{symbol:<{col_w[0]}}  {timeframe:<{col_w[1]}}  {info['new']:>{col_w[2]}}  "
            f"{info['status']:<{col_w[3]}}{error_note}"
        )

    print(separator)
    print(f"{'TOTAL':<{col_w[0]}}  {'':>{col_w[1]}}  {total_new:>{col_w[2]}}  ", end="")
    print(f"{'OK' if failed == 0 else f'{failed} FAILED'}")
    print()


def main() -> int:
    args = parse_args()
    settings = get_settings()

    pairs = build_pairs(settings, args.symbol, args.timeframe)
    if not pairs:
        logger.error("No symbol/timeframe pairs matched — check your filters or symbols.yaml.")
        return 1

    logger.info(
        f"Pulling {len(pairs)} pair(s) × {args.days} days "
        f"(symbols: {args.symbol or 'all'}, timeframe: {args.timeframe or 'all'})"
    )

    results = run(
        pairs=pairs,
        days=args.days,
        binance_base_url=settings.data.binance_api_base,
        max_candles=settings.data.max_candles_per_request,
    )

    print_summary(results, args.days)

    failed = sum(1 for r in results.values() if r["status"] == "FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user.")
        sys.exit(1)
    except Exception as exc:
        logger.error(f"Fatal error: {exc}", exc_info=True)
        sys.exit(1)
