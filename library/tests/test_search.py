"""Node library search: the 100-query check (P1.2), facets, filters, context and pasted resource ids."""

from functools import cache
from pathlib import Path

import pytest
import yaml

from library.resolve import load_library
from library.resource_ids import AWS_SERVICES, AZURE_TYPES, recognise
from library.search import Context, Index

QUERIES = yaml.safe_load((Path(__file__).parent / "search_queries.yaml").read_text())["queries"]
TOP = 3
PASS_MARK = 95


@cache
def index() -> Index:
    return Index(load_library())


def _rank(query: dict) -> int | None:
    context = Context(**query.get("context", {}))
    ids = [hit.type_id for hit in index().search(query["q"], context, limit=TOP).hits]
    return ids.index(query["expect"]) + 1 if query["expect"] in ids else None


def test_the_query_file_has_100_queries_that_each_name_a_real_type():
    library = load_library()
    assert len(QUERIES) == 100
    assert [q["expect"] for q in QUERIES if q["expect"] not in library] == []


def test_at_least_95_of_100_queries_find_their_type_in_the_top_3():
    misses = [(q["q"], q["expect"], [h.type_id for h in index().search(q["q"], Context(**q.get("context", {}))).hits[:TOP]])
              for q in QUERIES if _rank(q) is None]
    found = len(QUERIES) - len(misses)
    print(f"\nsearch score: {found}/{len(QUERIES)} in the top {TOP}")
    for query, expected, got in misses:
        print(f"  miss: {query!r} expected {expected}, got {got}")
    assert found >= PASS_MARK


@pytest.mark.parametrize("query", [q for q in QUERIES if q["kind"] == "context"], ids=lambda q: f"{q['q']}-{q['context']}")
def test_context_puts_the_matching_cloud_first(query):
    assert _rank(query) == 1


def test_every_type_a_resource_id_can_name_is_in_the_library():
    library = load_library()
    named = set()
    for value in AWS_SERVICES.values():
        named |= set(value.values()) if isinstance(value, dict) else {value}
    for type_ids in AZURE_TYPES.values():
        named |= set(type_ids)
    assert sorted(named - set(library)) == []


def test_a_pasted_arn_names_the_type_and_where_it_lives():
    found = recognise("arn:aws:dynamodb:ap-southeast-2:111122223333:table/orders")
    assert found.type_ids == ("data.dynamodb",)
    assert found.where == {"region": "ap-southeast-2", "account": "111122223333", "name": "orders"}


def test_an_rds_cluster_and_an_rds_database_are_different_types():
    cluster = recognise("arn:aws:rds:ap-southeast-2:111122223333:cluster:payments")
    database = recognise("arn:aws:rds:ap-southeast-2:111122223333:db:payments")
    assert cluster.type_ids == ("data.aurora-postgres",)
    assert database.type_ids == ("data.rds",)
    assert cluster.where["name"] == "payments"
    assert database.where["name"] == "payments"
    assert recognise("arn:aws:rds:ap-southeast-2:111122223333:snapshot:payments") is None


def test_an_ec2_instance_arn_names_a_virtual_machine():
    found = recognise("arn:aws:ec2:ap-southeast-2:111122223333:instance/i-0abc")
    assert found.type_ids == ("compute.vm",)
    assert found.where == {"region": "ap-southeast-2", "account": "111122223333", "name": "i-0abc"}


def test_a_pasted_azure_id_takes_the_most_specific_type():
    found = recognise("/subscriptions/0000/resourceGroups/rg-prod/providers/Microsoft.Sql/servers/s1/databases/appts")
    assert found.type_ids == ("data.azure-sql",)
    assert found.where == {"subscription": "0000", "resource_group": "rg-prod", "name": "appts"}


@pytest.mark.parametrize("text", ["arn:aws:unknownservice:::thing", "arn:aws:iam::1:user/bob", "/not/an/azure/id",
                                  "s3 bucket", "https://example.com"])
def test_text_that_is_not_a_known_resource_id_is_not_treated_as_one(text):
    assert recognise(text) is None


def test_generic_types_never_lead_a_specific_match():
    hits = index().search("queue").hits
    assert not hits[0].type_id.startswith("generic.")
    asked = index().search("generic queue").hits
    assert asked[0].type_id == "generic.messaging"


def test_facets_count_the_matches_and_filters_narrow_them():
    everything = index().search("redis", limit=50)
    assert everything.facets["cloud"] == {"aws": 1, "azure": 1}
    azure = index().search("redis", filters={"cloud": "azure"})
    assert [h.type_id for h in azure.hits] == ["data.redis"]
    importable = index().search("queue", filters={"importable": "yes"}, limit=50)
    assert importable.hits and all(load_library()[h.type_id]["importers"] for h in importable.hits)


def test_dragging_from_a_service_lifts_only_what_it_can_connect_to():
    from library.search import PLACEMENT_BOOST

    def scores(context):
        return {h.type_id: h.score for h in index().search("customers bucket", context, limit=50).hits}

    plain, dragged = scores(Context()), scores(Context(dragging_from="service.backend"))
    assert dragged["data.s3"] == plain["data.s3"] + PLACEMENT_BOOST  # a service reads and writes a bucket
    assert dragged["endpoint.customer"] == plain["endpoint.customer"]  # a service does not call its customers


def test_an_empty_or_unknown_query_finds_nothing():
    assert index().search("   ").hits == []
    assert index().search("zzzzqqqq").hits == []
