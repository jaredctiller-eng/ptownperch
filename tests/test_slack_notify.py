from datetime import date

from app.ical.parse import IcalEvent
from app.slack.notify import format_sync_messages, post_slack
from app.sync.engine import CanceledStay, Collision, SyncResult


def test_formats_all_change_kinds():
    result = SyncResult(
        new_events=[
            IcalEvent(uid="n@airbnb.com", start=date(2026, 9, 1), end=date(2026, 9, 5),
                      summary="Reserved"),
            IcalEvent(uid="b@airbnb.com", start=date(2026, 9, 8), end=date(2026, 9, 9),
                      summary="Airbnb (Not available)"),
        ],
        changed_events=[
            IcalEvent(uid="c@airbnb.com", start=date(2026, 9, 10), end=date(2026, 9, 12),
                      summary="Reserved"),
        ],
        canceled_stays=[
            CanceledStay(uid="gone@airbnb.com", check_in=date(2026, 9, 20), check_out=date(2026, 9, 22)),
        ],
        collisions=[Collision(airbnb_uid="n@airbnb.com", direct_reservation_id=7)],
    )
    msgs = format_sync_messages("Ptown Perch", result)
    assert len(msgs) == 5
    assert "New Airbnb booking" in msgs[0]
    assert "New Airbnb block" in msgs[1]
    assert "dates changed" in msgs[2]
    assert "canceled" in msgs[3]
    assert "2026-09-20" in msgs[3]
    assert "DOUBLE-BOOKING RISK" in msgs[4]
    assert "#7" in msgs[4]


def test_no_changes_no_messages():
    assert format_sync_messages("Ptown Perch", SyncResult()) == []


async def test_post_slack_noop_without_webhook(monkeypatch):
    # settings.slack_webhook_url defaults to "" in tests — must not raise or call out.
    # Monkeypatched explicitly (rather than relying on the default) so this test
    # still passes if a developer has a real SLACK_WEBHOOK_URL set locally.
    from app.config import settings

    monkeypatch.setattr(settings, "slack_webhook_url", "")
    await post_slack("hello")


async def test_post_slack_blocks_noop_without_webhook(monkeypatch):
    from app.config import settings
    from app.slack.notify import post_slack_blocks
    monkeypatch.setattr(settings, "slack_webhook_url", "")
    await post_slack_blocks({"blocks": []})
