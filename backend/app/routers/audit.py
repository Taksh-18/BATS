from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.deps import require
from app.models import AuditLog
from app.schemas import AuditRead, ChainStatus
from app.services import audit

router = APIRouter(prefix="/api/v1/audit-logs", tags=["audit"], dependencies=[Depends(require("audit:read"))])


@router.get("", response_model=list[AuditRead])
async def list_audit(limit: int = Query(200, ge=1, le=1000), db: AsyncSession = Depends(get_db)):
    return (await db.execute(
        select(AuditLog).order_by(AuditLog.audit_id.desc()).limit(limit)
    )).scalars().all()


@router.get("/verify", response_model=ChainStatus)
async def verify(db: AsyncSession = Depends(get_db)):
    return await audit.run_check(db)
