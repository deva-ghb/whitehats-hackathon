"""A CloudWatch read that stops at a page boundary keeps the other events from that millisecond."""

from app.connectors.cloudwatch import CloudWatch


def test_a_page_boundary_keeps_events_that_share_the_millisecond(monkeypatch):
    monkeypatch.setattr("app.connectors.cloudwatch.PAGES_PER_GROUP", 1)
    pages = {
        0: {"events": [{"timestamp": 5000, "eventId": "a", "message": "one", "logStreamName": "s"}],
            "nextToken": "more"},
        5000: {"events": [{"timestamp": 5000, "eventId": "b", "message": "two", "logStreamName": "s"}]},
    }

    def fake_filter(group, kwargs):
        return pages.get(kwargs["startTime"], {"events": []})

    source = CloudWatch({"log_groups": "app", "role_arn": "arn:aws:iam::1:role/reader"})
    source._filter = fake_filter
    source.page_size = 1
    first = source.read({"app": 0})
    assert [line["message"] for line in first.lines] == ["one"]
    second = source.read(first.bookmark)
    assert [line["message"] for line in second.lines] == ["two"]
