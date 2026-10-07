# Основна логіка: спершу готовий кеш без writer lock, потім запис за потреби.
# api.py викликає create()/read(); SQLite-транзакціями керує database.py.
import hashlib
from collections.abc import Callable
from itertools import chain

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from caching_service.database import write_session
from caching_service.models import Payload, Transformation
from caching_service.schemas import PayloadCreated, PayloadInput, PayloadOutput
from caching_service.storage import PayloadStorageError, PayloadStore
from caching_service.transformer import TRANSFORMER_VERSION, transform


class PayloadService:
    def __init__(
        self,
        engine: Engine,
        store: PayloadStore,
        transformer: Callable[[str], str] = transform,
        transformer_version: str = TRANSFORMER_VERSION,
    ) -> None:
        # Запам'ятовуємо залежності; обробка запускається пізніше в create().
        self.engine = engine
        self.store = store
        self.transformer = transformer
        self.transformer_version = transformer_version

    def create(self, request: PayloadInput) -> PayloadCreated:
        # request тут — перевірені списки PayloadInput, а не HTTP Request із api.py.
        sources = list(dict.fromkeys(chain(request.list_1, request.list_2)))
        # [POST 3] Готовий payload читаємо без writer lock, навіть якщо інший POST повільний.
        cached_payload = self._cached_payload(request, sources)
        if cached_payload is not None:
            return cached_payload

        # Нові перетворення/ID або ремонт файлу потребують транзакції запису.
        with write_session(self.engine) as session:
            # ПОВТОРНА перевірка під lock: інший запит міг заповнити кеш за час очікування.
            results = self._transform_strings(session, sources)
            # [POST 5–6] Формуємо текст і ID з готових перетворень.
            output, payload_id = self._build_payload(request, results)
            cached = session.get(Payload, payload_id) is not None
            if not cached:
                self.store.write(payload_id, output)
                session.add(Payload(id=payload_id))
            else:
                try:
                    self.store.read(payload_id)
                except PayloadStorageError:
                    # Зниклий/пошкоджений файл відновлюємо з уже сформованого output.
                    self.store.write(payload_id, output)
            response = PayloadCreated(id=payload_id, cached=cached)
        # write_session підтвердив commit; повертаємо модель в api.py.
        return response

    def read(self, payload_id: str) -> PayloadOutput | None:
        # [GET 2] Невідомий ID -> None, який api.py перетворить на 404.
        with Session(self.engine) as session:
            if session.get(Payload, payload_id) is None:
                return None
        # [GET 3] storage.py читає файл; transformer для GET не потрібний.
        return self.store.read(payload_id)

    def _cached_payload(self, request: PayloadInput, sources: list[str]) -> PayloadCreated | None:
        # Швидкий шлях лише для повного кешу: всі слова, запис ID і справний JSON.
        with Session(self.engine) as session:
            results = self._load_cached_strings(session, sources)
            if len(results) != len(sources):
                return None
            output, payload_id = self._build_payload(request, results)
            if session.get(Payload, payload_id) is None:
                return None
            try:
                self.store.read(payload_id)
            except PayloadStorageError:
                # Ремонт піде звичайним шляхом запису, а не всередині читання.
                return None
            return PayloadCreated(id=payload_id, cached=True)

    def _load_cached_strings(self, session: Session, sources: list[str]) -> dict[str, str]:
        # Читаємо «оригінал -> результат» для поточної версії; нічого не змінюємо.
        results: dict[str, str] = {}
        # Порції по 500 обмежують кількість параметрів одного SQL-запиту.
        for offset in range(0, len(sources), 500):
            rows = session.scalars(
                select(Transformation).where(
                    Transformation.version == self.transformer_version,
                    Transformation.source.in_(sources[offset : offset + 500]),
                )
            )
            results.update((row.source, row.result) for row in rows)
        return results

    def _transform_strings(self, session: Session, sources: list[str]) -> dict[str, str]:
        # Цей метод викликається під writer lock і спершу заново читає кеш.
        results = self._load_cached_strings(session, sources)
        for source in sources:
            if source not in results:
                # [POST 4] Лише відсутній рядок передається transform() із transformer.py.
                result = self.transformer(source)
                results[source] = result
                # Запис стане постійним після успішного commit у write_session().
                session.add(
                    Transformation(version=self.transformer_version, source=source, result=result)
                )
        return results

    @staticmethod
    def _build_payload(request: PayloadInput, results: dict[str, str]) -> tuple[str, str]:
        # [POST 5] zip дає пари; беремо обидва елементи кожної пари по черзі.
        output = ", ".join(
            results[value]
            for pair in zip(request.list_1, request.list_2, strict=True)
            for value in pair
        )
        # [POST 6] Однаковий кінцевий текст завжди дає той самий ID.
        payload_id = hashlib.sha256(output.encode("utf-8")).hexdigest()
        return output, payload_id
