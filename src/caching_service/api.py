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
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_database(settings.database_path)
        try:
            app.state.service = PayloadService(
                engine, PayloadStore(settings.payload_dir), transformer
            )
            yield
        finally:
            engine.dispose()

    app = FastAPI(title="Caching Service", version="0.1.0", lifespan=lifespan)

    async def storage_error(request: Request, exc: Exception) -> JSONResponse:
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
        result = request.app.state.service.create(body)
        if result.cached:
            response.status_code = status.HTTP_200_OK
        response.headers["Location"] = f"/payload/{result.id}"
        return result

    @app.get("/payload/{payload_id}", response_model=PayloadOutput)
    def read_payload(
        payload_id: Annotated[str, Path(pattern=r"^[0-9a-f]{64}$")], request: Request
    ) -> PayloadOutput:
        result = request.app.state.service.read(payload_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Payload not found")
        return result

    @app.get("/health")
    def health(request: Request) -> dict[str, str]:
        with request.app.state.service.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}

    return app


app = create_app()
