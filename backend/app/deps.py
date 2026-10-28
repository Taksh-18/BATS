from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import HospitalStaff
from app.permissions import ROLE_PERMISSIONS
from app.security import decode_access_token

_bearer = HTTPBearer(auto_error=False)


async def current_staff(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: AsyncSession = Depends(get_db),
) -> HospitalStaff:
    staff_id = decode_access_token(creds.credentials) if creds else None
    staff = await db.get(HospitalStaff, staff_id) if staff_id else None
    if staff is None:
        raise HTTPException(status_code=401, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return staff


def require(permission: str):
    """Dependency factory: the signed-in staff member must hold `permission` through their role."""
    async def checker(staff: HospitalStaff = Depends(current_staff)) -> HospitalStaff:
        if permission not in ROLE_PERMISSIONS.get(staff.role, frozenset()):
            raise HTTPException(status_code=403, detail=f"Your role ({staff.role}) is not allowed to do this")
        return staff
    return checker
