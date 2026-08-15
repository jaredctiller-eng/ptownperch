import httpx

from app.config import settings
from app.sync.engine import SyncResult


async def post_slack(text: str) -> None:
    if not settings.slack_webhook_url:
        return
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(settings.slack_webhook_url, json={"text": text})
        resp.raise_for_status()


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
    for uid in result.canceled_uids:
        msgs.append(f":x: Airbnb event canceled at *{property_name}* ({uid})")
    for c in result.collisions:
        msgs.append(
            f":rotating_light: *DOUBLE-BOOKING RISK* at {property_name}: Airbnb event "
            f"{c.airbnb_uid} overlaps direct reservation #{c.direct_reservation_id}. "
            f"Resolve immediately."
        )
    return msgs
