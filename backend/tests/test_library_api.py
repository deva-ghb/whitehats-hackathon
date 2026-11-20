"""The node library search endpoint: signed-in people of any role, ranked hits and facets."""


def _search(client, headers, **params):
    return client.get("/api/library/search", headers=headers, params=params)


def test_search_finds_a_misspelt_type_with_facets(client, ceo):
    res = _search(client, ceo, q="dynamdb")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["hits"][0]["type_id"] == "data.dynamodb"
    assert set(body["hits"][0]) == {"type_id", "display_name", "family", "score", "matched"}
    assert body["facets"]["family"]["data"] >= 1


def test_a_pasted_arn_and_the_canvas_cloud_rank_the_right_type_first(client, ceo):
    arn = _search(client, ceo, q="arn:aws:sqs:us-east-1:111122223333:order-events").json()
    assert arn["hits"][0]["type_id"] == "messaging.sqs"
    on_azure = _search(client, ceo, q="kubernetes", cloud="azure").json()
    on_aws = _search(client, ceo, q="kubernetes", cloud="aws").json()
    assert (on_azure["hits"][0]["type_id"], on_aws["hits"][0]["type_id"]) == ("boundary.aks", "boundary.eks")


def test_facet_filters_narrow_the_hits(client, ceo):
    azure = _search(client, ceo, q="redis", provider="azure").json()
    assert [h["type_id"] for h in azure["hits"]] == ["data.redis"]


def test_every_role_may_search_and_no_one_signed_out_may(client, ceo, engineer):
    assert _search(client, engineer, q="s3").status_code == 200
    assert _search(client, {}, q="s3").status_code == 401


def test_bad_input_is_refused(client, ceo):
    assert _search(client, ceo, q="x" * 501).status_code == 422
    assert _search(client, ceo, q="s3", cloud="oracle").status_code == 422
    assert _search(client, ceo, q="s3", limit=500).status_code == 422
