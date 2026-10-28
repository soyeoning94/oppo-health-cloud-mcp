#!/usr/bin/env python3
"""Remote Streamable HTTP MCP + private management API/dashboard."""
from __future__ import annotations

import functools
import hmac
import json
import os
from pathlib import Path

import anyio
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, PlainTextResponse

import cloud_mcp
from health_analytics import build_summary

HERE = Path(__file__).resolve().parent
DASHBOARD_FILE = HERE / "dashboard.html"


class BearerTokenMiddleware:
    """Small ASGI middleware that does not buffer MCP streaming responses."""

    def __init__(self, app, api_token: str):
        if not api_token:
            raise ValueError("OPPO_HEALTH_API_TOKEN is required")
        self.app = app
        self.api_token = api_token.encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")
            protected = path == "/mcp" or path.startswith("/mcp/") or path.startswith("/api/")
            if protected:
                headers = {key.lower(): value for key, value in scope.get("headers", [])}
                supplied = headers.get(b"authorization", b"")
                expected = b"Bearer " + self.api_token
                if not hmac.compare_digest(supplied, expected):
                    response = JSONResponse(
                        {"error": "unauthorized", "message": "Bearer token required"},
                        status_code=401,
                        headers={"WWW-Authenticate": "Bearer"},
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


def create_app(api_token: str, db_path: str | None = None,
               auth_file: str | None = None, dashboard_file: str | Path | None = None,
               public_hosts: list[str] | None = None):
    db_path = db_path or cloud_mcp.CACHE_DB
    auth_file = auth_file or cloud_mcp.AUTH_FILE
    dashboard = Path(dashboard_file or DASHBOARD_FILE)
    allowed_hosts = ["127.0.0.1:*", "localhost:*", "[::1]:*"]
    for host in public_hosts or []:
        clean = host.strip()
        if clean:
            allowed_hosts.extend([clean, f"{clean}:*"])
    sync_lock = anyio.Lock()

    mcp = FastMCP(
        "oppo_health_cloud",
        instructions=(
            "Private OPPO Health data service. cloud_sync writes the shared cache; "
            "cloud_query and cloud_analyze read it. Analytics label raw, vendor, and local provenance."
        ),
        streamable_http_path="/mcp",
        stateless_http=True,
        json_response=True,
        max_request_body_size=2 * 1024 * 1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[],
        ),
    )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False))
    async def cloud_sync(metric: cloud_mcp.MetricName, days: int = 7) -> dict:
        """从 OPPO 云端拉取指定指标并写入共享 SQLite 缓存。"""
        async with sync_lock:
            return await anyio.to_thread.run_sync(
                functools.partial(cloud_mcp.cloud_sync, metric, min(max(days, 1), 365))
            )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def cloud_query(metric: cloud_mcp.MetricName, date: str | None = None) -> dict:
        """查询共享缓存；date 支持 YYYYMMDD 或 YYYY-MM-DD。"""
        return await anyio.to_thread.run_sync(
            functools.partial(cloud_mcp.cloud_query, metric, date)
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def cloud_analyze(days: int = 7) -> dict:
        """读取透明、带来源标签的描述性统计与数据质量信息。"""
        return await anyio.to_thread.run_sync(
            functools.partial(cloud_mcp.cloud_analyze, min(max(days, 1), 365))
        )

    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False))
    async def cloud_status() -> dict:
        """检查认证文件、缓存数据集和记录数。"""
        return await anyio.to_thread.run_sync(cloud_mcp.cloud_status)

    @mcp.custom_route("/", methods=["GET"], include_in_schema=False)
    async def dashboard_route(request: Request):
        if not dashboard.exists():
            return PlainTextResponse("dashboard.html missing", status_code=503)
        return FileResponse(dashboard, media_type="text/html; charset=utf-8")

    @mcp.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def health_route(request: Request):
        return JSONResponse({"status": "ok", "service": "oppo-health-cloud-mcp"})

    @mcp.custom_route("/api/summary", methods=["GET"], include_in_schema=False)
    async def summary_route(request: Request):
        try:
            return JSONResponse(build_summary(db_path))
        except sqlite_error_types() as exc:
            return JSONResponse({"error": "cache_unavailable", "message": str(exc)}, status_code=503)

    @mcp.custom_route("/api/status", methods=["GET"], include_in_schema=False)
    async def status_route(request: Request):
        summary = build_summary(db_path)
        status = await anyio.to_thread.run_sync(cloud_mcp.cloud_status)
        return JSONResponse({
            "auth_ready": Path(auth_file).exists(),
            "cache_ready": Path(db_path).exists(),
            "metrics": list(cloud_mcp.MCP_METRIC_CHOICES),
            "data_quality": summary["data_quality"],
            "last_sync": status.get("last_sync", {}),
            "sync_history": await anyio.to_thread.run_sync(
                functools.partial(cloud_mcp.sync_history, 20)
            ),
            "mcp": {"transport": "streamable-http", "path": "/mcp", "authentication": "bearer"},
        })

    @mcp.custom_route("/api/sync", methods=["POST"], include_in_schema=False)
    async def sync_route(request: Request):
        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        metric = payload.get("metric")
        try:
            days = min(max(int(payload.get("days", 7)), 1), 365)
        except (TypeError, ValueError):
            return JSONResponse({"error": "invalid_days"}, status_code=400)
        if metric not in cloud_mcp.MCP_METRIC_CHOICES:
            return JSONResponse({"error": "invalid_metric", "available": cloud_mcp.MCP_METRIC_CHOICES},
                                status_code=400)
        async with sync_lock:
            result = await anyio.to_thread.run_sync(
                functools.partial(cloud_mcp.cloud_sync, metric, days)
            )
        return JSONResponse(result, status_code=502 if result.get("error") else 200)

    @mcp.custom_route("/api/query", methods=["GET"], include_in_schema=False)
    async def query_route(request: Request):
        metric = request.query_params.get("metric")
        date = request.query_params.get("date")
        if metric not in cloud_mcp.MCP_METRIC_CHOICES:
            return JSONResponse({"error": "invalid_metric"}, status_code=400)
        result = await anyio.to_thread.run_sync(
            functools.partial(cloud_mcp.cloud_query, metric, date)
        )
        return JSONResponse(result)

    return BearerTokenMiddleware(mcp.streamable_http_app(), api_token)


def sqlite_error_types():
    import sqlite3
    return (sqlite3.Error, OSError, ValueError)


_api_token = os.environ.get("OPPO_HEALTH_API_TOKEN", "")
app = create_app(
    api_token=_api_token,
    db_path=os.environ.get("OPPO_HEALTH_CACHE_DB") or cloud_mcp.CACHE_DB,
    auth_file=os.environ.get("OPPO_HEALTH_AUTH_FILE") or cloud_mcp.AUTH_FILE,
    public_hosts=[host for host in os.environ.get("OPPO_HEALTH_PUBLIC_HOSTS", "").split(",") if host],
) if _api_token else None


if __name__ == "__main__":
    import uvicorn

    if app is None:
        raise SystemExit("OPPO_HEALTH_API_TOKEN is required")
    uvicorn.run(
        "app_server:app",
        host=os.environ.get("OPPO_HEALTH_HOST", "127.0.0.1"),
        port=int(os.environ.get("OPPO_HEALTH_PORT", "9330")),
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level=os.environ.get("OPPO_HEALTH_LOG_LEVEL", "info").lower(),
    )
