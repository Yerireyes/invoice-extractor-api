"""Límites de uso: peticiones por IP (ventana deslizante) y cuota diaria global de documentos.

Ambos viven en memoria del proceso: sirven para una sola instancia (p. ej. Render gratuito).
Con varias réplicas habría que usar un almacenamiento compartido (`limits` soporta Redis).
"""

import math
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from limits import parse, storage, strategies

from app.core.errors import AppError, ErrorCode

GOOGLE_QUOTA_TIMEZONE = ZoneInfo("America/Los_Angeles")
"""Las cuotas diarias de Gemini se reinician a medianoche, hora del Pacífico."""


class IpRateLimiter:
    """Límite por clave (IP) con ventana deslizante, p. ej. `"10/minute"`."""

    def __init__(self, limit: str, *, clock: Callable[[], float] = time.time) -> None:
        self._item = parse(limit)
        self._strategy = strategies.MovingWindowRateLimiter(storage.MemoryStorage())
        self._clock = clock

    def hit(self, key: str) -> None:
        """Registra una petición; lanza `RATE_LIMITED` (429) si se supera el límite."""
        if self._strategy.hit(self._item, key):
            return
        reset_time, _ = self._strategy.get_window_stats(self._item, key)
        retry_after = max(1, math.ceil(reset_time - self._clock()))
        raise AppError(
            ErrorCode.RATE_LIMITED,
            params={"retry_after_s": retry_after},
            headers={"Retry-After": str(retry_after)},
        )


class DailyQuota:
    """Cuota global de documentos por día (se reinicia a medianoche de `timezone`).

    `limit=0` desactiva la cuota.
    """

    def __init__(
        self,
        limit: int,
        *,
        timezone: ZoneInfo = GOOGLE_QUOTA_TIMEZONE,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.limit = limit
        self._timezone = timezone
        self._now = now
        self._day = self._today()
        self._used = 0

    @property
    def used(self) -> int:
        self._roll_over()
        return self._used

    def consume(self, documents: int) -> None:
        """Reserva `documents`; si no caben todos, no reserva ninguno y lanza 503."""
        if not self.limit:
            return
        self._roll_over()
        if self._used + documents > self.limit:
            retry_after = self._seconds_until_reset()
            raise AppError(
                ErrorCode.LLM_QUOTA_EXCEEDED,
                params={"retry_after_s": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        self._used += documents

    def _today(self) -> datetime:
        local = self._now().astimezone(self._timezone)
        return local.replace(hour=0, minute=0, second=0, microsecond=0)

    def _roll_over(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._used = today, 0

    def _seconds_until_reset(self) -> int:
        next_midnight = (self._day + timedelta(days=1)).replace(tzinfo=None)
        reset = next_midnight.replace(tzinfo=self._timezone)  # respeta cambios de horario
        return max(1, math.ceil((reset - self._now()).total_seconds()))
