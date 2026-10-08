"""Optional accelerator discovery for the isolated research ML layer.

The rest of QUANTOS deliberately remains CPU-only. This module is the only
place where the research ML package decides whether a CUDA-backed estimator is
available. ``auto`` may fall back to CPU; explicit ``cuda`` fails closed.
"""

from __future__ import annotations

import warnings
from dataclasses import asdict, dataclass
from importlib import import_module
from typing import Literal

Accelerator = Literal["cpu", "cuda", "auto"]


@dataclass(frozen=True)
class AcceleratorStatus:
    """Serialisable, honest snapshot of the research accelerator state."""

    requested: str
    selected: str
    cuda_available: bool
    xgboost_installed: bool
    xgboost_cuda_build: bool
    cuda_device_count: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _xgboost_info() -> tuple[bool, bool]:
    try:
        xgb = import_module("xgboost")
    except Exception:
        return False, False
    try:
        info = xgb.build_info()
        return True, bool(info.get("USE_CUDA", False))
    except Exception:
        # A broken optional install must never make the CPU path unavailable.
        return True, False


def _cuda_device_count() -> int:
    try:
        # CuPy can still expose the driver device count when the optional
        # toolkit headers are absent; that expected local warning is not a
        # failure of the XGBoost CUDA path.
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="CUDA path could not be detected.*", category=UserWarning)
            cupy = import_module("cupy")
        return max(0, int(cupy.cuda.runtime.getDeviceCount()))
    except Exception:
        return 0


def inspect_accelerator(requested: Accelerator = "auto") -> AcceleratorStatus:
    """Inspect the optional XGBoost/CUDA path without mutating state."""

    if requested not in {"cpu", "cuda", "auto"}:
        raise ValueError("accelerator must be one of: cpu, cuda, auto")

    xgb_installed, xgb_cuda_build = _xgboost_info()
    device_count = _cuda_device_count()
    cuda_available = xgb_installed and xgb_cuda_build and device_count > 0

    if requested == "cpu":
        selected = "cpu"
        reason = "explicit_cpu"
    elif cuda_available:
        selected = "cuda"
        reason = "xgboost_cuda_build_and_device_visible"
    else:
        selected = "cpu"
        if not xgb_installed:
            reason = "xgboost_not_installed"
        elif not xgb_cuda_build:
            reason = "xgboost_cpu_build"
        elif device_count <= 0:
            reason = "no_cuda_device_visible"
        else:
            reason = "cuda_probe_failed"

    return AcceleratorStatus(
        requested=requested,
        selected=selected,
        cuda_available=bool(cuda_available),
        xgboost_installed=bool(xgb_installed),
        xgboost_cuda_build=bool(xgb_cuda_build),
        cuda_device_count=device_count,
        reason=reason,
    )


def resolve_accelerator(requested: Accelerator = "auto") -> str:
    """Return ``cpu``/``cuda``; explicit unavailable CUDA is a hard error."""

    status = inspect_accelerator(requested)
    if requested == "cuda" and not status.cuda_available:
        raise RuntimeError(
            "CUDA research accelerator requested but unavailable: "
            f"{status.reason} (xgboost={status.xgboost_installed}, "
            f"cuda_build={status.xgboost_cuda_build}, devices={status.cuda_device_count})"
        )
    return status.selected
