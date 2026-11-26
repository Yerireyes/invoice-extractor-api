from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


@pytest.fixture
def anyio_backend() -> str:
    """Los tests `@pytest.mark.anyio` corren solo con asyncio (el runtime de Uvicorn)."""
    return "asyncio"


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
