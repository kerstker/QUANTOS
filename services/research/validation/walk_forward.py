"""Compatibility exports for the domain-neutral validation splitters."""

from services.validation.walk_forward import purged_kfold, walk_forward_splits

__all__ = ["purged_kfold", "walk_forward_splits"]
