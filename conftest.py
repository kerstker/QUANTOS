"""Repo-root pytest configuration (Windows handle hygiene).

When pytest runs under a detached or agent-hosted shell, the inherited
console ``STD_INPUT_HANDLE`` can become an *invalid* handle mid-run.  Any
subprocess spawned with the default ``stdin=None`` then fails inside
``subprocess._make_inheritable`` with ``OSError: [WinError 6]`` before the
child even starts — surfacing as hundreds of unrelated test failures in
long suites (first observed 2026-08-02 as the 26/27 validation stage
failure: every ``check-attr``/CLI subprocess test in tests/governance).

Test-spawned children are non-interactive by contract, so route the
default stdin to NUL.  For stdout/stderr, preserve inheritance when the
host handles are healthy (live output in normal shells) and degrade to
NUL only when duplication of the inherited handle actually fails — the
gate must never depend on the host console.  Children that genuinely
need input or piped output still get it through an explicit
``stdin=``/``stdout=``/``stderr=``/``input=`` argument, which is left
untouched.  This shim weakens no assertion; it only stops a dead host
handle from poisoning subprocess creation.
"""
from __future__ import annotations

import subprocess

_RealPopen = subprocess.Popen


class _DevnullStdinPopen(_RealPopen):  # type: ignore[misc, valid-type]
    def __init__(self, *args: object, **kwargs: object) -> None:
        if kwargs.get("stdin") is None:
            kwargs["stdin"] = subprocess.DEVNULL
        try:
            super().__init__(*args, **kwargs)
        except OSError:
            # The inherited stdout/stderr handle is dead in this host;
            # fall back to NUL rather than failing the child spawn.
            for name in ("stdout", "stderr"):
                if kwargs.get(name) is None:
                    kwargs[name] = subprocess.DEVNULL
            super().__init__(*args, **kwargs)


subprocess.Popen = _DevnullStdinPopen  # type: ignore[misc]
