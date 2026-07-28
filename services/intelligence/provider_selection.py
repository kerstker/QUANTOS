"""Fail-closed selection for registered model providers.

This module reads registration metadata only. It has no network or credential
access and defaults to the caller-supplied offline provider.
"""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path

from pydantic import ValidationError

from quantos.contracts.external_model_provider import (
    ExternalModelProviderRegistration,
    ExternalModelProviderRegistry,
)


class ProviderSelectionError(ValueError):
    """The provider registry or requested selection is not admissible."""


def load_provider_registry(path: str | Path) -> ExternalModelProviderRegistry:
    candidate = Path(path)
    try:
        document = json.loads(candidate.read_text(encoding="utf-8"))
        return ExternalModelProviderRegistry.model_validate(document)
    except (OSError, json.JSONDecodeError, ValidationError, TypeError, ValueError) as exc:
        raise ProviderSelectionError(
            f"model provider registry is unavailable or invalid: {type(exc).__name__}"
        ) from exc


def select_provider(
    registry: ExternalModelProviderRegistry,
    *,
    requested_provider_id: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ExternalModelProviderRegistration:
    if not isinstance(registry, ExternalModelProviderRegistry):
        raise TypeError("provider selection requires the canonical registry")
    environment = os.environ if environ is None else environ
    selected = requested_provider_id
    if selected is None:
        selected = str(environment.get(registry.selection_env, "")).strip()
    if not selected:
        selected = registry.default_provider_id
    matches = [item for item in registry.providers if item.provider_id == selected]
    if len(matches) != 1:
        raise ProviderSelectionError(f"model provider is not registered: {selected!r}")
    provider = matches[0]
    if not provider.enabled:
        raise ProviderSelectionError(f"model provider is disabled: {selected!r}")
    return provider


__all__ = [
    "ProviderSelectionError",
    "load_provider_registry",
    "select_provider",
]
