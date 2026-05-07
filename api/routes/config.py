"""
Strategy configuration REST endpoints.

Mounted at ``/api/strategy/config`` by :func:`api.main._include_routes`.

Endpoints
---------
GET /strategies
    List all registered strategy names.

GET /{name}
    Return the current config for a strategy with field metadata.

PATCH /{name}
    Update strategy config fields in-memory (not persisted to YAML).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo

from core.config import get_settings

router = APIRouter()


# ── Helpers ──────────────────────────────────────────────────────────────────


def _field_meta(name: str, field: FieldInfo, value: Any) -> Dict[str, Any]:
    """Extract UI-relevant metadata from a Pydantic FieldInfo."""
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


def _get_strategy_model(name: str):
    """Return the Pydantic model instance for a strategy, or raise 404."""
    settings = get_settings()
    cfg = settings.strategies.get_strategy_config(name)
    if cfg is None:
        raise HTTPException(status_code=404, detail=f"Strategy '{name}' not found")
    return cfg


def _group_for_field(name: str) -> str:
    """Assign a field to a UI group based on its name."""
    if name in ("enabled", "description"):
        return "General"
    if "timeframe" in name:
        return "Timeframes"
    if any(k in name for k in ("ema", "adx", "rsi", "bb_", "volume")):
        return "Indicators"
    if any(k in name for k in ("entry", "scale")):
        return "Position Sizing"
    if any(k in name for k in ("exit", "momentum")):
        return "Exit Strategy"
    if any(k in name for k in ("breakeven", "ratchet", "lockin")):
        return "Break-even Ratchet"
    if any(k in name for k in ("stop", "atr_period", "hard_stop")):
        return "Stop-Loss"
    if "cooldown" in name or "time_stop" in name:
        return "Cooldown"
    if "session" in name or "dead_zone" in name:
        return "Session Filter"
    return "Other"


# ── Response models ──────────────────────────────────────────────────────────


class FieldMeta(BaseModel):
    name: str
    value: Any
    default: Any = None
    type: str
    description: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    group: str = "Other"
    source: str = "default"


class StrategyConfigResponse(BaseModel):
    strategy: str
    fields: List[FieldMeta]


class StrategyListResponse(BaseModel):
    strategies: List[str]


class StrategySummary(BaseModel):
    name: str
    enabled: bool


class StrategyOverviewResponse(BaseModel):
    strategies: List[StrategySummary]


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/strategies", response_model=StrategyListResponse)
def list_strategies() -> StrategyListResponse:
    """List all registered strategy names with configs."""
    settings = get_settings()
    names: List[str] = []
    for field_name in settings.strategies.model_fields:
        names.append(field_name)
    extra = settings.strategies.model_extra or {}
    for name in extra:
        if name not in names:
            names.append(name)
    return StrategyListResponse(strategies=sorted(names))


@router.get("/overview", response_model=StrategyOverviewResponse)
def strategy_overview() -> StrategyOverviewResponse:
    """Return enabled status and allocation weight for every strategy."""
    settings = get_settings()
    summaries: List[StrategySummary] = []

    for field_name in settings.strategies.model_fields:
        cfg = settings.strategies.get_strategy_config(field_name)
        if cfg is None:
            continue
        if isinstance(cfg, dict):
            enabled = cfg.get("enabled", False)
        else:
            enabled = getattr(cfg, "enabled", False)
        summaries.append(StrategySummary(name=field_name, enabled=enabled))

    return StrategyOverviewResponse(strategies=summaries)


@router.get("/{name}", response_model=StrategyConfigResponse)
def get_strategy_config(name: str) -> StrategyConfigResponse:
    """Return the current config for a strategy with field metadata and source info."""
    from core.config import get_config_source

    cfg = _get_strategy_model(name)

    if isinstance(cfg, dict):
        fields = [
            FieldMeta(name=k, value=v, type=type(v).__name__, group=_group_for_field(k))
            for k, v in cfg.items()
        ]
        return StrategyConfigResponse(strategy=name, fields=fields)

    values = cfg.model_dump()
    model_fields = cfg.model_fields
    sources = get_config_source(name, values)
    fields: List[FieldMeta] = []

    for field_name, field_info in model_fields.items():
        if field_name == "session_dead_zones":
            continue
        meta = _field_meta(field_name, field_info, values.get(field_name))
        meta["group"] = _group_for_field(field_name)
        meta["source"] = sources.get(field_name, "default")
        fields.append(FieldMeta(**meta))

    return StrategyConfigResponse(strategy=name, fields=fields)


@router.patch("/{name}", response_model=StrategyConfigResponse)
def update_strategy_config(name: str, updates: Dict[str, Any]) -> StrategyConfigResponse:
    """Update strategy config fields and persist to database."""
    from core.config import save_db_config, _load_db_config, reload_settings

    cfg = _get_strategy_model(name)

    if isinstance(cfg, dict):
        cfg.update(updates)
        db_overrides = _load_db_config(name) or {}
        db_overrides.update(updates)
        save_db_config(name, db_overrides)
        reload_settings()
        return get_strategy_config(name)

    current = cfg.model_dump()
    current.update(updates)

    try:
        validated = cfg.__class__(**current)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    for key, val in validated.model_dump().items():
        if key in updates:
            setattr(cfg, key, val)

    db_overrides = _load_db_config(name) or {}
    db_overrides.update(updates)
    save_db_config(name, db_overrides)
    reload_settings()

    return get_strategy_config(name)


@router.delete("/{name}/reset")
def reset_strategy_config(name: str) -> StrategyConfigResponse:
    """Delete DB overrides, revert to Pydantic defaults."""
    from core.config import delete_db_config, reload_settings

    _get_strategy_model(name)
    delete_db_config(name)
    reload_settings()
    return get_strategy_config(name)
