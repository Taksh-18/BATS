"""SIMULATION ONLY: a placeholder hospital HMS.

It receives issuance events from BATS (webhook) and lets a user record what happened to the
unit; those updates are sent back to BATS server-to-server with the shared API key, exactly
as a real HMS integration would. Unauthenticated by design: disable with ENABLE_MOCK_HMS=false.
"""
import hmac
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import MockHmsEvent
from app.schemas import HMSIssueEvent, HMSStatus

router = APIRouter(prefix="/mock-hms", tags=["mock-hms"])


def require_console_token(x_mock_hms_token: str | None = Header(None)) -> None:
    """On a public deployment this simulated HMS can change real stock, so it needs a token.
    Unset (local demos) means open."""
    expected = settings.mock_hms_token
    if expected and not (x_mock_hms_token and hmac.compare_digest(x_mock_hms_token.encode(), expected.encode())):
        raise HTTPException(status_code=401, detail="HMS console token required")


token_guard = [Depends(require_console_token)]

_state = {"online": True}


def _require_online() -> None:
    if not _state["online"]:
        raise HTTPException(status_code=503, detail="The HMS is offline")


def _view(row: MockHmsEvent) -> dict:
    return {"event_id": row.event_id, "hms_reference": row.hms_reference, "state": row.state,
            "received_at": row.received_at, "resolved_at": row.resolved_at, **row.payload}


@router.post("/webhook", status_code=202, dependencies=token_guard)
async def receive_issue_event(event: HMSIssueEvent, db: AsyncSession = Depends(get_db)) -> dict[str, str]:
    _require_online()
    reference = f"HMS-{uuid.uuid4().hex[:10].upper()}"
    inserted = (await db.execute(
        pg_insert(MockHmsEvent)
        .values(issue_id=event.issue_id, hms_reference=reference, payload=event.model_dump(mode="json"))
        .on_conflict_do_nothing(index_elements=["issue_id"])  # BATS retries must not create duplicates
        .returning(MockHmsEvent.hms_reference)
    )).scalar_one_or_none()
    if inserted is None:
        reference = (await db.execute(
            select(MockHmsEvent.hms_reference).where(MockHmsEvent.issue_id == event.issue_id)
        )).scalar_one()
    await db.commit()
    return {"status": "received", "hms_reference": reference}


@router.get("/events", dependencies=token_guard)
async def list_events(db: AsyncSession = Depends(get_db)) -> list[dict]:
    """The HMS inbox, newest first."""
    rows = (await db.execute(
        select(MockHmsEvent).order_by(MockHmsEvent.event_id.desc()).limit(100)
    )).scalars().all()
    return [_view(r) for r in rows]


class ReturnBody(BaseModel):
    cold_chain_ok: bool = True


async def _resolve(db: AsyncSession, event_id: int, action: str, new_state: str, cold_chain_ok: bool = True) -> dict:
    _require_online()
    row = (await db.execute(
        select(MockHmsEvent).where(MockHmsEvent.event_id == event_id).with_for_update()
    )).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="No such HMS event")
    if row.state != "Received":
        raise HTTPException(status_code=409, detail=f"Already recorded as {row.state}")

    try:
        async with httpx.AsyncClient(timeout=settings.hms_timeout_seconds) as client:
            resp = await client.post(
                settings.bats_inbound_url, headers={"X-HMS-Key": settings.hms_api_key},
                json={"event_ref": f"hms-event-{row.event_id}-{action}", "action": action,
                      "unit_id": row.payload["unit_id"], "cold_chain_ok": cold_chain_ok})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach BATS") from exc

    if resp.status_code >= 400:
        detail = (resp.json().get("detail") if resp.headers.get("content-type", "").startswith("application/json")
                  else None) or "BATS rejected the update"
        raise HTTPException(status_code=409 if resp.status_code == 409 else 502, detail=detail)

    row.state, row.resolved_at = new_state, datetime.now(timezone.utc)
    await db.commit()
    return _view(row)


@router.post("/events/{event_id}/transfused", dependencies=token_guard)
async def record_transfusion(event_id: int, db: AsyncSession = Depends(get_db)) -> dict:
    return await _resolve(db, event_id, "unit_transfused", "Transfused")


@router.post("/events/{event_id}/returned", dependencies=token_guard)
async def return_unit(event_id: int, body: ReturnBody, db: AsyncSession = Depends(get_db)) -> dict:
    return await _resolve(db, event_id, "unit_returned", "Returned", body.cold_chain_ok)


@router.get("/status", response_model=HMSStatus)
async def status() -> HMSStatus:
    return HMSStatus(online=_state["online"], endpoint=settings.hms_webhook_url)


@router.post("/toggle", response_model=HMSStatus, dependencies=token_guard)
async def toggle() -> HMSStatus:
    """Simulate an HMS outage / recovery so the failure path can be demonstrated."""
    _state["online"] = not _state["online"]
    return await status()
