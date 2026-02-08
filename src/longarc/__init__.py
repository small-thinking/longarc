"""LongArc package."""

__all__ = ["__version__", "package_name"]

__version__ = "0.1.0"


def package_name() -> str:
    """Return package identifier for smoke verification."""
    return "longarc"
