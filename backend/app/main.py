import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import catalogue as catalogue_api
from app.api import discovery as discovery_api
from app.core.config import Settings, get_settings
from app.services.catalogue import load_catalogue

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Fail fast: the app does not start with an invalid or missing catalogue.
        catalogue = load_catalogue(
            settings.catalogue_path,
            settings.catalogue_products_sheet,
            settings.catalogue_competitors_sheet,
        )
        for dup in catalogue.duplicate_competitors:
            logger.warning("Dropped duplicate competitor %s (%s)", dup.dropped_name, dup.domain)
        logger.info(
            "Loaded %d products and %d competitors from %s",
            len(catalogue.products), len(catalogue.competitors), catalogue.source_path.name,
        )  # fmt: skip
        app.state.catalogue = catalogue
        app.state.settings = settings
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(catalogue_api.router)
    app.include_router(discovery_api.router)

    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        c = app.state.catalogue
        return {"status": "ok", "products": len(c.products), "competitors": len(c.competitors)}

    return app


app = create_app()
