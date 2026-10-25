"""E2E: import from (fake) rossoctl, export, run the proxies like an agent pod would.

The store is the full in-process app with the plugin active; rossoctl's backend
is the fake. The exported scripts then run with ``python3 -I`` against a real
FastMCP streamable-HTTP server that stands in for the tool pods, reached
through the URL rossoctl injects (``ROSSOCTL_TOOL_<TOOL>_URL``).
"""

import io
import json
import socket
import subprocess
import sys
import threading
import time
import zipfile

import pytest

pytestmark = pytest.mark.integration


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def tool_pods():
    """One MCP server exposing the weather and reservation tools."""
    import uvicorn
    from mcp.server.fastmcp import FastMCP

    port = _free_port()
    mcp = FastMCP("tool-pods", host="127.0.0.1", port=port)

    @mcp.tool()
    def get_weather(city: str) -> str:
        if city == "Atlantis":
            raise ValueError("City Atlantis not found")
        return json.dumps({"temperature": 52, "weathercode": 53})

    @mcp.tool()
    def search_restaurants(city: str, cuisine: str = "", party_size: int = 2) -> str:
        return json.dumps([{"name": "Trattoria di Mare", "city": city, "cuisine": cuisine or "any"}])

    @mcp.tool()
    def place_reservation(restaurant_id: str, date_time: str, party_size: int, name: str, phone: str, email: str) -> str:
        return json.dumps({"confirmation_code": "ABC123", "restaurant_id": restaurant_id})

    server = uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started and time.time() < deadline:
        time.sleep(0.05)
    assert server.started
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    thread.join(timeout=5)


def _run(script, *args, env=None):
    proc = subprocess.run(
        [sys.executable, "-I", str(script), *args],
        capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin", **(env or {})},
    )
    return proc.returncode, proc.stdout, proc.stderr


@pytest.fixture
def package(store_client, tmp_path):
    r = store_client.post(
        "/plugins/rossoctl-tools/import",
        json={
            "namespace": "team1",
            "tools": [{"name": "weather-tool"}, {"name": "reservation-tool"}],
            "skill": {"name": "night-out-planner", "description": "Plan an evening.",
                      "instructions": "# Night-out planner\n"},
        },
    )
    assert r.status_code == 200, r.text
    r = store_client.get("/skills/night-out-planner/export-anthropic")
    assert r.status_code == 200, r.text
    zipfile.ZipFile(io.BytesIO(r.content)).extractall(tmp_path)
    return tmp_path / "night-out-planner"


def test_agent_side_proxies_call_tools(package, tool_pods):
    env = {
        "ROSSOCTL_TOOL_WEATHER_TOOL_URL": tool_pods,
        "ROSSOCTL_TOOL_RESERVATION_TOOL_URL": tool_pods,
    }
    code, out, err = _run(package / "scripts/get_weather.py", "--city", "Boston", env=env)
    assert code == 0, err
    assert json.loads(out)["weathercode"] == 53

    code, out, err = _run(package / "scripts/search_restaurants.py", "--city", "Boston", "--cuisine", "Italian", env=env)
    assert code == 0, err
    assert json.loads(out)[0]["name"] == "Trattoria di Mare"

    code, out, err = _run(
        package / "scripts/place_reservation.py",
        "--restaurant_id", "r1", "--date_time", "2026-10-22T19:00:00", "--party_size", "4",
        "--name", "Ada", "--phone", "555", "--email", "ada@example.com", env=env,
    )
    assert code == 0, err
    assert json.loads(out)["confirmation_code"] == "ABC123"

    code, _, err = _run(package / "scripts/get_weather.py", "--city", "Atlantis", env=env)
    assert code == 1 and "City Atlantis not found" in err


def test_schema_check_against_live_tool(package, tool_pods):
    env = {"ROSSOCTL_TOOL_WEATHER_TOOL_URL": tool_pods}
    code, out, _ = _run(package / "scripts/get_weather.py", "--check", env=env)
    # The fake backend's recorded schema differs from FastMCP's generated one
    # (FastMCP adds titles), so this is reported as drift with exit 4.
    report = json.loads(out)
    assert report["tool"] == "get_weather"
    assert code in (0, 4) and report["drift"] == (code == 4)


def test_tool_not_deployed_is_exit_3(package):
    # No injected URL: the proxy falls back to cluster DNS, which does not
    # resolve here, exactly like a namespace without the tool.
    code, _, err = _run(package / "scripts/get_weather.py", "--city", "Boston", env={"ROSSOCTL_NAMESPACE": "team2"})
    assert code == 3
    assert "weather-tool-mcp.team2.svc.cluster.local:8000" in err
