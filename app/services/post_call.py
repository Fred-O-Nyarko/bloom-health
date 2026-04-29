"""Post-call webhook — POSTs the severity verdict + transcript to the Bloom
portal once a conversation ends. Best-effort: errors are logged, not raised,
because the call has already happened and the bridge is winding down.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


async def send_post_call(
    call_sid: str,
    transcript: str,
    verdict: dict[str, Any],
) -> None:
    if not call_sid:
        logger.warning("send_post_call skipped: no call_sid")
        return

    payload = {
        "severity_level": verdict.get("severity_level", 1),
        "summary": verdict.get("summary", ""),
        "transcript": transcript,
        "signals": verdict.get("signals", {}),
        "reason": verdict.get("reason"),
    }
    url = (
        f"{settings.portal_base_url.rstrip('/')}/api/calls/by-sid/{call_sid}/post-call"
    )
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(url, json=payload)
        if r.status_code >= 400:
            logger.warning(
                f"Portal post-call ingest returned {r.status_code}: {r.text[:200]}"
            )
        else:
            logger.info(
                f"Posted post-call ingest to portal "
                f"(call_sid={call_sid}, severity=L{payload['severity_level']})"
            )
    except httpx.HTTPError as e:
        logger.warning(f"Could not POST post-call ingest to {url}: {e}")
