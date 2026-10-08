"""Compatibility imports for local publication; implementation is runtime-neutral."""
from quantos.local_artifacts import atomic_write_json

# Preserve module handles used by existing publication fault-injection callers.
from quantos.local_artifacts import os, time

__all__ = ["atomic_write_json"]
