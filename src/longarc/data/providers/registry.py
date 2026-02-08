"""Registry for data provider adapters."""

from __future__ import annotations

from longarc.data.providers.base import DataProvider
from longarc.data.providers.local_parquet import LocalParquetProvider
from longarc.data.providers.polygon import PolygonProvider


def get_provider(name: str, *, api_key: str | None = None) -> DataProvider:
    normalized = name.strip().lower()
    if normalized == "local_parquet":
        return LocalParquetProvider()
    if normalized == "polygon":
        if not api_key:
            raise ValueError("Provider 'polygon' requires --api-key or POLYGON_API_KEY.")
        return PolygonProvider(api_key=api_key)

    supported = "local_parquet, polygon"
    raise ValueError(f"Unsupported provider {name!r}. Expected one of: {supported}")
