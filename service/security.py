"""HTTP 身份、角色和租户边界。生产环境只信任经过验证的令牌声明。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from service.settings import settings

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    roles: frozenset[str]

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


def _validate_identifier(value: object, field: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise HTTPException(status_code=401, detail=f"无效的 {field}")
    return value


def _roles(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(item for item in value.split() if item)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return frozenset(value)
    return frozenset()


async def get_current_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> Principal:
    if not settings.is_production:
        subject = request.headers.get("X-Debug-User-Id")
        tenant_id = request.headers.get("X-Debug-Tenant-Id", "demo")
        if subject:
            return Principal(
                subject=_validate_identifier(subject, "调试用户标识"),
                tenant_id=_validate_identifier(tenant_id, "调试租户标识"),
                roles=_roles(request.headers.get("X-Debug-Roles", "customer")),
            )

    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="需要 Bearer 访问令牌")
    if not settings.jwt_secret_key:
        raise HTTPException(status_code=503, detail="认证服务未配置")

    try:
        options = {"require": ["exp", "sub"]}
        claims = jwt.decode(
            credentials.credentials,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options=options,
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="访问令牌无效或已过期") from exc

    return Principal(
        subject=_validate_identifier(claims.get("sub"), "用户标识"),
        tenant_id=_validate_identifier(claims.get("tenant_id"), "租户标识"),
        roles=_roles(claims.get("roles", ["customer"])),
    )


def require_roles(*roles: str) -> Callable:
    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_any_role(*roles):
            raise HTTPException(status_code=403, detail="权限不足")
        return principal

    return dependency
