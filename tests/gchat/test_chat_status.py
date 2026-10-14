"""
Unit tests for Google Chat MCP tools — the signed-in user's own custom status
"""

import datetime
import os
import sys
from unittest.mock import Mock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from core.utils import UserInputError

ME = "users/me/availability"
# 07:30 UTC is 10:30 AM in Amman (UTC+3).
FIXED_NOW = datetime.datetime(2026, 10, 14, 7, 30, tzinfo=datetime.timezone.utc)


def _unwrap(tool):
    """Unwrap a FunctionTool + decorator chain to the original async function."""
    fn = getattr(tool, "fn", tool)
    while hasattr(fn, "__wrapped__"):
        fn = fn.__wrapped__
    return fn


def _availability_service(state="ACTIVE"):
    service = Mock()
    availability = service.users.return_value.availability.return_value
    availability.get.return_value.execute.return_value = {"name": ME, "state": state}
    availability.patch.return_value.execute.return_value = {"name": ME}
    return service, availability


@pytest.fixture
def fixed_clock(monkeypatch):
    from gchat import chat_tools

    monkeypatch.setattr(chat_tools, "_utc_now", lambda: FIXED_NOW)


# ---------------------------------------------------------------------------
# set_chat_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_chat_status_sets_own_custom_status_with_ttl():
    """The status goes on users/me with a TTL, so Google clears it by itself."""
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    await _unwrap(set_chat_status)(
        service=service,
        user_google_email="me@example.com",
        text="On a break",
        emoji_unicode="☕",
        minutes=15,
    )

    availability.patch.assert_called_once_with(
        name=ME,
        updateMask="custom_status",
        body={
            "customStatus": {
                "text": "On a break",
                "emoji": {"unicode": "☕"},
                "ttl": "900s",
            }
        },
    )
    availability.markAsDoNotDisturb.assert_not_called()


@pytest.mark.asyncio
async def test_set_chat_status_turns_on_do_not_disturb_for_same_duration():
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    await _unwrap(set_chat_status)(
        service=service,
        user_google_email="me@example.com",
        text="At prayer",
        emoji_unicode="\U0001f54c",
        minutes=15,
        do_not_disturb=True,
    )

    availability.markAsDoNotDisturb.assert_called_once_with(
        name=ME, body={"ttl": "900s"}
    )
    assert availability.patch.call_args.kwargs["body"]["customStatus"]["ttl"] == "900s"


@pytest.mark.asyncio
async def test_set_chat_status_fills_back_at_in_the_given_time_zone(fixed_clock):
    """{back_at} becomes the local end time, with no leading zero on the hour."""
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    result = await _unwrap(set_chat_status)(
        service=service,
        user_google_email="me@example.com",
        text="On a break, back at {back_at}",
        emoji_unicode="☕",
        minutes=15,
        time_zone="Asia/Amman",
    )

    sent = availability.patch.call_args.kwargs["body"]["customStatus"]["text"]
    assert sent == "On a break, back at 10:45 AM"
    assert "On a break, back at 10:45 AM" in result


@pytest.mark.asyncio
async def test_set_chat_status_back_at_defaults_to_utc(fixed_clock):
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    await _unwrap(set_chat_status)(
        service=service,
        user_google_email="me@example.com",
        text="Back at {back_at}",
        emoji_unicode="☕",
        minutes=15,
    )

    sent = availability.patch.call_args.kwargs["body"]["customStatus"]["text"]
    assert sent == "Back at 7:45 AM"


@pytest.mark.asyncio
@pytest.mark.parametrize("minutes", [0, -5])
async def test_set_chat_status_rejects_minutes_below_one(minutes):
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    with pytest.raises(UserInputError, match="minutes"):
        await _unwrap(set_chat_status)(
            service=service,
            user_google_email="me@example.com",
            text="On a break",
            emoji_unicode="☕",
            minutes=minutes,
        )
    availability.patch.assert_not_called()


@pytest.mark.asyncio
async def test_set_chat_status_rejects_text_over_64_characters(fixed_clock):
    """Too-long text is refused, never silently cut (which could drop the time)."""
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    with pytest.raises(UserInputError, match="64"):
        await _unwrap(set_chat_status)(
            service=service,
            user_google_email="me@example.com",
            text="x" * 57 + " {back_at}",  # 65 once filled with "7:45 AM"
            emoji_unicode="☕",
            minutes=15,
        )
    availability.patch.assert_not_called()


@pytest.mark.asyncio
async def test_set_chat_status_measures_length_after_filling_back_at(fixed_clock):
    """The 64-character limit applies to what Chat shows, not to the template."""
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    await _unwrap(set_chat_status)(
        service=service,
        user_google_email="me@example.com",
        text="x" * 56 + " {back_at}",  # 66 as a template, 64 once filled
        emoji_unicode="☕",
        minutes=15,
    )

    sent = availability.patch.call_args.kwargs["body"]["customStatus"]["text"]
    assert len(sent) == 64


@pytest.mark.asyncio
async def test_set_chat_status_rejects_unknown_time_zone():
    service, availability = _availability_service()

    from gchat.chat_tools import set_chat_status

    with pytest.raises(UserInputError, match="Mars/Olympus"):
        await _unwrap(set_chat_status)(
            service=service,
            user_google_email="me@example.com",
            text="Back at {back_at}",
            emoji_unicode="☕",
            minutes=15,
            time_zone="Mars/Olympus",
        )
    availability.patch.assert_not_called()


# ---------------------------------------------------------------------------
# clear_chat_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_clear_chat_status_ends_do_not_disturb_then_clears_text():
    service, availability = _availability_service(state="DO_NOT_DISTURB")

    from gchat.chat_tools import clear_chat_status

    await _unwrap(clear_chat_status)(
        service=service,
        user_google_email="me@example.com",
    )

    availability.get.assert_called_once_with(name=ME)
    availability.markAsActive.assert_called_once()
    assert availability.markAsActive.call_args.kwargs["name"] == ME
    availability.patch.assert_called_once_with(
        name=ME, updateMask="custom_status", body={}
    )


@pytest.mark.asyncio
async def test_clear_chat_status_leaves_presence_alone_when_not_in_do_not_disturb():
    service, availability = _availability_service(state="ACTIVE")

    from gchat.chat_tools import clear_chat_status

    await _unwrap(clear_chat_status)(
        service=service,
        user_google_email="me@example.com",
    )

    availability.markAsActive.assert_not_called()
    availability.patch.assert_called_once_with(
        name=ME, updateMask="custom_status", body={}
    )
