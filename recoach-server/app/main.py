from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .config import get_settings
from .errors import error_payload
from .ids import new_id
from .routes import forks, memories, meta, metrics, sessions, turns
from .services import turns as turn_store


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    # 进程重启中断的 Turn 恢复为可重试的 error 态（CANCELLED/ProcessRestart），
    # 同一 clientTurnId 重试时路由会原子 claim 后重新执行（B7）。
    turn_store.recover_stale_streaming()
    yield


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_id("req")


def _get_cors_headers(request: Request, settings) -> dict[str, str]:
    """从请求的 Origin 头提取并验证 CORS 响应头。
    
    这确保异常处理器返回的响应也包含正确的 CORS 头。
    """
    origin = request.headers.get("origin", "")
    headers = {}
    
    if origin and origin in settings.cors_origin_list:
        headers["access-control-allow-origin"] = origin
        headers["access-control-allow-credentials"] = "true"
    
    return headers


def _envelope(
    request: Request,
    status: int,
    code: str,
    *,
    headers: dict[str, str] | None = None,
    message: str | None = None,
    retryable: bool | None = None,
) -> JSONResponse:
    """统一错误信封：body 与响应头共用同一个 requestId。

    Starlette 会把 @app.exception_handler(Exception) 注册为 ServerErrorMiddleware
    的 error_handler，其响应由最外层中间件直发、不经过 RequestIdMiddleware 的
    send_wrapper，因此必须在这里显式带上 x-request-id。
    
    同样需要手动添加 CORS 头，因为异常响应可能不经过 CORSMiddleware。
    """
    headers = dict(headers or {})
    headers["x-request-id"] = _request_id(request)
    
    # 添加 CORS 头
    settings = get_settings()
    cors_headers = _get_cors_headers(request, settings)
    headers.update(cors_headers)
    
    return JSONResponse(
        status_code=status,
        content={
            "error": error_payload(
                code,
                request_id=_request_id(request),
                message=message,
                retryable=retryable,
            )
        },
        headers=headers,
    )


class RequestIdMiddleware:
    """纯 ASGI 中间件：生成/透传 x-request-id 并注入每个响应头。

    不用 BaseHTTPMiddleware（starlette 1.6 在路由异常时 call_next 会抛异常，
    返回路径不会执行，异常响应会漏掉 header）；在 send 层注入对
    异常 handler 响应与 SSE 流式响应均可靠。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = next(
            (v.decode("latin-1") for k, v in scope.get("headers", []) if k.lower() == b"x-request-id"),
            "",
        ).strip()
        if not request_id:
            request_id = new_id("req")
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                # 路由（SSE）与 _envelope 已带 x-request-id 时不重复添加
                if not any(k.lower() == b"x-request-id" for k, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                    message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_wrapper)


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="知返 Re:Coach API", version="1.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        # 404/405 等框架级 HTTP 异常统一错误信封；保留框架附带的 header（如 405 的 allow）。
        headers = dict(exc.headers or {})
        return _envelope(request, exc.status_code, "INVALID_REQUEST", headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError):
        return _envelope(request, 422, "INVALID_REQUEST")

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, _exc: Exception):
        return _envelope(request, 500, "INTERNAL")

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "recoach-server"}

    app.include_router(sessions.router, prefix="/api/v1")
    app.include_router(turns.router, prefix="/api/v1")
    app.include_router(forks.router, prefix="/api/v1")
    app.include_router(metrics.router, prefix="/api/v1")
    app.include_router(memories.router, prefix="/api/v1")
    app.include_router(meta.router, prefix="/api/v1")
    return app


app = create_app()
