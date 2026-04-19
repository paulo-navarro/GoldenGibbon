"""
Strategy registry with auto-discovery.

Scans ``core.strategies`` for all concrete subclasses of
:class:`~core.strategies.base.Strategy` and registers them by
their :attr:`name` property.

Usage::

    from core.strategies.registry import get_registry

    registry = get_registry()          # {"smart_hodler": <class>, ...}
    cls = registry["smart_hodler"]
    strategy = cls(config_dict)
"""

from __future__ import annotations

import importlib
import pkgutil
from typing import Dict, Type

import structlog

from core.strategies.base import Strategy

logger = structlog.get_logger(__name__)

_registry: Dict[str, Type[Strategy]] = {}
_discovered = False


def _discover() -> None:
    """Import all modules in ``core.strategies`` and collect subclasses."""
    global _discovered  # noqa: PLW0603
    if _discovered:
        return

    import core.strategies as pkg

    for finder, module_name, is_pkg in pkgutil.iter_modules(pkg.__path__):
        if module_name in ("base", "registry"):
            continue
        try:
            mod = importlib.import_module(f"core.strategies.{module_name}")
            for attr_name in dir(mod):
                attr = getattr(mod, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, Strategy)
                    and attr is not Strategy
                    and not getattr(attr, "__abstractmethods__", None)
                ):
                    # Instantiate temporarily to read the name property
                    try:
                        instance = attr.__new__(attr)
                        name = type(instance).name.fget(instance)  # type: ignore[union-attr]
                    except Exception:
                        continue
                    if name and name not in _registry:
                        _registry[name] = attr
                        logger.debug("strategy_registry.registered", name=name, cls=attr.__name__)
        except Exception as exc:
            logger.warning("strategy_registry.import_failed", module=module_name, error=str(exc))

    _discovered = True


def get_registry() -> Dict[str, Type[Strategy]]:
    """Return the strategy registry, populating it on first call."""
    _discover()
    return _registry


def reset_registry() -> None:
    """Clear the registry. Used in tests."""
    global _discovered  # noqa: PLW0603
    _registry.clear()
    _discovered = False
