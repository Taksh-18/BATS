from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_staff
from app.models import BloodIssue, BloodRequisition, BloodUnit, HmsOutbox, HospitalStaff
from app.schemas import BloodType, BloodUnitRead, IssuableUnit, StatsRead, UnitPage, UnitStatus
from app.services import audit
from app.services.compat import acceptable_types
from app.timeutil import today

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


@router.get("/units/issuable", response_model=list[IssuableUnit])
async def issuable_units(requisition_id: int = Query(...), db: AsyncSession = Depends(get_db),
                         me: HospitalStaff = Depends(current_staff)):
    """Units that could be issued for this requisition right now: available, not expiring today,
    and blood-group compatible. Exact matches first, then (Critical only) compatible substitutes."""
    req = await db.get(BloodRequisition, requisition_id)
    if req is None or req.hospital_id != me.hospital_id:
        raise HTTPException(status_code=404, detail="Requisition not found")
    units = (await db.execute(
        select(BloodUnit).where(BloodUnit.status == "Available", BloodUnit.expiry_date > today())
        .order_by(BloodUnit.expiry_date, BloodUnit.unit_id)
    )).scalars().all()
    ok = [u for u in units if u.blood_type in acceptable_types(req.blood_type_requested, u.component, req.urgency_level)]
    ok.sort(key=lambda u: u.blood_type != req.blood_type_requested)  # stable: exact matches first
    return [IssuableUnit(**BloodUnitRead.model_validate(u).model_dump(),
                         exact_match=u.blood_type == req.blood_type_requested) for u in ok[:100]]


@router.get("/stats", response_model=StatsRead)
async def stats(db: AsyncSession = Depends(get_db), me: HospitalStaff = Depends(current_staff)):
    day = today()
    live = BloodUnit.status == "Available", BloodUnit.expiry_date > day  # expired units are not stock
    count = lambda q: db.scalar(q)  # noqa: E731
    by_type = dict((await db.execute(
        select(BloodUnit.blood_type, func.count()).where(*live)
        .group_by(BloodUnit.blood_type)
    )).all())
    open_reqs = BloodRequisition.hospital_id == me.hospital_id, \
        BloodRequisition.status.in_(("Pending", "Partially Fulfilled"))
    return StatsRead(
        total_units=await count(select(func.count()).select_from(BloodUnit)),
        available_units=await count(
            select(func.count()).select_from(BloodUnit).where(*live)),
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
                BloodUnit.expiry_date > day, BloodUnit.expiry_date <= day + timedelta(days=EXPIRING_SOON_DAYS))),
        available_by_type={t: by_type.get(t, 0) for t in BLOOD_TYPES},
        hms_pending=await count(
            select(func.count()).select_from(HmsOutbox)
            .where(HmsOutbox.hospital_id == me.hospital_id, HmsOutbox.status == "Pending")),
        audit_chain_valid=audit.last_check["valid"],
    )
