from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.auth import UserIdentity
from app.models.rbac import Permission, ResourcePolicy, Role, RolePermission, UserRoleBinding

BUILTIN_PERMISSIONS: dict[str, dict[str, str]] = {
    "menu.connections.view": {
        "resource_type": "menu",
        "action": "view",
        "description": "View the connection sidebar and related entry points.",
    },
    "datasource.manage": {
        "resource_type": "datasource",
        "action": "manage",
        "description": "Create, edit, import and test datasource configurations.",
    },
    "datasource.connect": {
        "resource_type": "datasource",
        "action": "connect",
        "description": "Open interactive sessions to configured datasources.",
    },
    "datasource.browse": {
        "resource_type": "datasource",
        "action": "browse",
        "description": "Browse database, schema, table and object metadata.",
    },
    "query.execute": {
        "resource_type": "query",
        "action": "execute",
        "description": "Execute SQL against authorized datasources.",
    },
    "transfer.execute": {
        "resource_type": "transfer",
        "action": "execute",
        "description": "Run cross-datasource transfer jobs.",
    },
    "sql_file.execute": {
        "resource_type": "sql_file",
        "action": "execute",
        "description": "Execute uploaded SQL file tasks.",
    },
    "schema.diff": {
        "resource_type": "schema_diff",
        "action": "execute",
        "description": "Run schema diff and sync planning operations.",
    },
    "data.compare": {
        "resource_type": "data_compare",
        "action": "execute",
        "description": "Run data comparison and reconciliation preparation flows.",
    },
    "export.database": {
        "resource_type": "export",
        "action": "database",
        "description": "Export database structures or datasets.",
    },
    "export.query": {
        "resource_type": "export",
        "action": "query",
        "description": "Export query results to files.",
    },
    "history.view": {
        "resource_type": "history",
        "action": "view",
        "description": "View query and operation history.",
    },
    "ai.use": {
        "resource_type": "ai",
        "action": "use",
        "description": "Use AI completion and assistant features.",
    },
    "drivers.manage": {
        "resource_type": "driver",
        "action": "manage",
        "description": "Manage driver packages and runtime assets.",
    },
    "settings.manage": {
        "resource_type": "settings",
        "action": "manage",
        "description": "Open and update local application settings.",
    },
    "authorization.manage": {
        "resource_type": "authorization",
        "action": "manage",
        "description": "Manage RBAC role bindings and datasource resource policies.",
    },
    "audit.event.view": {
        "resource_type": "audit",
        "action": "view",
        "description": "View structured audit events and query audit trails.",
    },
    "approval.ticket.view": {
        "resource_type": "approval_ticket",
        "action": "view",
        "description": "View submitted approval tickets and approval progress.",
    },
    "approval.ticket.create": {
        "resource_type": "approval_ticket",
        "action": "create",
        "description": "Create DDL or DML change tickets.",
    },
    "approval.ticket.submit": {
        "resource_type": "approval_ticket",
        "action": "submit",
        "description": "Submit draft change tickets into an approval flow.",
    },
    "approval.ticket.approve": {
        "resource_type": "approval_ticket",
        "action": "approve",
        "description": "Approve or reject pending approval steps.",
    },
    "approval.ticket.execute": {
        "resource_type": "approval_ticket",
        "action": "execute",
        "description": "Trigger or retry approved change ticket execution jobs.",
    },
}

BUILTIN_ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": set(BUILTIN_PERMISSIONS),
    "developer": {
        "menu.connections.view",
        "datasource.connect",
        "datasource.browse",
        "query.execute",
        "transfer.execute",
        "sql_file.execute",
        "schema.diff",
        "data.compare",
        "export.database",
        "export.query",
        "history.view",
        "ai.use",
        "settings.manage",
        "approval.ticket.view",
        "approval.ticket.create",
        "approval.ticket.submit",
    },
    "viewer": {
        "menu.connections.view",
        "datasource.browse",
        "export.query",
        "history.view",
        "approval.ticket.view",
    },
}


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class AccessContext:
    user: UserIdentity
    roles: tuple[str, ...]
    permissions: frozenset[str]
    policies: tuple[ResourcePolicy, ...]


class AuthorizationService:
    def sync_builtin_authorization(self, db: Session) -> None:
        dirty = False
        for code, spec in BUILTIN_PERMISSIONS.items():
            permission = db.get(Permission, code)
            if permission is None:
                permission = Permission(code=code)
                db.add(permission)
                dirty = True
            permission.resource_type = spec["resource_type"]
            permission.action = spec["action"]
            permission.description = spec["description"]

        for role_code, permission_codes in BUILTIN_ROLE_PERMISSIONS.items():
            role = db.get(Role, role_code)
            if role is None:
                role = Role(code=role_code)
                db.add(role)
                dirty = True
            role.name = role_code.replace("_", " ").title()
            role.description = f"Built-in {role_code} role for DBX enterprise access control."
            role.built_in = True

            existing_bindings = {
                binding.permission_code
                for binding in db.execute(
                    select(RolePermission).where(RolePermission.role_code == role_code)
                ).scalars()
            }
            for permission_code in permission_codes:
                if permission_code in existing_bindings:
                    continue
                db.add(
                    RolePermission(
                        role_code=role_code,
                        permission_code=permission_code,
                        effect="allow",
                        conditions={},
                    )
                )
                dirty = True

        db.commit()

    def build_access_context(self, db: Session, user: UserIdentity) -> AccessContext:
        role_codes = self._role_codes_for_user(db, user)
        permissions = self._permission_codes_for_roles(db, role_codes)
        policies = self._resource_policies_for_principals(db, user, role_codes)
        return AccessContext(
            user=user,
            roles=tuple(sorted(role_codes)),
            permissions=frozenset(permissions),
            policies=tuple(policies),
        )

    def check_permission(
        self,
        context: AccessContext,
        permission_code: str,
        *,
        datasource_id: str | None = None,
        database: str | None = None,
        schema: str | None = None,
        table: str | None = None,
    ) -> AccessDecision:
        if permission_code not in context.permissions:
            return AccessDecision(False, f"Missing permission: {permission_code}")

        if "admin" in context.roles or datasource_id is None:
            return AccessDecision(True)

        matching_policies = [
            policy
            for policy in context.policies
            if policy.resource_type == "datasource"
            and policy.resource_key in {"*", datasource_id}
            and policy.enabled
            and (policy.permission_code is None or policy.permission_code == permission_code)
            and self._matches_conditions(policy.conditions, database=database, schema=schema, table=table)
        ]
        if not matching_policies:
            return AccessDecision(False, f"Datasource scope denied: {datasource_id}")

        if any(policy.effect == "deny" for policy in matching_policies):
            return AccessDecision(False, f"Datasource scope denied: {datasource_id}")

        if any(policy.effect == "allow" for policy in matching_policies):
            return AccessDecision(True)
        return AccessDecision(False, f"Datasource scope denied: {datasource_id}")

    def list_users(self, db: Session) -> list[UserIdentity]:
        return db.execute(select(UserIdentity).order_by(UserIdentity.email.asc())).scalars().all()

    def list_role_bindings(self, db: Session, *, user_id: str | None = None) -> list[UserRoleBinding]:
        stmt = select(UserRoleBinding).order_by(UserRoleBinding.created_at.desc())
        if user_id:
            stmt = stmt.where(UserRoleBinding.user_id == user_id)
        return db.execute(stmt).scalars().all()

    def upsert_role_binding(
        self,
        db: Session,
        *,
        user_id: str,
        role_code: str,
        granted_by: str | None,
        expires_at: datetime | None,
    ) -> tuple[UserRoleBinding, str]:
        user = db.get(UserIdentity, user_id)
        if user is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
        role = db.get(Role, role_code)
        if role is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

        binding = db.execute(
            select(UserRoleBinding).where(
                UserRoleBinding.user_id == user_id,
                UserRoleBinding.role_code == role_code,
            )
        ).scalar_one_or_none()
        mutation = "granted" if binding is None else "updated"
        if binding is None:
            binding = UserRoleBinding(
                user_id=user_id,
                role_code=role_code,
                granted_by=granted_by,
                expires_at=expires_at,
            )
            db.add(binding)
        else:
            binding.granted_by = granted_by
            binding.expires_at = expires_at

        db.commit()
        db.refresh(binding)
        return binding, mutation

    def delete_role_binding(self, db: Session, binding_id: str) -> UserRoleBinding:
        binding = db.get(UserRoleBinding, binding_id)
        if binding is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role binding not found")
        db.delete(binding)
        db.commit()
        return binding

    def list_resource_policies(
        self,
        db: Session,
        *,
        principal_type: str | None = None,
        principal_ref: str | None = None,
    ) -> list[ResourcePolicy]:
        stmt = select(ResourcePolicy).order_by(ResourcePolicy.updated_at.desc(), ResourcePolicy.created_at.desc())
        if principal_type:
            stmt = stmt.where(ResourcePolicy.principal_type == principal_type)
        if principal_ref:
            stmt = stmt.where(ResourcePolicy.principal_ref == principal_ref)
        return db.execute(stmt).scalars().all()

    def create_resource_policy(
        self,
        db: Session,
        *,
        principal_type: str,
        principal_ref: str,
        resource_type: str,
        resource_key: str,
        permission_code: str | None,
        effect: str,
        conditions: dict[str, Any],
        enabled: bool,
    ) -> ResourcePolicy:
        self._validate_policy_fields(db, principal_type=principal_type, permission_code=permission_code, effect=effect)
        policy = ResourcePolicy(
            principal_type=principal_type,
            principal_ref=principal_ref.strip(),
            resource_type=resource_type.strip(),
            resource_key=resource_key.strip(),
            permission_code=permission_code.strip() if permission_code else None,
            effect=effect,
            conditions=conditions or {},
            enabled=enabled,
        )
        db.add(policy)
        db.commit()
        db.refresh(policy)
        return policy

    def update_resource_policy(
        self,
        db: Session,
        policy_id: str,
        *,
        principal_type: str,
        principal_ref: str,
        resource_type: str,
        resource_key: str,
        permission_code: str | None,
        effect: str,
        conditions: dict[str, Any],
        enabled: bool,
    ) -> ResourcePolicy:
        self._validate_policy_fields(db, principal_type=principal_type, permission_code=permission_code, effect=effect)
        policy = db.get(ResourcePolicy, policy_id)
        if policy is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource policy not found")

        policy.principal_type = principal_type
        policy.principal_ref = principal_ref.strip()
        policy.resource_type = resource_type.strip()
        policy.resource_key = resource_key.strip()
        policy.permission_code = permission_code.strip() if permission_code else None
        policy.effect = effect
        policy.conditions = conditions or {}
        policy.enabled = enabled

        db.commit()
        db.refresh(policy)
        return policy

    def delete_resource_policy(self, db: Session, policy_id: str) -> ResourcePolicy:
        policy = db.get(ResourcePolicy, policy_id)
        if policy is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Resource policy not found")
        db.delete(policy)
        db.commit()
        return policy

    def _role_codes_for_user(self, db: Session, user: UserIdentity) -> set[str]:
        role_codes = {user.role} if user.role else set()
        now = datetime.now(UTC)
        stmt = select(UserRoleBinding.role_code).where(
            UserRoleBinding.user_id == user.id,
            or_(UserRoleBinding.expires_at.is_(None), UserRoleBinding.expires_at > now),
        )
        role_codes.update(db.execute(stmt).scalars().all())
        return role_codes

    def _permission_codes_for_roles(self, db: Session, role_codes: set[str]) -> set[str]:
        if not role_codes:
            return set()
        stmt = select(RolePermission.permission_code).where(
            RolePermission.role_code.in_(role_codes), RolePermission.effect == "allow"
        )
        return set(db.execute(stmt).scalars().all())

    def _resource_policies_for_principals(
        self,
        db: Session,
        user: UserIdentity,
        role_codes: set[str],
    ) -> list[ResourcePolicy]:
        principals: list[tuple[str, str]] = [("user", user.id)]
        principals.extend(("role", role_code) for role_code in role_codes)
        conditions = [
            (ResourcePolicy.principal_type == principal_type) & (ResourcePolicy.principal_ref == principal_ref)
            for principal_type, principal_ref in principals
        ]
        if not conditions:
            return []
        stmt = select(ResourcePolicy).where(or_(*conditions), ResourcePolicy.enabled.is_(True))
        return list(db.execute(stmt).scalars().all())

    def _matches_conditions(
        self,
        conditions: dict[str, Any],
        *,
        database: str | None,
        schema: str | None,
        table: str | None,
    ) -> bool:
        allowed_databases = self._normalize_items(conditions.get("databases"))
        if database and allowed_databases and "*" not in allowed_databases and database not in allowed_databases:
            return False

        allowed_schemas = self._normalize_items(conditions.get("schemas"))
        if schema and allowed_schemas:
            schema_candidates = {
                schema,
                f"{database}.{schema}" if database else schema,
            }
            if "*" not in allowed_schemas and schema_candidates.isdisjoint(allowed_schemas):
                return False

        allowed_tables = self._normalize_items(conditions.get("tables"))
        if table and allowed_tables:
            table_candidates = {table}
            if schema:
                table_candidates.add(f"{schema}.{table}")
            if database and schema:
                table_candidates.add(f"{database}.{schema}.{table}")
            if "*" not in allowed_tables and table_candidates.isdisjoint(allowed_tables):
                return False

        return True

    def _normalize_items(self, value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    def _validate_policy_fields(
        self,
        db: Session,
        *,
        principal_type: str,
        permission_code: str | None,
        effect: str,
    ) -> None:
        if principal_type not in {"user", "role"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="principal_type must be user or role")
        if effect not in {"allow", "deny"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="effect must be allow or deny")
        if permission_code and db.get(Permission, permission_code.strip()) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Permission not found")
