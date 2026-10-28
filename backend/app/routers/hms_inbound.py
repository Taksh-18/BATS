import hashlib

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Hospital
from app.schemas import HMSInboundEvent
from app.services import hms_inbound

router = APIRouter(prefix="/api/v1/hms", tags=["hms-inbound"])


async def hospital_for_key(x_hms_key: str | None = Header(None), db: AsyncSession = Depends(get_db)) -> int:
    """Each hospital has its own HMS key (stored only as a SHA-256 hash). The key identifies the
    hospital, so one hospital's HMS can never touch another hospital's units."""
    if not x_hms_key:
        raise HTTPException(status_code=401, detail="Invalid HMS key")
    digest = hashlib.sha256(x_hms_key.encode()).hexdigest()
    hospital_id = (await db.execute(
        select(Hospital.hospital_id).where(Hospital.hms_key_hash == digest)
    )).scalar_one_or_none()
    if hospital_id is None:
        raise HTTPException(status_code=401, detail="Invalid HMS key")
    return hospital_id


@router.post("/inbound")
async def inbound(body: HMSInboundEvent, hospital_id: int = Depends(hospital_for_key),
                  db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    try:
        return {"status": await hms_inbound.apply(db, hospital_id, body)}
    except hms_inbound.InboundError as exc:
        await db.rollback()
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    except IntegrityError as exc:  # hms_inbound.unit_id foreign key: the unit does not exist
        await db.rollback()
        raise HTTPException(status_code=404, detail=f"Unit {body.unit_id} not found") from exc
