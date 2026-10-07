# Збірка образу сервера: Python -> встановлення пакета -> запуск Uvicorn.
FROM python:3.12-slim

# Налаштування Python/pip; CACHE_DATA_DIR передає шлях у config.py.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    CACHE_DATA_DIR=/app/data

# Робоча папка й потрібні для встановлення файли всередині контейнера.
WORKDIR /app
COPY pyproject.toml README.md requirements.lock ./
COPY src ./src
COPY examples ./examples

# На етапі збірки встановлюємо пакет і даємо користувачу app доступ до data.
RUN pip install --constraint requirements.lock . \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /app/data \
    && chown app:app /app/data

# Запускаємо під користувачем app; EXPOSE лише документує порт.
USER app
EXPOSE 8000
# Docker періодично звертається до /health із api.py.
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# Ця команда виконується при запуску контейнера.
CMD ["uvicorn", "caching_service.api:app", "--host", "0.0.0.0", "--port", "8000"]
