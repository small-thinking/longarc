"""Data providers for LongArc."""

from longarc.data.providers.base import DataProvider, DownloadResult
from longarc.data.providers.local_parquet import LocalParquetProvider
from longarc.data.providers.polygon import PolygonProvider
from longarc.data.providers.registry import get_provider

__all__ = [
    "DataProvider",
    "DownloadResult",
    "LocalParquetProvider",
    "PolygonProvider",
    "get_provider",
]
