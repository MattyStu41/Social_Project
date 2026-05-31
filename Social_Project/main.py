from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from config import settings
from database import init_schema
from logging_config import configure_logging
from routers import admin, analytics, auth, drafts, platforms, posts, repurpose
from routers import settings as settings_router
from scheduler import (
    reconcile_processing_posts,
    shutdown_scheduler,
    start_scheduler,
)

configure_logging()
log = logging.getLogger("jack")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_schema()
    # F-30: any post left in `processing` from a crashed previous run gets
    # reconciled before the scheduler starts so we never lose a post.
    await reconcile_processing_posts()
    if not settings.disable_scheduler:
        start_scheduler()
    log.info("JACK Social Scheduler started (base_url=%s)", settings.base_url)
    try:
        yield
    finally:
        if not settings.disable_scheduler:
            shutdown_scheduler()
        log.info("JACK Social Scheduler stopped")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

if settings.cors_origin_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict:
    return {"status": "ok", "version": settings.app_version, "auth": settings.auth_enabled}


app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(platforms.router, prefix="/api/platforms", tags=["platforms"])
app.include_router(posts.router, prefix="/api/posts", tags=["posts"])
app.include_router(drafts.router, prefix="/api/drafts", tags=["drafts"])
app.include_router(repurpose.router, prefix="/api/repurpose", tags=["repurpose"])
app.include_router(settings_router.router, prefix="/api/settings", tags=["settings"])
app.include_router(analytics.router, prefix="/api/analytics", tags=["analytics"])

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def _page(name: str):
    """Serve a single HTML page from /static/<name>.html under a pretty URL."""
    from fastapi.responses import FileResponse

    path = os.path.join(_STATIC_DIR, f"{name}.html")

    async def _route():
        return FileResponse(path)

    return _route


# Pretty URLs for the operator-facing pages. Each one is just a static
# .html file served from /static; the SPA logic lives in its own .js file.
for _page_name in ("studio", "repurpose", "calendar", "analytics", "settings"):
    app.add_api_route(f"/{_page_name}", _page(_page_name), include_in_schema=False)


if os.path.isdir(_STATIC_DIR):
    app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=bool(os.getenv("DEV_RELOAD")),
    )
