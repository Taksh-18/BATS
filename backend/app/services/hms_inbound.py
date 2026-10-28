"""Applies updates the hospital HMS sends back to BATS (the 'return' half of the two-way sync)."""
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BloodIssue, BloodRequisition, BloodUnit, HmsInbound
from app.schemas import HMSInboundEvent
from app.services import audit


class InboundError(Exception):
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


async def apply(db: AsyncSession, ev: HMSInboundEvent) -> str:
    """Returns "applied" or "duplicate". Raises InboundError if the update makes no sense."""
    # Claim the event key first: a replay hits the conflict and changes nothing.
    claimed = (await db.execute(
        pg_insert(HmsInbound)
        .values(inbound_key=ev.event_ref, unit_id=ev.unit_id, action=ev.action)
        .on_conflict_do_nothing(index_elements=["inbound_key"])
        .returning(HmsInbound.inbound_key)
    )).scalar_one_or_none()
    if claimed is None:
        await db.rollback()
        return "duplicate"

    # (the FK on hms_inbound.unit_id already rejected unknown units with an IntegrityError above)
    unit = (await db.execute(
        select(BloodUnit).where(BloodUnit.unit_id == ev.unit_id).with_for_update()
    )).scalar_one()
    if unit.status != "Issued":
        raise InboundError(409, f"Unit {unit.unit_id} is {unit.status}; only an Issued unit can be {ev.action[5:]}")

    if ev.action == "unit_transfused":
        unit.status = "Transfused"
        await audit.record(db, None, "hms", "HMS_TRANSFUSED",
                           f"hospital HMS reports unit {unit.unit_id} ({unit.blood_type}) transfused")
    else:
        issue = (await db.execute(
            select(BloodIssue).where(BloodIssue.unit_id == unit.unit_id)
        )).scalar_one()
        issue.returned_at = datetime.now(timezone.utc)
        # A returned bag is never put straight back on the shelf: it must be re-inspected.
        unit.status = "Quarantined"

        requisition = (await db.execute(
            select(BloodRequisition).where(BloodRequisition.requisition_id == issue.requisition_id).with_for_update()
        )).scalar_one()
        kept = (await db.execute(
            select(func.count()).select_from(BloodIssue)
            .where(BloodIssue.requisition_id == requisition.requisition_id, BloodIssue.returned_at.is_(None))
        )).scalar_one()
        if requisition.status != "Cancelled":
            requisition.status = ("Fulfilled" if kept >= requisition.quantity
                                  else "Partially Fulfilled" if kept > 0 else "Pending")
        cold = "cold chain intact" if ev.cold_chain_ok else "COLD CHAIN BROKEN"
        await audit.record(db, None, "hms", "HMS_RETURNED",
                           f"hospital HMS returned unit {unit.unit_id} ({unit.blood_type}), {cold}; "
                           f"quarantined for inspection, requisition {requisition.requisition_id} now {requisition.status}")
    await db.commit()
    return "applied"
