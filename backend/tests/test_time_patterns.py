"""What each time pattern the Insider WhiteHat uses must and must not accept (rule 8, R-040)."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.whitehats.builtin import _ISO_IN_TEXT, _SYSLOG, _parse_stamp, event_when, read_zone

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.mark.parametrize("message, when", [
    ("2026-10-23T10:15:00Z login ok", datetime(2026, 10, 23, 10, 15, tzinfo=UTC)),
    ("user=ada at 2026-10-23 10:15:00.123+05:30 ok", datetime(2026, 10, 23, 10, 15, 0, 123000, tzinfo=IST)),
    ("2026-10-23T10:15:00+0530 Accepted password", datetime(2026, 10, 23, 10, 15, tzinfo=IST)),
    ("2026-10-23T10:15:00 Accepted password", datetime(2026, 10, 23, 10, 15, tzinfo=UTC)),
])
def test_an_iso_stamp_anywhere_in_the_line_is_read(message, when):
    assert _ISO_IN_TEXT.search(message)
    assert event_when({"message": message}) == when


@pytest.mark.parametrize("message", [
    "2026-10-23 Accepted password",
    "10:15:00 Accepted password",
    "2026/10/23 10:15:00 Accepted password",
    "2026-10-23T10:15 Accepted password",
])
def test_a_date_or_clock_alone_is_not_an_iso_stamp(message):
    assert _ISO_IN_TEXT.search(message) is None
    assert event_when({"message": message}) is None


@pytest.mark.parametrize("message", ["Oct 23 10:15:00 web-1 sshd[88]: Accepted", "Oct  3 10:15:00 web-1 sshd"])
def test_a_syslog_stamp_is_read(message):
    assert _SYSLOG.search(message)
    assert event_when({"message": message}) is not None


@pytest.mark.parametrize("message", [
    "October 23 10:15:00 web-1 sshd",
    "oct 23 10:15:00 web-1 sshd",
    "Oct 23 10:15 web-1 sshd",
    "XOct 23 10:15:00 web-1 sshd",
])
def test_other_month_and_clock_shapes_are_not_syslog(message):
    assert _SYSLOG.search(message) is None


@pytest.mark.parametrize("name, offset", [
    ("+05:30", timedelta(hours=5, minutes=30)),
    ("+0530", timedelta(hours=5, minutes=30)),
    ("-08:00", timedelta(hours=-8)),
    ("+14:00", timedelta(hours=14)),
    ("-12:00", timedelta(hours=-12)),
])
def test_a_signed_offset_is_a_zone(name, offset):
    assert read_zone(name).utcoffset(None) == offset


@pytest.mark.parametrize("name", ["05:30", "+5:30", "+05:3", "+05:30:00", "+24:00", "+14:01", "+05:60", "Mars/Base", ""])
def test_an_unsigned_short_or_impossible_offset_is_not_a_zone(name):
    assert read_zone(name) is None


@pytest.mark.parametrize("stamp, offset", [
    ("2026-10-23T10:15:00+0530", timedelta(hours=5, minutes=30)),
    ("2026-10-23T10:15:00-0800", timedelta(hours=-8)),
    ("2026-10-23T10:15:00.5-0130", timedelta(hours=-1, minutes=-30)),
])
def test_a_compact_offset_is_read(stamp, offset):
    assert _parse_stamp(stamp).utcoffset() == offset


def test_a_stamp_that_needs_no_colon_is_left_alone():
    assert _parse_stamp("2026-10-23T10:15:00+05:30") == datetime(2026, 10, 23, 10, 15, tzinfo=IST)
    # The dashes of a bare date are not an offset.
    assert _parse_stamp("2026-10-23") == datetime(2026, 10, 23, tzinfo=UTC)
    assert _parse_stamp("2026-10-23T10:15:00+053") is None
