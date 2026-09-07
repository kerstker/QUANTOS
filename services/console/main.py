"""Official read-only console API composition root."""
from services.console.data_console import DEFAULT_DATA_CONSOLE_PORT
from services.console.risk_console import DEFAULT_RISK_CONSOLE_PORT
from services.console.api.data_port import data_catalog_runtime
from services.console.api.hourly_alpha_views import router as hourly_alpha_router
from services.console.api.main import app
from services.console.api.market_dashboard import router as market_dashboard_router
from services.console.api.forward_dashboard import router as forward_dashboard_router
from services.console.api.mft_execution_views import router as mft_execution_router
from services.console.api.risk_port import risk_console_runtime


data_catalog_runtime.configure(DEFAULT_DATA_CONSOLE_PORT)
risk_console_runtime.configure(DEFAULT_RISK_CONSOLE_PORT)
app.include_router(hourly_alpha_router)
app.include_router(mft_execution_router)
app.include_router(market_dashboard_router)
app.include_router(forward_dashboard_router)


__all__ = ["app"]

from services.console.api.ai_factory import router as ai_factory_router
app.include_router(ai_factory_router)
