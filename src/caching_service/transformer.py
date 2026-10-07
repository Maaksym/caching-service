# Імітація зовнішньої обробки одного рядка; кешем керує service.py.
# Якщо змінюємо алгоритм, змінюємо версію, щоб не використовувати старі перетворення.
TRANSFORMER_VERSION = "uppercase-v1"


def transform(value: str) -> str:
    """Stand-in for a deterministic external service."""
    # [POST 4] Наприклад "hello" -> "HELLO"; повертаємо текст назад у service.py.
    return value.upper()
