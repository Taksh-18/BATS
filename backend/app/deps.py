from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import HospitalStaff
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
