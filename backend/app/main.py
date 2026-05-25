from asyncio import Event, Task, create_task
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes.approval import run_due_ticket_scheduler, stop_due_ticket_scheduler
from app.api.router import api_router
from app.config import get_settings
from app.db.session import SessionLocal
from app.services.approval import ApprovalService
from app.services.auth import AuthService
from app.services.authorization import AuthorizationService

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    db = SessionLocal()
    scheduler_stop = Event()
    scheduler_task: Task[None] | None = None
    try:
        AuthService(settings).sync_default_provider(db)
        AuthorizationService().sync_builtin_authorization(db)
        ApprovalService().sync_builtin_flows(db)
        scheduler_task = create_task(run_due_ticket_scheduler(scheduler_stop))
    finally:
        db.close()
    try:
        yield
    finally:
        if scheduler_task is not None:
            await stop_due_ticket_scheduler(scheduler_stop, scheduler_task)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420", "http://localhost:8000", "http://localhost:4224"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix=settings.api_v1_prefix)


@app.get("/health", tags=["health"])
def root_healthcheck() -> dict[str, str]:
    return {"status": "ok"}
