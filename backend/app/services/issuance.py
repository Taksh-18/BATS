"""Two-person issuance.

One staff member REQUESTS a unit for a requisition. A DIFFERENT staff member, signed in on their
own device, reviews and APPROVES it (re-entering their own password as a step-up check). Only the
approval changes stock. Nobody ever types a colleague's password into their own session.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import BloodIssue, BloodRequisition, BloodUnit, HospitalStaff, IssuanceRequest
from app.schemas import ApproveBody, HMSIssueEvent
from app.security import verify_password
from app.services import audit, hms_outbox
from app.services.compat import acceptable_types
from app.services.ratelimit import FailureLimiter
from app.timeutil import today

OPEN_REQUISITION_STATES = ("Pending", "Partially Fulfilled")

# Re-entering a password to approve is still a password prompt, so guessing is limited.
stepup_limiter = FailureLimiter(max_failures=5, window_seconds=15 * 60)


class IssuanceError(Exception):
    status_code = 409
    log_blocked = True  # write an ISSUANCE_BLOCKED audit entry for this failure

    def __init__(self, rule: str, message: str, mark_request: str | None = None):
        super().__init__(message)
        self.rule = rule
        self.message = message
        self.mark_request = mark_request  # close the request with this status, if it is now hopeless


class NotFoundError(IssuanceError):
    status_code = 404


class ForbiddenError(IssuanceError):
    # 403, not 401: the user's session is fine, so the client must not log them out.
    status_code = 403


class TooManyAttemptsError(IssuanceError):
    status_code = 429
    log_blocked = False  # the lockout itself was logged; don't let spam flood the log


@dataclass
class IssueResult:
    issue: BloodIssue
    requisition_status: str
    hms_synced: bool


async def _lock(db: AsyncSession, model, pk: int, label: str):
    pk_col = model.__mapper__.primary_key[0]
    row = (await db.execute(select(model).where(pk_col == pk).with_for_update())).scalar_one_or_none()
    if row is None:
        raise NotFoundError("not_found", f"{label} {pk} not found")
    return row


def _precheck(unit: BloodUnit, requisition: BloodRequisition, requester: HospitalStaff) -> None:
    """The conditions that must hold both when a request is made and again when it is approved."""
    # Rule 1: unit must be Available
    if unit.status != "Available":
        raise IssuanceError("unit_unavailable", f"Unit {unit.unit_id} is {unit.status}, not Available", "Failed")
    # Rule 2: unit must not be expired. A unit expiring today is already unusable (conservative).
    if unit.expiry_date <= today():
        raise IssuanceError("unit_expired", f"Unit {unit.unit_id} expires {unit.expiry_date} and can't be issued", "Failed")
    if requisition.status not in OPEN_REQUISITION_STATES:
        raise IssuanceError("requisition_closed", f"Requisition is {requisition.status}", "Failed")
    if unit.blood_type not in acceptable_types(requisition.blood_type_requested, unit.component,
                                               requisition.urgency_level):
        raise IssuanceError("blood_type_mismatch",
                            f"{unit.blood_type} {unit.component} can't be given for a {requisition.blood_type_requested} "
                            f"{requisition.urgency_level} requisition", "Failed")
    if requester.hospital_id != requisition.hospital_id:
        raise IssuanceError("wrong_hospital", "The requester must belong to the requesting hospital", "Failed")


async def _guarded(db: AsyncSession, actor_id: int, actor_username: str, what: str,
                   request_id: int | None, fn):
    """Runs fn; on a rule failure rolls back, closes the request if hopeless, and audit-logs it."""
    try:
        return await fn()
    except IssuanceError as exc:
        await db.rollback()
        if request_id and exc.mark_request:
            await db.execute(
                update(IssuanceRequest)
                .where(IssuanceRequest.request_id == request_id, IssuanceRequest.status == "Pending")
                .values(status=exc.mark_request, decided_at=func.now(),
                        failure_rule=exc.rule if exc.mark_request == "Failed" else None))
        if exc.log_blocked:
            await audit.record(db, actor_id, actor_username, "ISSUANCE_BLOCKED", f"{what}: {exc.rule}")
        await db.commit()
        raise


# ---------------------------------------------------------------- request
async def create_request(db: AsyncSession, requester: HospitalStaff, unit_id: int, requisition_id: int) -> IssuanceRequest:
    rid, rname = requester.staff_id, requester.username  # survive a rollback

    async def run() -> IssuanceRequest:
        unit = await db.get(BloodUnit, unit_id)
        requisition = await db.get(BloodRequisition, requisition_id)
        if unit is None or requisition is None:
            raise NotFoundError("not_found", "Unit or requisition not found")
        _precheck(unit, requisition, requester)

        # free the unit if an old pending request for it has simply timed out
        await db.execute(update(IssuanceRequest)
                         .where(IssuanceRequest.unit_id == unit_id, IssuanceRequest.status == "Pending",
                                IssuanceRequest.expires_at < func.now()).values(status="Expired"))
        req = IssuanceRequest(
            unit_id=unit_id, requisition_id=requisition_id, requested_by_staff_id=requester.staff_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=settings.issuance_request_ttl_minutes))
        db.add(req)
        try:
            await db.flush()
        except IntegrityError as exc:
            raise IssuanceError("already_requested", f"Unit {unit_id} already has a pending request") from exc
        await audit.record(db, requester.staff_id, requester.username, "ISSUANCE_REQUESTED",
                           f"request {req.request_id}: unit {unit_id} ({unit.blood_type}) for requisition "
                           f"{requisition_id}; waiting for a second staff member")
        await db.commit()
        return req

    return await _guarded(db, rid, rname, f"request for unit {unit_id} / requisition {requisition_id}", None, run)


# ---------------------------------------------------------------- approve
async def approve(db: AsyncSession, verifier: HospitalStaff, request_id: int, body: ApproveBody) -> IssueResult:
    vid, vname = verifier.staff_id, verifier.username
    return await _guarded(db, vid, vname, f"approval of request {request_id}", request_id,
                          lambda: _approve(db, verifier, vid, vname, request_id, body))


async def _approve(db: AsyncSession, verifier: HospitalStaff, vid: int, vname: str,
                   request_id: int, body: ApproveBody) -> IssueResult:
    req: IssuanceRequest = await _lock(db, IssuanceRequest, request_id, "Request")
    if req.status != "Pending":
        raise IssuanceError("request_not_pending", f"This request is already {req.status}")
    if req.expires_at < datetime.now(timezone.utc):
        raise IssuanceError("request_expired", "This request timed out; the issuer must raise a new one", "Expired")
    requester: HospitalStaff = await db.get(HospitalStaff, req.requested_by_staff_id)

    # Rule 3: approver must be a different person (checked first: it needs no password or DB state)
    if requester.staff_id == vid:
        raise IssuanceError("same_staff", "A second staff member must approve; you raised this request")

    key = str(vid)
    wait = stepup_limiter.retry_after(key)
    if wait:
        raise TooManyAttemptsError("too_many_attempts", f"Too many wrong passwords. Try again in {(wait + 59) // 60} min.")
    if not await verify_password(body.password, verifier.password_hash):
        if stepup_limiter.fail(key):  # one audit line when the lockout starts, none after it
            await db.rollback()
            await audit.record(db, vid, vname, "APPROVAL_LOCKOUT", "5 wrong passwords on approval; locked 15 min")
            await db.commit()
        raise ForbiddenError("password_incorrect", "Your password is incorrect")
    stepup_limiter.reset(key)

    unit: BloodUnit = await _lock(db, BloodUnit, req.unit_id, "Blood unit")
    requisition: BloodRequisition = await _lock(db, BloodRequisition, req.requisition_id, "Requisition")
    _precheck(unit, requisition, requester)  # rules 1 and 2 again: things may have changed since the request
    if verifier.hospital_id != requisition.hospital_id:
        raise IssuanceError("wrong_hospital", "The approver must belong to the requesting hospital")
    # Rule 4: approver must explicitly confirm
    if not body.confirmed:
        raise IssuanceError("verification_declined", "Tick the confirmation box to approve")

    # Rule 5: update status, record the issuance, its audit entry and its HMS event atomically
    unit.status = "Issued"
    issue = BloodIssue(requisition_id=requisition.requisition_id, unit_id=unit.unit_id,
                       issued_by_staff_id=requester.staff_id, verified_by_staff_id=vid)
    db.add(issue)
    try:
        await db.flush()  # DB constraints (UNIQUE unit_id, two-person CHECK) are the last line of defence
    except IntegrityError as exc:
        raise IssuanceError("constraint_violation", "Issuance rejected by database constraints", "Failed") from exc

    issued_count = (await db.execute(
        select(func.count()).select_from(BloodIssue)
        .where(BloodIssue.requisition_id == requisition.requisition_id, BloodIssue.returned_at.is_(None))
    )).scalar_one()
    requisition.status = "Fulfilled" if issued_count >= requisition.quantity else "Partially Fulfilled"
    req.status, req.decided_by_staff_id, req.decided_at = "Approved", vid, datetime.now(timezone.utc)
    await audit.record(db, vid, vname, "BLOOD_ISSUED_DUAL_VERIFIED",
                       f"unit {unit.unit_id} ({unit.blood_type}) for requisition {requisition.requisition_id}; "
                       f"requested by {requester.username}, approved by {vname} (request {req.request_id})")
    outbox = hms_outbox.enqueue(db, issue.issue_id, requisition.hospital_id, HMSIssueEvent(
        issue_id=issue.issue_id, unit_id=unit.unit_id, requisition_id=requisition.requisition_id,
        hospital_id=requisition.hospital_id, blood_type=unit.blood_type,
        issued_by_staff_id=requester.staff_id, verified_by_staff_id=vid, issued_at=issue.issue_date))
    await db.flush()
    outbox_id, status = outbox.outbox_id, requisition.status
    await db.commit()  # issuance, audit entry and queued HMS event are durable together

    # First delivery attempt happens now; if the HMS is down the background worker keeps retrying.
    synced = await hms_outbox.deliver(db, outbox_id)
    return IssueResult(issue=issue, requisition_status=status, hms_synced=synced)


# ---------------------------------------------------------- decline / cancel
async def decline(db: AsyncSession, decider: HospitalStaff, request_id: int) -> IssuanceRequest:
    did, dname = decider.staff_id, decider.username

    async def run() -> IssuanceRequest:
        req: IssuanceRequest = await _lock(db, IssuanceRequest, request_id, "Request")
        if req.status != "Pending":
            raise IssuanceError("request_not_pending", f"This request is already {req.status}")
        if req.requested_by_staff_id == did:
            raise IssuanceError("same_staff", "Use Cancel for your own request")
        req.status, req.decided_by_staff_id, req.decided_at = "Declined", did, datetime.now(timezone.utc)
        await audit.record(db, did, dname, "ISSUANCE_DECLINED", f"request {request_id} (unit {req.unit_id}) declined")
        await db.commit()
        return req

    return await _guarded(db, did, dname, f"decline of request {request_id}", None, run)


async def cancel(db: AsyncSession, user: HospitalStaff, request_id: int) -> IssuanceRequest:
    uid, uname = user.staff_id, user.username

    async def run() -> IssuanceRequest:
        req: IssuanceRequest = await _lock(db, IssuanceRequest, request_id, "Request")
        if req.requested_by_staff_id != uid:
            raise ForbiddenError("not_your_request", "Only the person who raised a request can cancel it")
        if req.status != "Pending":
            raise IssuanceError("request_not_pending", f"This request is already {req.status}")
        req.status, req.decided_at = "Cancelled", datetime.now(timezone.utc)
        await audit.record(db, uid, uname, "ISSUANCE_CANCELLED", f"request {request_id} (unit {req.unit_id}) cancelled")
        await db.commit()
        return req

    return await _guarded(db, uid, uname, f"cancel of request {request_id}", None, run)
