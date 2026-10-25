# skillberry-plugin-rossoctl-tools

Use [rossoctl](https://github.com/rossoctl/rossoctl) tools in Skillberry Store
skills. Design: [docs/design/rossoctl-skills.md](../../docs/design/rossoctl-skills.md).

rossoctl hosts every rossoctl tool. The store only holds *proxies* of them,
and reaches rossoctl only through its backend REST API. It never connects to
tool pods. With this plugin active:

- **Import.** `POST /plugins/rossoctl-tools/import` creates one store `mcp`
  tool per MCP tool of each selected rossoctl tool, plus an optional skill. The
  tools carry `packaging_params.endpoint = {provider: "rossoctl", tool,
  namespace, port, path}`.
- **Store-side execution.** `POST /tools/{id}/execute` and vMCP calls on these
  tools go through rossoctl's `POST /api/v1/tools/{ns}/{tool}/invoke`, so rossoctl
  makes the call under its own authorization. The store can then test,
  evaluate and dream against live tools.
- **Export.** Each tool exports as a runnable proxy CLI, `scripts/<tool>.py`.
  The script resolves the tool's in-cluster URL in the *agent's* namespace at
  run time, trying in order:
  1. `ROSSOCTL_TOOL_<TOOL>_URL` (rossoctl injects it at agent creation);
  2. `ROSSOCTL_MCP_GATEWAY_URL` (only for tools imported with a `gateway_prefix`);
  3. `http://<tool>-mcp.<ns>.svc.cluster.local:<port>/mcp`. Here `<ns>` comes
     from `ROSSOCTL_NAMESPACE`, else the pod's ServiceAccount namespace file,
     else the import namespace. `<port>` comes from `ROSSOCTL_TOOL_<TOOL>_PORT`,
     else the import port.

  The package also gets:
  - `rossoctl-tools.json` (`skillberry.ai/rossoctl-tools/v1`);
  - frontmatter `metadata.rossoctl-requires-tools`;
  - a "Tools (rossoctl)" note.
- **Re-import.** A package that contains `rossoctl-tools.json` re-imports as the
  same proxy tools, so "export → edit in git → `POST /skills/import-anthropic`"
  is lossless.
- **Dependencies for rossoctl.** The opt-in computed field
  `_rossoctl_required_tools` is returned only when named explicitly, for example
  `GET /skills/?fields=uuid,name,_rossoctl_required_tools`. Its value is the
  sorted list of rossoctl tools a skill needs. rossoctl's skill autosync reads it.

`<TOOL>` is the rossoctl tool name upper-cased, with every non-alphanumeric
character replaced by `_` (`weather-tool` → `WEATHER_TOOL`). rossoctl uses the
same rule.

## Routes

| Route | Permission | What it does |
|---|---|---|
| `GET /plugins/rossoctl-tools/tools?namespace=` | `tools:list` | rossoctl's tools in a namespace (pass-through of its `GET /tools`) |
| `POST /plugins/rossoctl-tools/import` | `tools:create` | `{namespace, tools: [{name, gateway_prefix?}], skill?: {name, description, instructions?, tags?}, tags?}` → `{tools: [{uuid, name, rossoctl_tool}], skill_uuid, errors}` |
| `POST /plugins/rossoctl-tools/check` | `tools:list` | `{skill?, tool_uuids?}` → per-tool input-schema drift against the live tool. No writes. |
| `POST /plugins/rossoctl-tools/resync` | `tools:create` | A new version of each drifted tool (same name, new uuid), plus a new skill version |

On a name collision with a store tool that proxies a *different* rossoctl tool,
the new tool is named `<rossoctl_tool>__<mcp_tool>`, with `-` replaced by `_`.

## Configuration

| Env | Default | Meaning |
|---|---|---|
| `ROSSOCTL_BACKEND_URL` | unset; the plugin is inactive without it | for example `http://rossoctl-backend.rossoctl-system.svc.cluster.local:8000` |
| `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT` | unset | Keycloak client-credentials grant for a service account with the `rossoctl-operator` role. The token is cached until 30 s before expiry and refreshed once on a 401. Leave these unset when rossoctl runs with `ENABLE_AUTH=false`. |
| `ROSSOCTL_ALLOWED_NAMESPACES` | `""` (any namespace) | Comma-separated namespaces the plugin may target |
| `ROSSOCTL_TIMEOUT` | `30` | Seconds per backend call |
| `ROSSOCTL_DEFAULT_NAMESPACE` | `default` | Import namespace for re-imported packages whose scripts do not record one |
| `ROSSOCTL_FORWARD_CALLER_TOKEN` | `false` | Forward the store caller's bearer to rossoctl instead of the service token. Use it for delegated ACL deployments where both trust the same Keycloak. |

The proxy script reads these at run time, in the agent's pod:
- the variables listed under **Export** above;
- `ROSSOCTL_TOOL_TIMEOUT`, in seconds (default 60);
- core's `MCP_PROXY_AUTH_HEADER`, `TRACEPARENT` and `TRACESTATE`.

## Enabling

- Install the plugin with the `plugin-rossoctl-tools` extra, or `plugins-all`.
- Set `ROSSOCTL_BACKEND_URL`.
- Make sure the deployment's plugin config does not list `rossoctl-tools` under
  `disabled`.

rossoctl's Helm chart does all of this when `featureFlags.toolSkills=true`.

Without the plugin, the store still holds and exports these tools:
- their proxies resolve only `MCP_PROXY_<TOOL>_URL`;
- store-side execution returns 422 (provider unavailable);
- `fields=_rossoctl_required_tools` returns 400.

## Tests

```bash
source .venv/bin/activate
pytest plugins/skillberry-plugin-rossoctl-tools/tests
```

The tests run against a fake rossoctl backend (`tests/fake_backend.py`) through
`httpx` transports, with no network. The e2e tests run the exported scripts
against a FastMCP streamable-HTTP server that stands in for the tool pods.
