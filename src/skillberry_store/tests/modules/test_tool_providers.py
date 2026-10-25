"""Provider-backed tools: registry, validator, dispatch and the 422 path.

Core ships no provider, so these tests register a throwaway ``test`` provider.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from skillberry_store.fast_api.tools_api import register_tools_api
from skillberry_store.modules import tool_providers
from skillberry_store.modules.tool_providers import (
    CallResult,
    ProviderContext,
    execute_via_provider,
    get_provider,
    register_provider,
    validate_mcp_packaging,
)
from skillberry_store.schemas.tool_schema import ToolSchema
from skillberry_store.services.exceptions import ToolProviderUnavailable
from skillberry_store.services.tools_service import ToolsService


class FakeProvider:
    name = "test"

    def __init__(self, result=None, raises=None):
        self.result = result or CallResult(text="sunny")
        self.raises = raises
        self.calls = []
        self.validated = []

    def validate(self, endpoint):
        self.validated.append(endpoint)
        if endpoint.get("bad"):
            raise ValueError("bad endpoint")

    async def list_tools(self, endpoint, ctx):
        return []

    async def call_tool(self, packaging_params, arguments, ctx):
        self.calls.append((packaging_params, arguments, ctx))
        if self.raises:
            raise self.raises
        return self.result

    def proxy_resolution(self, packaging_params):
        return [{"kind": "url", "url": "http://example/mcp"}]


@pytest.fixture
def active():
    """A mutable 'which plugin slugs are active' set wired as the resolver."""
    slugs = {"owner"}
    tool_providers.set_active_resolver(lambda s: s in slugs)
    yield slugs
    tool_providers.set_active_resolver(None)
    tool_providers.unregister_provider("test")


def _tool(**pp):
    params = {"mcp_tool_name": "get_weather", "endpoint": {"provider": "test"}}
    params.update(pp)
    return {
        "uuid": "u1",
        "name": "get_weather",
        "packaging_format": "mcp",
        "packaging_params": params,
        "module_name": "get_weather.py",
    }


# --- registry ------------------------------------------------------------


def test_registry_respects_active_resolver(active):
    p = FakeProvider()
    register_provider(p, "owner")
    assert get_provider("test") is p
    active.discard("owner")
    assert get_provider("test") is None


def test_unknown_provider_is_none(active):
    assert get_provider("nope") is None
    assert get_provider(None) is None


def test_two_plugins_cannot_share_a_provider_name(active):
    register_provider(FakeProvider(), "owner")
    with pytest.raises(ValueError, match="already registered"):
        register_provider(FakeProvider(), "other")


def test_invalid_provider_name_rejected():
    p = FakeProvider()
    p.name = "Bad Name"
    with pytest.raises(ValueError):
        register_provider(p, "owner")


# --- validator matrix ----------------------------------------------------


@pytest.mark.parametrize(
    "pp,ok",
    [
        ({"mcp_tool_name": "t", "mcp_url": "http://x/sse"}, True),
        ({"mcp_tool_name": "t", "endpoint": {"provider": "acme"}}, True),
        ({"mcp_tool_name": "t", "mcp_url": "u", "endpoint": {"provider": "acme"}}, True),
        ({"mcp_tool_name": "t"}, False),  # neither url nor endpoint
        ({"mcp_url": "http://x/sse"}, False),  # no tool name
        ({"mcp_tool_name": "bad name!", "mcp_url": "u"}, False),
        ({"mcp_tool_name": "x" * 129, "mcp_url": "u"}, False),
        ({"mcp_tool_name": "t", "endpoint": "acme"}, False),  # not an object
        ({"mcp_tool_name": "t", "endpoint": {}}, False),  # no provider
        ({"mcp_tool_name": "t", "endpoint": {"provider": "Acme"}}, False),
        ({"mcp_tool_name": "t", "endpoint": {"provider": "a" * 33}}, False),
        (
            {"mcp_tool_name": "t", "endpoint": {"provider": "acme", "x": "y" * 5000}},
            False,
        ),  # > 4 KiB
        (None, False),
        ({}, False),
    ],
)
def test_validator_matrix(pp, ok):
    if ok:
        validate_mcp_packaging("mcp", pp)
    else:
        with pytest.raises(ValueError):
            validate_mcp_packaging("mcp", pp)


def test_validator_ignores_non_mcp():
    validate_mcp_packaging("code", None)


def test_validator_calls_active_provider_validate(active):
    p = FakeProvider()
    register_provider(p, "owner")
    validate_mcp_packaging("mcp", _tool()["packaging_params"])
    assert p.validated == [{"provider": "test"}]
    with pytest.raises(ValueError, match="bad endpoint"):
        validate_mcp_packaging("mcp", _tool(endpoint={"provider": "test", "bad": 1})[
            "packaging_params"
        ])


def test_validator_skips_provider_validate_when_inactive(active):
    p = FakeProvider()
    register_provider(p, "owner")
    active.discard("owner")
    # Would raise if the provider's validate ran.
    validate_mcp_packaging(
        "mcp", _tool(endpoint={"provider": "test", "bad": 1})["packaging_params"]
    )


def test_schema_accepts_endpoint_without_mcp_url():
    t = ToolSchema(
        name="get_weather",
        packaging_format="mcp",
        packaging_params={"mcp_tool_name": "get_weather", "endpoint": {"provider": "x"}},
    )
    assert t.packaging_params["endpoint"]["provider"] == "x"


def test_schema_rejects_mcp_without_url_or_endpoint():
    with pytest.raises(ValueError):
        ToolSchema(
            name="t", packaging_format="mcp", packaging_params={"mcp_tool_name": "t"}
        )


# --- dispatch ------------------------------------------------------------


def test_execute_via_provider_maps_results(active):
    p = FakeProvider(result=CallResult(text="ok", structured={"a": 1}))
    register_provider(p, "owner")
    out = asyncio.run(
        execute_via_provider(_tool(), {"city": "Boston"}, ProviderContext(env_id="e"))
    )
    assert out == {"return value": "ok", "structured": {"a": 1}}
    pp, args, ctx = p.calls[0]
    assert args == {"city": "Boston"} and ctx.env_id == "e"


def test_execute_via_provider_is_error(active):
    register_provider(FakeProvider(result=CallResult(text="nope", is_error=True)), "owner")
    out = asyncio.run(execute_via_provider(_tool(), {}, ProviderContext()))
    assert out == {"error": "nope"}


def test_execute_via_provider_exception_is_error(active):
    register_provider(FakeProvider(raises=ConnectionError("down")), "owner")
    out = asyncio.run(execute_via_provider(_tool(), {}, ProviderContext()))
    assert "down" in out["error"]


def test_execute_via_provider_unavailable(active):
    with pytest.raises(ToolProviderUnavailable, match="needs provider 'test'"):
        asyncio.run(execute_via_provider(_tool(), {}, ProviderContext()))


# --- service and API -----------------------------------------------------


def _client(tool):
    app = FastAPI()
    handler = MagicMock()
    handler.descriptions = None
    handler.resolve_to_uuid_or_error.return_value = "u1"
    handler.read_dict.return_value = tool
    register_tools_api(app, service=ToolsService(handler))
    return TestClient(app), handler


def test_execute_endpoint_422_when_provider_missing(active):
    client, _ = _client(_tool())
    r = client.post("/tools/get_weather/execute", json={"city": "Boston"})
    assert r.status_code == 422
    assert "needs provider 'test'" in r.json()["detail"]


def test_execute_endpoint_uses_provider_and_forwards_bearer(active):
    p = FakeProvider(result=CallResult(text="sunny"))
    register_provider(p, "owner")
    client, handler = _client(_tool())
    r = client.post(
        "/tools/get_weather/execute",
        json={"city": "Boston"},
        headers={"Authorization": "Bearer tok123"},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"return value": "sunny"}
    assert p.calls[0][1] == {"city": "Boston"}
    assert p.calls[0][2].bearer == "tok123"
    handler.read_file.assert_not_called()  # no module file needed


def test_execute_endpoint_provider_error_is_500(active):
    register_provider(FakeProvider(result=CallResult(text="bad", is_error=True)), "owner")
    client, _ = _client(_tool())
    r = client.post("/tools/get_weather/execute", json={})
    assert r.status_code == 500
    assert "bad" in r.json()["detail"]


def test_service_create_validates_mcp_packaging():
    handler = MagicMock()
    handler.object_exists.return_value = False
    svc = ToolsService(handler)
    with pytest.raises(ValueError):
        svc.create(
            {"name": "t", "packaging_format": "mcp", "packaging_params": {"mcp_tool_name": "t"}},
            module_content=b"",
            module_filename="t.py",
        )
    handler.write_dict.assert_not_called()


def test_vmcp_source_dispatches_provider_backed(active):
    from skillberry_store.modules.vmcp_server import _HandlerToolSource

    p = FakeProvider(result=CallResult(text="sunny"))
    register_provider(p, "owner")
    src = _HandlerToolSource(handler=MagicMock())
    out = asyncio.run(src.execute(_tool(), {"city": "Boston"}, None))
    assert out == {"return value": "sunny"}


def test_vmcp_source_unavailable_provider_is_error_result(active):
    from skillberry_store.modules.vmcp_server import _HandlerToolSource

    src = _HandlerToolSource(handler=MagicMock())
    out = asyncio.run(src.execute(_tool(), {}, None))
    assert "needs provider 'test'" in out["error"]
