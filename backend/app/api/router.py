from fastapi import APIRouter

from app.api.routes.audit import router as audit_router
from app.api.routes.approval import router as approval_router
from app.api.routes.access import router as access_router
from app.api.routes.auth import router as auth_router
from app.api.routes.health import router as health_router

api_router = APIRouter()
api_router.include_router(auth_router)
api_router.include_router(access_router)
api_router.include_router(approval_router)
api_router.include_router(audit_router)
api_router.include_router(health_router)
