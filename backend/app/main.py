"""FastAPI app factory: exactly three endpoints, JSON-only errors."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.config import Settings
from app.llm.gateway import LLMGateway, ProviderUnavailable
from app.llm.simulation import parse_simulation_header
from app.logsetup import setup_logging
from app.repository import SessionRepository
from app.schemas import ChatRequest, ChatResponse, HealthResponse, HistoryResponse
from app.services.chat import ChatService

logger = logging.getLogger("vani.app")
VERSION = "1.0.0"


def create_app(
    settings: Settings | None = None,
    deployment_overrides: dict[str, dict[str, Any]] | None = None,
) -> FastAPI:
    settings = settings or Settings()
    setup_logging(settings.log_level, settings.api_keys)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(settings.sqlite_path)
        repo = SessionRepository(settings.sqlite_path)
        app.state.repo = repo
        app.state.gateway = None
        app.state.chat = None
        if not settings.api_keys:
            logger.error("LLM_API_KEYS is empty: /health will report 503 and /chat will fail")
            yield
            return
        gateway = LLMGateway(settings, deployment_overrides)
        await gateway.warm_up()
        app.state.gateway = gateway
        app.state.chat = ChatService(settings, repo, gateway)
        logger.info(
            "Vani ready: keys=%d models=%s cache=%s simulation=%s",
            gateway.keys_configured,
            sorted(set(settings.models_by_key.values())),
            settings.cache_type if settings.cache_enabled else "off",
            settings.enable_failure_simulation,
        )
        yield

    docs = settings.enable_api_docs
    app = FastAPI(
        title="Vani API",
        version=VERSION,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.origins,
        allow_origin_regex=settings.allowed_origin_regex or None,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Simulate-Failure"],
        allow_credentials=False,
    )

    # ---- error handlers: always JSON with "detail" ----------------------
    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=getattr(exc, "headers", None))

    @app.exception_handler(ProviderUnavailable)
    async def _providers(request: Request, exc: ProviderUnavailable) -> JSONResponse:
        return JSONResponse({"detail": "all providers unavailable"}, status_code=503)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled error on %s %s: %s", request.method, request.url.path, type(exc).__name__)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    # ---- endpoints -------------------------------------------------------
    @app.get("/health", response_model=HealthResponse)
    async def health(request: Request) -> Any:
        gateway: LLMGateway | None = getattr(request.app.state, "gateway", None)
        try:
            await asyncio.to_thread(db.ping, settings.sqlite_path)
        except Exception:
            return JSONResponse({"detail": "database unavailable"}, status_code=503)
        if gateway is None or gateway.keys_configured < 1:
            return JSONResponse({"detail": "no LLM keys loaded"}, status_code=503)
        return {
            "status": "ok",
            "keys_configured": gateway.keys_configured,
            "cache_enabled": settings.cache_enabled,
            "cache_type": settings.cache_type.lower() if settings.cache_enabled else None,
            "failure_simulation": settings.enable_failure_simulation,
            "version": VERSION,
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(body: ChatRequest, request: Request) -> Any:
        t0 = request.state.t0 if hasattr(request.state, "t0") else time.perf_counter()
        simulate: dict[str, int] = {}
        if settings.enable_failure_simulation:
            simulate = parse_simulation_header(request.headers.get("x-simulate-failure"), settings.key_ids)
        service: ChatService | None = request.app.state.chat
        if service is None:
            raise ProviderUnavailable("no keys configured")
        return await service.chat(body, simulate, t0)

    @app.get("/sessions/{session_id}/messages", response_model=HistoryResponse)
    async def history(session_id: str, request: Request) -> Any:
        repo: SessionRepository = request.app.state.repo
        messages = await repo.get_messages(session_id)
        if not messages:
            return JSONResponse({"detail": "session not found"}, status_code=404)
        return {"session_id": session_id, "messages": messages}

    @app.middleware("http")
    async def _timer(request: Request, call_next):
        request.state.t0 = time.perf_counter()
        return await call_next(request)

    return app
