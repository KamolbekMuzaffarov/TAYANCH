from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import account, admin, alerts, auth, console, facility, geo, live, live_records, public, registry, voice
from app.core.config import get_settings
from app.core.errors import install_handlers
from app.core.logging import configure_logging

log = logging.getLogger("perinatal.access")
MAX_BODY = 20 * 1024 * 1024
LIVE_PREFIX = "/api/v1/live/"
# small flat JSON only (size + nesting capped before parsing): live layer, accounts, admin console
GUARDED_PREFIXES = (LIVE_PREFIX, "/api/v1/account/", "/api/v1/admin/", "/api/v1/facility/")
PUBLIC_PREFIX = "/api/v1/public/"


def json_depth_exceeds(body: bytes, limit: int) -> bool:
    """Nesting depth of a JSON document without parsing it (string contents are skipped)."""
    depth, in_str, esc = 0, False, False
    for ch in body:
        if in_str:
            if esc:
                esc = False
            elif ch == 0x5C:
                esc = True
            elif ch == 0x22:
                in_str = False
        elif ch == 0x22:
            in_str = True
        elif ch in (0x7B, 0x5B):
            depth += 1
            if depth > limit:
                return True
        elif ch in (0x7D, 0x5D):
            depth -= 1
    return False


def _warm_official() -> None:
    from app.db import SessionLocal
    from app.services.live_engine import geo
    from app.services.official_stats import stats
    with SessionLocal() as db:
        geo.load(db)
    stats.load(geo.districts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Live layer background work: SLA scheduler tick + (development only) the visit simulator."""
    from app.services import live_sim
    tasks = live_sim.start()
    try:
        await asyncio.to_thread(live.prefill_events)
    except Exception:  # noqa: BLE001 - database not seeded yet
        pass
    try:  # official district statistics: parse + match once, then serve from memory
        await asyncio.to_thread(_warm_official)
    except Exception:  # noqa: BLE001
        pass
    try:
        yield
    finally:
        for t in tasks:
            t.cancel()


def create_app() -> FastAPI:
    s = get_settings()
    configure_logging(s.LOG_LEVEL)
    try:  # account layer: new tables / nullable columns on an older database file, demo admin persona
        from app.db import SessionLocal, engine
        from app.services.schema import ensure_demo_admin, ensure_demo_facility_admin, ensure_demo_pinfl, ensure_schema
        ensure_schema(engine)
        if not s.is_production and s.LIVE_DEMO_LOGIN:
            with SessionLocal() as db:
                ensure_demo_admin(db)
                ensure_demo_facility_admin(db)
                ensure_demo_pinfl(db)
    except Exception as exc:  # noqa: BLE001 - database missing / not seeded yet
        logging.getLogger("perinatal.schema").warning("account schema check skipped: %s", type(exc).__name__)
    app = FastAPI(title="Perinatal AI API", version="0.1.0",
                  description="Privacy-first maternal, fetal and newborn monitoring platform for Uzbekistan. "
                              "Clinical decisions remain with clinicians; Claude is used only for non-clinical help "
                              "through the Privacy Gateway.",
                  docs_url="/api/docs" if not s.is_production else None, openapi_url="/api/openapi.json",
                  lifespan=lifespan)
    # CORS: explicitly configured origins only - a wildcard is never honoured.
    origins = [o.strip().rstrip("/") for o in s.CORS_ORIGINS.split(",") if o.strip() and "*" not in o]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, max_age=600, allow_methods=["GET", "POST", "PATCH", "DELETE"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"])

    @app.middleware("http")
    async def security_and_access_log(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex[:16]
        cl = request.headers.get("content-length")
        if cl and cl.isdigit() and int(cl) > MAX_BODY:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=413, content={"error": {"code": "payload_too_large", "message": "Request too large"}})
        if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith(GUARDED_PREFIXES):
            # live layer accepts small flat JSON only: cap the size and the nesting depth before any parsing
            from fastapi.responses import JSONResponse

            def _reject(status: int, code: str, message: str):
                return JSONResponse(status_code=status, headers={"X-Request-ID": request.state.request_id},
                                    content={"error": {"code": code, "message": message, "details": [],
                                                       "request_id": request.state.request_id}})
            if cl and cl.isdigit() and int(cl) > s.LIVE_MAX_BODY_BYTES:
                return _reject(413, "payload_too_large", "Request too large")
            body = await request.body()
            if len(body) > s.LIVE_MAX_BODY_BYTES:
                return _reject(413, "payload_too_large", "Request too large")
            if json_depth_exceeds(body, s.LIVE_MAX_JSON_DEPTH):
                return _reject(400, "json_too_deep", "JSON nesting is too deep")
        t0 = time.monotonic()
        response = await call_next(request)
        route = request.scope.get("route")
        # Route template only (no path params, no query string, no body) -> no identifiers in logs.
        log.info("%s %s %s %.0fms rid=%s", request.method, getattr(route, "path", "-"), response.status_code,
                 (time.monotonic() - t0) * 1000, request.state.request_id)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        if not (request.url.path.startswith(PUBLIC_PREFIX) and response.status_code == 200):
            response.headers["Cache-Control"] = "no-store"  # public aggregates keep their own "public, max-age=60"
        response.headers["Permissions-Policy"] = "microphone=(self), camera=(), geolocation=()"
        response.headers["Content-Security-Policy"] = ("default-src 'none'; frame-ancestors 'none'" if
                                                       not request.url.path.startswith("/api/docs") else
                                                       "default-src 'self' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; "
                                                       "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net")
        if s.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    install_handlers(app)
    v1 = APIRouter(prefix="/api/v1")
    for r in (auth.router, auth.me_router, geo.router, registry.router, alerts.router, voice.router, admin.router,
              live.router, live_records.router, account.router, public.router, console.router, facility.admin_router, facility.router):
        v1.include_router(r)
    app.include_router(v1)

    @app.get("/health", tags=["ops"])
    def health():
        return {"status": "ok", "env": s.APP_ENV}

    return app


app = create_app()
