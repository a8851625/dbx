from asyncio import Event, Task, create_task
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes.approval import run_due_ticket_scheduler, stop_due_ticket_scheduler
from app.api.router import api_router
from app.api.routes.web_runtime import router as web_runtime_router
from app.config import get_settings
from app.db.session import SessionLocal
from app.middleware.audit_context import AuditContextMiddleware
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
app.add_middleware(AuditContextMiddleware, auth_service_factory=lambda: AuthService(settings))
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:1420", "http://127.0.0.1:1420", "http://localhost:8000", "http://localhost:4224"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(web_runtime_router, prefix="/api")
app.include_router(api_router, prefix=settings.api_v1_prefix)

static_dir = Path(settings.app_static_dir)
if static_dir.exists():
    assets_dir = static_dir / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")


@app.get("/health", tags=["health"])
def root_healthcheck() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def spa_index() -> Response:
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"detail": "Static frontend is not built"}, status_code=503)


@app.get("/{frontend_path:path}", include_in_schema=False)
def spa_fallback(frontend_path: str) -> Response:
    if frontend_path.startswith("api/") or frontend_path == "health":
        return JSONResponse({"detail": "Not Found"}, status_code=404)
    candidate = static_dir / frontend_path
    if candidate.is_file():
        return FileResponse(candidate)
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse({"detail": "Static frontend is not built"}, status_code=503)
