"""Notifications and the calendar feed (Phase 5): /api/calendar.ics, /api/feed,
/api/notify."""

from __future__ import annotations

import os
from datetime import timedelta

from fastapi import APIRouter, Request, Response

from logr import ics, notify
from logr.models import utcnow

from .deps import StoreDep, UserViewDep, View, bad_request, not_found
from .schemas import ChannelOut, FeedOut, NotifyOut, NotifyTestOut

router = APIRouter(prefix="/api")

CHANNEL_PREFS = {"email": "notify_email", "telegram": "notify_telegram_chat", "discord": "notify_discord_webhook"}
ICS_TYPE = "text/calendar; charset=utf-8"


def notifier_config(request: Request) -> notify.NotifierConfig:
    return request.app.state.notifier


# --- calendar feed -----------------------------------------------------------------


@router.get("/calendar.ics", response_class=Response,
            responses={200: {"content": {"text/calendar": {}}}, 404: {"description": "missing or unknown token"}})
def calendar_ics(store: StoreDep, token: str | None = None) -> Response:
    # The token is the credential: no cookie needed, and a missing token is a 404
    # like an unknown one, so guessing learns nothing.
    user_id = ics.FeedTokens(store.db).user_for(token or "")
    if user_id is None:
        raise not_found("unknown calendar feed")
    body = ics.build_calendar(ics.tracked_events(store, user_id, utcnow()))
    return Response(content=body.encode("utf-8"), media_type=ICS_TYPE,
                    headers={"Cache-Control": "private, max-age=300"})


def _base_url(request: Request) -> str:
    base = notifier_config(request).base_url or os.environ.get("LOGR_BASE_URL", "").strip()
    return (base or str(request.base_url)).rstrip("/")


def _feed_out(request: Request, token: str) -> FeedOut:
    url = f"{_base_url(request)}/api/calendar.ics?token={token}"
    _, sep, rest = url.partition("://")
    return FeedOut(url=url, webcal_url=f"webcal://{rest}" if sep else url)


@router.get("/feed", response_model=FeedOut)
def get_feed(request: Request, view: UserViewDep) -> FeedOut:
    return _feed_out(request, ics.FeedTokens(view.store.db).get_or_create(view.user.id))


@router.post("/feed/rotate", response_model=FeedOut)
def rotate_feed(request: Request, view: UserViewDep) -> FeedOut:
    return _feed_out(request, ics.FeedTokens(view.store.db).rotate(view.user.id))


# --- notifications -----------------------------------------------------------------


def _fmt_offset(o: timedelta) -> str:
    secs = int(o.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if secs % size == 0:
            return f"{secs // size}{unit}"
    return f"{secs // 60}m"  # pragma: no cover - offsets are whole minutes


DEFAULT_OFFSETS_TEXT = ",".join(_fmt_offset(o) for o in notify.DEFAULT_OFFSETS)


def _user_prefs(view: View) -> dict[str, str]:
    return {k: v for k, v in view.prefs.all().items() if v}


@router.get("/notify", response_model=NotifyOut)
def get_notify(request: Request, view: UserViewDep) -> NotifyOut:
    config = notifier_config(request)
    prefs = _user_prefs(view)
    available = {"email": config.email_enabled, "telegram": config.telegram_enabled, "discord": True}
    channels: dict[str, ChannelOut] = {}
    for name, key in CHANNEL_PREFS.items():
        value = prefs.get(key)
        if value and name == "discord":
            value = notify.mask_secret(value)
        channels[name] = ChannelOut(configured=bool(value), available=available[name], destination=value)
    return NotifyOut(
        channels=channels,
        remind_offsets=prefs.get("remind_offsets") or DEFAULT_OFFSETS_TEXT,
        last_pass_at=notify.Outbox(view.store.db).last_pass(view.user.id),
    )


@router.post("/notify/test", response_model=NotifyTestOut)
def test_notify(request: Request, view: UserViewDep) -> NotifyTestOut:
    prefs = _user_prefs(view)
    if not any(prefs.get(k) for k in CHANNEL_PREFS.values()):
        raise bad_request("no notification channel configured; set notify_email, "
                          "notify_telegram_chat or notify_discord_webhook first")
    return NotifyTestOut(results=notify.send_test(prefs, notifier_config(request)))
