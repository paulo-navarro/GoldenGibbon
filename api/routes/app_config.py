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

from core.config import (
    NAMESPACE_MODELS,
    _load_app_config,
    delete_app_config,
    get_settings,
    reload_settings,
    save_app_config,
)

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


# ── Endpoints ────────────────────────────────────────────────────────────────


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
    reload_settings()
    return get_namespace_config(namespace)


@router.delete("/{namespace}/reset", response_model=NamespaceConfigResponse)
def reset_namespace_config(namespace: str) -> NamespaceConfigResponse:
    _validate_namespace(namespace)
    delete_app_config(namespace)
    reload_settings()
    return get_namespace_config(namespace)
