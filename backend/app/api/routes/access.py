from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_service, get_current_user
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.schemas.access import AccessCheckRequest, AccessCheckResponse, AccessContextResponse, ResourcePolicyResponse
from app.schemas.auth import CurrentUserResponse
from app.services.authorization import AuthorizationService

router = APIRouter(prefix="/access", tags=["access"])


@router.get("/me", response_model=AccessContextResponse)
def my_access_context(
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AccessContextResponse:
    context = authorization_service.build_access_context(db, current_user)
    return AccessContextResponse(
        user=CurrentUserResponse.model_validate(current_user),
        roles=list(context.roles),
        permissions=sorted(context.permissions),
        policies=[
            ResourcePolicyResponse(
                principal_type=policy.principal_type,
                principal_ref=policy.principal_ref,
                resource_type=policy.resource_type,
                resource_key=policy.resource_key,
                permission_code=policy.permission_code,
                effect=policy.effect,
                conditions=policy.conditions or {},
            )
            for policy in context.policies
        ],
    )


@router.post("/check", response_model=AccessCheckResponse)
def check_access(
    payload: AccessCheckRequest,
    current_user: UserIdentity = Depends(get_current_user),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> AccessCheckResponse:
    context = authorization_service.build_access_context(db, current_user)
    decision = authorization_service.check_permission(
        context,
        payload.permission,
        datasource_id=payload.datasource_id,
        database=payload.database,
        schema=payload.schema,
        table=payload.table,
    )
    return AccessCheckResponse(allowed=decision.allowed, reason=decision.reason)
