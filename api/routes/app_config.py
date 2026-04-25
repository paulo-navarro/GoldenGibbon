"""
Generic app configuration CRUD endpoints.

Mounted at ``/api/config`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /namespaces
    List all valid config namespace names.

GET /{namespace}
    Return current config with field metadata.

PATCH /{namespace}
    Partial update — validate, persist, reload.

DELETE /{namespace}/reset
    Delete DB override, revert to Pydantic defaults.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

import structlog

from core.config import (
    NAMESPACE_MODELS,
    _load_app_config,
    delete_app_config,
    get_settings,
    reload_settings,
    save_app_config,
)

logger = structlog.get_logger(__name__)
router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _field_meta(name: str, field: FieldInfo, value: Any) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "name": name,
        "value": value,
        "default": field.default if field.default is not None else None,
    }

    if field.description:
        meta["description"] = field.description

    if isinstance(value, bool):
        meta["type"] = "bool"
    elif isinstance(value, int):
        meta["type"] = "int"
    elif isinstance(value, float):
        meta["type"] = "float"
    elif isinstance(value, str):
        meta["type"] = "str"
    elif isinstance(value, list):
        meta["type"] = "list"
    elif isinstance(value, dict):
        meta["type"] = "dict"
    else:
        meta["type"] = "str"

    for constraint in field.metadata:
        if hasattr(constraint, "ge"):
            meta["min"] = constraint.ge
        if hasattr(constraint, "gt"):
            meta["min"] = constraint.gt
        if hasattr(constraint, "le"):
            meta["max"] = constraint.le
        if hasattr(constraint, "lt"):
            meta["max"] = constraint.lt

    return meta


def _validate_namespace(namespace: str):
    if namespace not in NAMESPACE_MODELS:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown namespace '{namespace}'. Valid: {sorted(NAMESPACE_MODELS.keys())}",
        )
    return NAMESPACE_MODELS[namespace]


# ── Response models ──────────────────────────────────────────────────────────


class FieldMeta(BaseModel):
    name: str
    value: Any
    default: Any = None
    type: str
    description: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None


class NamespaceConfigResponse(BaseModel):
    namespace: str
    source: str
    fields: List[FieldMeta]


class NamespaceListResponse(BaseModel):
    namespaces: List[str]


# ── Trading mode side-effects ────────────────────────────────���────────────────


def _handle_trading_mode_side_effects(namespace: str, merged: Dict[str, Any]) -> None:
    """
    When a trading mode is enabled, enforce mutual exclusion, clear stale
    worker state, and seed a portfolio snapshot with real Binance balances
    so the dashboard shows correct values immediately.
    """
    from core.tasks import clear_worker_state

    if namespace == "live_trading" and merged.get("enabled"):
        # Disable paper trading
        paper_data = _load_app_config("paper_trading") or {}
        if paper_data.get("enabled"):
            paper_data["enabled"] = False
            save_app_config("paper_trading", paper_data)
            logger.info("trading_mode: auto-disabled paper_trading")

        clear_worker_state()
        _seed_live_portfolio_snapshot()

    elif namespace == "paper_trading" and merged.get("enabled"):
        # Disable live trading
        live_data = _load_app_config("live_trading") or {}
        if live_data.get("enabled"):
            live_data["enabled"] = False
            save_app_config("live_trading", live_data)
            logger.info("trading_mode: auto-disabled live_trading")

        clear_worker_state()


def _seed_live_portfolio_snapshot() -> None:
    """
    Fetch real balances from Binance and create a PortfolioSnapshot
    so the dashboard reflects actual account state before the next tick.
    """
    try:
        from datetime import datetime, timezone
        from decimal import Decimal

        from core.config import get_settings
        from core.execution.binance import BinanceExecutor
        from core.portfolio import PortfolioManager
        from db import get_session
        from db.models import PortfolioSnapshot as PortfolioSnapshotORM

        settings = get_settings()
        pm = PortfolioManager(initial_capital=Decimal("0"))
        executor = BinanceExecutor.from_settings(
            strategy_name="_seed",
            portfolio_manager=pm,
        )
        account = executor.get_account_info()
        balances = account["balances"]

        usdt_free = balances.get("USDT", {}).get("free", Decimal("0"))
        usdt_locked = balances.get("USDT", {}).get("locked", Decimal("0"))
        usdt_total = usdt_free + usdt_locked

        # Calculate value of asset holdings for enabled symbols
        positions_value = Decimal("0")
        asset_count = 0
        enabled_symbols = [s.symbol for s in settings.enabled_symbols]

        asset_to_symbol = {}
        for sym in enabled_symbols:
            base_asset = sym.replace("USDT", "")
            if base_asset in balances:
                asset_to_symbol[base_asset] = sym

        if asset_to_symbol:
            prices = executor.get_ticker_prices(list(asset_to_symbol.values()))
            for base_asset, sym in asset_to_symbol.items():
                bal = balances[base_asset]
                qty = bal.get("free", Decimal("0")) + bal.get("locked", Decimal("0"))
                price = prices.get(sym)
                if price and qty > 0:
                    positions_value += qty * price
                    asset_count += 1
                    logger.info(
                        "trading_mode: found asset on exchange",
                        asset=base_asset, qty=str(qty), price=str(price),
                        value_usdt=str(qty * price),
                    )

        total_equity = usdt_total + positions_value
        now = datetime.now(timezone.utc)
        snapshot = PortfolioSnapshotORM(
            run_id=f"live_seed_{now.strftime('%Y%m%dT%H%M%S')}",
            timestamp=now,
            usdt_balance=usdt_total,
            positions_value=positions_value,
            total_equity=total_equity,
            daily_pnl=Decimal("0"),
            total_pnl=Decimal("0"),
            open_positions_count=asset_count,
            trading_mode="live",
        )

        with get_session() as session:
            session.add(snapshot)

        logger.info(
            "trading_mode: seeded live portfolio snapshot",
            usdt_balance=str(usdt_total),
            positions_value=str(positions_value),
            total_equity=str(total_equity),
        )

    except Exception as exc:
        logger.warning(
            "trading_mode: failed to seed live snapshot (will sync on next tick)",
            error=str(exc),
        )


# ── Endpoints ─────────────────────────────────────��──────────────────────────


@router.get("/namespaces", response_model=NamespaceListResponse)
def list_namespaces() -> NamespaceListResponse:
    return NamespaceListResponse(namespaces=sorted(NAMESPACE_MODELS.keys()))


@router.get("/{namespace}", response_model=NamespaceConfigResponse)
def get_namespace_config(namespace: str) -> NamespaceConfigResponse:
    model_cls = _validate_namespace(namespace)

    db_data = _load_app_config(namespace)
    if db_data:
        source = "db"
        instance = model_cls(**db_data)
    else:
        source = "default"
        instance = model_cls()

    values = instance.model_dump()
    fields: List[FieldMeta] = []
    for field_name, field_info in instance.model_fields.items():
        meta = _field_meta(field_name, field_info, values.get(field_name))
        fields.append(FieldMeta(**meta))

    return NamespaceConfigResponse(namespace=namespace, source=source, fields=fields)


@router.patch("/{namespace}", response_model=NamespaceConfigResponse)
def update_namespace_config(namespace: str, updates: Dict[str, Any]) -> NamespaceConfigResponse:
    model_cls = _validate_namespace(namespace)

    db_data = _load_app_config(namespace) or {}
    merged = {**db_data, **updates}

    try:
        model_cls(**merged)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    save_app_config(namespace, merged)

    _handle_trading_mode_side_effects(namespace, merged)

    reload_settings()
    return get_namespace_config(namespace)


@router.delete("/{namespace}/reset", response_model=NamespaceConfigResponse)
def reset_namespace_config(namespace: str) -> NamespaceConfigResponse:
    _validate_namespace(namespace)
    delete_app_config(namespace)
    reload_settings()
    return get_namespace_config(namespace)
