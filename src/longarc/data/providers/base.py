"""Provider contracts shared by market data adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class DownloadResult:
    symbol: str
    timeframe: str
    input_rows: int
    total_rows: int


class DataProvider(Protocol):
    """Minimal interface for pluggable data download providers."""

    def download_symbol(
        self,
        base_path: str | Path,
        symbol: str,
        timeframe: str,
        start: str,
        end: str,
    ) -> DownloadResult: ...
