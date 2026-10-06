# Change the version whenever transformation behavior changes to invalidate old cache entries.
TRANSFORMER_VERSION = "uppercase-v1"


def transform(value: str) -> str:
    """Stand-in for a deterministic external service."""
    return value.upper()
