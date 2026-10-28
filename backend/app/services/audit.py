"""Hash-chained audit log. Callers add entries inside their own transaction, so a
business change and its audit row commit (or roll back) together.

Tamper evidence has two layers:
  1. the hash chain catches edits and deletions in the middle of the log;
  2. an anchor file, kept outside the database, records the chain head at every check,
     which catches deleted or rewritten *tail* entries that a chain alone cannot see.
Someone who can rewrite both the database and the anchor file can still forge history;
store the anchor on separate, write-once storage if that threat matters.
"""
import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AuditLog

log = logging.getLogger(__name__)

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 727001  # pg advisory lock: serialises appends so the chain has no forks

# Result of the most recent verification (background monitor or manual), shown on the dashboard.
last_check: dict = {"valid": None, "checked": 0, "checked_at": None, "reason": None}


def _digest(prev_hash: str, created_at: datetime, actor_id: int | None,
            actor: str, action: str, details: str) -> str:
    payload = json.dumps(
        [prev_hash, created_at.astimezone(timezone.utc).isoformat(), actor_id, actor, action, details],
        separators=(",", ":"), ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def record(db: AsyncSession, actor_id: int | None, actor_username: str,
                 action: str, details: str) -> AuditLog:
    await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    prev = (await db.execute(
        select(AuditLog.hash).order_by(AuditLog.audit_id.desc()).limit(1)
    )).scalar_one_or_none() or GENESIS_HASH

    now = datetime.now(timezone.utc)
    entry = AuditLog(
        created_at=now, actor_staff_id=actor_id, actor_username=actor_username,
        action=action, details=details, prev_hash=prev,
        hash=_digest(prev, now, actor_id, actor_username, action, details),
    )
    db.add(entry)
    await db.flush()
    return entry


async def verify_chain(db: AsyncSession) -> dict:
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.audit_id))).scalars().all()
    prev = GENESIS_HASH
    for row in rows:
        expected = _digest(prev, row.created_at, row.actor_staff_id,
                           row.actor_username, row.action, row.details)
        if row.prev_hash != prev or row.hash != expected:
            return {"valid": False, "checked": len(rows), "first_bad_audit_id": row.audit_id,
                    "reason": "hash chain broken: an entry was edited, removed or reordered"}
        prev = row.hash
    head = (rows[-1].audit_id, rows[-1].hash) if rows else None
    return {"valid": True, "checked": len(rows), "first_bad_audit_id": None, "reason": None, "head": head}


def _read_anchors() -> list[tuple[int, str]]:
    path = Path(settings.audit_anchor_path)
    if not path.exists():
        return []
    anchors = []
    for line in path.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0].isdigit():
            anchors.append((int(parts[0]), parts[1]))
    return anchors


async def verify_all(db: AsyncSession) -> dict:
    """Chain check plus anchor check. On success, advances the anchor file to the current head."""
    result = await verify_chain(db)
    head = result.pop("head", None)
    if not result["valid"]:
        return result

    anchors = _read_anchors()
    if anchors:
        found = dict((await db.execute(
            select(AuditLog.audit_id, AuditLog.hash).where(AuditLog.audit_id.in_([a for a, _ in anchors]))
        )).all())
        for audit_id, digest in anchors:
            if found.get(audit_id) != digest:
                return {"valid": False, "checked": result["checked"], "first_bad_audit_id": audit_id,
                        "reason": "anchored entry is missing or was rewritten (log truncated or replaced)"}

    if head and (not anchors or head[0] > anchors[-1][0]):
        with Path(settings.audit_anchor_path).open("a") as f:
            f.write(f"{head[0]} {head[1]} {datetime.now(timezone.utc).isoformat()}\n")
    return result


async def run_check(db: AsyncSession) -> dict:
    result = await verify_all(db)
    last_check.update(valid=result["valid"], checked=result["checked"],
                      checked_at=datetime.now(timezone.utc), reason=result["reason"])
    if not result["valid"]:
        log.error("AUDIT TAMPERING DETECTED at entry %s: %s", result["first_bad_audit_id"], result["reason"])
    return result
