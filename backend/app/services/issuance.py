from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import BloodIssue, BloodRequisition, BloodUnit, HospitalStaff
from app.schemas import HMSIssueEvent, IssueRequest
from app.security import verify_password
from app.services import audit, hms_outbox
from app.services.ratelimit import FailureLimiter

OPEN_REQUISITION_STATES = ("Pending", "Partially Fulfilled")

# Guessing a colleague's password through the verifier form is still password guessing.
verifier_limiter = FailureLimiter(max_failures=5, window_seconds=15 * 60)


class IssuanceError(Exception):
    status_code = 409
    log_blocked = True  # write an ISSUANCE_BLOCKED audit entry for this failure

    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule
        self.message = message


class NotFoundError(IssuanceError):
    status_code = 404


class VerifierAuthError(IssuanceError):
    # 403, not 401: the issuer's session is fine, so the client must not log them out.
    status_code = 403


class TooManyAttemptsError(IssuanceError):
    status_code = 429
    log_blocked = False  # already logged when the lockout began; don't let spam flood the log


@dataclass
class IssueResult:
    issue: BloodIssue
    requisition_status: str
    hms_synced: bool


async def _get_locked_or_404(db: AsyncSession, model, pk: int, label: str):
    pk_col = model.__mapper__.primary_key[0]
    row = (await db.execute(select(model).where(pk_col == pk).with_for_update())).scalar_one_or_none()
    if row is None:
        raise NotFoundError("not_found", f"{label} {pk} not found")
    return row


async def issue_blood_unit(db: AsyncSession, issuer: HospitalStaff, req: IssueRequest) -> IssueResult:
    # Capture now: a rollback expires ORM attributes, and we still need them to log the failure.
    issuer_id, issuer_username = issuer.staff_id, issuer.username
    try:
        return await _issue(db, issuer, req)
    except IssuanceError as exc:
        await db.rollback()
        if exc.log_blocked:
            await audit.record(db, issuer_id, issuer_username, "ISSUANCE_BLOCKED",
                               f"unit {req.unit_id} for requisition {req.requisition_id}: {exc.rule}")
            await db.commit()
        raise


async def _issue(db: AsyncSession, issuer: HospitalStaff, req: IssueRequest) -> IssueResult:
    issuer_id, issuer_username = issuer.staff_id, issuer.username  # survive a mid-flow rollback
    limiter_key = f"{issuer_id}:{req.verifier_username.lower()}"
    wait = verifier_limiter.retry_after(limiter_key)
    if wait:
        raise TooManyAttemptsError("too_many_attempts",
                                   f"Too many failed verifier sign-ins. Try again in {(wait + 59) // 60} min.")

    # Lock the unit and requisition rows so concurrent issues of the same unit serialize.
    unit: BloodUnit = await _get_locked_or_404(db, BloodUnit, req.unit_id, "Blood unit")
    requisition: BloodRequisition = await _get_locked_or_404(
        db, BloodRequisition, req.requisition_id, "Requisition")

    # The verifier must prove who they are; a staff ID typed into a form proves nothing.
    verifier = (await db.execute(
        select(HospitalStaff).where(HospitalStaff.username == req.verifier_username)
    )).scalar_one_or_none()
    if not await verify_password(req.verifier_password, verifier.password_hash if verifier else None):
        locked_now = verifier_limiter.fail(limiter_key)
        if locked_now:  # one audit line when the lockout starts, none for the attempts after it
            await db.rollback()
            await audit.record(db, issuer_id, issuer_username, "VERIFIER_LOCKOUT",
                               f"5 failed verifier sign-ins for '{req.verifier_username[:50]}'; locked 15 min")
            await db.commit()
        raise VerifierAuthError("verifier_auth_failed", "Verifier username or password is incorrect")
    verifier_limiter.reset(limiter_key)

    # Rule 1: unit must be Available
    if unit.status != "Available":
        raise IssuanceError("unit_unavailable", f"Unit {unit.unit_id} is {unit.status}, not Available")

    # Rule 2: unit must not be expired
    if unit.expiry_date < date.today():
        raise IssuanceError("unit_expired", f"Unit {unit.unit_id} expired on {unit.expiry_date}")

    # Rule 3: verifier must be a different person from the issuer
    if issuer.staff_id == verifier.staff_id:
        raise IssuanceError("same_staff", "Issuer and verifier must be two different staff members")

    # Rule 4: verifier must explicitly confirm
    if not req.verifier_confirmed:
        raise IssuanceError("verification_declined", "Verifier did not confirm the issuance")

    # Context checks that protect the requisition (beyond the five core rules)
    if requisition.status not in OPEN_REQUISITION_STATES:
        raise IssuanceError("requisition_closed", f"Requisition is {requisition.status}")
    if unit.blood_type != requisition.blood_type_requested:
        raise IssuanceError("blood_type_mismatch",
                            f"Unit is {unit.blood_type}, requisition needs {requisition.blood_type_requested}")
    if issuer.hospital_id != requisition.hospital_id or verifier.hospital_id != requisition.hospital_id:
        raise IssuanceError("wrong_hospital", "Issuer and verifier must belong to the requesting hospital")

    # Rule 5: update status, record the issuance, its audit entry and its HMS event atomically
    unit.status = "Issued"
    issue = BloodIssue(
        requisition_id=requisition.requisition_id,
        unit_id=unit.unit_id,
        issued_by_staff_id=issuer.staff_id,
        verified_by_staff_id=verifier.staff_id,
    )
    db.add(issue)
    try:
        await db.flush()  # DB constraints (UNIQUE unit_id, two-person CHECK) are the last line of defence
    except IntegrityError as exc:
        raise IssuanceError("constraint_violation", "Issuance rejected by database constraints") from exc

    issued_count = (await db.execute(
        select(func.count()).select_from(BloodIssue)
        .where(BloodIssue.requisition_id == requisition.requisition_id, BloodIssue.returned_at.is_(None))
    )).scalar_one()
    requisition.status = "Fulfilled" if issued_count >= requisition.quantity else "Partially Fulfilled"
    await audit.record(db, issuer.staff_id, issuer.username, "BLOOD_ISSUED_DUAL_VERIFIED",
                       f"unit {unit.unit_id} ({unit.blood_type}) for requisition {requisition.requisition_id}; "
                       f"issuer {issuer.username}, verifier {verifier.username}")
    outbox = hms_outbox.enqueue(db, issue.issue_id, requisition.hospital_id, HMSIssueEvent(
        issue_id=issue.issue_id,
        unit_id=unit.unit_id,
        requisition_id=requisition.requisition_id,
        hospital_id=requisition.hospital_id,
        blood_type=unit.blood_type,
        issued_by_staff_id=issuer.staff_id,
        verified_by_staff_id=verifier.staff_id,
        issued_at=issue.issue_date,
    ))
    await db.flush()
    outbox_id, status = outbox.outbox_id, requisition.status
    await db.commit()  # issuance, audit entry and queued HMS event are durable together

    # First delivery attempt happens now; if the HMS is down the background worker keeps retrying.
    synced = await hms_outbox.deliver(db, outbox_id)
    return IssueResult(issue=issue, requisition_status=status, hms_synced=synced)
