"""FastAPI app: OpenAI-compatible /v1/chat/completions plus health, models and stats."""
import json
import logging
import time

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .auth import AuthError, identify
from .breaker import CircuitBreaker
from .config import Settings, get_settings
from .executor import ExecutionLimits, Executor
from .limits import RateLimited, RateLimiter
from .policy import RulesPolicy, StaticPolicy
from .registry import Registry
from .schemas import ChatRequest, RouterError
from .service import RouterService
from .tracker import Tracker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("model-router")


def build_service(settings: Settings, completion_fn=None, clock=time.monotonic, sleep=time.sleep) -> RouterService:
    registry = Registry.load(settings.models_file)
    if completion_fn is None and settings.demo_mode:
        from .demo import DemoProvider

        completion_fn = DemoProvider(sleep)
    breaker = CircuitBreaker(settings.breaker_failure_threshold, settings.breaker_cooldown_s, clock)
    limits = ExecutionLimits(settings.per_call_timeout_s, settings.request_deadline_s, settings.max_model_calls,
                             settings.max_fallback_hops, settings.max_retries_per_call, settings.retry_backoff_s)
    executor = Executor(registry, breaker, limits, completion_fn, clock, sleep)
    tracker = Tracker(settings.log_db_path, settings.debug_log_text, settings.debug_retention_days)
    if settings.routing_policy == "static":
        policy = StaticPolicy(registry, settings.default_chain_ids)
    else:
        policy = RulesPolicy(registry, settings.rules_policy_file)
    return RouterService(settings, registry, executor, tracker, policy, clock)


def create_app(settings: Settings | None = None, service: RouterService | None = None,
               rate_limiter: RateLimiter | None = None) -> FastAPI:
    settings = settings or get_settings()
    service = service or build_service(settings)
    limiter = rate_limiter or RateLimiter(settings.rate_limit_per_minute, settings.per_client_daily_limit)

    app = FastAPI(title="Model Router", version="0.1.0")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_methods=["GET", "POST"],
                       allow_headers=["Authorization", "Content-Type"])

    @app.exception_handler(RouterError)
    async def router_error(_: Request, exc: RouterError):
        return JSONResponse(exc.body(), status_code=exc.status)

    @app.get("/api/health")
    def health():
        return {
            "status": "ok",
            "demo_mode": settings.demo_mode,
            "policy_version": service.policy.version,
            "models_enabled": [o.id for o in service.registry.enabled()],
            "price_checked": service.registry.price_checked,
            "breakers": service.executor.breaker.snapshot(),
        }

    @app.get("/api/models")
    def models():
        return {"price_checked": service.registry.price_checked, "options": [
            {"id": o.id, "provider_model": o.model, "tier": o.tier, "reasoning_effort": o.reasoning_effort,
             "input_usd_per_mtok": o.input_usd_per_mtok, "output_usd_per_mtok": o.output_usd_per_mtok,
             "per_call_usd": o.per_call_usd, "web_search": o.web_search,
             "demo_allowed": o.demo_allowed,
             "reference": o.id in (service.settings.reference_model, service.settings.reference_search_model)}
            for o in service.registry.enabled()]}

    @app.get("/v1/models")
    def openai_models():
        return {"object": "list", "data": [{"id": "auto", "object": "model", "owned_by": "model-router"}] + [
            {"id": o.id, "object": "model", "owned_by": "model-router"} for o in service.registry.enabled()]}

    @app.get("/api/stats")
    def stats():
        return {**service.tracker.stats(), "cost_basis": "simulated" if settings.demo_mode else "calculated",
                "price_checked": service.registry.price_checked}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.body()
        if len(body) > settings.max_request_bytes:
            raise RouterError(413, "request_too_large",
                              f"Request body is {len(body)} bytes; the limit is {settings.max_request_bytes}.")
        try:
            caller = identify(request, settings)
        except AuthError as e:
            raise RouterError(401, "unauthorized", str(e)) from None
        try:
            req = ChatRequest.model_validate_json(body)
        except ValidationError as e:
            first = e.errors()[0]
            where = ".".join(str(p) for p in first.get("loc", ()))
            raise RouterError(400, "invalid_request", f"{where}: {first.get('msg')}") from None
        except json.JSONDecodeError:
            raise RouterError(400, "invalid_request", "Body is not valid JSON.") from None
        if caller.role != "admin":
            try:
                limiter.check(caller.identity)
            except RateLimited as e:
                raise RouterError(429, e.code, str(e)) from None
        # Model calls block; run them in a worker thread so the server keeps serving other requests
        response = await run_in_threadpool(service.handle, req, caller.role, caller.identity)
        r = response["router"]
        log.info("id=%s routed_to=%s fallbacks=%s cost=$%.6f latency=%sms", response["id"], r["routed_to"],
                 r["fallbacks_used"], r["cost_usd"], r["latency_ms"])
        return response

    return app


_app: FastAPI | None = None


def __getattr__(name: str):
    """`uvicorn app.main:app` builds the app on first access, so importing this module
    (for example in tests) has no side effects such as creating the log database."""
    global _app
    if name == "app":
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
