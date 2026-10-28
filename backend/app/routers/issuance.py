from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.database import get_db
from app.deps import current_staff, require
from app.models import BloodIssue, BloodRequisition, BloodUnit, HospitalStaff, IssuanceRequest
from app.schemas import (ApproveBody, IssuanceRequestCreate, IssuanceRequestRead, IssueListItem, IssueRead,
                         IssueResponse)
from app.services import issuance
from app.services.issuance import IssuanceError

router = APIRouter(prefix="/api/v1", tags=["issuance"])

handler = Depends(require("issuance:handle"))


def _http(exc: IssuanceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail={"rule": exc.rule, "message": exc.message})


async def _view(db: AsyncSession, req: IssuanceRequest) -> IssuanceRequestRead:
    unit = await db.get(BloodUnit, req.unit_id)
    who = await db.get(HospitalStaff, req.requested_by_staff_id)
    return IssuanceRequestRead(
        request_id=req.request_id, unit_id=req.unit_id, requisition_id=req.requisition_id,
        blood_type=unit.blood_type, component=unit.component, requested_by_staff_id=who.staff_id,
        requested_by_name=who.full_name, status=req.status, created_at=req.created_at, expires_at=req.expires_at)


@router.post("/issuance-requests", response_model=IssuanceRequestRead, status_code=201)
async def create_request(body: IssuanceRequestCreate, db: AsyncSession = Depends(get_db),
                         me: HospitalStaff = handler) -> IssuanceRequestRead:
    try:
        req = await issuance.create_request(db, me, body.unit_id, body.requisition_id)
    except IssuanceError as exc:
        raise _http(exc) from exc
    return await _view(db, req)


@router.get("/issuance-requests", response_model=list[IssuanceRequestRead])
async def list_requests(db: AsyncSession = Depends(get_db), me: HospitalStaff = handler):
    """Open requests raised at my hospital (mine and my colleagues')."""
    rows = (await db.execute(
        select(IssuanceRequest)
        .join(HospitalStaff, HospitalStaff.staff_id == IssuanceRequest.requested_by_staff_id)
        .where(IssuanceRequest.status == "Pending", IssuanceRequest.expires_at > datetime.now(timezone.utc),
               HospitalStaff.hospital_id == me.hospital_id)
        .order_by(IssuanceRequest.request_id.desc())
    )).scalars().all()
    return [await _view(db, r) for r in rows]


@router.post("/issuance-requests/{request_id}/approve", response_model=IssueResponse)
async def approve_request(request_id: int, body: ApproveBody, db: AsyncSession = Depends(get_db),
                          me: HospitalStaff = handler) -> IssueResponse:
    try:
        result = await issuance.approve(db, me, request_id, body)
    except IssuanceError as exc:
        raise _http(exc) from exc
    return IssueResponse(**IssueRead.model_validate(result.issue).model_dump(),
                         requisition_status=result.requisition_status, hms_synced=result.hms_synced)


@router.post("/issuance-requests/{request_id}/decline", response_model=IssuanceRequestRead)
async def decline_request(request_id: int, db: AsyncSession = Depends(get_db), me: HospitalStaff = handler):
    try:
        return await _view(db, await issuance.decline(db, me, request_id))
    except IssuanceError as exc:
        raise _http(exc) from exc


@router.post("/issuance-requests/{request_id}/cancel", response_model=IssuanceRequestRead)
async def cancel_request(request_id: int, db: AsyncSession = Depends(get_db), me: HospitalStaff = handler):
    try:
        return await _view(db, await issuance.cancel(db, me, request_id))
    except IssuanceError as exc:
        raise _http(exc) from exc


@router.get("/issuances", response_model=list[IssueListItem])
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
