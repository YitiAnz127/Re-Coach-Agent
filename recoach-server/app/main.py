from __future__ import annotations

import json
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .auth import AuthMiddleware
from .config import check_base_url_security, get_settings
from .errors import error_payload
from .ids import new_id
from .routes import forks, memories, meta, metrics, sessions, turns
from .services import coach as coach_service
from .services import turns as turn_store

# 客户端可控的 x-request-id 只允许这一组字符，避免响应头/日志注入。
_REQUEST_ID_RE = re.compile(r"[^A-Za-z0-9._:-]")
_REQUEST_ID_MAX = 128


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    # 进程重启中断的 Turn 恢复为可重试的 error 态（CANCELLED/ProcessRestart），
    # 同一 clientTurnId 重试时路由会原子 claim 后重新执行（B7）。
    turn_store.recover_stale_streaming()
    yield


def _sanitize_request_id(raw: str) -> str:
    """只保留安全字符并截断；不合法/超长一律丢弃，由服务端重新生成。"""
    cleaned = _REQUEST_ID_RE.sub("", raw)[:_REQUEST_ID_MAX]
    return cleaned or new_id("req")


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_id("req")


def _get_cors_headers(request: Request, settings) -> dict[str, str]:
    """从请求的 Origin 头提取并验证 CORS 响应头。

    这确保异常处理器返回的响应也包含正确的 CORS 头。
    通配符配置下不回显具体 Origin，也不带凭证——与 CORSMiddleware 的判定保持一致。
    """
    origin = request.headers.get("origin", "")
    headers = {}

    if not origin:
        return headers

    if "*" in settings.cors_origin_list:
        headers["access-control-allow-origin"] = "*"
        return headers

    if origin in settings.cors_origin_list:
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


class MaxBodySizeMiddleware:
    """限制请求体大小。

    uvicorn 没有请求体大小上限的 CLI 选项，而 FastAPI 会把整个 body 读进内存
    再交给 Pydantic 校验，所以必须在读之前拦。

    Content-Length 只用于提前拒绝明显超限的请求；最终仍按实际收到的字节数
    判断。客户端声明较小长度、漏掉长度或使用 chunked 时都不能绕过上限。

    代价：请求体会先在中间件里缓冲（最多 max_bytes），再重放给应用。
    对 JSON API 无影响；若将来要支持流式上传，需要改成按块校验。
    """

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max_bytes

    async def _deny(self, scope, send, status: int = 413) -> None:
        request_id = (scope.get("state") or {}).get("request_id") or ""
        payload = json.dumps(
            {"error": error_payload("INVALID_REQUEST", request_id=request_id or None)},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(payload)).encode("latin-1")),
        ]
        if request_id:
            headers.append((b"x-request-id", request_id.encode("latin-1")))
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or self.max_bytes <= 0:
            await self.app(scope, receive, send)
            return

        declared = next(
            (
                v.decode("latin-1").strip()
                for k, v in scope.get("headers", [])
                if k.lower() == b"content-length"
            ),
            "",
        )
        try:
            length = int(declared) if declared else None
        except ValueError:
            await self._deny(scope, send, status=400)
            return

        if length is not None and length < 0:
            await self._deny(scope, send, status=400)
            return

        # 快路径：声明就超限，连读都不用读
        if length is not None and length > self.max_bytes:
            await self._deny(scope, send)
            return

        # 不信任声明值：始终有界读取并按实际字节数复核。
        body = bytearray()
        finished = False
        while not finished:
            message = await receive()
            if message["type"] == "http.request":
                body.extend(message.get("body", b""))
                if len(body) > self.max_bytes:
                    await self._deny(scope, send)
                    return
                finished = not message.get("more_body", False)
            elif message["type"] == "http.disconnect":
                return

        drained = False

        async def replay_receive():
            nonlocal drained
            if not drained:
                drained = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            # body 已交付；后续调用交还给原始 receive（例如等待 http.disconnect）
            return await receive()

        await self.app(scope, replay_receive, send)


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
        # 客户端可控，必须先净化再回填进响应头与日志。
        request_id = _sanitize_request_id(request_id)
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

    # 启动期配置校验：携带密钥的端点不得是公网明文 http，否则密钥会裸奔。
    # 放在这里而不是 lifespan，是为了让"配置错了"在任何入口都被发现
    # （lifespan 在 TestClient 不进出上下文时不会执行）。
    # 失败就**起不来**：这类错误静默运行时看不出来，而代价是密钥泄露。
    #
    # 只检查**实际生效的 provider 会用到**的那个端点：provider=template 时
    # llm_base_url / deepseek_base_url 根本不参与请求，为一个惰性配置让长驻服务
    # 起不来（compose 的 restart: unless-stopped 下会变成重启循环）是过度拦截。
    provider, _model = coach_service.resolve_provider()
    base_url_problem: str | None = None
    if provider == "openai_compatible":
        base_url_problem = check_base_url_security(
            settings.llm_base_url, bool(settings.llm_api_key)
        )
    elif provider == "deepseek":
        base_url_problem = check_base_url_security(
            settings.deepseek_base_url, bool(settings.effective_deepseek_key)
        )
    if base_url_problem:
        raise RuntimeError(base_url_problem)

    app = FastAPI(
        title="知返 Re:Coach API",
        version="1.1.0",
        lifespan=lifespan,
        # 生产（已配令牌）默认不暴露交互式文档与 openapi.json。
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    # 中间件自外向内：RequestId -> CORS -> MaxBodySize -> Auth -> 路由。
    # （Starlette 把后 add 的放在更外层，因此这里的 add 顺序与生效顺序相反。）
    # 实测确认：超大 body + 无 token 返回 413 而非 401（MaxBodySize 在 Auth 外层）；
    # 该 413 带 access-control-allow-origin（CORS 在 MaxBodySize 外层）。
    # Auth 必须位于 CORS 内层，否则 401 响应没有 CORS 头，浏览器读不到错误体。
    # MaxBodySize 放在 Auth 外层：超大请求体不该先做令牌比较再拒绝。
    app.add_middleware(AuthMiddleware)
    app.add_middleware(MaxBodySizeMiddleware, max_bytes=settings.max_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=settings.allow_credentials,
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
