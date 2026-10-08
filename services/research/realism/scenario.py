"""历史情景重演的兼容导出。核心纯函数位于无领域反向依赖的共享层。"""
from quantos.scenario import (
    CRYPTO_CRISES,
    iso_to_ms,
    max_drawdown_window,
    window_stats,
)

__all__ = [
    "CRYPTO_CRISES",
    "iso_to_ms",
    "max_drawdown_window",
    "window_stats",
]
