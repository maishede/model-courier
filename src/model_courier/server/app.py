"""FastAPI application factory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import FastAPI

from .auth import TokenStore
from .config import ServerConfig
from .db import Database
from .quotas import QuotaManager
from .repository import TaskRepository
from .storage import ArtifactStore


@dataclass
class AppState:
    config: ServerConfig
    database: Database
    repository: TaskRepository
    storage: ArtifactStore
    tokens: TokenStore
    quota: QuotaManager


def create_app(config: ServerConfig | None = None) -> FastAPI:
    config = config or ServerConfig.from_root(Path("data"))
    database = Database.open(config.database_path)
    database.initialize()
    storage = ArtifactStore(config.artifact_root)
    state = AppState(
        config=config,
        database=database,
        repository=TaskRepository(database),
        storage=storage,
        tokens=TokenStore(database),
        quota=QuotaManager(database, config.artifact_root),
    )
    app = FastAPI(title="ModelCourier", version="0.1.0")
    app.state.model_courier = state

    from .routes_devices import router as device_router
    from .routes_workers import router as worker_router

    app.include_router(device_router, prefix="/v1")
    app.include_router(worker_router, prefix="/v1")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
