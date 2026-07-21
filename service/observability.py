"""最小可观测性：请求标识、Prometheus 指标和 OpenTelemetry 自动追踪。"""
from __future__ import annotations

import time
import uuid

from fastapi import FastAPI, Request
from prometheus_client import Counter, Histogram

REQUEST_COUNT = Counter("customer_service_http_requests_total", "HTTP 请求总数", ["method", "route", "status"])
REQUEST_DURATION = Histogram("customer_service_http_request_duration_seconds", "HTTP 请求耗时", ["method", "route"])


def configure_observability(app: FastAPI) -> None:
    @app.middleware("http")
    async def add_request_id_and_metrics(request: Request, call_next):
        started = time.perf_counter()
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        response = await call_next(request)
        route = request.scope.get("route")
        route_name = getattr(route, "path", request.url.path)
        elapsed = time.perf_counter() - started
        REQUEST_DURATION.labels(request.method, route_name).observe(elapsed)
        REQUEST_COUNT.labels(request.method, route_name, str(response.status_code)).inc()
        response.headers["X-Request-Id"] = request_id
        return response

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        # 未配置采集器时仍保留本地指标，不影响客服主链路。
        pass
