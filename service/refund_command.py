"""退款命令边界：生产环境调用订单域服务，开发环境才使用 SQLite Mock。"""
from __future__ import annotations

import asyncio

import httpx
from fastapi import HTTPException

from service.order_workflow import RefundWorkflow
from service.settings import settings


class RefundCommandService:
    async def confirm(
        self,
        *,
        tenant_id: str,
        order_no: str,
        user_id: str,
        idempotency_key: str,
        reason: str,
    ) -> dict:
        if not settings.is_production:
            return await asyncio.to_thread(
                RefundWorkflow().confirm_refund, order_no, user_id, idempotency_key, reason
            )

        assert settings.order_service_base_url and settings.order_service_token
        url = f"{settings.order_service_base_url.rstrip('/')}/internal/orders/{order_no}/refunds"
        headers = {
            "Authorization": f"Bearer {settings.order_service_token}",
            "Idempotency-Key": idempotency_key,
            "X-Tenant-Id": tenant_id,
            "X-Actor-Id": user_id,
        }
        payload = {"reason": reason}
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=503, detail="订单服务暂时不可用") from exc

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="订单不存在或无权操作")
        if response.status_code in {409, 422}:
            raise HTTPException(status_code=409, detail="订单当前状态不允许退款")
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="订单服务执行退款失败")
        data = response.json()
        return {
            "status": data.get("status", "submitted"),
            "idempotent_replay": bool(data.get("idempotent_replay", False)),
            "order_no": order_no,
        }
