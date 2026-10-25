"""E2E: exported proxy scripts call a real streamable-HTTP MCP server.

A FastMCP ``streamable-http`` server runs in-process as a *test fixture* only
(store runtime code never uses an MCP server or client for this). A test
provider whose ``proxy_resolution`` points at it is registered, a skill with
provider-backed tools is exported through ``GET /skills/{name}/export-anthropic``,
and each generated script is run with ``python3 -I`` in a subprocess.
"""

import io
import json
import socket
import subprocess
import sys
import threading
import time
import zipfile

import httpx
import pytest

from skillberry_store.modules import tool_providers

BASE_URL = "http://localhost:8000"

pytestmark = pytest.mark.integration


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mcp_server():
    """A FastMCP streamable-HTTP server with a success and a failing tool."""
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    port = _free_port()
    mcp = FastMCP("proxy-fixture", host="127.0.0.1", port=port)

    @mcp.tool()
    def get_weather(city: str) -> str:
        """Current weather for a city."""
        if city == "Atlantis":
            raise ValueError("City Atlantis not found")
        return json.dumps({"city": city, "temperature": 52, "weathercode": 53})

    @mcp.tool()
    def add(a: int, b: int, verbose: bool = False) -> str:
        """Add two numbers."""
        return f"{a}+{b}={a + b}" if verbose else str(a + b)

    config = uvicorn.Config(
        mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started, "fixture MCP server did not start"
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


class _FixtureProvider:
    name = "proxytest"

    def __init__(self, url):
        self.url = url

    def validate(self, endpoint):
        pass

    async def list_tools(self, endpoint, ctx):
        return []

    async def call_tool(self, pp, args, ctx):
        raise NotImplementedError

    def proxy_resolution(self, pp):
        return [
            {"kind": "env", "var": "PROXYTEST_OVERRIDE_URL"},
            {"kind": "url", "url": self.url},
        ]


@pytest.fixture
def provider(mcp_server):
    previous = tool_providers._active_resolver
    tool_providers.register_provider(_FixtureProvider(mcp_server), "proxytest-owner")
    tool_providers.set_active_resolver(
        lambda slug: slug == "proxytest-owner" or (previous is None or previous(slug))
    )
    yield
    tool_providers.unregister_provider("proxytest")
    tool_providers.set_active_resolver(previous)


def _create_tool(name, props, required, description):
    from skillberry_store.services.registry import get_service

    svc = get_service("tool")
    data = {
        "name": name,
        "description": description,
        "packaging_format": "mcp",
        "programming_language": "python",
        "params": {"type": "object", "properties": props, "required": required},
        "packaging_params": {
            "mcp_tool_name": name,
            "endpoint": {"provider": "proxytest"},
        },
        "tags": ["mcp"],
    }
    return svc.create(data, module_content=b"# stub\n", module_filename=f"{name}.py")


def _export(skill_name):
    r = httpx.get(f"{BASE_URL}/skills/{skill_name}/export-anthropic", timeout=30)
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _run(script, *args, env=None):
    proc = subprocess.run(
        [sys.executable, "-I", str(script), *args],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def exported(run_sbs, provider, tmp_path):
    suffix = str(int(time.time() * 1000))
    skill_name = f"proxy_e2e_skill_{suffix}"
    weather = _create_tool(
        "get_weather", {"city": {"type": "string"}}, ["city"], "Current weather."
    )
    add = _create_tool(
        "add",
        {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
            "verbose": {"type": "boolean"},
        },
        ["a", "b"],
        "Add two numbers.",
    )
    r = httpx.post(
        f"{BASE_URL}/skills/",
        params={
            "name": skill_name,
            "description": "Proxy export e2e",
            "tool_uuids": [weather["uuid"], add["uuid"]],
        },
        timeout=30,
    )
    assert r.status_code == 200, r.text
    zf = _export(skill_name)
    zf.extractall(tmp_path)
    yield tmp_path / skill_name, skill_name
    httpx.delete(f"{BASE_URL}/skills/{skill_name}", timeout=30)
    for t in (weather, add):
        httpx.delete(f"{BASE_URL}/tools/{t['uuid']}", timeout=30)


def test_exported_proxies_call_live_server(exported):
    root, _ = exported
    code, out, err = _run(root / "scripts" / "get_weather.py", "--city", "Boston")
    assert code == 0, err
    assert json.loads(out) == {"city": "Boston", "temperature": 52, "weathercode": 53}

    code, out, err = _run(root / "scripts" / "add.py", "--a", "2", "--b", "40", "--verbose")
    assert code == 0, err
    assert out.strip() == "2+40=42"

    code, out, err = _run(root / "scripts" / "add.py", "--json", '{"a": 1, "b": 2}')
    assert code == 0, err
    assert out.strip() == "3"


def test_tool_error_is_exit_1(exported):
    root, _ = exported
    code, out, err = _run(root / "scripts" / "get_weather.py", "--city", "Atlantis")
    assert code == 1
    assert "City Atlantis not found" in err


def test_unreachable_is_exit_3(exported):
    root, _ = exported
    dead = f"http://127.0.0.1:{_free_port()}/mcp"
    code, _, err = _run(
        root / "scripts" / "get_weather.py",
        "--city",
        "Boston",
        env={"PROXYTEST_OVERRIDE_URL": dead},
    )
    assert code == 3
    assert "cannot reach" in err


def test_usage_error_is_exit_2(exported):
    root, _ = exported
    code, _, _ = _run(root / "scripts" / "add.py", "--a", "1")
    assert code == 2


def test_skill_md_has_tools_section(exported):
    root, _ = exported
    md = (root / "SKILL.md").read_text()
    assert "## Tools" in md
    assert "python3 scripts/get_weather.py --city <string>" in md


def test_without_provider_proxy_needs_env(exported, mcp_server):
    """Same tools, provider unregistered: export still works, env var step only."""
    _, skill_name = exported
    tool_providers.unregister_provider("proxytest")
    zf = _export(skill_name)
    script = zf.read(f"{skill_name}/scripts/get_weather.py").decode()
    from skillberry_store.tools.anthropic.mcp_proxy import extract_spec

    assert extract_spec(script)["steps"] == [
        {"kind": "env", "var": "MCP_PROXY_GET_WEATHER_URL"}
    ]
    r = httpx.post(
        f"{BASE_URL}/tools/get_weather/execute", json={"city": "Boston"}, timeout=30
    )
    assert r.status_code == 422
    assert "needs provider 'proxytest'" in r.json()["detail"]
