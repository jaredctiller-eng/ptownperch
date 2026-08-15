import logging
from urllib.parse import urlsplit

import httpx

from app.config import settings
from app.sync.engine import SyncResult

logger = logging.getLogger(__name__)


async def post_slack(text: str) -> None:
    # Slack incoming-webhook URLs embed the secret token in the PATH
    # (https://hooks.slack.com/services/T../B../XXXX), so only the host is
    # safe to log. We check resp.is_error directly instead of
    # resp.raise_for_status(): the latter constructs an httpx.HTTPStatusError
    # whose message embeds the full URL, reachable via __context__ even from a
    # `raise ... from None` wrapper. Never constructing that exception avoids
    # the leak entirely rather than relying on suppressing its traceback.
    if not settings.slack_webhook_url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        if resp.is_error:
            safe_host = urlsplit(settings.slack_webhook_url).netloc
            logger.error("slack post failed (%s): %s", resp.status_code, safe_host)
            raise RuntimeError(f"slack post failed ({resp.status_code}) to {safe_host}")


def format_sync_messages(property_name: str, result: SyncResult) -> list[str]:
    msgs = []
    for e in result.new_events:
        kind = "New Airbnb booking" if e.is_reservation else "New Airbnb block"
        msgs.append(f":tada: {kind} at *{property_name}*: {e.start} → {e.end}")
    for e in result.changed_events:
        msgs.append(
            f":calendar: Airbnb dates changed at *{property_name}*: "
            f"now {e.start} → {e.end} ({e.uid})"
        )
    for stay in result.canceled_stays:
        msgs.append(
            f":x: Airbnb booking canceled at *{property_name}*: "
            f"{stay.check_in} → {stay.check_out} ({stay.uid})"
        )
    for c in result.collisions:
        msgs.append(
            f":rotating_light: *DOUBLE-BOOKING RISK* at {property_name}: Airbnb event "
            f"{c.airbnb_uid} overlaps direct reservation #{c.direct_reservation_id}. "
            f"Resolve immediately."
        )
    return msgs
