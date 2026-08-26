import httpx
import pytest
import respx

from frisch.collectors.debs import DebCollector
from frisch.config import Config, PuppetDBConfig, PuppetDBEnvConfig
from frisch.errors import SourceUnavailable
from frisch.model import ServiceIdentity

BASE = "https://openvoxdb.example.org:8081"


def collector(**kwargs) -> DebCollector:
    config = Config()
    config.puppetdb = PuppetDBConfig(
        environments={"prod": PuppetDBEnvConfig(base_url=BASE)},
        package_patterns=kwargs.get("patterns", ["^python3-nectar-"]),
        package_names=kwargs.get("names", ["python3-langstroth"]),
    )
    return DebCollector(config, ServiceIdentity(config.services))


def pql_responses(probe, rows):
    """Mock POST /pdb/query/v4: first call is the probe, second the query."""
    route = respx.post(f"{BASE}/pdb/query/v4")
    route.side_effect = [
        httpx.Response(200, json=probe),
        httpx.Response(200, json=rows),
    ]
    return route


@respx.mock
def test_snapshot_rollup(cursors):
    pql_responses(
        probe=[{"certname": "node1"}],
        rows=[
            {
                "certname": "api1",
                "package_name": "python3-langstroth",
                "version": "2.1.0-1",
            },
            {
                "certname": "api2",
                "package_name": "python3-langstroth",
                "version": "2.1.0-1",
            },
            {
                "certname": "api3",
                "package_name": "python3-langstroth",
                "version": "2.0.0-1",
            },
        ],
    )
    result = collector().collect("prod", cursors)
    assert result.full_snapshot
    (obs,) = result.observations
    assert obs.source_key == "pkg:python3-langstroth"
    assert obs.service == "langstroth"
    # Headline is the majority version; the full rollup is in meta.
    assert obs.version == "2.1.0-1"
    assert obs.precision == "interval"
    assert obs.meta["mixed"] is True
    assert obs.meta["versions"] == [
        {"version": "2.1.0-1", "node_count": 2},
        {"version": "2.0.0-1", "node_count": 1},
    ]
    assert obs.meta["node_count"] == 3


@respx.mock
def test_empty_probe_is_unavailable(cursors):
    respx.post(f"{BASE}/pdb/query/v4").mock(
        return_value=httpx.Response(200, json=[])
    )
    with pytest.raises(SourceUnavailable, match="package_inventory"):
        collector().collect("prod", cursors)


@respx.mock
def test_http_error_is_unavailable(cursors):
    respx.post(f"{BASE}/pdb/query/v4").mock(return_value=httpx.Response(403))
    with pytest.raises(SourceUnavailable, match="query failed"):
        collector().collect("prod", cursors)


def test_no_filter_is_unavailable(cursors):
    with pytest.raises(SourceUnavailable, match="configured"):
        collector(patterns=[], names=[]).collect("prod", cursors)


def test_backfill_unsupported(cursors):
    from frisch.errors import BackfillUnsupported

    with pytest.raises(BackfillUnsupported):
        collector().backfill("prod", cursors)


@respx.mock
def test_pql_filter_built_from_config(cursors):
    import json

    route = pql_responses(probe=[{"certname": "n"}], rows=[])
    collector().collect("prod", cursors)
    query = json.loads(route.calls[1].request.content)["query"]
    assert 'package_name ~ "^python3-nectar-"' in query
    assert 'package_name = "python3-langstroth"' in query
