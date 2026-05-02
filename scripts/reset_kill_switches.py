"""
One-shot script to reset all kill-switch states in the database.

Clears triggered flags and peak_equity so the next tick re-initialises
from current equity.  Sets trading_mode to "live" for all records.

The running Celery worker will detect the DB reset on the next tick
and sync its in-memory kill-switch state automatically — no worker
restart required.

Usage:
    docker compose run --rm app python scripts/reset_kill_switches.py
"""

from db import get_session
from db.models import StrategyStateRecord


def main() -> None:
    updated = 0
    with get_session() as session:
        records = session.query(StrategyStateRecord).all()
        for rec in records:
            data = rec.state_data or {}
            changed = False

            for key, default in (
                ("kill_switch_triggered", False),
                ("kill_switch_reason", None),
                ("kill_switch_trigger_time", None),
                ("kill_switch_peak_equity", "0"),
                ("kill_switch_day_start_equity", "0"),
                ("kill_switch_current_day", None),
            ):
                if data.get(key) != default:
                    data[key] = default
                    changed = True

            data["kill_switch_trading_mode"] = "live"
            changed = True

            if changed:
                rec.state_data = data
                updated += 1

        session.commit()

    print(f"Reset kill-switch for {updated}/{len(records)} strategy states.")


if __name__ == "__main__":
    main()
