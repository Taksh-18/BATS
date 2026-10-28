"""Blood-unit lifecycle (FR6): expired units are marked Expired, units about to expire are
quarantined so they can't be allocated, and stale issuance requests are closed."""
import asyncio
import logging
from datetime import timedelta

from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import SessionLocal
from app.models import BloodUnit, IssuanceRequest
from app.services import audit
from app.timeutil import today

log = logging.getLogger(__name__)


async def sweep(db: AsyncSession) -> tuple[int, int]:
    """Returns (units expired, units quarantined). Single atomic UPDATEs, so a concurrent issuance
    that already holds a unit's row lock simply finishes first."""
    day = today()
    expired = (await db.execute(
        update(BloodUnit).where(BloodUnit.status.in_(("Available", "Reserved", "Quarantined")),
                                BloodUnit.expiry_date <= day)
        .values(status="Expired").returning(BloodUnit.unit_id)
    )).scalars().all()
    quarantined = (await db.execute(
        update(BloodUnit).where(BloodUnit.status == "Available",
                                BloodUnit.expiry_date <= day + timedelta(days=settings.quarantine_days_before_expiry))
        .values(status="Quarantined").returning(BloodUnit.unit_id)
    )).scalars().all()
    await db.execute(
        update(IssuanceRequest).where(IssuanceRequest.status == "Pending", IssuanceRequest.expires_at < func.now())
        .values(status="Expired")
    )
    if expired or quarantined:
        await audit.record(db, None, "system", "EXPIRY_SWEEP",
                           f"{len(expired)} unit(s) marked Expired {sorted(expired)}; "
                           f"{len(quarantined)} unit(s) quarantined as nearing expiry {sorted(quarantined)}")
    await db.commit()
    return len(expired), len(quarantined)


async def worker() -> None:
    while True:
        try:
            async with SessionLocal() as db:
                await sweep(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("expiry sweep failed")
        await asyncio.sleep(settings.expiry_sweep_interval_seconds)
