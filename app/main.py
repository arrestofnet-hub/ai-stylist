from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.routes.admin import router as admin_router
from app.routes.generations import router as generations_router
from app.routes.profiles import router as profiles_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.4.0",
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
        "version": "0.4.0",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
