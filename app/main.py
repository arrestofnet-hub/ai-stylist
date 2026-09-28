from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.db import init_db
from app.routes.profiles import router as profiles_router


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    description="AI Stylist backend",
    lifespan=lifespan,
)

app.include_router(profiles_router, prefix="/api/v1")


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": settings.app_name,
        "status": "ok",
        "version": "0.2.0",
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "healthy"}
