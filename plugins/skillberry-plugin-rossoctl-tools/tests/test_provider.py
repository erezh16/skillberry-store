"""RossoctlProvider: validation, store-side calls and proxy resolution."""

import asyncio

import pytest

from skillberry_store.modules.tool_providers import ProviderContext
from skillberry_plugin_rossoctl_tools.provider import (
    RossoctlProvider,
    env_token,
    proxy_steps,
    result_text,
)


def pp(**ep):
    endpoint = {"provider": "rossoctl", "tool": "weather-tool", "namespace": "team1", "port": 8000, "path": "/mcp"}
    endpoint.update(ep)
    return {"mcp_tool_name": "get_weather", "endpoint": endpoint}


@pytest.mark.parametrize(
    "ep",
    [
        {"tool": "Weather"},
        {"tool": "a" * 64},
        {"namespace": "team_1"},
        {"port": 0},
        {"port": 70000},
        {"port": "8000"},
        {"path": "mcp"},
        {"gateway_prefix": "bad prefix"},
    ],
)
def test_validate_rejects(ep, backend):
    with pytest.raises(ValueError):
        RossoctlProvider(backend).validate(pp(**ep)["endpoint"])


def test_validate_accepts(backend):
    RossoctlProvider(backend).validate(pp(gateway_prefix="weather_")["endpoint"])


def test_env_token_matches_rossoctl_rule():
    assert env_token("weather-tool") == "WEATHER_TOOL"
    assert env_token("night.out-utils") == "NIGHT_OUT_UTILS"


def test_proxy_steps_order():
    steps = proxy_steps(pp())
    assert steps[0]["kind"] == "env" and steps[0]["var"] == "ROSSOCTL_TOOL_WEATHER_TOOL_URL"
    assert len(steps) == 2  # no gateway step without a gateway prefix
    dns = steps[1]
    assert dns["template"] == "http://{tool}-mcp.{ns}.svc.cluster.local:{port}{path}"
    assert dns["vars"]["ns"] == {
        "env": "ROSSOCTL_NAMESPACE",
        "file": "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
        "default": "team1",
    }
    assert dns["vars"]["port"] == {"env": "ROSSOCTL_TOOL_WEATHER_TOOL_PORT", "default": "8000"}
    assert all(s["timeout_env"] == "ROSSOCTL_TOOL_TIMEOUT" for s in steps)


def test_proxy_steps_with_gateway():
    steps = proxy_steps(pp(gateway_prefix="weather_"))
    assert steps[1]["vars"]["gw"] == {"env": "ROSSOCTL_MCP_GATEWAY_URL"}
    assert steps[1]["tool_name"] == "weather_get_weather"


def test_call_tool_and_list_tools(backend, fake):
    provider = RossoctlProvider(backend)
    ok = asyncio.run(provider.call_tool(pp(), {"city": "Boston"}, ProviderContext()))
    assert not ok.is_error and "52" in ok.text
    err = asyncio.run(provider.call_tool(pp(), {"city": "Atlantis"}, ProviderContext()))
    assert err.is_error and err.text == "City Atlantis not found"
    tools = asyncio.run(provider.list_tools(pp()["endpoint"], ProviderContext()))
    assert tools[0]["name"] == "get_weather" and tools[0]["annotations"] == {"readOnlyHint": True}
    assert {r["host"] for r in fake.requests} <= {"rossoctl-backend.test", "keycloak.test"}


def test_result_text_joins_text_and_data():
    assert result_text({"content": [{"type": "text", "text": "a"}, {"type": "data", "data": {"x": 1}}, {"type": "unknown"}]}) == 'a\n{"x": 1}'
