import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas import HMSInboundEvent
from app.services import hms_inbound

router = APIRouter(prefix="/api/v1/hms", tags=["hms-inbound"])


def require_hms_key(x_hms_key: str | None = Header(None)) -> None:
    # Constant-time comparison so response timing can't be used to guess the key.
    if not x_hms_key or not hmac.compare_digest(x_hms_key.encode(), settings.hms_api_key.encode()):
        raise HTTPException(status_code=401, detail="Invalid HMS key")


@router.post("/inbound", dependencies=[Depends(require_hms_key)])
async def inbound(body: HMSInboundEvent, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        return {"status": await hms_inbound.apply(db, body)}
    except hms_inbound.InboundError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except IntegrityError as exc:  # hms_inbound.unit_id foreign key: the unit does not exist
        await db.rollback()
        raise HTTPException(status_code=404, detail=f"Unit {body.unit_id} not found") from exc
