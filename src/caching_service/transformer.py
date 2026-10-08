# Simulated external transformer.
# Change this version if the transformation logic changes.
TRANSFORMER_VERSION = "uppercase-v1"


# Transform one string.
def transform(value: str) -> str:
    """Stand-in for a deterministic external service."""
    # Example: "hello" becomes "HELLO".
    return value.upper()
