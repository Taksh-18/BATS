from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.deps import current_staff
from app.models import BloodIssue, BloodRequisition, BloodUnit, HospitalStaff
from app.schemas import IssueListItem, IssueRead, IssueRequest, IssueResponse
from app.services.issuance import IssuanceError, issue_blood_unit

router = APIRouter(prefix="/api/v1/issuances", tags=["issuance"])


@router.post("", response_model=IssueResponse, status_code=201)
async def create_issuance(body: IssueRequest, db: AsyncSession = Depends(get_db),
                          me: HospitalStaff = Depends(current_staff)) -> IssueResponse:
    try:
        result = await issue_blood_unit(db, me, body)
    except IssuanceError as exc:
        raise HTTPException(status_code=exc.status_code,
                            detail={"rule": exc.rule, "message": exc.message}) from exc
    return IssueResponse(
        **IssueRead.model_validate(result.issue).model_dump(),
        requisition_status=result.requisition_status,
        hms_synced=result.hms_synced,
    )


@router.get("", response_model=list[IssueListItem])
async def list_issuances(limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db),
                         me: HospitalStaff = Depends(current_staff)):
    issuer, verifier = aliased(HospitalStaff), aliased(HospitalStaff)
    rows = (await db.execute(
        select(BloodIssue, BloodUnit.blood_type, issuer, verifier)
        .join(BloodUnit, BloodUnit.unit_id == BloodIssue.unit_id)
        .join(BloodRequisition, BloodRequisition.requisition_id == BloodIssue.requisition_id)
        .join(issuer, issuer.staff_id == BloodIssue.issued_by_staff_id)
        .join(verifier, verifier.staff_id == BloodIssue.verified_by_staff_id)
        .where(BloodRequisition.hospital_id == me.hospital_id)
        .order_by(BloodIssue.issue_id.desc()).limit(limit)
    )).all()
    return [
        IssueListItem(
            issue_id=i.issue_id, unit_id=i.unit_id, requisition_id=i.requisition_id, blood_type=bt,
            issuer_username=a.username, issuer_name=a.full_name,
            verifier_username=v.username, verifier_name=v.full_name, issue_date=i.issue_date,
        )
        for i, bt, a, v in rows
    ]
