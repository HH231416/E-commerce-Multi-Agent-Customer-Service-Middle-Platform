"""HTTP 身份、角色和租户边界。生产环境只信任经过验证的令牌声明。

鉴权来源优先级：
1. 非生产环境的调试身份头（仅本地开发）
2. `Authorization: Bearer <JWT>`
3. HttpOnly 会话 Cookie（由 /api/auth/session 换取）

Cookie 鉴权额外做同源校验：写请求只允许 Origin 与 Host 一致，
配合 SameSite=Lax 阻断跨站请求伪造。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from service.settings import settings

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_bearer = HTTPBearer(auto_error=False)

SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "cs_session")
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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


def decode_claims(token: str) -> dict:
    """校验签名、受众、签发方与必要声明，返回声明字典。"""
    if not settings.jwt_secret_key:
        raise HTTPException(status_code=503, detail="认证服务未配置")

    try:
        return jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=["HS256"],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "sub"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="访问令牌无效或已过期") from exc


def principal_from_claims(claims: dict) -> Principal:
    return Principal(
        subject=_validate_identifier(claims.get("sub"), "用户标识"),
        tenant_id=_validate_identifier(claims.get("tenant_id"), "租户标识"),
        roles=_roles(claims.get("roles", ["customer"])),
    )


def _assert_same_origin(request: Request) -> None:
    """Cookie 鉴权下的写请求必须来自同源页面。

    浏览器对跨站请求会带上 Origin，因此 Origin 与可信用 Host 不一致即判定为 CSRF。
    可信 Host 依次取 Host、X-Forwarded-Host（反向代理场景）与 ALLOWED_ORIGINS 白名单。
    非浏览器客户端通常不带 Origin，此检查对其放行（它们不会自动携带 Cookie）。
    """
    origin = request.headers.get("origin")
    if not origin:
        return

    origin_netloc = urlsplit(origin).netloc
    trusted = {
        value
        for value in (
            request.headers.get("host", ""),
            request.headers.get("x-forwarded-host", ""),
        )
        if value
    }
    for item in os.getenv("ALLOWED_ORIGINS", "").split(","):
        item = item.strip()
        if item:
            trusted.add(urlsplit(item).netloc or item)

    if not origin_netloc or origin_netloc not in trusted:
        raise HTTPException(status_code=403, detail="跨站请求被拒绝")


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

    via_cookie = credentials is None
    token = credentials.credentials if credentials else request.cookies.get(SESSION_COOKIE_NAME)

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要 Bearer 访问令牌或有效会话",
        )

    if via_cookie and request.method.upper() not in _SAFE_METHODS:
        _assert_same_origin(request)

    return principal_from_claims(decode_claims(token))


def require_roles(*roles: str) -> Callable:
    async def dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_any_role(*roles):
            raise HTTPException(status_code=403, detail="权限不足")
        return principal

    return dependency


def set_session_cookie(response: Response, token: str, max_age: int) -> None:
    """写入 HttpOnly 会话 Cookie：JavaScript 无法读取，降低 XSS 窃取风险。"""
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=settings.is_production,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")