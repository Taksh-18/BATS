from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import current_staff
from app.models import HospitalStaff
from app.netutil import client_ip
from app.permissions import permissions_for
from app.schemas import LoginRequest, MeRead, StaffRead, TokenResponse
from app.security import create_access_token, verify_password
from app.services import audit
from app.services.ratelimit import FailureLimiter

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

WINDOW = 15 * 60
# Three layers, so no single trick works:
#  - per (account, IP): stops guessing at one account from one place, without letting a stranger
#    lock the real user out of their own sign-in from elsewhere
#  - per account overall: stops a distributed guessing campaign (high threshold)
#  - per IP: stops one client trying many accounts
pair_limiter = FailureLimiter(max_failures=5, window_seconds=WINDOW)
account_limiter = FailureLimiter(max_failures=25, window_seconds=WINDOW)
ip_limiter = FailureLimiter(max_failures=30, window_seconds=WINDOW)


def _me(staff: HospitalStaff) -> MeRead:
    return MeRead(**StaffRead.model_validate(staff).model_dump(), permissions=permissions_for(staff.role))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, request: Request, db: AsyncSession = Depends(get_db)) -> TokenResponse:
    ip = client_ip(request)
    account = body.username.lower()
    pair_key = f"{account}|{ip}"

    wait = max(pair_limiter.retry_after(pair_key), account_limiter.retry_after(account), ip_limiter.retry_after(ip))
    if wait:  # blocked attempts are not logged: the lockout entry below already records it
        raise HTTPException(status_code=429, detail="Too many failed sign-ins. Please try again later.",
                            headers={"Retry-After": str(wait)})

    staff = (await db.execute(
        select(HospitalStaff).where(HospitalStaff.username == body.username)
    )).scalar_one_or_none()

    if not await verify_password(body.password, staff.password_hash if staff else None):
        locked_now = pair_limiter.fail(pair_key)
        account_limiter.fail(account)
        ip_limiter.fail(ip)
        await audit.record(db, staff.staff_id if staff else None, body.username[:50],
                           "LOGIN_LOCKOUT" if locked_now else "LOGIN_FAILED",
                           f"too many failures from {ip}; locked 15 minutes" if locked_now else "invalid credentials")
        await db.commit()
        raise HTTPException(status_code=401, detail="Invalid username or password")

    pair_limiter.reset(pair_key)
    await audit.record(db, staff.staff_id, staff.username, "LOGIN", "staff signed in")
    await db.commit()
    return TokenResponse(access_token=create_access_token(staff.staff_id), staff=_me(staff))


@router.get("/me", response_model=MeRead)
async def me(staff: HospitalStaff = Depends(current_staff)) -> MeRead:
    return _me(staff)
