from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def root_healthcheck() -> dict[str, str]:
    return {"status": "ok"}
