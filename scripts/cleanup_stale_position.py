"""
One-shot script to delete a stale PositionRecord and reset
StrategyStateRecord to FLAT.

Usage:
    docker compose run --rm app python scripts/cleanup_stale_position.py BIOUSDT smart_hodler
"""

import sys
from db import get_session
from db.models import PositionRecord, StrategyStateRecord


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python scripts/cleanup_stale_position.py <SYMBOL> <STRATEGY>")
        sys.exit(1)

    symbol = sys.argv[1]
    strategy = sys.argv[2]

    with get_session() as session:
        pos = session.query(PositionRecord).filter_by(symbol=symbol, strategy=strategy).first()
        state = session.query(StrategyStateRecord).filter_by(symbol=symbol, strategy=strategy).first()

        if pos is None and (state is None or state.state == "flat"):
            print(f"Nothing to clean up — no position and state is already flat for {strategy}:{symbol}")
            return

        if pos is not None:
            print(f"Deleting PositionRecord: size={pos.size}, entry_price={pos.entry_price}")
            session.delete(pos)

        if state is not None and state.state in ("position", "reduced"):
            print(f"Resetting StrategyStateRecord from '{state.state}' to 'flat'")
            state.state = "flat"
            if state.state_data:
                state.state_data = {**state.state_data, "cooldown_remaining": 0}

    print(f"Done — {strategy}:{symbol} cleaned up.")


if __name__ == "__main__":
    main()
