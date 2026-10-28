from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_staff
from app.models import BloodIssue, BloodRequisition, BloodUnit, HmsOutbox, HospitalStaff
from app.schemas import BloodType, BloodUnitRead, StatsRead, UnitPage, UnitStatus
from app.services import audit

router = APIRouter(prefix="/api/v1", tags=["inventory"], dependencies=[Depends(current_staff)])

EXPIRING_SOON_DAYS = 7
BLOOD_TYPES = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]
Component = Literal["Whole Blood", "Plasma", "Platelets", "Packed RBC"]


@router.get("/units", response_model=UnitPage)
async def list_units(
    blood_type: BloodType | None = None,
    component: Component | None = None,
    status: UnitStatus | None = None,
    q: str | None = Query(None, max_length=50),
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Network-wide stock (FR2), soonest expiry first, filtered and paginated on the server."""
    conds = []
    if blood_type:
        conds.append(BloodUnit.blood_type == blood_type.value)
    if component:
        conds.append(BloodUnit.component == component)
    if status:
        conds.append(BloodUnit.status == status.value)
    if q and q.strip():
        term = q.strip()
        if term.lower().startswith("unit-"):  # "UNIT-6" is an exact unit lookup, not a substring search
            conds.append(cast(BloodUnit.unit_id, String) == term[5:].strip())
        else:
            like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            conds.append(or_(
                cast(BloodUnit.unit_id, String).ilike(like, escape="\\"),
                BloodUnit.batch_number.ilike(like, escape="\\"),
                BloodUnit.blood_type.ilike(like, escape="\\"),
                BloodUnit.component.ilike(like, escape="\\"),
            ))

    total = await db.scalar(select(func.count()).select_from(BloodUnit).where(*conds))
    items = (await db.execute(
        select(BloodUnit).where(*conds)
        .order_by(BloodUnit.expiry_date, BloodUnit.unit_id).limit(limit).offset(offset)
    )).scalars().all()
    return UnitPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/units/issuable", response_model=list[BloodUnitRead])
async def issuable_units(blood_type: BloodType | None = None, db: AsyncSession = Depends(get_db)):
    """Units that could be issued right now (available and not expired), for the issuance form."""
    conds = [BloodUnit.status == "Available", BloodUnit.expiry_date >= date.today()]
    if blood_type:
        conds.append(BloodUnit.blood_type == blood_type.value)
    return (await db.execute(
        select(BloodUnit).where(*conds).order_by(BloodUnit.expiry_date, BloodUnit.unit_id).limit(100)
    )).scalars().all()


@router.get("/stats", response_model=StatsRead)
async def stats(db: AsyncSession = Depends(get_db), me: HospitalStaff = Depends(current_staff)):
    today = date.today()
    count = lambda q: db.scalar(q)  # noqa: E731
    by_type = dict((await db.execute(
        select(BloodUnit.blood_type, func.count()).where(BloodUnit.status == "Available")
        .group_by(BloodUnit.blood_type)
    )).all())
    open_reqs = BloodRequisition.hospital_id == me.hospital_id, \
        BloodRequisition.status.in_(("Pending", "Partially Fulfilled"))
    return StatsRead(
        total_units=await count(select(func.count()).select_from(BloodUnit)),
        available_units=await count(
            select(func.count()).select_from(BloodUnit).where(BloodUnit.status == "Available")),
        active_requisitions=await count(select(func.count()).select_from(BloodRequisition).where(*open_reqs)),
        critical_requisitions=await count(
            select(func.count()).select_from(BloodRequisition)
            .where(*open_reqs, BloodRequisition.urgency_level == "Critical")),
        issuances=await count(
            select(func.count()).select_from(BloodIssue)
            .join(BloodRequisition, BloodRequisition.requisition_id == BloodIssue.requisition_id)
            .where(BloodRequisition.hospital_id == me.hospital_id)),
        expiring_soon=await count(
            select(func.count()).select_from(BloodUnit).where(
                BloodUnit.status.in_(("Available", "Quarantined")),
                BloodUnit.expiry_date.between(today, today + timedelta(days=EXPIRING_SOON_DAYS)))),
        available_by_type={t: by_type.get(t, 0) for t in BLOOD_TYPES},
        hms_pending=await count(
            select(func.count()).select_from(HmsOutbox)
            .where(HmsOutbox.hospital_id == me.hospital_id, HmsOutbox.status == "Pending")),
        audit_chain_valid=audit.last_check["valid"],
    )
