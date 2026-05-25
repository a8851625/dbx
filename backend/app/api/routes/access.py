from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from app.api.deps import get_authorization_service, get_current_user, require_permission
from app.db.session import get_db
from app.models.auth import UserIdentity
from app.schemas.access import (
    AccessCheckRequest,
    AccessCheckResponse,
    AccessContextResponse,
    ResourcePolicyDetailResponse,
    ResourcePolicyResponse,
    ResourcePolicyUpsertRequest,
    RoleBindingResponse,
    RoleBindingUpsertRequest,
)
from app.schemas.auth import CurrentUserResponse
from app.services.audit import AuditService
from app.services.authorization import AuthorizationService

router = APIRouter(prefix="/access", tags=["access"])


def get_audit_service() -> AuditService:
    return AuditService()


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


@router.get("/admin/users", response_model=list[CurrentUserResponse])
def list_users(
    _: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[CurrentUserResponse]:
    users = authorization_service.list_users(db)
    return [CurrentUserResponse.model_validate(user) for user in users]


@router.get("/admin/role-bindings", response_model=list[RoleBindingResponse])
def list_role_bindings(
    user_id: str | None = Query(default=None),
    _: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[RoleBindingResponse]:
    bindings = authorization_service.list_role_bindings(db, user_id=user_id)
    return [RoleBindingResponse.model_validate(binding) for binding in bindings]


@router.post("/admin/role-bindings", response_model=RoleBindingResponse)
def upsert_role_binding(
    request: Request,
    payload: RoleBindingUpsertRequest,
    current_user: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> RoleBindingResponse:
    binding, mutation = authorization_service.upsert_role_binding(
        db,
        user_id=payload.user_id,
        role_code=payload.role_code,
        granted_by=current_user.id,
        expires_at=payload.expires_at,
    )
    audit_service.record_event(
        db,
        event_type=f"access.role_binding.{mutation}",
        category="access",
        action=mutation,
        actor=audit_service.build_actor(user=current_user, request=request),
        resource_type="role_binding",
        resource_id=binding.id,
        resource_name=f"{binding.user_id}:{binding.role_code}",
        payload={
            "user_id": binding.user_id,
            "role_code": binding.role_code,
            "expires_at": binding.expires_at.isoformat() if binding.expires_at else None,
            "granted_by": binding.granted_by,
        },
        commit=True,
    )
    return RoleBindingResponse.model_validate(binding)


@router.delete("/admin/role-bindings/{binding_id}", response_model=RoleBindingResponse)
def delete_role_binding(
    binding_id: str,
    request: Request,
    current_user: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> RoleBindingResponse:
    binding = authorization_service.delete_role_binding(db, binding_id)
    audit_service.record_event(
        db,
        event_type="access.role_binding.revoked",
        category="access",
        action="revoke",
        actor=audit_service.build_actor(user=current_user, request=request),
        resource_type="role_binding",
        resource_id=binding.id,
        resource_name=f"{binding.user_id}:{binding.role_code}",
        payload={
            "user_id": binding.user_id,
            "role_code": binding.role_code,
            "expires_at": binding.expires_at.isoformat() if binding.expires_at else None,
            "granted_by": binding.granted_by,
        },
        commit=True,
    )
    return RoleBindingResponse.model_validate(binding)


@router.get("/admin/resource-policies", response_model=list[ResourcePolicyDetailResponse])
def list_resource_policies(
    principal_type: str | None = Query(default=None),
    principal_ref: str | None = Query(default=None),
    _: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
) -> list[ResourcePolicyDetailResponse]:
    policies = authorization_service.list_resource_policies(
        db,
        principal_type=principal_type,
        principal_ref=principal_ref,
    )
    return [ResourcePolicyDetailResponse.model_validate(policy) for policy in policies]


@router.post("/admin/resource-policies", response_model=ResourcePolicyDetailResponse)
def create_resource_policy(
    request: Request,
    payload: ResourcePolicyUpsertRequest,
    current_user: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> ResourcePolicyDetailResponse:
    policy = authorization_service.create_resource_policy(
        db,
        principal_type=payload.principal_type,
        principal_ref=payload.principal_ref,
        resource_type=payload.resource_type,
        resource_key=payload.resource_key,
        permission_code=payload.permission_code,
        effect=payload.effect,
        conditions=payload.conditions,
        enabled=payload.enabled,
    )
    audit_service.record_event(
        db,
        event_type="access.resource_policy.created",
        category="access",
        action="create",
        actor=audit_service.build_actor(user=current_user, request=request),
        resource_type="resource_policy",
        resource_id=policy.id,
        resource_name=f"{policy.principal_type}:{policy.principal_ref}",
        payload={
            "principal_type": policy.principal_type,
            "principal_ref": policy.principal_ref,
            "resource_type": policy.resource_type,
            "resource_key": policy.resource_key,
            "permission_code": policy.permission_code,
            "effect": policy.effect,
            "conditions": policy.conditions,
            "enabled": policy.enabled,
        },
        commit=True,
    )
    return ResourcePolicyDetailResponse.model_validate(policy)


@router.put("/admin/resource-policies/{policy_id}", response_model=ResourcePolicyDetailResponse)
def update_resource_policy(
    policy_id: str,
    request: Request,
    payload: ResourcePolicyUpsertRequest,
    current_user: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> ResourcePolicyDetailResponse:
    policy = authorization_service.update_resource_policy(
        db,
        policy_id,
        principal_type=payload.principal_type,
        principal_ref=payload.principal_ref,
        resource_type=payload.resource_type,
        resource_key=payload.resource_key,
        permission_code=payload.permission_code,
        effect=payload.effect,
        conditions=payload.conditions,
        enabled=payload.enabled,
    )
    audit_service.record_event(
        db,
        event_type="access.resource_policy.updated",
        category="access",
        action="update",
        actor=audit_service.build_actor(user=current_user, request=request),
        resource_type="resource_policy",
        resource_id=policy.id,
        resource_name=f"{policy.principal_type}:{policy.principal_ref}",
        payload={
            "principal_type": policy.principal_type,
            "principal_ref": policy.principal_ref,
            "resource_type": policy.resource_type,
            "resource_key": policy.resource_key,
            "permission_code": policy.permission_code,
            "effect": policy.effect,
            "conditions": policy.conditions,
            "enabled": policy.enabled,
        },
        commit=True,
    )
    return ResourcePolicyDetailResponse.model_validate(policy)


@router.delete("/admin/resource-policies/{policy_id}", response_model=ResourcePolicyDetailResponse)
def delete_resource_policy(
    policy_id: str,
    request: Request,
    current_user: UserIdentity = Depends(require_permission("authorization.manage")),
    db: Session = Depends(get_db),
    authorization_service: AuthorizationService = Depends(get_authorization_service),
    audit_service: AuditService = Depends(get_audit_service),
) -> ResourcePolicyDetailResponse:
    policy = authorization_service.delete_resource_policy(db, policy_id)
    audit_service.record_event(
        db,
        event_type="access.resource_policy.deleted",
        category="access",
        action="delete",
        actor=audit_service.build_actor(user=current_user, request=request),
        resource_type="resource_policy",
        resource_id=policy.id,
        resource_name=f"{policy.principal_type}:{policy.principal_ref}",
        payload={
            "principal_type": policy.principal_type,
            "principal_ref": policy.principal_ref,
            "resource_type": policy.resource_type,
            "resource_key": policy.resource_key,
            "permission_code": policy.permission_code,
            "effect": policy.effect,
            "conditions": policy.conditions,
            "enabled": policy.enabled,
        },
        commit=True,
    )
    return ResourcePolicyDetailResponse.model_validate(policy)
