# HTTP-вхід сервера: створення застосунку, POST /payload, GET /payload/{id}, GET /health.
# POST: schemas.py перевіряє дані -> create_payload() -> service.py виконує обробку.
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import FastAPI, HTTPException, Path, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from caching_service.config import Settings
from caching_service.database import create_database
from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput
from caching_service.service import PayloadService
from caching_service.storage import PayloadStorageError, PayloadStore
from caching_service.transformer import transform

logger = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None, transformer: Callable[[str], str] = transform
) -> FastAPI:
    # Можна передати налаштування і transformer з тесту; інакше беремо стандартні.
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Під час запуску: database.py створює підключення і таблиці з models.py.
        engine = create_database(settings.database_path)
        try:
            # Один сервіс для застосунку; кожен його запит відкриє власну сесію бази.
            app.state.service = PayloadService(
                engine, PayloadStore(settings.payload_dir), transformer
            )
            yield  # До цього рядка — підготовка; після нього — завершення сервера.
        finally:
            engine.dispose()

    app = FastAPI(title="Caching Service", version="0.1.0", lifespan=lifespan)

    async def storage_error(request: Request, exc: Exception) -> JSONResponse:
        # Причина лишається в журналі, а клієнт отримує HTTP 503.
        logger.error("Storage operation failed", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse(status_code=503, content={"detail": "Storage temporarily unavailable"})

    app.add_exception_handler(SQLAlchemyError, storage_error)
    app.add_exception_handler(PayloadStorageError, storage_error)

    @app.post(
        "/payload",
        response_model=PayloadCreated,
        status_code=status.HTTP_201_CREATED,
        responses={200: {"model": PayloadCreated, "description": "An existing payload was reused"}},
    )
    def create_payload(body: PayloadInput, request: Request, response: Response) -> PayloadCreated:
        # [POST 2] body вже перевірено через PayloadInput із schemas.py.
        # Передаємо списки в PayloadService.create() із service.py.
        result = request.app.state.service.create(body)
        # [POST 7] Готовий ID повертаємо клієнту: новий результат — 201, існуючий — 200.
        if result.cached:
            response.status_code = status.HTTP_200_OK
        response.headers["Location"] = f"/payload/{result.id}"
        return result

    @app.get("/payload/{payload_id}", response_model=PayloadOutput)
    def read_payload(
        payload_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], request: Request
    ) -> PayloadOutput:
        # [GET 1] FastAPI перевіряє формат ID; далі йдемо в service.py -> read().
        result = request.app.state.service.read(payload_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Payload not found")
        return result

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        # Проста перевірка доступу до бази; не створює payload і не викликає transformer.
        with request.app.state.service.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


# Uvicorn бере саме цей об'єкт: uvicorn caching_service.api:app.
app = create_app()
