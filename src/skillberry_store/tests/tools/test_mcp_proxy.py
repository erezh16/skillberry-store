"""Unit tests for the runnable MCP proxy generator (tools/anthropic/mcp_proxy.py).

The generated script is exercised by executing it in-process (``exec`` of its
source with a stubbed ``urllib.request.urlopen``) so these tests need no
network; ``tests/e2e/test_mcp_proxy_export.py`` runs real scripts against a
real streamable-HTTP MCP server.
"""

import ast
import io
import json
import sys
import zipfile

import pytest

from skillberry_store.modules import tool_providers
from skillberry_store.tools.anthropic import mcp_proxy
from skillberry_store.tools.anthropic.exporter import (
    _build_file_structure,
    export_skill_to_anthropic_format,
)
from skillberry_store.tools.anthropic.mcp_proxy import (
    canonical_schema_sha256,
    default_steps,
    extract_spec,
    render_mcp_proxy_cli,
    render_tools_section,
)


def _tool(name="get_weather", props=None, required=None, description="Weather.", **extra):
    props = props if props is not None else {"city": {"type": "string", "description": "City"}}
    tool = {
        "uuid": f"u-{name}",
        "name": name,
        "description": description,
        "packaging_format": "mcp",
        "programming_language": "python",
        "params": {"type": "object", "properties": props, "required": required or []},
        "packaging_params": {"mcp_tool_name": name, "endpoint": {"provider": "test"}},
        "module_name": f"{name}.py",
    }
    tool.update(extra)
    return tool


# --- a fake transport for the generated runtime ---------------------------


class FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, content_type="application/json", headers=None):
        super().__init__(body)
        self.headers = {"Content-Type": content_type, **(headers or {})}

    def __iter__(self):
        return iter(self.getvalue().splitlines(keepends=True))


class FakeServer:
    """Answers the generated client's requests; records what it saw."""

    def __init__(self, call_result=None, sse=False, tools=None, rpc_error=None):
        self.call_result = call_result or {"content": [{"type": "text", "text": "sunny"}]}
        self.sse = sse
        self.tools = tools
        self.rpc_error = rpc_error
        self.requests = []

    def _reply(self, payload, headers=None):
        if self.sse:
            body = (
                ": ping\n\nevent: message\ndata: " + json.dumps(payload) + "\n\n"
            ).encode()
            return FakeResponse(body, "text/event-stream", headers)
        return FakeResponse(json.dumps(payload).encode(), "application/json", headers)

    def urlopen(self, req, timeout=None):
        body = json.loads(req.data) if req.data else None
        self.requests.append((req.get_method(), req.full_url, dict(req.header_items()), body))
        if req.get_method() == "DELETE":
            return FakeResponse(b"")
        method = body.get("method")
        if "id" not in body or method is None:  # notification or a reply to us
            return FakeResponse(b"", headers={})
        if method == "initialize":
            return self._reply(
                {"jsonrpc": "2.0", "id": body["id"], "result": {"protocolVersion": "2025-06-18"}},
                {"Mcp-Session-Id": "sess-1"},
            )
        if method == "tools/call":
            if self.rpc_error:
                return self._reply({"jsonrpc": "2.0", "id": body["id"], "error": self.rpc_error})
            return self._reply({"jsonrpc": "2.0", "id": body["id"], "result": self.call_result})
        if method == "tools/list":
            return self._reply({"jsonrpc": "2.0", "id": body["id"], "result": {"tools": self.tools or []}})
        raise AssertionError(method)


def run_script(source, argv, monkeypatch, server=None, stdin=None, env=None):
    """Execute a generated script's ``main(argv)``; return (exit, stdout, stderr)."""
    for k, v in (env or {}).items():
        monkeypatch.setenv(k, v)
    ns = {"__name__": "proxy_under_test"}
    exec(compile(source, "proxy.py", "exec"), ns)
    if server is not None:
        monkeypatch.setattr(ns["urllib"].request, "urlopen", server.urlopen)
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    if stdin is not None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    try:
        code = ns["main"](argv)
    except SystemExit as e:
        code = e.code
    return code, out.getvalue(), err.getvalue()


URL_STEP = [{"kind": "url", "url": "http://tool.test/mcp"}]


# --- generation safety -----------------------------------------------------


HOSTILE = [
    "'''; import os; os.system('echo pwned') #",
    '"""\nimport os\n"""',
    "\\'''\\\\'''",
    "line1\nline2\r\n\x00nul",
    "unicode     \U0001f600",
    "{city} {0} %s",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_descriptions_cannot_inject_code(text, monkeypatch):
    tool = _tool(
        description=text,
        props={"city": {"type": "string", "description": text}, text[:5] or "x": {"type": "string"}},
    )
    source = render_mcp_proxy_cli(tool, URL_STEP)
    ast.parse(source)
    spec = extract_spec(source)
    assert spec["description"] == text
    assert spec["input_schema"]["properties"]["city"]["description"] == text
    code, out, _ = run_script(source, ["--describe"], monkeypatch)
    assert code == 0
    assert json.loads(out)["description"] == text


def test_invalid_tool_name_is_refused():
    tool = _tool()
    tool["packaging_params"]["mcp_tool_name"] = "bad'name"
    with pytest.raises(ValueError):
        render_mcp_proxy_cli(tool, URL_STEP)


@pytest.mark.parametrize("key", ["headers", "Authorization", "token", "client_secret"])
def test_credentials_are_never_embedded(key):
    tool = _tool()
    tool["packaging_params"]["endpoint"][key] = "s3cr3t"
    with pytest.raises(ValueError, match="credentials"):
        render_mcp_proxy_cli(tool, URL_STEP)


def test_script_is_stdlib_only():
    source = render_mcp_proxy_cli(_tool(), URL_STEP)
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module.split(".")[0])
    assert imported <= {"json", "argparse", "os", "sys", "urllib", "hashlib"}


def test_render_is_deterministic():
    assert render_mcp_proxy_cli(_tool(), URL_STEP) == render_mcp_proxy_cli(_tool(), URL_STEP)


def test_default_steps_env_name():
    assert default_steps(_tool(name="get-weather.v2")) == [
        {"kind": "env", "var": "MCP_PROXY_GET_WEATHER_V2_URL"}
    ]


# --- argparse from schema --------------------------------------------------


ALL_TYPES = {
    "s": {"type": "string"},
    "i": {"type": "integer"},
    "n": {"type": "number"},
    "b": {"type": "boolean"},
    "a": {"type": "array", "items": {"type": "string"}},
    "o": {"type": "object"},
    "u": {"type": ["string", "null"]},
}


def test_argparse_every_json_type(monkeypatch):
    server = FakeServer()
    source = render_mcp_proxy_cli(_tool(props=ALL_TYPES), URL_STEP)
    argv = ["--s", "x", "--i", "3", "--n", "2.5", "--b", "--a", '["p","q"]', "--o", '{"k":1}', "--u", "y"]
    code, out, _ = run_script(source, argv, monkeypatch, server)
    assert code == 0, out
    call = [r for r in server.requests if r[3] and r[3].get("method") == "tools/call"][0]
    assert call[3]["params"]["arguments"] == {
        "s": "x", "i": 3, "n": 2.5, "b": True, "a": ["p", "q"], "o": {"k": 1}, "u": "y"
    }


def test_no_flag_for_boolean_false(monkeypatch):
    server = FakeServer()
    source = render_mcp_proxy_cli(_tool(props={"b": {"type": "boolean"}}), URL_STEP)
    run_script(source, ["--no-b"], monkeypatch, server)
    call = [r for r in server.requests if r[3] and r[3].get("method") == "tools/call"][0]
    assert call[3]["params"]["arguments"] == {"b": False}


def test_json_and_stdin_arguments(monkeypatch):
    server = FakeServer()
    source = render_mcp_proxy_cli(_tool(), URL_STEP)
    run_script(source, ["-", "--json", '{"x": 1}'], monkeypatch, server, stdin='{"city": "Boston"}')
    call = [r for r in server.requests if r[3] and r[3].get("method") == "tools/call"][0]
    assert call[3]["params"]["arguments"] == {"city": "Boston", "x": 1}


def test_missing_required_is_usage_error(monkeypatch):
    source = render_mcp_proxy_cli(_tool(required=["city"]), URL_STEP)
    code, _, err = run_script(source, [], monkeypatch, FakeServer())
    assert code == 2
    assert "--city" in err


def test_bad_integer_is_usage_error(monkeypatch):
    source = render_mcp_proxy_cli(_tool(props={"i": {"type": "integer"}}), URL_STEP)
    code, _, _ = run_script(source, ["--i", "abc"], monkeypatch, FakeServer())
    assert code == 2


# --- resolution order --------------------------------------------------------


STEPS = [
    {"kind": "env", "var": "FIRST_URL"},
    {"kind": "template", "template": "{gw}", "vars": {"gw": {"env": "GW_URL"}}, "tool_name": "pfx_get_weather"},
    {
        "kind": "template",
        "template": "http://{tool}-mcp.{ns}:{port}{path}",
        "vars": {
            "tool": {"default": "weather-tool"},
            "ns": {"env": "NS", "file": "/nonexistent/ns", "default": "team1"},
            "port": {"env": "PORT", "default": "8000"},
            "path": {"default": "/mcp"},
        },
    },
]


@pytest.mark.parametrize(
    "env,url,tool_name",
    [
        ({"FIRST_URL": "http://first/mcp", "GW_URL": "http://gw/mcp"}, "http://first/mcp", "get_weather"),
        ({"GW_URL": "http://gw/mcp"}, "http://gw/mcp", "pfx_get_weather"),
        ({}, "http://weather-tool-mcp.team1:8000/mcp", "get_weather"),
        ({"NS": "team2", "PORT": "9090"}, "http://weather-tool-mcp.team2:9090/mcp", "get_weather"),
    ],
)
def test_resolution_order(env, url, tool_name, monkeypatch):
    for var in ("FIRST_URL", "GW_URL", "NS", "PORT"):
        monkeypatch.delenv(var, raising=False)
    server = FakeServer()
    source = render_mcp_proxy_cli(_tool(), STEPS)
    code, _, _ = run_script(source, ["--city", "Boston"], monkeypatch, server, env=env)
    assert code == 0
    assert {r[1] for r in server.requests} == {url}
    call = [r for r in server.requests if r[3] and r[3].get("method") == "tools/call"][0]
    assert call[3]["params"]["name"] == tool_name


def test_template_reads_file(tmp_path, monkeypatch):
    nsfile = tmp_path / "namespace"
    nsfile.write_text("team7\n")
    steps = [{"kind": "template", "template": "http://x.{ns}/mcp", "vars": {"ns": {"file": str(nsfile)}}}]
    server = FakeServer()
    run_script(render_mcp_proxy_cli(_tool(), steps), [], monkeypatch, server)
    assert server.requests[0][1] == "http://x.team7/mcp"


def test_no_endpoint_is_exit_3(monkeypatch):
    monkeypatch.delenv("MCP_PROXY_GET_WEATHER_URL", raising=False)
    source = render_mcp_proxy_cli(_tool(), default_steps(_tool()))
    code, _, err = run_script(source, ["--city", "Boston"], monkeypatch, FakeServer())
    assert code == 3
    assert "MCP_PROXY_GET_WEATHER_URL" in err


# --- protocol, results and exit codes ---------------------------------------


@pytest.mark.parametrize("sse", [False, True])
def test_success_prints_text(sse, monkeypatch):
    server = FakeServer(sse=sse, call_result={"content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]})
    code, out, _ = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), ["--city", "X"], monkeypatch, server)
    assert code == 0
    assert out == "a\nb\n"
    methods = [(r[0], (r[3] or {}).get("method")) for r in server.requests]
    assert methods == [
        ("POST", "initialize"),
        ("POST", "notifications/initialized"),
        ("POST", "tools/call"),
        ("DELETE", None),
    ]
    # Session id and negotiated protocol version are sent after initialize.
    headers = {k.lower(): v for k, v in server.requests[2][2].items()}
    assert headers["mcp-session-id"] == "sess-1"
    assert headers["mcp-protocol-version"] == "2025-06-18"
    assert "text/event-stream" in headers["accept"]


@pytest.mark.parametrize("sse", [False, True])
def test_is_error_is_exit_1(sse, monkeypatch):
    server = FakeServer(sse=sse, call_result={"content": [{"type": "text", "text": "City X not found"}], "isError": True})
    code, out, err = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), ["--city", "X"], monkeypatch, server)
    assert code == 1
    assert out == ""
    assert "City X not found" in err


def test_rpc_error_is_exit_1(monkeypatch):
    server = FakeServer(rpc_error={"code": -32602, "message": "invalid params"})
    code, _, err = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), [], monkeypatch, server)
    assert code == 1 and "invalid params" in err


def test_raw_prints_result_json(monkeypatch):
    server = FakeServer()
    code, out, _ = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), ["--raw"], monkeypatch, server)
    assert code == 0
    assert json.loads(out)["content"][0]["text"] == "sunny"


def test_structured_only_result(monkeypatch):
    server = FakeServer(call_result={"content": [], "structuredContent": {"t": 1}})
    code, out, _ = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), [], monkeypatch, server)
    assert code == 0 and json.loads(out) == {"t": 1}


def test_server_initiated_request_gets_method_not_found(monkeypatch):
    class Asking(FakeServer):
        def urlopen(self, req, timeout=None):
            body = json.loads(req.data) if req.data else None
            if body and body.get("method") == "tools/call":
                self.requests.append(("POST", req.full_url, {}, body))
                payload = (
                    "data: " + json.dumps({"jsonrpc": "2.0", "id": 99, "method": "sampling/createMessage", "params": {}}) + "\n\n"
                    "data: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "ok"}]}}) + "\n\n"
                )
                return FakeResponse(payload.encode(), "text/event-stream")
            return super().urlopen(req, timeout)

    server = Asking(sse=True)
    code, out, _ = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), [], monkeypatch, server)
    assert code == 0 and out == "ok\n"
    replies = [r[3] for r in server.requests if r[3] and r[3].get("id") == 99]
    assert replies and replies[0]["error"]["code"] == -32601


def test_auth_header_and_trace_from_env(monkeypatch):
    server = FakeServer()
    run_script(
        render_mcp_proxy_cli(_tool(), URL_STEP), [], monkeypatch, server,
        env={"MCP_PROXY_AUTH_HEADER": "Bearer abc", "TRACEPARENT": "00-1-2-01"},
    )
    headers = {k.lower(): v for k, v in server.requests[0][2].items()}
    assert headers["authorization"] == "Bearer abc"
    assert headers["traceparent"] == "00-1-2-01"


def test_http_error_is_exit_3(monkeypatch):
    import urllib.error

    class Down(FakeServer):
        def urlopen(self, req, timeout=None):
            raise urllib.error.URLError("connection refused")

    code, _, err = run_script(render_mcp_proxy_cli(_tool(), URL_STEP), [], monkeypatch, Down())
    assert code == 3 and "connection refused" in err


def test_check_detects_drift(monkeypatch):
    schema = {"type": "object", "properties": {"city": {"type": "string"}}}
    tool = _tool()
    tool["packaging_params"]["input_schema_sha256"] = canonical_schema_sha256(schema)
    source = render_mcp_proxy_cli(tool, URL_STEP)
    same = FakeServer(tools=[{"name": "get_weather", "inputSchema": schema}])
    assert run_script(source, ["--check"], monkeypatch, same)[0] == 0
    changed = FakeServer(tools=[{"name": "get_weather", "inputSchema": {"type": "object"}}])
    code, out, _ = run_script(source, ["--check"], monkeypatch, changed)
    assert code == 4 and json.loads(out)["drift"] is True


# --- export wiring ---------------------------------------------------------


class _Provider:
    name = "test"

    def validate(self, endpoint):
        pass

    async def list_tools(self, endpoint, ctx):
        return []

    async def call_tool(self, pp, args, ctx):
        raise NotImplementedError

    def proxy_resolution(self, pp):
        return [{"kind": "url", "url": "http://from-provider/mcp"}]


@pytest.fixture
def provider():
    tool_providers.set_active_resolver(lambda s: True)
    tool_providers.register_provider(_Provider(), "owner")
    yield
    tool_providers.unregister_provider("test")
    tool_providers.set_active_resolver(None)


SKILL = {"name": "planner", "description": "Plan things.", "tool_uuids": [], "snippet_uuids": []}


def test_export_renders_proxy_with_provider_steps(provider):
    files = _build_file_structure(SKILL, [_tool()], [], {"get_weather": "def get_weather(city: str):\n    pass"})
    script = files["planner/scripts/get_weather.py"].decode()
    assert extract_spec(script)["steps"] == [{"kind": "url", "url": "http://from-provider/mcp"}]
    skill_md = files["planner/SKILL.md"].decode()
    assert "## Tools" in skill_md
    assert "python3 scripts/get_weather.py [--city <string>]" in skill_md


def test_export_without_provider_uses_env_step():
    files = _build_file_structure(SKILL, [_tool()], [], {})
    spec = extract_spec(files["planner/scripts/get_weather.py"].decode())
    assert spec["steps"] == [{"kind": "env", "var": "MCP_PROXY_GET_WEATHER_URL"}]


def test_sse_mcp_tool_keeps_stub():
    tool = _tool()
    tool["packaging_params"] = {"mcp_tool_name": "get_weather", "mcp_url": "http://x/sse"}
    stub = "def get_weather(city: str):\n    pass"
    files = _build_file_structure(SKILL, [tool], [], {"get_weather": stub})
    assert files["planner/scripts/get_weather.py"].decode() == stub + "\n"
    assert "## Tools" not in files["planner/SKILL.md"].decode()


def test_kill_switch_keeps_stub(monkeypatch):
    monkeypatch.setenv("SBS_MCP_PROXY_EXPORT", "false")
    stub = "def get_weather(city: str):\n    pass"
    files = _build_file_structure(SKILL, [_tool()], [], {"get_weather": stub})
    assert files["planner/scripts/get_weather.py"].decode() == stub + "\n"
    assert "## Tools" not in files["planner/SKILL.md"].decode()


def test_export_without_mcp_tools_is_unchanged():
    code_tool = {"name": "add", "programming_language": "python", "packaging_format": "code"}
    before = export_skill_to_anthropic_format(SKILL, [code_tool], [], {"add": "def add(a, b):\n    return a + b"})
    names = zipfile.ZipFile(io.BytesIO(before)).namelist()
    assert names == ["planner/SKILL.md", "planner/scripts/add.py"]
    md = zipfile.ZipFile(io.BytesIO(before)).read("planner/SKILL.md").decode()
    assert "## Tools" not in md


@pytest.mark.parametrize("placement", ["append", "prepend", "omit"])
def test_tools_section_placement(placement):
    skill = {**SKILL, "extra": {"tools_section": placement}}
    snippet = {"name": "i", "content": "# Planner\n\nInstructions.", "tags": ["file:SKILL.md"]}
    md = _build_file_structure(skill, [_tool()], [snippet], {})["planner/SKILL.md"].decode()
    assert md.startswith("---\n")
    if placement == "omit":
        assert "## Tools" not in md
    elif placement == "append":
        assert md.index("Instructions.") < md.index("## Tools")
    else:
        assert md.index("## Tools") < md.index("# Planner")


def test_tools_section_marks_writes():
    ro = _tool(name="get_weather", extra={"mcp_annotations": {"readOnlyHint": True}})
    rw = _tool(name="place_reservation")
    text = render_tools_section([ro, rw], {})
    weather = text.split("### get_weather")[1].split("###")[0]
    booking = text.split("### place_reservation")[1]
    assert "Read-only" in weather
    assert "explicit confirmation" in booking


def test_npx_cache_key_includes_generator_version():
    from skillberry_store.tools import publish

    key = publish.cache_key({"modified_at": "a"}, [], [])
    assert mcp_proxy.MCP_PROXY_GENERATOR_VERSION in key


def test_property_named_like_an_option(monkeypatch):
    """`verbose` and `v` are ordinary flags; reserved names go through --json."""
    props = {"verbose": {"type": "boolean"}, "v": {"type": "string"}, "check": {"type": "string"}}
    server = FakeServer()
    source = render_mcp_proxy_cli(_tool(props=props), URL_STEP)
    code, _, _ = run_script(source, ["--verbose", "--v", "x", "--json", '{"check": "c"}'], monkeypatch, server)
    assert code == 0
    call = [r for r in server.requests if r[3] and r[3].get("method") == "tools/call"][0]
    assert call[3]["params"]["arguments"] == {"verbose": True, "v": "x", "check": "c"}
    usage = render_tools_section([_tool(props=props)], {})
    assert "[--verbose]" in usage and "--json '{\"check\": ...}'" in usage
