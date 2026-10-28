from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_staff
from app.models import HospitalStaff
from app.schemas import LoginRequest, StaffRead, TokenResponse
from app.security import create_access_token, verify_password
from app.services import audit
from app.services.ratelimit import FailureLimiter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# Per account (stops targeted guessing) and per IP (stops one client trying many accounts).
account_limiter = FailureLimiter(max_failures=5, window_seconds=15 * 60)
ip_limiter = FailureLimiter(max_failures=30, window_seconds=15 * 60)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    ip = request.client.host if request.client else "unknown"
    account_key = body.username.lower()

    wait = max(account_limiter.retry_after(account_key), ip_limiter.retry_after(ip))
    if wait:  # blocked attempts are not logged: the lockout entry below already records it
        raise HTTPException(status_code=429, detail="Too many failed sign-ins. Please try again later.",
                            headers={"Retry-After": str(wait)})

    staff = (await db.execute(
        select(HospitalStaff).where(HospitalStaff.username == body.username)
    )).scalar_one_or_none()

    if not await verify_password(body.password, staff.password_hash if staff else None):
        locked_now = account_limiter.fail(account_key)
        ip_limiter.fail(ip)
        await audit.record(db, staff.staff_id if staff else None, body.username[:50],
                           "LOGIN_LOCKOUT" if locked_now else "LOGIN_FAILED",
                           "too many failures; account locked for 15 minutes" if locked_now else "invalid credentials")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    account_limiter.reset(account_key)
    await audit.record(db, staff.staff_id, staff.username, "LOGIN", "staff signed in")
    await db.commit()
    return TokenResponse(access_token=create_access_token(staff.staff_id),
                         staff=StaffRead.model_validate(staff))


@router.get("/me", response_model=StaffRead)
async def me(staff: HospitalStaff = Depends(current_staff)) -> HospitalStaff:
    return staff
