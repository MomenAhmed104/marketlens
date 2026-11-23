import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import catalogue as catalogue_api
from app.api import discovery as discovery_api
from app.api import images as images_api
from app.api import intelligence as intelligence_api
from app.api import matching as matching_api
from app.api import review as review_api
from app.api import runs as runs_api
from app.core.config import Settings, get_settings
from app.services.catalogue import load_catalogue
from app.services.images.catalogue import apply_catalogue_images, load_catalogue_images
from app.orchestration.pipeline import PipelineDeps, RunManager
from app.orchestration.store import InMemoryRunStore
from app.services.review_store import ReviewStore
from app.stage9.context import budget_mode
from app.stage9.manager import BudgetedRunManager

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
        with_images = apply_catalogue_images(catalogue, load_catalogue_images(settings.catalogue_images_path))
        logger.info("Catalogue images: %d of %d products", with_images, len(catalogue.products))
        app.state.catalogue = catalogue
        app.state.settings = settings
        app.state.review_store = ReviewStore(settings.review_db_path, settings.discovery_excluded_competitor_domains)
        # Phase 7: temporary, in-memory run state (cleared on restart).
        # Stage 9 budget mode (STAGE9_LEDGER_PATH set): every run reserves budget first and is metered.
        manager_cls = BudgetedRunManager if budget_mode(settings) else RunManager
        app.state.run_manager = manager_cls(
            InMemoryRunStore(settings.pipeline_max_runs_retained, settings.pipeline_run_ttl_minutes),
            PipelineDeps.default(settings, catalogue, app.state.review_store),
        )
        if budget_mode(settings):
            logger.warning("Stage 9 budget mode: ledger %s; paid calls only inside budgeted runs",
                           settings.stage9_ledger_path)  # fmt: skip
        yield
        await app.state.run_manager.shutdown()  # cancel active runs on shutdown

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(catalogue_api.router)
    app.include_router(discovery_api.router)
    app.include_router(review_api.router)
    app.include_router(intelligence_api.router)
    app.include_router(matching_api.router)
    app.include_router(runs_api.router)
    app.include_router(images_api.router)

    @app.get("/api/health", tags=["health"])
    def health() -> dict:
        c = app.state.catalogue
        return {"status": "ok", "products": len(c.products), "competitors": len(c.competitors)}

    return app


app = create_app()
