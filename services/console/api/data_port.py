"""Fail-closed data-catalog port configured by the console composition root."""

from __future__ import annotations

from typing import Dict, List, Protocol


class DataCatalogPort(Protocol):
    data_backend: str

    def enumerate_real_datasets(self) -> List[Dict]: ...

    def dataset_content_hash(self, dataset_id: str, version: str) -> str: ...

    def verify_point_in_time_safe(self, dataset_id: str) -> bool: ...


class DataCatalogUnavailable(RuntimeError):
    pass


class DataCatalogRuntime:
    def __init__(self) -> None:
        self._port: DataCatalogPort | None = None

    def configure(self, port: DataCatalogPort) -> None:
        if self._port is None:
            self._port = port
            return
        if self._port is not port:
            raise RuntimeError("data catalog runtime is already configured")

    def require(self) -> DataCatalogPort:
        if self._port is None:
            raise DataCatalogUnavailable("data catalog runtime must be configured by services.console.main")
        return self._port


data_catalog_runtime = DataCatalogRuntime()
