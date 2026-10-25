# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Runnable MCP proxy CLIs for provider-backed tools in exported skills.

A provider-backed ``mcp`` tool (``packaging_params.endpoint``, see
``modules/tool_providers.py``) exports as a self-contained, standard-library
only Python CLI instead of the bodiless stub. The script speaks MCP streamable
HTTP to whatever endpoint its *resolution steps* yield at run time, so the
same skill can be used wherever the tool is reachable.

The store makes no MCP calls here: this module only generates text. The
resolution steps are data. A provider supplies them through
``proxy_resolution()``; without an active provider the only step is the
environment variable ``MCP_PROXY_<TOOL>_URL``.

Code-generation safety: manifest values are never interpolated into Python
source. The spec is JSON-serialized with ``ensure_ascii=True``, every ``'`` is
escaped as ``\\u0027`` (so the raw triple-single-quoted literal cannot be
terminated early), and the docstring uses only ``mcp_tool_name``, which the
validator restricts to ``[A-Za-z0-9_.-]``.
"""

import ast
import hashlib
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

MCP_PROXY_GENERATOR_VERSION = "1"

_SPEC_PREFIX = "_SPEC = json.loads(r'''"
_SPEC_SUFFIX = "''')"


def env_token(name: str) -> str:
    """``name`` upper-cased with every non-alphanumeric character as ``_``."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def canonical_schema_sha256(schema: Any) -> str:
    """sha256 over the canonical JSON of an input schema.

    The same canonicalization is used by ``--check`` inside the generated
    script and by anyone recording ``input_schema_sha256`` at import.
    """
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def proxy_export_enabled() -> bool:
    """Kill switch: ``SBS_MCP_PROXY_EXPORT=false`` exports stubs as before."""
    return os.getenv("SBS_MCP_PROXY_EXPORT", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


def default_steps(tool: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Resolution when no active provider supplies steps: one env var."""
    pp = tool.get("packaging_params") or {}
    name = pp.get("mcp_tool_name") or tool.get("name", "")
    return [{"kind": "env", "var": f"MCP_PROXY_{env_token(name)}_URL"}]


def input_schema_of(tool: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON Schema of a tool's inputs, from its stored ``params``."""
    params = tool.get("params") or {}
    schema: Dict[str, Any] = {
        "type": "object",
        "properties": dict(params.get("properties") or {}),
    }
    required = params.get("required") or []
    if required:
        schema["required"] = list(required)
    return schema


def is_read_only(tool: Dict[str, Any]) -> bool:
    """True only when the tool's MCP annotations say ``readOnlyHint: true``."""
    annotations = (tool.get("extra") or {}).get("mcp_annotations") or {}
    return annotations.get("readOnlyHint") is True


_FORBIDDEN_KEY_RE = re.compile(r"^(headers|auth.*|token.*|.*secret.*)$", re.I)


def _check_no_credentials(obj: Any, where: str) -> None:
    """Refuse to embed anything that looks like a credential in an export."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and _FORBIDDEN_KEY_RE.match(k):
                raise ValueError(
                    f"Refusing to export '{where}.{k}' into a proxy script: "
                    "credentials must never be embedded in a skill package"
                )
            _check_no_credentials(v, f"{where}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _check_no_credentials(v, f"{where}[{i}]")


def build_spec(tool: Dict[str, Any], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The data embedded in a generated proxy script."""
    pp = tool.get("packaging_params") or {}
    _check_no_credentials(pp, "packaging_params")
    _check_no_credentials(steps, "steps")
    schema = input_schema_of(tool)
    return {
        "generator_version": MCP_PROXY_GENERATOR_VERSION,
        "name": pp.get("mcp_tool_name") or tool.get("name"),
        "description": tool.get("description") or "",
        "input_schema": schema,
        "input_schema_sha256": pp.get("input_schema_sha256") or "",
        "read_only": is_read_only(tool),
        "steps": steps,
    }


def _spec_literal(spec: Dict[str, Any]) -> str:
    text = json.dumps(spec, ensure_ascii=True, sort_keys=True)
    # ``'`` occurs only inside JSON strings, where ' is equivalent.
    return text.replace("'", "\\u0027")


def render_mcp_proxy_cli(tool: Dict[str, Any], steps: List[Dict[str, Any]]) -> str:
    """Render the proxy script for ``tool`` with the given resolution steps."""
    spec = build_spec(tool, steps)
    name = spec["name"]
    if not isinstance(name, str) or not re.match(r"^[A-Za-z0-9_.\-]{1,128}$", name):
        raise ValueError(f"Invalid mcp_tool_name for proxy export: {name!r}")
    header = (
        "#!/usr/bin/env python3\n"
        f'"""MCP proxy CLI for tool {name}.\n\n'
        "Generated by skillberry-store; do not edit. Run with --help for usage.\n"
        '"""\n'
        "import json\n\n"
        f"{_SPEC_PREFIX}{_spec_literal(spec)}{_SPEC_SUFFIX}\n"
    )
    return header + _RUNTIME


def extract_spec(script: str) -> Optional[Dict[str, Any]]:
    """Read the embedded spec back out of a generated script, without executing it.

    Returns None when ``script`` is not a generated proxy.
    """
    try:
        tree = ast.parse(script)
    except SyntaxError:
        return None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == "_SPEC"
            and isinstance(node.value, ast.Call)
            and node.value.args
            and isinstance(node.value.args[0], ast.Constant)
            and isinstance(node.value.args[0].value, str)
        ):
            try:
                spec = json.loads(node.value.args[0].value)
            except ValueError:
                return None
            return spec if isinstance(spec, dict) else None
    return None


def apply_mcp_proxies(
    tools: List[Dict[str, Any]], tool_modules: Optional[Dict[str, str]]
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Swap provider-backed tools' modules for runnable proxy scripts.

    Returns ``(tool_modules, proxy_tools)``: a new module map, and the tools
    that were rendered as proxies (empty when none, or when the kill switch is
    off — exports then stay byte-identical to the stub behavior).
    """
    from skillberry_store.modules import tool_providers

    modules = dict(tool_modules or {})
    proxied: List[Dict[str, Any]] = []
    if not proxy_export_enabled():
        return modules, proxied
    for tool in tools:
        endpoint = tool_providers.endpoint_of(tool)
        if endpoint is None:
            continue
        provider = tool_providers.get_provider(endpoint.get("provider"))
        steps = (
            provider.proxy_resolution(tool["packaging_params"])
            if provider is not None
            else default_steps(tool)
        )
        modules[tool["name"]] = render_mcp_proxy_cli(tool, steps)
        proxied.append(tool)
    return modules, proxied


# Must match _BUILTIN in the generated runtime below.
RESERVED_OPTION_NAMES = frozenset({"json", "describe", "check", "raw", "help"})


def _usage_line(tool: Dict[str, Any], script_path: str) -> str:
    schema = input_schema_of(tool)
    required = set(schema.get("required") or [])
    parts = [f"python3 {script_path}"]
    via_json = []
    for prop, info in schema["properties"].items():
        if prop in RESERVED_OPTION_NAMES or not prop or prop.startswith("-"):
            via_json.append(prop)
            continue
        ptype = (info or {}).get("type", "value")
        if isinstance(ptype, list):
            ptype = next((t for t in ptype if t != "null"), "value")
        if ptype == "boolean":
            arg = f"--{prop}"
        else:
            arg = f"--{prop} <{ptype}>"
        parts.append(arg if prop in required else f"[{arg}]")
    if via_json:
        keys = ", ".join(json.dumps(p) for p in via_json)
        parts.append(f"[--json '{{{keys}: ...}}']")
    return " ".join(parts)


def render_tools_section(tools: List[Dict[str, Any]], script_paths: Dict[str, str]) -> str:
    """The generic SKILL.md "Tools" section for proxy-exported tools.

    Args:
        tools: The proxy-exported tools.
        script_paths: tool name -> script path relative to the skill root.
    """
    lines = [
        "## Tools",
        "",
        "This skill includes a runnable script for each of its tools. Run them from "
        "the skill directory with `python3`; each prints the tool's result to stdout. "
        "Prefer these scripts over any other tool of the same name.",
        "",
    ]
    for tool in sorted(tools, key=lambda t: t.get("name", "")):
        path = script_paths.get(tool["name"], f"scripts/{tool['name']}.py")
        lines.append(f"### {tool['name']}")
        lines.append("")
        description = (tool.get("description") or "").strip()
        if description:
            lines.append(description)
            lines.append("")
        lines.append(f"`{_usage_line(tool, path)}`")
        lines.append("")
        if is_read_only(tool):
            lines.append("- Read-only.")
        else:
            lines.append(
                "- **May change state.** Show the user what you are about to do and "
                "get an explicit confirmation before running it."
            )
        lines.append("")
    lines.extend(
        [
            "Exit codes: `0` success · `1` the tool reported an error (message on "
            "stderr) · `2` invalid arguments · `3` the tool is not reachable (not "
            "deployed, or no endpoint configured; the message says which) · `4` the "
            "tool's input schema changed (`--check`).",
            "",
            "Use `--describe` to print a tool's full input schema, and "
            "`--json '{...}'` to pass arguments as one JSON object. Do not invent "
            "results when a script fails; report the error.",
            "",
        ]
    )
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The generated script's runtime. Fixed text, standard library only, Python
# >= 3.8. It reads everything specific to one tool from _SPEC.
# --------------------------------------------------------------------------- #
_RUNTIME = r'''
import argparse
import os
import sys
import urllib.error
import urllib.request

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26")
EXIT_OK, EXIT_TOOL_ERROR, EXIT_USAGE, EXIT_UNREACHABLE, EXIT_DRIFT = 0, 1, 2, 3, 4
VERBOSE = False


class Unreachable(Exception):
    """Endpoint resolution or transport failure (exit 3)."""


class RpcError(Exception):
    """A JSON-RPC error response from the server."""

    def __init__(self, error):
        self.error = error if isinstance(error, dict) else {"message": str(error)}
        super().__init__(self.error.get("message", "JSON-RPC error"))


def log(msg):
    if VERBOSE:
        sys.stderr.write("[mcp-proxy] %s\n" % msg)


def _env_token(name):
    out = []
    for ch in name:
        out.append(ch.upper() if ch.isalnum() and ch.isascii() else "_")
    return "".join(out)


# ---- endpoint resolution -------------------------------------------------- #

def _resolve_var(spec):
    if not isinstance(spec, dict):
        return ""
    env = spec.get("env")
    if env and os.environ.get(env):
        return os.environ[env].strip()
    path = spec.get("file")
    if path:
        try:
            with open(path) as f:
                value = f.read().strip()
            if value:
                return value
        except OSError:
            pass
    default = spec.get("default")
    return "" if default is None else str(default)


def _describe_step(step):
    kind = step.get("kind")
    if kind == "env":
        return "env %s" % step.get("var")
    if kind == "url":
        return "url %s" % step.get("url")
    if kind == "template":
        return "template %s" % step.get("template")
    return "unknown step %r" % kind


def resolve_endpoint():
    """Return (url, tool_name, step) from the first step that yields a URL."""
    tried = []
    for step in _SPEC.get("steps") or []:
        kind = step.get("kind")
        url = ""
        if kind == "env":
            url = os.environ.get(step.get("var") or "", "").strip()
        elif kind == "url":
            url = (step.get("url") or "").strip()
        elif kind == "template":
            values = {}
            for key, var in (step.get("vars") or {}).items():
                values[key] = _resolve_var(var)
            if all(values.values()):
                try:
                    url = step.get("template", "").format(**values)
                except (KeyError, IndexError, ValueError):
                    url = ""
        tried.append(_describe_step(step))
        if url:
            log("resolved %s via %s" % (url, _describe_step(step)))
            return url, step.get("tool_name") or _SPEC["name"], step
    raise Unreachable(
        "no endpoint for tool '%s'; tried: %s. Set %s to the tool's MCP URL."
        % (
            _SPEC["name"],
            "; ".join(tried) or "nothing",
            ((_SPEC.get("steps") or [{}])[0].get("var")
             or "MCP_PROXY_%s_URL" % _env_token(_SPEC["name"])),
        )
    )


def _timeout(step):
    for var in (step.get("timeout_env"), "MCP_PROXY_TIMEOUT"):
        if var and os.environ.get(var):
            try:
                return float(os.environ[var])
            except ValueError:
                pass
    return 60.0


# ---- streamable HTTP client ----------------------------------------------- #

class Session:
    def __init__(self, url, timeout):
        self.url = url
        self.timeout = timeout
        self.session_id = None
        self.protocol_version = None
        self.next_id = 0

    def _headers(self):
        h = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        auth = os.environ.get("MCP_PROXY_AUTH_HEADER", "").strip()
        if auth:
            name, sep, value = auth.partition(":")
            if sep and name.strip() and " " not in name.strip():
                h[name.strip()] = value.strip()
            else:
                h["Authorization"] = auth
        for env, header in (("TRACEPARENT", "traceparent"), ("TRACESTATE", "tracestate")):
            if os.environ.get(env):
                h[header] = os.environ[env]
        return h

    def _open(self, method, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.url, data=data, method=method, headers=self._headers())
        try:
            return urllib.request.urlopen(req, timeout=self.timeout)
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read(2000).decode("utf-8", "replace").strip()
            except Exception:
                pass
            raise Unreachable("HTTP %s from %s%s" % (e.code, self.url, (": " + detail) if detail else ""))
        except (urllib.error.URLError, OSError) as e:
            reason = getattr(e, "reason", e)
            raise Unreachable("cannot reach %s: %s" % (self.url, reason))

    def _messages(self, resp):
        """Yield JSON-RPC messages from a JSON or SSE response body."""
        ctype = (resp.headers.get("Content-Type") or "").lower()
        if "text/event-stream" in ctype:
            data_lines = []
            for raw in resp:
                line = raw.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if data_lines:
                        payload = "\n".join(data_lines)
                        data_lines = []
                        try:
                            yield json.loads(payload)
                        except ValueError:
                            log("ignoring non-JSON SSE data: %r" % payload[:200])
                    continue
                if line.startswith(":"):
                    continue
                field, _, value = line.partition(":")
                if value.startswith(" "):
                    value = value[1:]
                if field == "data":
                    data_lines.append(value)
            if data_lines:
                try:
                    yield json.loads("\n".join(data_lines))
                except ValueError:
                    pass
        else:
            text = resp.read().decode("utf-8", "replace").strip()
            if not text:
                return
            try:
                msg = json.loads(text)
            except ValueError:
                raise Unreachable("invalid JSON from %s: %r" % (self.url, text[:200]))
            if isinstance(msg, list):
                for m in msg:
                    yield m
            else:
                yield msg

    def request(self, method, params=None):
        self.next_id += 1
        rid = self.next_id
        body = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            body["params"] = params
        log("-> %s" % method)
        resp = self._open("POST", body)
        sid = resp.headers.get("Mcp-Session-Id")
        if sid:
            self.session_id = sid
        with resp:
            for msg in self._messages(resp):
                if not isinstance(msg, dict):
                    continue
                if "method" in msg and "id" in msg:
                    # A server-initiated request (sampling, elicitation, ...):
                    # this client supports none of them.
                    self._reply_unsupported(msg["id"])
                    continue
                if msg.get("id") != rid:
                    continue
                if "error" in msg:
                    raise RpcError(msg["error"])
                return msg.get("result") or {}
        raise Unreachable("no response to %s from %s" % (method, self.url))

    def notify(self, method, params=None):
        body = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        resp = self._open("POST", body)
        resp.close()

    def _reply_unsupported(self, rid):
        try:
            self.notify_raw({
                "jsonrpc": "2.0",
                "id": rid,
                "error": {"code": -32601, "message": "Method not supported by this client"},
            })
        except Unreachable:
            pass

    def notify_raw(self, body):
        resp = self._open("POST", body)
        resp.close()

    def initialize(self):
        try:
            result = self.request("initialize", {
                "protocolVersion": PROTOCOL_VERSIONS[0],
                "capabilities": {},
                "clientInfo": {"name": "skillberry-mcp-proxy", "version": _SPEC.get("generator_version", "1")},
            })
        except RpcError as e:
            raise Unreachable("initialize failed at %s: %s" % (self.url, e))
        version = result.get("protocolVersion")
        self.protocol_version = version if isinstance(version, str) else PROTOCOL_VERSIONS[0]
        self.notify("notifications/initialized")

    def close(self):
        if not self.session_id:
            return
        try:
            self._open("DELETE").close()
        except Exception:
            pass


# ---- arguments ------------------------------------------------------------ #

# Option names the CLI itself uses. A schema property with one of these names
# gets no flag of its own; pass it through --json instead.
_BUILTIN = {"json", "describe", "check", "raw", "help"}


def _json_type(info):
    t = (info or {}).get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), None)
    return t


def _json_literal(value):
    try:
        return json.loads(value)
    except ValueError:
        return value


def build_parser():
    schema = _SPEC.get("input_schema") or {}
    description = _SPEC.get("description") or ""
    parser = argparse.ArgumentParser(
        prog=os.path.basename(sys.argv[0]),
        description=description,
        epilog="Exit codes: 0 ok, 1 tool error, 2 usage, 3 unreachable, 4 schema drift.",
    )
    parser.add_argument("stdin", nargs="?", choices=["-"], help="read arguments as JSON from stdin")
    parser.add_argument("--json", dest="_json", metavar="JSON", help="arguments as one JSON object")
    parser.add_argument("--describe", dest="_describe", action="store_true", help="print the tool's spec and exit")
    parser.add_argument("--check", dest="_check", action="store_true", help="compare the live input schema with the recorded one")
    parser.add_argument("--raw", dest="_raw", action="store_true", help="print the full MCP result as JSON")
    parser.add_argument("-v", dest="_verbose", action="store_true", help="log resolution and protocol steps to stderr")
    props = []
    for i, (prop, info) in enumerate((schema.get("properties") or {}).items()):
        if prop in _BUILTIN or not prop or prop.startswith("-"):
            continue
        dest = "p%d" % i
        help_text = (info or {}).get("description") or None
        jt = _json_type(info)
        if jt == "boolean":
            parser.add_argument("--" + prop, dest=dest, action="store_const", const=True, default=None, help=help_text)
            parser.add_argument("--no-" + prop, dest=dest, action="store_const", const=False, help=argparse.SUPPRESS)
        else:
            conv = {"string": str, "integer": int, "number": float}.get(jt, _json_literal)
            parser.add_argument("--" + prop, dest=dest, type=conv, default=None, metavar=(jt or "value").upper(), help=help_text)
        props.append((prop, dest))
    return parser, props


def collect_arguments(parser, props, ns):
    args = {}
    if ns.stdin == "-":
        text = sys.stdin.read()
        try:
            data = json.loads(text) if text.strip() else {}
        except ValueError as e:
            parser.error("stdin is not valid JSON: %s" % e)
        if not isinstance(data, dict):
            parser.error("stdin JSON must be an object")
        args.update(data)
    if ns._json:
        try:
            data = json.loads(ns._json)
        except ValueError as e:
            parser.error("--json is not valid JSON: %s" % e)
        if not isinstance(data, dict):
            parser.error("--json must be a JSON object")
        args.update(data)
    for prop, dest in props:
        value = getattr(ns, dest)
        if value is not None:
            args[prop] = value
    missing = [r for r in (_SPEC.get("input_schema") or {}).get("required") or [] if r not in args]
    if missing:
        parser.error("missing required argument(s): %s" % ", ".join("--" + m for m in missing))
    return args


# ---- commands --------------------------------------------------------------- #

def _schema_sha256(schema):
    import hashlib
    canonical = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _connect():
    url, tool_name, step = resolve_endpoint()
    session = Session(url, _timeout(step))
    session.initialize()
    return session, tool_name


def cmd_check():
    session, tool_name = _connect()
    try:
        cursor = None
        while True:
            result = session.request("tools/list", {"cursor": cursor} if cursor else {})
            for tool in result.get("tools") or []:
                if tool.get("name") == tool_name:
                    live = _schema_sha256(tool.get("inputSchema") or {})
                    recorded = _SPEC.get("input_schema_sha256") or ""
                    print(json.dumps({"tool": tool_name, "live_sha256": live, "recorded_sha256": recorded, "drift": bool(recorded) and live != recorded}))
                    return EXIT_DRIFT if recorded and live != recorded else EXIT_OK
            cursor = result.get("nextCursor")
            if not cursor:
                break
    except RpcError as e:
        raise Unreachable("tools/list failed: %s" % e)
    finally:
        session.close()
    sys.stderr.write("tool '%s' is not offered by the server\n" % tool_name)
    return EXIT_DRIFT


def cmd_call(arguments, raw):
    session, tool_name = _connect()
    try:
        try:
            result = session.request("tools/call", {"name": tool_name, "arguments": arguments})
        except RpcError as e:
            sys.stderr.write("error: %s\n" % e)
            return EXIT_TOOL_ERROR
    finally:
        session.close()
    if raw:
        print(json.dumps(result, indent=2))
        return EXIT_TOOL_ERROR if result.get("isError") else EXIT_OK
    texts = [c.get("text", "") for c in result.get("content") or [] if c.get("type") == "text"]
    text = "\n".join(texts)
    if result.get("isError"):
        sys.stderr.write((text or "the tool reported an error") + "\n")
        return EXIT_TOOL_ERROR
    if text:
        print(text)
    elif result.get("structuredContent") is not None:
        print(json.dumps(result["structuredContent"]))
    return EXIT_OK


def main(argv=None):
    global VERBOSE
    parser, props = build_parser()
    ns = parser.parse_args(argv)
    VERBOSE = ns._verbose
    if ns._describe:
        print(json.dumps({
            "name": _SPEC.get("name"),
            "description": _SPEC.get("description"),
            "read_only": _SPEC.get("read_only"),
            "input_schema": _SPEC.get("input_schema"),
        }, indent=2))
        return EXIT_OK
    try:
        if ns._check:
            return cmd_check()
        return cmd_call(collect_arguments(parser, props, ns), ns._raw)
    except Unreachable as e:
        sys.stderr.write("unreachable: %s\n" % e)
        return EXIT_UNREACHABLE


if __name__ == "__main__":
    sys.exit(main())
'''
