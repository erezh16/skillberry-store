"""Opt-in computed skill fields registered by plugins (generalized ``_npx_install``)."""

from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillberry_store.fast_api.skills_api import register_skills_api
from skillberry_store.services import field_selection as fs
from skillberry_store.services.field_selection import parse_fields_spec


@pytest.fixture
def computed():
    slugs = {"p1"}
    calls = []

    def compute(item, tools):
        calls.append(item.get("uuid"))
        return sorted(t["name"] for t in tools)

    fs.set_computed_field_active_resolver(lambda s: s in slugs)
    fs.register_computed_field("skill", "_tool_names", compute, "p1")
    yield slugs, calls
    fs.unregister_computed_field("skill", "_tool_names")
    fs.set_computed_field_active_resolver(None)


def test_name_must_be_underscored():
    with pytest.raises(ValueError):
        fs.register_computed_field("skill", "tool_names", lambda i, t: 1, "p1")


def test_cannot_shadow_declared_field():
    with pytest.raises(ValueError):
        fs.register_computed_field("skill", "_npx_install", lambda i, t: 1, "p1")


def test_never_in_a_preset(computed):
    for preset in ("minimal", "narrow", "wide", "full"):
        assert "_tool_names" not in parse_fields_spec(preset, "skill")
    assert "_tool_names" in parse_fields_spec("name,_tool_names", "skill")


def test_inactive_owner_is_unknown_field(computed):
    slugs, _ = computed
    slugs.clear()
    with pytest.raises(ValueError, match="unrecognized"):
        parse_fields_spec("name,_tool_names", "skill")


def test_unregistered_is_unknown_field():
    with pytest.raises(ValueError):
        parse_fields_spec("name,_tool_names", "skill")


# --- through the REST API --------------------------------------------------


SKILLS = [
    {"uuid": "s1", "name": "a", "description": "d", "tool_uuids": ["t1", "t2"]},
    {"uuid": "s2", "name": "b", "description": "d", "tool_uuids": []},
]
TOOLS = {"t1": {"uuid": "t1", "name": "zeta"}, "t2": {"uuid": "t2", "name": "alpha"}}


@pytest.fixture
def client(monkeypatch):
    service = MagicMock()

    def select(item, fields):
        allow = parse_fields_spec(fields, "skill")
        return {k: v for k, v in item.items() if k in allow}

    service.list_all.side_effect = lambda fields=None, **kw: [select(s, fields) for s in SKILLS]
    service.get.side_effect = lambda name, fields=None: select(
        next(s for s in SKILLS if s["name"] == name or s["uuid"] == name), fields
    )
    tools_service = MagicMock()
    tools_service.get.side_effect = lambda uuid, fields=None: TOOLS[uuid]
    import skillberry_store.services.registry as registry

    monkeypatch.setattr(registry, "get_service", lambda kind: tools_service)
    app = FastAPI()
    register_skills_api(app, service=service)
    return TestClient(app), tools_service


def test_list_with_explicit_field(client, computed):
    c, _ = client
    r = c.get("/skills/", params={"fields": "name,_tool_names"})
    assert r.status_code == 200, r.text
    assert r.json() == [
        {"name": "a", "_tool_names": ["alpha", "zeta"]},
        {"name": "b", "_tool_names": []},
    ]


def test_get_with_explicit_field(client, computed):
    c, _ = client
    r = c.get("/skills/a", params={"fields": "_tool_names"})
    assert r.status_code == 200, r.text
    assert r.json() == {"_tool_names": ["alpha", "zeta"]}


def test_default_list_does_no_extra_work(client, computed):
    c, tools_service = client
    _, calls = computed
    r = c.get("/skills/")
    assert r.status_code == 200
    assert all("_tool_names" not in s for s in r.json())
    r = c.get("/skills/", params={"fields": "full"})
    assert all("_tool_names" not in s for s in r.json())
    assert calls == []
    tools_service.get.assert_not_called()


def test_inactive_field_is_400(client, computed):
    c, _ = client
    slugs, _ = computed
    slugs.clear()
    r = c.get("/skills/", params={"fields": "name,_tool_names"})
    assert r.status_code == 400
