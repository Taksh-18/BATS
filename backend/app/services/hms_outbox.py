"""Reliable HMS delivery: events are queued in hms_outbox and retried with backoff."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import SessionLocal
from app.models import HmsOutbox, Hospital
from app.schemas import HMSIssueEvent
from app.services import audit, hms

log = logging.getLogger(__name__)

FIRST_RETRY_SECONDS = 30
MAX_BACKOFF_SECONDS = 3600
WORKER_GRACE_SECONDS = 20  # the issuing request tries first; the worker waits this long


def enqueue(db: AsyncSession, issue_id: int, hospital_id: int, event: HMSIssueEvent) -> HmsOutbox:
    row = HmsOutbox(
        issue_id=issue_id, hospital_id=hospital_id, payload=event.model_dump(mode="json"),
        next_attempt_at=datetime.now(timezone.utc) + timedelta(seconds=WORKER_GRACE_SECONDS),
    )
    db.add(row)
    return row


async def deliver(db: AsyncSession, outbox_id: int) -> bool:
    """Try to push one queued event. Returns True if the HMS has it (now or already)."""
    row = (await db.execute(
        select(HmsOutbox).where(HmsOutbox.outbox_id == outbox_id).with_for_update(skip_locked=True)
    )).scalar_one_or_none()
    if row is None:  # another worker holds it
        return False
    if row.status == "Sent":
        await db.rollback()
        return True

    ok = await hms.notify_issue(HMSIssueEvent.model_validate(row.payload))
    now = datetime.now(timezone.utc)
    row.attempts += 1
    hospital = await db.get(Hospital, row.hospital_id)

    if ok:
        row.status, row.sent_at, row.last_error = "Sent", now, None
        still_pending = (await db.execute(
            select(func.count()).select_from(HmsOutbox).where(
                HmsOutbox.hospital_id == row.hospital_id, HmsOutbox.status == "Pending",
                HmsOutbox.outbox_id != row.outbox_id)
        )).scalar_one()
        if not still_pending:
            hospital.hms_sync_status = "Connected"
        suffix = f" after {row.attempts} attempts" if row.attempts > 1 else ""
        await audit.record(db, None, "system", "HMS_SYNC", f"issue {row.issue_id} pushed to hospital HMS{suffix}")
    else:
        row.last_error = "HMS unreachable or rejected the event"
        row.next_attempt_at = now + timedelta(
            seconds=min(FIRST_RETRY_SECONDS * 2 ** (row.attempts - 1), MAX_BACKOFF_SECONDS))
        hospital.hms_sync_status = "Error"
        if row.attempts == 1:  # log the first failure only, so an outage can't flood the audit log
            await audit.record(db, None, "system", "HMS_SYNC_FAILED",
                               f"issue {row.issue_id} not delivered to hospital HMS; retrying automatically")
    await db.commit()
    return ok


async def retry_due() -> int:
    async with SessionLocal() as db:
        ids = (await db.execute(
            select(HmsOutbox.outbox_id)
            .where(HmsOutbox.status == "Pending", HmsOutbox.next_attempt_at <= func.now())
            .order_by(HmsOutbox.next_attempt_at).limit(20)
        )).scalars().all()
    delivered = 0
    for outbox_id in ids:
        async with SessionLocal() as db:
            delivered += await deliver(db, outbox_id)
    return delivered


async def worker(interval_seconds: int) -> None:
    while True:
        try:
            await retry_due()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("HMS retry pass failed")
        await asyncio.sleep(interval_seconds)
