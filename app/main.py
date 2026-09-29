from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.config import settings
from app.db import connection, init_db
from app.routes.admin import router as admin_router
from app.routes.generations import router as generations_router
from app.routes.profiles import router as profiles_router
from app.services.image_service import recover_stale_generations


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    recover_stale_generations()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.5.0",
    description="AI Stylist backend and virtual try-on API",
    lifespan=lifespan,
)

app.include_router(profiles_router, prefix="/api/v1")
app.include_router(generations_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "ok",
        "version": "0.5.0",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}


@app.get("/ready")
def readiness():
    database_ready = False
    try:
        with connection() as conn:
            conn.execute("SELECT 1").fetchone()
        database_ready = True
    except Exception:
        database_ready = False

    provider_ready = bool(settings.openai_api_key)
    ready = database_ready and provider_ready

    payload = {
        "status": "ready" if ready else "not_ready",
        "database": "ready" if database_ready else "unavailable",
        "image_provider": "configured" if provider_ready else "not_configured",
    }

    if ready:
        return payload

    return JSONResponse(status_code=503, content=payload)
