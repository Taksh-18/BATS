import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal, engine, get_db
from app.routers import audit, auth, hms_inbound, inventory, issuance, mock_hms, requisitions
from app.services import audit as audit_service
from app.services import expiry, hms_outbox

log = logging.getLogger(__name__)
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"

# Scripts run only from this origin (Tailwind is self-hosted in frontend/vendor); the one third-party
# asset is Font Awesome's stylesheet and fonts, pinned to a version and integrity-checked in the HTML.
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com",  # Tailwind's runtime injects <style>
    "font-src https://cdnjs.cloudflare.com",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


async def _audit_monitor() -> None:
    """Verify the audit chain on startup and periodically, so tampering is noticed without anyone asking."""
    while True:
        try:
            async with SessionLocal() as db:
                await audit_service.run_check(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("audit verification failed to run")
        await asyncio.sleep(settings.audit_verify_interval_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tasks = [asyncio.create_task(expiry.worker()),  # first, so stock is correct before anything reads it
             asyncio.create_task(_audit_monitor()),
             asyncio.create_task(hms_outbox.worker(settings.hms_retry_interval_seconds))]
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await engine.dispose()


app = FastAPI(
    title="Blood Availability Tracking System (BATS)",
    version="0.2.0",
    lifespan=lifespan,
    # The API description is a map for attackers: only published when explicitly enabled.
    docs_url="/docs" if settings.enable_docs else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.enable_docs else None,
)

if settings.cors_origins:  # only needed if the frontend is ever hosted on a different origin
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path not in ("/docs", "/openapi.json"):  # Swagger UI needs its own CDN assets
        response.headers["Content-Security-Policy"] = CSP
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if settings.enable_hsts:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


for r in (auth.router, inventory.router, requisitions.router, issuance.router, audit.router, hms_inbound.router):
    app.include_router(r)
if settings.enable_mock_hms:  # unauthenticated simulation; never enable in production
    app.include_router(mock_hms.router)


@app.get("/health", tags=["system"])
async def health(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}


# Registered last so every API route above wins; serves the web app from the same origin as the API.
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
