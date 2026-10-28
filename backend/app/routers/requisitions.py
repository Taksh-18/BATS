from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_staff
from app.models import BloodIssue, BloodRequisition, HospitalStaff
from app.schemas import RequisitionCreate, RequisitionListItem, RequisitionRead
from app.services import audit

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])


@router.get("", response_model=list[RequisitionListItem])
async def list_requisitions(limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db),
                            me: HospitalStaff = Depends(current_staff)):
    issued = (select(BloodIssue.requisition_id, func.count().label("n"))
              .where(BloodIssue.returned_at.is_(None))
              .group_by(BloodIssue.requisition_id).subquery())
    rows = (await db.execute(
        select(BloodRequisition, HospitalStaff.full_name, func.coalesce(issued.c.n, 0))
        .join(HospitalStaff, HospitalStaff.staff_id == BloodRequisition.staff_id)
        .outerjoin(issued, issued.c.requisition_id == BloodRequisition.requisition_id)
        .where(BloodRequisition.hospital_id == me.hospital_id)
        .order_by(BloodRequisition.requisition_id.desc()).limit(limit)
    )).all()
    return [
        RequisitionListItem(**RequisitionRead.model_validate(r).model_dump(), staff_name=name, issued_count=n)
        for r, name, n in rows
    ]


@router.post("", response_model=RequisitionRead, status_code=201)
async def create_requisition(body: RequisitionCreate, db: AsyncSession = Depends(get_db),
                             me: HospitalStaff = Depends(current_staff)):
    req = BloodRequisition(
        hospital_id=me.hospital_id, staff_id=me.staff_id,
        blood_type_requested=body.blood_type_requested.value,
        quantity=body.quantity, urgency_level=body.urgency_level.value,
    )
    db.add(req)
    await db.flush()
    await audit.record(db, me.staff_id, me.username, "REQUISITION_RAISED",
                       f"requisition {req.requisition_id}: {body.quantity}x {req.blood_type_requested} "
                       f"({req.urgency_level})")
    await db.commit()
    await db.refresh(req)
    return req
