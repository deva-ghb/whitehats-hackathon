"""Sense points come from attack paths. The same graph always ranks the same way."""

import json
import time
from pathlib import Path

import pytest

from app.services.sense_points import ATTACK_FLOW, also_on_paths, analyse, modes, passes

_REPO = Path(__file__).resolve().parents[3]


def _node(node_id, **extra):
    body = {"id": node_id, "name": node_id, "modes": ["telemetry"]}
    body.update(extra)
    return body


def _shop():
    """Two ways in, one function in front of a watched ledger."""
    return {
        "nodes": [
            _node("api", type="edge.api-gateway@1", modes=["network"]),
            _node("repo", type="repo.github@1", modes=["code"]),
            _node("fn", type="service.function@2", modes=["telemetry"], properties={"auth": False, "watch_cost": 1}),
            _node(
                "db",
                type="data.aurora-postgres@1",
                criticality="crown-jewel",
                data_classification=["PCI"],
                modes=["data"],
                properties={"telemetry": True},
            ),
        ],
        "edges": [
            {"from": "api", "to": "fn", "type": "calls"},
            {"from": "fn", "to": "repo", "type": "deploys_from"},
            {"from": "fn", "to": "db", "type": "reads"},
        ],
    }


def test_the_gaps_the_cover_did_not_need_follow_it_most_paths_first():
    graph = _shop()
    result = analyse(graph)
    assert [point["target"] for point in result["sense_points"]] == ["fn"]
    more = also_on_paths(graph, result)
    # The ledger is watched already, so it is no gap; the gateway and the repo are each on one path, and tie by name.
    assert [(point["target"], point["paths"]) for point in more] == [("api", 1), ("repo", 1)]
    assert more == also_on_paths(graph, result)


def test_the_shared_function_covers_both_paths_and_ranks_first():
    result = analyse(_shop())
    assert [path["nodes"] for path in result["paths"]] == [
        ["api", "fn", "db"],
        ["repo", "fn", "db"],
    ]
    assert result["paths"][0]["weight"] == 11
    assert result["sense_points"][0]["techniques"] == ["T1190", "T1552.001", "T1648"]
    assert result["sense_points"][0]["target"] == "fn"
    assert result["sense_points"][0]["mode"] == "telemetry"
    assert result["sense_points"][0]["paths"] == 2
    assert result["sense_points"][0]["coverage"] == 1
    assert result["sense_points"][0]["why"] == "Covers 2 of 2 attack paths. No telemetry and no control on this node."
    assert [point["target"] for point in result["sense_points"]] == ["fn"]
    node_chokes = [point for point in result["choke_points"] if point["kind"] == "node"]
    assert node_chokes[0]["id"] == "db"
    assert node_chokes[0]["paths"] == 2
    assert node_chokes[1] == {"kind": "node", "id": "fn", "paths": 2, "weight": 22}
    assert result["entry_points"] == ["api", "repo"]
    assert result["crown_jewels"] == ["db"]
    assert result["coverage_gaps"] == ["api", "fn", "repo"]


def _two_ways(missing_auth_on_b: bool):
    """Two watched ways in, each through its own unwatched function, to one watched jewel. Only b's weight can change."""
    b_properties = {"auth": False} if missing_auth_on_b else {}
    return {
        "nodes": [
            _node("in_a", type="edge.api-gateway@1", properties={"telemetry": True}),
            _node("in_b", type="edge.api-gateway@1", properties={"telemetry": True}),
            _node("a", type="service.function@2"),
            _node("b", type="service.function@2", properties=b_properties),
            _node("db", type="data.aurora-postgres@1", criticality="crown-jewel", properties={"telemetry": True}),
        ],
        "edges": [
            {"from": "in_a", "to": "a", "type": "calls"},
            {"from": "in_b", "to": "b", "type": "calls"},
            {"from": "a", "to": "db", "type": "reads"},
            {"from": "b", "to": "db", "type": "reads"},
        ],
    }


def _weights(result):
    return sorted(path["weight"] for path in result["paths"])


def _first_function(result):
    return next(point["id"] for point in result["choke_points"] if point.get("id") in {"a", "b"})


def test_a_heavier_path_changes_the_first_suggestion_and_the_first_choke_point():
    # R-056: the graphs differ only in one missing control on b's path. Equal weights fall back to the id.
    plain, heavier = analyse(_two_ways(False)), analyse(_two_ways(True))
    assert _weights(heavier) == [_weights(plain)[0], _weights(plain)[1] + 1]
    assert plain["sense_points"][0]["target"] == "a"
    assert heavier["sense_points"][0]["target"] == "b"
    assert (_first_function(plain), _first_function(heavier)) == ("a", "b")
    assert analyse(_two_ways(True)) == heavier  # deterministic


def test_a_cheaper_node_ranks_above_one_that_only_sorts_earlier():
    graph = {
        "nodes": [
            _node("a", type="edge.api-gateway@1", modes=["network"], properties={"watch_cost": 3}),
            _node("m", type="service.function@2", modes=["code"], properties={"watch_cost": 1}),
            _node("db", criticality="crown-jewel", properties={"telemetry": True}, modes=["data"]),
        ],
        "edges": [
            {"from": "a", "to": "m", "type": "calls"},
            {"from": "m", "to": "db", "type": "reads"},
        ],
    }
    assert [point["target"] for point in analyse(graph)["sense_points"]] == ["m"]


def test_weight_counts_each_technique_once_and_each_missing_control():
    graph = {
        "nodes": [
            _node("api", type="edge.api-gateway@1", attack_techniques=["T1190"], properties={"tls": False}),
            _node(
                "db", criticality="crown-jewel", attack_techniques=["T1190", "T1078"], properties={"telemetry": True}
            ),
        ],
        "edges": [{"from": "api", "to": "db", "type": "calls", "attack_techniques": ["T1078", "T1199"]}],
    }
    assert analyse(graph)["paths"][0]["weight"] == 4


def test_equal_coverage_breaks_the_tie_by_id():
    graph = {
        "nodes": [
            _node("b", type="edge.api-gateway@1"),
            _node("a", type="repo.github@1", modes=["code"]),
            _node("db", criticality="crown-jewel", data_classification=["PHI"], properties={"telemetry": True}),
        ],
        "edges": [
            {"from": "a", "to": "db", "type": "reads"},
            {"from": "b", "to": "db", "type": "calls"},
        ],
    }
    assert [point["target"] for point in analyse(graph)["sense_points"]] == ["a", "b"]


def test_a_control_and_existing_telemetry_are_not_suggested():
    graph = _shop()
    graph["nodes"][0]["properties"] = {"control": True}
    targets = [point["target"] for point in analyse(graph)["sense_points"]]
    assert "api" not in targets
    assert "db" not in targets
    assert targets == ["fn"]


def _hook(kind):
    return {
        "nodes": [
            {"id": "hook", "name": "payments", "type": kind, "properties": {"webhook": True}},
            _node("db", criticality="crown-jewel", properties={"telemetry": True}),
        ],
        "edges": [{"from": "hook", "to": "db", "type": "writes"}],
    }


def test_a_webhook_is_an_entry_and_its_modes_come_from_the_library():
    result = analyse(_hook("service.backend@1"))
    assert [path["nodes"] for path in result["paths"]] == [["hook", "db"]]
    assert [point["target"] for point in result["sense_points"]] == ["hook"]
    point = result["sense_points"][0]
    assert point["confidence"] == "high"
    assert point["mode"] == "code"
    assert point["modes"] == modes({"type": "service.backend@1"})
    assert point["modes"][0] == "code" and len(point["modes"]) > 1


def test_a_kind_the_library_does_not_know_is_watched_by_telemetry_with_low_confidence():
    point = analyse(_hook("thing.nobody-made@1"))["sense_points"][0]
    assert (point["mode"], point["modes"], point["confidence"]) == ("telemetry", ["telemetry"], "low")


def test_the_modes_of_a_kind_put_its_default_sense_points_first_and_a_node_can_name_its_own():
    repo = modes({"type": "repo.github@1"})
    assert repo[:2] == ["code", "secrets"] and set(repo) == {"code", "secrets", "supply_chain"}
    assert modes({"type": "repo.github@1", "modes": ["network", "network"]}) == ["network"]


def test_no_crown_jewel_ranks_nothing():
    graph = {"nodes": [_node("api", type="edge.api-gateway@1")], "edges": []}
    assert analyse(graph) == {
        "paths": [],
        "is_complete": True,
        "choke_points": [],
        "sense_points": [],
        "entry_points": ["api"],
        "crown_jewels": [],
        "coverage_gaps": [],
    }


def test_the_same_graph_ranks_the_same_way_twice():
    assert analyse(_shop()) == analyse(_shop())


def test_a_graph_that_is_not_an_object_is_refused():
    with pytest.raises(TypeError, match="must be an object"):
        analyse([])


def test_a_backwards_edge_still_carries_the_attack_to_the_service():
    graph = {
        "nodes": [
            _node("gateway", type="edge.api-gateway@1"),
            _node("service", type="service.function@2"),
            _node("ledger", criticality="crown-jewel", properties={"telemetry": True}),
        ],
        "edges": [
            {"from": "service", "to": "gateway", "type": "routes_through"},
            {"from": "service", "to": "ledger", "type": "writes"},
        ],
    }
    assert ["gateway", "service", "ledger"] in [path["nodes"] for path in analyse(graph)["paths"]]


def test_the_proving_ground_clone_reaches_the_shop_from_the_public_gateway():
    document = json.loads((_REPO / "library/examples/proving-ground.clone.json").read_text())
    result = analyse(document)
    assert "n_http_api" in result["entry_points"]
    assert "n_orders" in result["crown_jewels"]
    reached = [
        path["nodes"] for path in result["paths"] if path["nodes"][0] == "n_http_api" and "n_shop_api" in path["nodes"]
    ]
    assert reached
    assert any("n_orders" in path or "n_customers" in path for path in reached)
    assert result["coverage_gaps"]
    assert result["is_complete"] is True


def test_five_hundred_nodes_stop_at_the_path_cap():
    nodes = [
        _node("entry", type="edge.api-gateway@1"),
        _node("jewel", criticality="crown-jewel", properties={"telemetry": True}),
    ]
    nodes.extend(_node(f"n{index}", type="service.function@2") for index in range(498))
    edges = []
    for index in range(20):
        edges.append({"from": "entry", "to": f"n{index}", "type": "calls"})
        for other in range(20):
            edges.append({"from": f"n{index}", "to": f"n{20 + other}", "type": "calls"})
            edges.append({"from": f"n{20 + index}", "to": f"n{40 + other}", "type": "calls"})
        edges.append({"from": f"n{40 + index}", "to": "jewel", "type": "reads"})
    assert len(nodes) == 500
    started = time.perf_counter()
    result = analyse({"nodes": nodes, "edges": edges})
    assert time.perf_counter() - started < 2
    # 20 x 20 x 20 = 8000 paths exist, so the cap must be what stopped the search.
    assert len(result["paths"]) == 2000
    assert result["is_complete"] is False
    assert result["entry_points"] == ["entry"]
    assert result["crown_jewels"] == ["jewel"]


def test_every_schema_edge_type_has_an_attack_flow_direction():
    schema = json.loads((_REPO / "library/schemas/clone.schema.json").read_text())
    edge_types = schema["$defs"]["edge"]["properties"]["type"]["enum"]
    assert sorted(ATTACK_FLOW) == sorted(edge_types)
    assert set(ATTACK_FLOW.values()) <= {"forward", "reverse", "both", "none"}


def test_an_edge_type_with_no_direction_is_refused():
    graph = {
        "nodes": [_node("api", type="edge.api-gateway@1")],
        "edges": [{"from": "api", "to": "api", "type": "teleports_to"}],
    }
    with pytest.raises(ValueError, match="'teleports_to' has no attack-flow direction"):
        analyse(graph)


def test_a_dense_graph_with_no_reachable_jewel_stops_within_the_budget():
    # 40 services all calling each other give about 70 million partial paths of
    # six nodes. None reaches the jewel, so only the step budget can stop it.
    services = [f"s{index:02d}" for index in range(40)]
    nodes = [
        _node("entry", type="edge.api-gateway@1"),
        _node("jewel", criticality="crown-jewel", properties={"telemetry": True}),
    ]
    nodes.extend(_node(name, type="service.function@2") for name in services)
    edges = [{"from": "entry", "to": name, "type": "calls"} for name in services]
    edges.extend({"from": one, "to": other, "type": "calls"} for one in services for other in services if one != other)
    started = time.perf_counter()
    result = analyse({"nodes": nodes, "edges": edges})
    assert time.perf_counter() - started < 2
    assert result["paths"] == []
    assert result["is_complete"] is False


def test_a_shared_host_carries_the_attack_to_every_service_on_it():
    graph = {
        "nodes": [
            _node("web", type="edge.api-gateway@1"),
            _node("host", type="compute.ec2@1"),
            _node("billing", type="service.backend@1"),
            _node("ledger", criticality="crown-jewel", properties={"telemetry": True}),
        ],
        "edges": [
            {"from": "web", "to": "host", "type": "runs_on"},
            {"from": "billing", "to": "host", "type": "runs_on"},
            {"from": "billing", "to": "ledger", "type": "writes"},
        ],
    }
    assert [path["nodes"] for path in analyse(graph)["paths"]] == [["web", "host", "billing", "ledger"]]


def test_a_service_holds_the_rights_of_the_identity_it_runs_as():
    graph = {
        "nodes": [
            _node("api", type="edge.api-gateway@1"),
            _node("fn", type="service.function@2"),
            _node("admin", type="identity.iam-role@1", criticality="crown-jewel"),
        ],
        "edges": [
            {"from": "api", "to": "fn", "type": "calls"},
            {"from": "fn", "to": "admin", "type": "runs_as"},
        ],
    }
    assert [path["nodes"] for path in analyse(graph)["paths"]] == [["api", "fn", "admin"]]


def _acting():
    return {
        "nodes": [
            _node("api", type="edge.api-gateway@1", properties={"watch_cost": 3}),
            _node("fn", type="service.function@2", properties={"telemetry": True}),
            {"id": "role", "name": "role", "type": "identity.iam-role@1", "properties": {"watch_cost": 1}},  # no modes: the library's
            _node("idle", type="service.function@2", properties={"telemetry": True}),
            _node("idle-role", type="identity.iam-role@1"),
            _node("ledger", criticality="crown-jewel", properties={"telemetry": True}),
        ],
        "edges": [
            {"from": "api", "to": "fn", "type": "calls"},
            {"from": "fn", "to": "ledger", "type": "writes"},
            {"from": "fn", "to": "role", "type": "runs_as"},
            {"from": "idle", "to": "idle-role", "type": "runs_as"},
        ],
    }


def test_the_identity_a_service_runs_as_is_on_every_path_through_it():
    result = analyse(_acting())
    (path,) = result["paths"]
    assert path["nodes"] == ["api", "fn", "ledger"]
    assert path["acts_as"] == ["role"]
    assert passes(path, "role") and passes(path, "fn") and not passes(path, "idle-role")
    # The role is a gap like any node on the path, and watching it is cheaper than the gateway, so it is the pick.
    assert result["coverage_gaps"] == ["api", "role"]
    assert [point["target"] for point in result["sense_points"]] == ["role"]
    assert result["sense_points"][0]["modes"] == ["identity"]


def test_an_identity_that_is_already_a_step_of_the_path_is_not_listed_twice():
    graph = {
        "nodes": [
            _node("api", type="edge.api-gateway@1"),
            _node("fn", type="service.function@2"),
            _node("admin", type="identity.iam-role@1", criticality="crown-jewel"),
        ],
        "edges": [{"from": "api", "to": "fn", "type": "calls"}, {"from": "fn", "to": "admin", "type": "runs_as"}],
    }
    (path,) = analyse(graph)["paths"]
    assert path["nodes"] == ["api", "fn", "admin"] and path["acts_as"] == []


def test_sharing_a_group_carries_no_attack():
    graph = {
        "nodes": [
            _node("api", type="edge.api-gateway@1"),
            _node("vpc", type="network.vpc@1"),
            _node("ledger", criticality="crown-jewel", properties={"telemetry": True}),
        ],
        "edges": [
            {"from": "api", "to": "vpc", "type": "contained_in"},
            {"from": "ledger", "to": "vpc", "type": "contained_in"},
        ],
    }
    assert analyse(graph)["paths"] == []
