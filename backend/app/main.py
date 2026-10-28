import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal, engine, get_db
from app.services import audit as audit_service
from app.services import hms_outbox
from app.routers import audit, auth, hms_inbound, inventory, issuance, mock_hms, requisitions


log = logging.getLogger(__name__)


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
    tasks = [asyncio.create_task(_audit_monitor()),
             asyncio.create_task(hms_outbox.worker(settings.hms_retry_interval_seconds))]
    yield
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await engine.dispose()


app = FastAPI(
    title="Blood Availability Tracking System (BATS)",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth.router, inventory.router, requisitions.router, issuance.router, audit.router, hms_inbound.router):
    app.include_router(r)
if settings.enable_mock_hms:  # unauthenticated simulation; never enable in production
    app.include_router(mock_hms.router)


@app.get("/health", tags=["system"])
async def health(db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    await db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "connected"}
