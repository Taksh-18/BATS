import logging

import httpx

from app.config import settings
from app.schemas import HMSIssueEvent

log = logging.getLogger(__name__)


async def notify_issue(event: HMSIssueEvent) -> bool:
    """Push an issuance event to the hospital's HMS. Returns False on any failure."""
    try:
        async with httpx.AsyncClient(timeout=settings.hms_timeout_seconds) as client:
            resp = await client.post(settings.hms_webhook_url, content=event.model_dump_json(),
                                     headers={"Content-Type": "application/json"})
            resp.raise_for_status()
        return True
    except httpx.HTTPError as exc:
        log.warning("HMS sync failed for issue %s: %s", event.issue_id, exc)
        return False
