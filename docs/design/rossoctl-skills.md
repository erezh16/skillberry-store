# Skills that use rossoctl tools — Design

Status: **Proposal (revision 5)**
Owner: skillberry-store (the `rossoctl-tools` plugin). rossoctl is unchanged.
Scope: Stage 5 of [rossoctl-integration.md](../rossoctl-integration.md#staged-improvement), "Skills that use rossoctl tools", extended to store tools that *become* rossoctl tools. The integration doc's sketch is the section [Stage 1: store skills that include rossoctl tools through proxy scripts](../rossoctl-integration.md#stage-1-store-skills-that-include-rossoctl-tools-through-proxy-scripts).

Revision notes:
* **r5 — Core design principles (§0.1) applied to the whole design.**
  - **rossoctl gets no changes.** Every rossoctl change of r4 (R1–R10: flag, required-tools annotation, agent-create validation and URL injection, UI, Helm, installer, `/connect` annotations) is dropped; §4.6 gives the store-side replacement for each.
  - **Stage 3 no longer needs a rossoctl runner or endpoint.** It is renumbered **stage 2**. The plugin deploys promoted tools through rossoctl's *existing* `POST /api/v1/tools`, with a runner image owned by skillberry-store.
  - **Core is cut to three generic changes:** the `isError` fix, a tool-provider hook (now also deciding how a provider-backed tool is exported) and package contribution hooks. The proxy generator moves into the plugin. The opt-in computed skill field is dropped: no rossoctl component reads it any more. §15 lists what the r4 implementation must change.
* r4 — rossoctl hosts every rossoctl tool; the store works with proxies through rossoctl's backend API. Removed streamable vMCP (old stage 2).
* r3 — Staged the feature. r2 — Core/plugin split. r1 — Initial proposal.

## 0. Principles and stages

### 0.1 Core design principles

These hold for every section, from design through implementation. When a section and a principle disagree, the principle wins and the section is a defect.

> **P-1. Little to no changes in rossoctl.** Change rossoctl only when it is absolutely required, or when the alternative is extremely costly in effort or in runtime performance. Every proposed rossoctl change must name the store-side alternative it was compared with, and why that alternative is not acceptable.
>
> **P-2. Store changes live in the `rossoctl-tools` plugin.** A change to core skillberry-store is allowed only as a generic, must-have change with clear value for store users who never use rossoctl. Each core change states that standalone value and why the plugin cannot do the job through existing extension points.

Applying P-1 and P-2 gives this split:

| Layer | What it holds | Size |
|---|---|---|
| rossoctl | Nothing. The store uses rossoctl's existing REST API, Kubernetes objects and Helm values. | — |
| rossoctl/examples (a separate repo) | An agent that can run skill scripts (§5.3). This is the one piece outside the store. It is an example-agent change, not a platform change, and users can bring their own agent image instead. | S |
| skillberry-store core | C1 `isError` fix, C2 tool-provider hook, C3 package contribution hooks (§4.1) | S–M |
| `rossoctl-tools` plugin | Everything else: backend client, provider, proxy generator, package contents, import, check, resync, promotion, the stage 2 runner image | L |

### 0.2 Stages

| Stage | What a user gets | skillberry-store | rossoctl | Sections |
|---|---|---|---|---|
| **1. Skills from existing rossoctl tools** | In the store, compose a skill from tools already deployed in rossoctl. Agents get the tools through the skill as runnable proxy scripts. The store can execute the tools for testing and evaluation. | Core C1–C3. The plugin: backend client, provider, proxy generator, package contents, `/tools`, `/import`, `/check`, `/resync`. | **None.** Deployed with existing chart values (§6). | §1–§9 |
| **2. Promote store code tools to rossoctl tools** | Write code tools in the store, then promote them. rossoctl runs them as a native tool, with AuthBridge, policy, the catalog and sharing across agents, and the skill switches to proxies of it. | Plugin: `/promote` and the runner image. **No core change.** | **None.** Uses `POST /api/v1/tools`. | §13 |
| *Deferred: rossoctl-side enhancements* | Agent-create validation, URL injection, console cards. | — | Each would need its own justification under P-1. None is planned. | §12 |

**Who owns what.**
- **The store owns** composition, versions, tool source, and the runner image for promoted tools.
- **rossoctl runs** every tool, through APIs it already has.

**Dependencies.** Stage 1 stands alone. Stage 2 uses stage 1's backend client and `/import`.

**Implementers: start with §14.** It fixes the remaining decisions, gives exact interfaces and file locations, defines "done", and orders the work. §15 says what the existing r4 branches must change.

Sources inspected (latest `main` of each repo):

| Repo | Commit | Notes |
|---|---|---|
| skillberry-store (this repo) | `da481ee` | |
| `rossoctl/rossoctl` | `1cac9f3f` (2026-10-19) | Every rossoctl reference below is to this commit and is **read-only use** of rossoctl. |
| `rossoctl/examples` | `f1fc5df2` (2026-10-13) | `weather_tool`, `reservation_tool`, `claude_agent`, `generic_agent` |

In rossoctl, backend paths are relative to `rossoctl/backend/app/`, UI paths to `rossoctl/ui-v2/src/`, and chart paths to `charts/rossoctl/`.

---

## 1. Problem (stage 1)

Today a skillberry-store skill cannot use a rossoctl tool, for four reasons:

1. **Exported proxies are not runnable.** For a `packaging_format: "mcp"` tool, `export-anthropic` writes a bodiless stub (`mcp_content_from_manifest`, `fast_api/server_utils.py:54-95`) to `scripts/<tool>.py`. An agent that receives it cannot call anything.
2. **No way to reach the tools.** The store's MCP client is SSE-only (`modules/file_executor.py`). rossoctl tools serve streamable HTTP at `http://<tool>-mcp.<ns>.svc.cluster.local:<port>/mcp`. rossoctl's backend, however, already lists and invokes any tool (`POST /tools/{ns}/{name}/connect`, `/invoke`), so the store can work through it instead of adding a transport.
3. **Location is per namespace.** One fixed `packaging_params.mcp_url` cannot serve a skill shared across namespaces.
4. **No declared dependency.** Nothing in a skill package says "I need tool X".

On the rossoctl side, an agent gets one MCP tool through `mcpToolName` → `MCP_URL`, but any number of skills through `request.skills`. A skill that carries its tools as scripts therefore removes the single-tool limit, with no rossoctl change.

## 2. Goals and non-goals

### Goals

- **G0. rossoctl unchanged; store core minimal.** The principles of §0.1.
- **G1. Runnable tool proxies in skills.** A skill that aggregates rossoctl tools exports one self-contained, standard-library-only CLI per MCP tool.
- **G2. Late binding.** The same skill works in every namespace that runs the tools. The endpoint is resolved in the agent's pod at run time, and is never baked into the export.
- **G3. Declared dependencies.** The package declares the rossoctl tools it needs, for people (SKILL.md) and for programs (frontmatter `metadata` and `rossoctl-tools.json`).
- **G4. Pre-flight checks from the store.** Before an agent is created, the store can report, per namespace, which of a skill's tools are deployed, ready and on the recorded port (`/check`). This replaces r4's rossoctl-side agent-create validation.
- **G5. Import.** The plugin creates proxy tools, and optionally a skill skeleton, from live rossoctl tools, through rossoctl's backend API.
- **G6. Store-side execution.** The store executes these tools (`/tools/{id}/execute`, vMCP) for testing, evaluation and dreaming. rossoctl performs the call (`/invoke`); the store never connects to tool pods.
- **G7. No credentials in the proxy.** Authentication on the agent's egress is AuthBridge's job.

### Non-goals

- **N0. Any change to rossoctl,** including its backend, UI, chart, installer, operator and docs. §12 keeps a list of candidates for the record. None is planned.
- **N1. Any MCP transport work in store core.** The proxy speaks streamable HTTP, but it is generated text from the plugin that runs in agent pods.
- **N2. Store ACL enforcement.** The store is assumed to run in `disabled` mode (§8 covers `delegated`).
- **N3. Changing how other `mcp` tools export.** Tools with only an SSE `mcp_url` keep today's stub.

## 3. Overview

```
 rossoctl-system                                              team1 (tenant namespace)
 ┌─────────────────────────────┐  existing REST /api/v1/tools ┌──────────────────────┐   MCP    ┌──────────────────────┐
 │ skillberry-store            │  (list, get, connect, invoke;│ rossoctl-backend     │─────────▶│ weather-tool-mcp     │
 │  rossoctl-tools plugin ─────┼─ stage 2: POST /tools) ─────▶│  (unchanged)         │          │ reservation-tool-mcp │
 │  · import tools as proxies  │◀──── tool schemas ───────────│                      │          └──────────▲───────────┘
 │  · /check per namespace     │                              │  existing autosync ──┼──┐                  │ tools/call
 │  · export: proxies,         │◀── GET /skills/ (as today) ──│  + skill fetch       │  │       ┌──────────┴───────────┐
 │    rossoctl-tools.json,     │                              └──────────────────────┘  └─────▶ │ agent pod            │
 │    SKILL.md Tools section   │──── zip (existing skill-fetcher init container) ──────────────▶│  scripts/*.py proxy  │
 └─────────────────────────────┘                                                                │  AuthBridge (egress) │
                                                                                                └──────────────────────┘
```

Every arrow into or out of rossoctl uses an endpoint or mechanism that exists today:
- **`/tools` REST:** `routers/tools.py:744, 864, 1609, 2435, 2516`.
- **Skill autosync:** `services/skill_autosync.py`, seeded by `templates/skillberry-autosync-configmap.yaml`.
- **The external-skill fetch:** `routers/agents_skills.py`, the `fetch-skill-*` init container, `GET {store}/skills/{name}/export-anthropic`.

How the pieces compose:
- **Store without the plugin:** no change to anything a user sees, except the `isError` fix (C1).
- **Store with the plugin:** stage 1 in full. Agents that can run scripts use the skills. rossoctl does not know the feature exists.
- **The example agent change (§5.3):** gives rossoctl's example agents the ability to run skill scripts.

---

## 4. skillberry-store changes

### 4.0 The split

All rossoctl knowledge lives in the plugin. Core code never contains the strings `rossoctl` or `svc.cluster.local` outside tests; a test enforces this (§14.6). The plugin depends on core's extension points, and core never names the plugin.

| Concern | Core (generic) | `rossoctl-tools` plugin |
|---|---|---|
| Error results | C1: `isError` reported as an error | — |
| Executing a remote tool | C2: a tool whose `packaging_params.endpoint.provider` names a provider is executed by that provider | Provider `rossoctl`: calls `/invoke` |
| Exporting a remote tool | C2: the provider may supply the tool's exported module | The streamable-HTTP proxy generator, with rossoctl resolution steps |
| Extra package content | C3: contributors add files, frontmatter `metadata` and a SKILL.md section; claimers take files on import | `rossoctl-tools.json`, `metadata.rossoctl-requires-tools`, the generated Tools section |
| Everything that talks to rossoctl | — | backend client, credential, `/tools`, `/import`, `/check`, `/resync`, `/promote`, the runner image |

### 4.1 Core changes, each with its standalone justification

#### C1. Correct error results (bug fix)

**Standalone value.** `execute_mcp_tool` returns `content[0].text` and ignores `isError`, so a failing remote tool looks successful to every store user with an SSE `mcp` tool.

**Change:**
- an `isError: true` result is returned as an execution error;
- all text items are joined with newlines;
- `structuredContent` is returned as `"structured"`.

**Why it is not in the plugin:** it is a bug in core's own MCP path.

This changes behavior: an error result is now HTTP 500 at `/tools/{id}/execute`. It is noted in `CHANGELOG.md`.

#### C2. Tool-provider hook

**Standalone value.** It lets a plugin own how a remote tool is reached and packaged, instead of the store connecting to a fixed SSE `mcp_url`. Any plugin can use it: a corporate tool gateway, an authenticated MCP endpoint, a tool catalog with its own invocation API. Core ships no provider.

**Why it is not in the plugin:** tool execution (`ToolsService.execute`, vMCP) and export (`gather_export_inputs`) are core code paths. A plugin has no extension point there today. The alternatives were judged worse:
- *Have the plugin serve its own SSE MCP endpoint and point each tool's `mcp_url` at it.* This adds a long-lived server inside the plugin, a loopback hop on every call, and a store-internal URL inside tool manifests, which would leak into exports and backups.
- *Import rossoctl tools as `code` tools whose module calls rossoctl.* The module would need the store's rossoctl credential at execution time, and it would be exported to agents.

**Shape.** `packaging_params` gains an optional, opaque `endpoint` block:

```jsonc
"packaging_format": "mcp",
"packaging_params": {
  "mcp_tool_name": "get_weather",   // required
  "mcp_url": "http://…/sse",        // optional when "endpoint" is present
  "endpoint": {"provider": "<name>", "...": "provider-specific, opaque to core"},
  "input_schema_sha256": "…"        // optional
}
```

Validation is shared by the schema and by `ToolsService.create`/`update`:
- `mcp_tool_name` matches `^[A-Za-z0-9_.\-]{1,128}$`;
- `mcp_url` or `endpoint` is present;
- `endpoint.provider` matches `^[a-z][a-z0-9-]{0,31}$`;
- `endpoint` is at most 4 KiB.

The active provider's own `validate` also runs. Core never interprets the rest of the block.

**Provider protocol** (`modules/tool_providers.py`):

```python
class ToolProvider(Protocol):
    name: str
    export_version: str                                   # changes when export_module output changes
    def validate(self, endpoint: dict) -> None: ...       # raise ValueError
    async def call_tool(self, packaging_params: dict, arguments: dict, ctx: ProviderContext) -> CallResult: ...
    def export_module(self, tool: dict) -> Optional[str]: ...   # None = core's default stub
```

- **Registry:** a provider is registered by its plugin (`PluginBase.register_tool_provider`). It is visible only while that plugin is active (`PluginLoader.is_active`).
- **Execution:** a tool with `endpoint` is executed by its active provider. Without an active provider, `/tools/{id}/execute` returns **422** *"Tool 'X' needs provider 'Y', which is not available in this store"*. vMCP returns that as an error result. Core never guesses a URL.
- **Export:** in `SkillsService.gather_export_inputs`, the module of a tool with `endpoint` is `provider.export_module(tool)` when it returns text. Otherwise it is today's stub. **Without an active provider, exports are exactly today's.** The npx artifact cache key includes `(provider.name, provider.export_version)` for each active provider that backs a tool of the skill.
- **Removed from r4:**
  - `list_tools` and `proxy_resolution` in the protocol: core never calls them.
  - The core proxy generator, the `MCP_PROXY_*` default step and `SBS_MCP_PROXY_EXPORT`: all moved to the plugin (§4.2.4).

#### C3. Package contribution hooks

**Standalone value.** General extension points for any plugin that adds metadata to skill packages. Examples: a provenance plugin attaching an attestation file, a policy plugin adding a manifest, a docs plugin adding a generated usage section.

**Why it is not in the plugin:** the exporter and importer are core.
- An exporter `ExportContributor` may return new files, frontmatter `metadata` entries and a SKILL.md section. A path or key collision fails the export, naming the plugin.
- An importer `ImportClaimer` may claim files and return the tools to create for them. Unclaimed files follow today's path.
- Contributed SKILL.md sections are wrapped in `<!-- skillberry:generated:begin/end -->` markers. The importer drops marked blocks, so a re-import does not duplicate them.
- `parse_skill_metadata` keeps the frontmatter `metadata` mapping (`extra.frontmatter_metadata`) instead of dropping it, and the exporter writes it back. This is a standalone fix: round trips become lossless for any metadata.
- Hooks are consulted only while their plugin is active. With none active, output is byte-identical to today's.

#### Dropped from core in r5

| r4 core item | Why it is dropped |
|---|---|
| Opt-in computed skill fields (`_rossoctl_required_tools`) | Its only consumer was rossoctl's autosync (r4 R2), which is not being changed. The dependency is still declared in the package (G3). |
| Core proxy generator (`tools/anthropic/mcp_proxy.py`) and the core "Tools" SKILL.md section | Provider-specific. C2's `export_module` and C3's SKILL.md section give the plugin everything it needs. |
| `SBS_MCP_PROXY_EXPORT` | Becomes the plugin setting `ROSSOCTL_PROXY_EXPORT`. |

### 4.2 The `rossoctl-tools` plugin

`plugins/skillberry-plugin-rossoctl-tools/`:
- slug `rossoctl-tools`, type `IMPORTER`;
- optional extra `plugin-rossoctl-tools`, included in `plugins-all`;
- dependency `httpx` only. The stage 2 runner has its own extra (§13.4).

It is active when `ROSSOCTL_BACKEND_URL` is set and the store's plugin config does not disable it. It makes no network calls until a route is invoked or a provider-backed tool is executed.

#### 4.2.1 Talking to rossoctl

All calls go to rossoctl's backend REST API, `{ROSSOCTL_BACKEND_URL}/api/v1`, which is unchanged:

| rossoctl endpoint (existing) | Used for | Role |
|---|---|---|
| `GET /tools?namespace=` (`routers/tools.py:744`) | discovery for the store UI | viewer |
| `GET /tools/{ns}/{name}` (`:864`) | existence, `readyStatus`, Service port (`service.ports[0].port`) | viewer |
| `POST /tools/{ns}/{name}/connect` (`:2435`) | MCP tool list: `{name, description, input_schema}` | operator |
| `POST /tools/{ns}/{name}/invoke` (`:2516`) | store-side execution | operator |
| `POST /tools` (`:1609`), `DELETE /tools/{ns}/{name}` (`:950`) | stage 2 deploy and garbage collection | operator |

**Credential.**
- The plugin uses the Keycloak client-credentials grant (`ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT`). The token is cached until 30 s before expiry and refreshed once on a 401.
- With rossoctl's `ENABLE_AUTH=false` (`ui.auth.enabled=false`), no credential is needed.
- The credential comes from an existing chart mechanism, the `apiOAuthSecret` job (§6.2). A dedicated Keycloak client created by an administrator works the same way.
- In `delegated` mode the caller's bearer is forwarded instead (§8).

**Configuration** (plugin env):

| Env | Default | Meaning |
|---|---|---|
| `ROSSOCTL_BACKEND_URL` | unset (plugin inactive) | e.g. `http://rossoctl-backend.rossoctl-system.svc.cluster.local:8000` |
| `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT` | unset | client-credentials grant; omit when rossoctl runs without auth |
| `ROSSOCTL_ALLOWED_NAMESPACES` | `""` (any) | comma-separated list of namespaces the plugin may target |
| `ROSSOCTL_TIMEOUT` | `30` | seconds per backend call |
| `ROSSOCTL_PROXY_EXPORT` | `true` | `false` exports today's stubs instead of proxies |
| `ROSSOCTL_FORWARD_CALLER_TOKEN` | `false` | delegated mode (§8) |
| `ROSSOCTL_RUNNER_IMAGE`, `ROSSOCTL_STORE_URL` | see §13 | stage 2 |

**SSRF.** The only outbound destinations are `ROSSOCTL_BACKEND_URL` and the token endpoint. Tool and namespace names are validated as DNS-1123 labels and used only in URL paths.

#### 4.2.2 Provider `rossoctl`

The `endpoint` block it owns:

```jsonc
"endpoint": {
  "provider": "rossoctl",
  "tool": "weather-tool",        // rossoctl tool (workload) name
  "namespace": "team1",          // namespace imported from; target of store-side execution
  "port": 8000,                  // Service port at import (GET /tools/{ns}/{name})
  "path": "/mcp",
  "gateway_prefix": "weather_",  // optional
  "promoted_from": "<uuid>",     // stage 2 only
  "bundle_digest": "sha256:…"    // stage 2 only
}
```

- `validate`: `tool` and `namespace` are DNS-1123 labels, `port` is in 1..65535, `path` starts with `/`.
- `call_tool`: `POST /tools/{ns}/{tool}/invoke`. `result.content` and `result.isError` map to core's `CallResult`, and 502/504 to a transport error.
- `export_module`: the proxy CLI (§4.2.4), unless `ROSSOCTL_PROXY_EXPORT=false`.
- `export_version`: the proxy generator version.

#### 4.2.3 Agent-side endpoint resolution

The proxy runs in the agent's pod. Because rossoctl injects nothing, it resolves the endpoint itself, in this order:

1. **`ROSSOCTL_TOOL_<TOOL>_URL`**, if set. This is an override the user can set through the agent's existing `envVars`. `<TOOL>` is the tool name upper-cased, with every non-alphanumeric character replaced by `_`.
2. **`ROSSOCTL_MCP_GATEWAY_URL`**, only for tools imported with a `gateway_prefix`. The call then uses `<prefix><mcp_tool_name>`.
3. **`http://<tool>-mcp.<ns>.svc.cluster.local:<port><path>`**, the cluster address of the Service that `POST /tools` and the example deploy scripts create (`routers/tools.py:1548`).
   - `<ns>` comes from `ROSSOCTL_NAMESPACE`, else the pod's ServiceAccount namespace file (`/var/run/secrets/kubernetes.io/serviceaccount/namespace`, mounted for Deployment and StatefulSet agents), else the import namespace.
   - `<port>` comes from `ROSSOCTL_TOOL_<TOOL>_PORT`, else the import port.

Step 3 is the normal path, and it is what makes the skill portable across namespaces (G2). Its one weakness is a tool redeployed on a different port. `/check` detects that, and `/resync` fixes it (§4.2.6).

#### 4.2.4 The proxy CLI (plugin)

`python3 scripts/<tool>.py --<prop> …`, with these options:
- `--json '{…}'`, or `-` to read the arguments from stdin;
- `--describe`, `--check` (schema hash), `--raw`, `-v`.

**Exit codes:**
- 0: success;
- 1: the tool returned `isError`;
- 2: usage error;
- 3: the endpoint does not resolve, or the transport failed;
- 4: the input schema drifted.

**Runtime.** Standard library only, Python ≥ 3.8. No credentials are embedded; `MCP_PROXY_AUTH_HEADER` is read from the environment when set, and `TRACEPARENT`/`TRACESTATE` are forwarded.

**Protocol:** streamable HTTP, revisions 2025-03-26 and 2025-06-18.
1. `initialize`, accepting a JSON or an SSE response.
2. `notifications/initialized`.
3. `tools/call`. Server-initiated requests get the JSON-RPC error `-32601`.
4. A best-effort `DELETE` of the session.

**Generation safety.** Manifest values are never interpolated into code. The spec is embedded as JSON (`ensure_ascii`, `'` escaped) in a raw literal, and the docstring uses only `mcp_tool_name`. Keys named `headers`, `auth*`, `token*` or `*secret*` are refused.

#### 4.2.5 Package contents (contributor and claimer)

These are added only to skills that contain a rossoctl-backed tool:

- **Frontmatter:** `metadata.rossoctl-requires-tools: "reservation-tool,weather-tool"`.
- **`rossoctl-tools.json`:**

  ```json
  {"schema": "skillberry.ai/rossoctl-tools/v1",
   "tools": [{"tool": "weather-tool", "port": 8000, "path": "/mcp",
              "mcp_tools": [{"name": "get_weather", "script": "scripts/get_weather.py",
                             "schema_sha256": "…", "read_only": true}]}]}
  ```

- **SKILL.md section "Tools":**
  - per tool, a usage line generated from the schema;
  - a read-only marker, or "may change state: confirm with the user first";
  - the exit-code legend;
  - a rossoctl note: tools resolve in the agent's namespace, and exit 3 means the tool is not deployed there.
- **Import claimer:** it claims `rossoctl-tools.json` and the scripts it lists, and rebuilds the provider tools from the JSON and each script's embedded spec. "Export → edit in git → re-import" is therefore lossless.

**Read-only marking.** rossoctl's `/connect` does not return MCP annotations (`MCPToolSchema`, `routers/tools.py:328-333`), and r5 does not change that. The tools are therefore treated as writes unless told otherwise:
- `/import` accepts `read_only: ["get_weather", …]` per tool reference;
- the marking is stored as `extra.mcp_annotations.readOnlyHint` and can be edited on the store tool.

#### 4.2.6 Routes

| Route | Permission | Behavior |
|---|---|---|
| `GET /plugins/rossoctl-tools/tools?namespace=` | `tools:list` | Pass-through of rossoctl's `GET /tools`, for a picker in the store UI |
| `POST /plugins/rossoctl-tools/import` | `tools:create` | `{namespace, tools: [{name, gateway_prefix?, read_only?: [str]}], skill?: {name, description, instructions?, tags?}, tags?}`. For each rossoctl tool: `GET /tools/{ns}/{name}` (existence and port), then `/connect`. Creates one store tool per MCP tool. On a name collision with a tool of another rossoctl tool, the new tool is named `<tool>__<name>`. Optionally creates the skill, with `instructions` as a `file:SKILL.md` snippet. |
| `POST /plugins/rossoctl-tools/check` | `tools:list` | `{skill? , tool_uuids?, namespaces?: [str]}`. **Schema drift** via `/connect`. **Pre-flight (G4)** for each namespace (default: the import namespace) and each rossoctl tool: `present`, `ready` (`readyStatus`), `port` and `port_matches`. No writes. |
| `POST /plugins/rossoctl-tools/resync` | `tools:create` | A new tool version (same name, new uuid) for each tool whose schema drifted or whose import-namespace port changed, plus a new skill version. |
| `POST /plugins/rossoctl-tools/promote`, `GET …/promote/{id}`, `GET …/bundles/{digest}`, `POST …/promote/gc` | see §13 | stage 2 |

### 4.3 Behavior without the plugin

| Situation | Behavior without `rossoctl-tools` installed or active |
|---|---|
| Store start, APIs, UI | Unchanged |
| Export of any skill | Byte-identical to today's, including skills with rossoctl-backed tools: those get today's stub |
| Execute or vMCP on a rossoctl-backed tool | 422 naming the missing provider |
| Create or update such a tool | Accepted, with the generic shape check only |
| Import of a package that contains `rossoctl-tools.json` | Today's rules: the JSON becomes a document snippet and the scripts become code tools |
| `/plugins/rossoctl-tools/*` | 404 |

### 4.4 Store work summary

| # | Item | Layer | Size |
|---|---|---|---|
| C1 | `isError` fix | core | S |
| C2 | Provider hook: shape validation, registry, execute dispatch, 422, `export_module`, npx cache key | core | M |
| C3 | Contribution and claim hooks, generated-section markers, kept frontmatter `metadata` | core | S |
| S1 | Plugin: backend client and credential, provider, proxy generator, package contents, claimer, `/tools`, `/import`, `/check` (drift and pre-flight), `/resync` | plugin | M |
| S2 | Docs: plugin README, the demo guide, the core env var doc (none new in core) | plugin and docs | S |
| S3 | Stage 2: `/promote`, bundle route, GC, runner image (§13) | plugin | M |

### 4.5 Deployment of the store in rossoctl, with existing chart values

See §6. No chart change is needed.

### 4.6 r4's rossoctl changes and their store-side replacements

| r4 item (dropped) | What it gave | r5 replacement (no rossoctl change) | What is lost |
|---|---|---|---|
| R1 `toolSkills` flag | A gate for R2–R5 | Not needed. The plugin is gated by `ROSSOCTL_BACKEND_URL` and the store's plugin config. | — |
| R2 `rossoctl.io/required-tools` on skill ConfigMaps | rossoctl knew a skill's tools | The dependency lives in the package (frontmatter, `rossoctl-tools.json`) and is queried through the plugin's `/check` | rossoctl itself cannot show or act on it |
| R3 agent-create validation and warnings | A warning, or a 422, for a missing tool | `/check` with `namespaces` before creating the agent; exit code 3 at run time, naming the tool | No automatic check inside rossoctl's agent create |
| R3 URL injection | The real Service port in the pod | Resolution step 3, the import port, plus `/check`'s `port_matches` and `/resync`; or a manual `ROSSOCTL_TOOL_<TOOL>_URL` in the agent's `envVars` | A port change needs a resync before agents pick it up |
| R4 automatic AuthBridge routes | Per-agent outbound routes | The existing `outboundRoutes` field at agent create, when tools need token exchange; the default passthrough needs none | Manual |
| R5 console cards and chips | Visibility in the rossoctl UI | The store UI (plugin card, tool tags `rossoctl-tool:<name>`) and `/check` | Nothing shown in the rossoctl console |
| R6 Helm plugin config and store credential | Automatic setup | Existing values: `skillberryStore.image`, `skillberryStore.extraEnv`, `apiOAuthSecret` (§6.2) | One values file to write |
| R7 installer flag, reservation-tool script | Demo convenience | The console's existing Reservation Tool preset (`ImportToolPage.tsx:64`), or `kubectl apply` | — |
| R9 rossoctl docs and e2e | — | The demo guide and the e2e live in the store repo | — |
| R10 `/connect` annotations | Read-only tools detected automatically | The `read_only` option on `/import` (§4.2.5) | A manual list |

---

## 5. rossoctl and agents

### 5.1 rossoctl

No changes. What the feature relies on:
- `POST /api/v1/tools` creates a tool: its workload, the Service `<name>-mcp`, the ServiceAccount and an `AgentRuntime` of type `tool` (`routers/tools.py:1609-1840`, `_ensure_tool_agentruntime :1170`).
- `/connect` and `/invoke` (`:2435`, `:2516`); `GET /tools/...` (`:744`, `:864`).
- Skill autosync from the store (`services/skill_autosync.py`), seeded by the chart when the store is enabled. It creates an external-skill ConfigMap for every store skill that passes `allowed-tags`.
- Agent creation with `skills: [...]` (`routers/agents_create.py`). For each external skill, rossoctl adds an init container that fetches `GET {store}/skills/{name}/export-anthropic` and unpacks it to `/app/skills/<cm>`, and it sets `SKILL_FOLDERS` (`routers/agents_skills.py`).

### 5.2 What a user does instead of r4's rossoctl features

1. **Check before creating an agent:** `POST /plugins/rossoctl-tools/check {"skill": "night-out-planner", "namespaces": ["team2"]}`.
2. **Create the agent as today**, with the skill linked. No MCP tool is needed.
3. **Optionally pin an endpoint:** add `ROSSOCTL_TOOL_<TOOL>_URL` to the agent's `envVars`.

### 5.3 Agents that run skill scripts (`rossoctl/examples`)

The feature needs an agent that **runs** skill scripts. This is the one change outside skillberry-store. It is in the separate examples repository, not the rossoctl platform, and any agent image that can run `python3` scripts from its skill folders works without it.

1. **`claude_agent`** (the primary demo agent): at startup, symlink each `SKILL_FOLDERS` entry that has a `SKILL.md` to `~/.claude/skills/<name>`, so Claude Code discovers the skills. Put `python3` on `PATH` in the image.
2. **`generic_agent`** (secondary): an opt-in `run_skill_script(skill, script, arguments)` tool, enabled with `SKILL_SCRIPTS_ENABLED=true`. Scripts are resolved strictly under `<skill>/scripts/` with a realpath check, and run with no shell, a timeout and an output cap.

If the examples maintainers do not accept these changes, the store repo publishes the same two agents as its own example images. No other component changes.

---

## 6. Deployment and integration

### 6.1 Topology

```
rossoctl-system:  rossoctl-backend, rossoctl-ui, skillberry-store (+ rossoctl-tools plugin), keycloak, phoenix…
team1:            weather-tool (+ -mcp Svc, AgentRuntime type=tool), reservation-tool (same), night-out agent
```

- **The store** reaches only rossoctl's backend.
- **The backend** reaches the tools, for import, check and store-side execution.
- **Agents** reach tools in their own namespace, through AuthBridge egress.
- **Stage 2 runner pods** reach the store only to fetch their bundle. This is the same path the skill-fetcher init containers already use.

### 6.2 Helm values (existing keys only)

```yaml
featureFlags:
  skills: true
  externalSkills: true            # autosync + skill fetch into agents

components:
  skillberryStore:
    enabled: true

skillberryStore:
  image:
    repository: ghcr.io/skillberry-ai/skillberry-store
    tag: "<release>-full"         # an image built with plugins-all (or PLUGIN_EXTRAS=plugin-rossoctl-tools)
  extraEnv:                       # existing key (values.yaml, skillberryStore.extraEnv)
    - name: ROSSOCTL_BACKEND_URL
      value: http://rossoctl-backend.rossoctl-system.svc.cluster.local:8000
    - name: ROSSOCTL_ALLOWED_NAMESPACES
      value: "team1,team2"
    # Only with ui.auth.enabled=true. Reuses the existing api-oauth-secret job's
    # client (rossoctl-api, realm role rossoctl-operator):
    - {name: ROSSOCTL_CLIENT_ID,      valueFrom: {secretKeyRef: {name: rossoctl-api-oauth-secret, key: CLIENT_ID}}}
    - {name: ROSSOCTL_CLIENT_SECRET,  valueFrom: {secretKeyRef: {name: rossoctl-api-oauth-secret, key: CLIENT_SECRET}}}
    - {name: ROSSOCTL_TOKEN_ENDPOINT, valueFrom: {secretKeyRef: {name: rossoctl-api-oauth-secret, key: TOKEN_ENDPOINT}}}
  autosync:
    allowedTags: "rossoctl-approved"   # optional

apiOAuthSecret:
  enabled: true                   # existing job: registers the client and writes the Secret above
```

- **Plugin enablement.** The chart sets no `SKILLBERRY_PLUGIN_CONFIG`, so every installed plugin is enabled (`plugins/config.py`), and `rossoctl-tools` activates because `ROSSOCTL_BACKEND_URL` is set. To disable other plugins, mount a plugin config through the same `extraEnv` (as a ConfigMap reference), or build an image with fewer plugin extras.
- **The credential.** The `apiOAuthSecret` job writes its Secret to the release namespace (`templates/api-oauth-secret-job.yaml`, `TARGET_NAMESPACE`), which is the store's namespace. To use a separate identity, an administrator creates another client-credentials client in Keycloak and a Secret by hand. The values above then name that Secret instead.
- **Store image.** Publishing a `-full` image (`make docker-build-full`) is part of the store's release checklist.

### 6.3 Kind

```bash
scripts/kind/setup-rossoctl.sh --with-all --with-skills --with-examples \
  --rossoctl-values ./rossoctl-tools-values.yaml        # the values of §6.2
```

`--with-examples` deploys `weather-tool`. Add `reservation-tool` through the console's Reservation Tool preset, or with a `kubectl apply` manifest kept in the store repo (`plugins/skillberry-plugin-rossoctl-tools/deploy/reservation-tool.yaml`).

### 6.4 OpenShift

The same values apply. The store pod runs under the restricted SCC with no change.

---

## 7. Cross-cutting concerns

### 7.1 Security

- **The proxy is only a client.** Policy stays with AuthBridge: `token-exchange`, the opt-in `opa`, `ibac` and `sparc` plugins (which see proxy calls through `mcp-parser`, default path `/mcp`), and the tool's own authorization. A skill cannot reach a tool the pod's egress cannot reach.
- **The store holds an operator credential.**
  - It lives in a Secret mounted only into the store pod, is never written to store objects or exports, and the token is cached in memory only.
  - Reusing `rossoctl-api` gives the store the same rights as the API client. A dedicated client limits blast radius and audit confusion; §11 Q3.
- **The exported zip holds no secrets.** The generator refuses credential-like keys.
- **Write tools.**
  - The Tools section asks the model to confirm before running a tool that is not read-only.
  - This is guidance. Enforcement belongs to AuthBridge (`ibac`, `opa`) or to the tool.
- **Plugin SSRF.** Only the configured backend and token endpoint are contacted, and only with validated names.
- **Store-side execution goes through rossoctl.**
  - In `disabled` store mode, anyone who can call the store can execute a rossoctl tool with the store's identity.
  - Keep `ROSSOCTL_ALLOWED_NAMESPACES` tight. §8 closes this.
- **Stage 2 bundles are code.**
  - A bundle is served by digest and verified by digest in the runner.
  - Promotion requires `skills:create` and, optionally, an approval tag (§11 Q6).

### 7.2 Versions and drift

- **Autosync records the oldest version** (integration doc, "Skill versions"). Agents fetch the skill by name, which is the head. For this feature the package is self-describing, so there is no metadata to get out of step. The integration doc's stage 1 still applies to skills in general.
- **Schema drift.** `--check` and `/check` detect it, and `/resync` creates new versions. At run time the server is authoritative: wrong arguments surface as the tool's error, with exit code 1.
- **Port drift.** `/check` reports `port_matches: false`, and `/resync` records the new port. Until then, agents can be pinned with `ROSSOCTL_TOOL_<TOOL>_PORT` or `_URL`.
- **A missing tool** gives exit code 3, with the URL and the resolution step that was tried.

### 7.3 Observability

The example tools emit OTEL spans, so Phoenix shows `execute_tool` spans for proxy calls with no change. The proxy forwards `TRACEPARENT`/`TRACESTATE` when set, so tool spans can join the agent's trace.

### 7.4 Testing

**Store core** (`make test`, plus a CI job with no plugins installed):
- the C1 error mapping;
- the C2 validator matrix, registry gating, the 422, `export_module` dispatch and the npx cache key;
- the C3 hooks: collisions, failure naming the plugin, marker stripping, metadata round trip;
- a guard that core contains no `rossoctl` or `svc.cluster.local`;
- golden exports byte-identical with no provider active.

**Plugin unit tests:**
- the backend client against a fake rossoctl backend (`httpx` transport): bearer token, refresh on 401, only the backend host contacted;
- the provider;
- generator safety (hostile input through `ast.parse` and execution);
- argument parsing for every JSON type;
- JSON and SSE response frames;
- resolution order;
- `rossoctl-tools.json`, the Tools section, the claimer round trip;
- `/check`: drift and pre-flight per namespace, including port mismatch;
- `/resync`.

**Plugin e2e** (marker `integration`):
- (a) Generated proxies run with `python3 -I` against a FastMCP `streamable-http` fixture, asserting exit codes 0, 1, 2 and 3.
- (b) The full store with the plugin against the fake backend: `/import`, Execute, export, re-import.
- (c) The **local cluster e2e** (`plugins/skillberry-plugin-rossoctl-tools/tests/cluster/run-local-demo.sh`): rossoctl from `main` (unchanged) on a kwok control plane, the example tools, and the store from source. It runs every step of §9 that does not need an LLM.

**Kind e2e** (manual or nightly, in the store repo): §9 without an LLM, with `kubectl exec` into the agent pod to run a proxy.

**rossoctl: none.**

---

## 8. Future: store ACL in `delegated` mode

The agent-side proxy path is unchanged: it never talks to the store.

- **Placement.** Imported tools and the skill are tagged `namespace:<ns>`. Sharing a skill to another namespace is a guarded tag write. `/check` warns when a skill's namespaces are not covered by its tools' namespaces.
- **Identity toward rossoctl.**
  - User-initiated plugin calls (`/import`, `/check`, `/resync`, `/promote`, Execute) forward the caller's Keycloak bearer (`ROSSOCTL_FORWARD_CALLER_TOKEN=true`). rossoctl then authorizes and audits the user. **rossoctl needs no change:** it already validates Keycloak JWTs.
  - Autonomous runs (evaluator, dreaming) use the plugin owner's identity, which is the service credential.
- **Store-side execution** requires `execute` on the tool, scoped to its namespace.
- **Stage 2 bundle fetch.** The runner fetches the bundle with the pod's ServiceAccount token, once the store verifies Kubernetes tokens (integration doc, stage 3). Until then, the bundle URL is digest-addressed (§13.3).

---

## 9. Demo: "night-out-planner"

### 9.1 What it shows

1. One skill gives an agent two rossoctl tools. The agent has no `mcpToolName` and no `MCP_URLS`; its only configuration is the skill.
2. The skill says when and how to use the tools:
   - the weather is current conditions, not a forecast;
   - WMO weather codes and °F;
   - `place_reservation` is a write that needs confirmation;
   - the shape of availability slots.
3. The same skill works unchanged in another namespace that runs the tools.
4. The store checks, before an agent is created, which namespaces can run the skill (`/check`).
5. Policy stays with the tool: Phoenix shows the tool spans, and AuthBridge is on the path.
6. *(Optional)* The store executes the same tools for evaluation.
7. **rossoctl is unmodified.**

### 9.2 Tools used (rossoctl examples, no API keys)

| rossoctl tool | Source | MCP tools | External deps |
|---|---|---|---|
| `weather-tool` | `rossoctl/examples/mcp/weather_tool`; `--with-examples` deploys it | `get_weather(city)` | Internet (Open-Meteo, no key). Without it the tool reports "temporarily unavailable", and the skill continues. |
| `reservation-tool` | `rossoctl/examples/mcp/reservation_tool`; the console preset | `search_restaurants`, `check_availability`, `place_reservation` (write), `cancel_reservation` (write), `list_reservations` | None. A deterministic mock with Boston, New York, San Francisco and Austin. |

### 9.3 The skill

Six store tools, all with `endpoint.provider: rossoctl`, plus one instructions snippet tagged `file:SKILL.md`, and the skill `night-out-planner`, tagged `rossoctl-approved`. The import marks `get_weather`, `search_restaurants`, `check_availability` and `list_reservations` read-only.

The exported package:

```
night-out-planner/
  SKILL.md                  # frontmatter (rossoctl-requires-tools) + instructions + generated Tools section
  rossoctl-tools.json
  scripts/{get_weather,search_restaurants,check_availability,place_reservation,cancel_reservation,list_reservations}.py
```

The instructions are in [`docs/demos/rossoctl-night-out-planner/instructions.md`](../demos/rossoctl-night-out-planner/instructions.md).

### 9.4 Demo script

Setup: rossoctl from `main`, unchanged, installed with the values of §6.2 and `--with-examples`; plus `reservation-tool` in `team1`.

1. **Import.** `POST /plugins/rossoctl-tools/import` with `team1`, both tools, the read-only list and the skill. You get six tools.
2. **Store-side check.** Execute `search_restaurants {"city": "Boston", "cuisine": "Italian"}`. It returns Trattoria di Mare. The backend log shows `/invoke`.
3. **Pre-flight.** `POST /plugins/rossoctl-tools/check {"skill": "night-out-planner", "namespaces": ["team1", "team2"]}`:
   - `team1`: both tools present, ready, ports match;
   - `team2`: `reservation-tool` is missing.
4. **rossoctl picks the skill up.** Existing autosync creates `night-out-planner` in `team1` within about 30 s.
5. **Create the agent.** `claude_agent` from the examples branch, with `night-out-planner` checked under Linked Skills and no MCP tool. Check that the pod has `SKILL_FOLDERS=/app/skills/night-out-planner` and the scripts. Then:
   `kubectl exec … python3 /app/skills/night-out-planner/scripts/search_restaurants.py --city Boston`
6. **Chat.** "Plan dinner for 4 in Boston tomorrow at 7pm, Italian, mid-range."
   - weather (current, not a forecast);
   - search, then availability: 19:00 is taken, so 19:30 or 20:00 is offered;
   - the agent collects contact details and a confirmation;
   - `place_reservation`, then a confirmation code;
   - then "What reservations do I have?"
7. **Observe.** Phoenix tool spans, and the AuthBridge log.
8. **Portability.**
   - In `team2` (no reservation tool), the agent's reservation scripts exit 3 with *"cannot reach http://reservation-tool-mcp.team2…"*, and the agent reports it.
   - Deploy the reservation tool in `team2`: the same skill, unchanged, works.

The user guide is [`docs/demos/rossoctl-night-out-planner.md`](../demos/rossoctl-night-out-planner.md).

---

## 10. Rollout plan

### Stage 1: skills from existing rossoctl tools

| Milestone | Contents | Exit criterion |
|---|---|---|
| 1.M1 Core | C1–C3 and the no-plugin CI job | Golden exports unchanged with no provider; the core-only suite green; no MCP runtime dependency added; the no-rossoctl guard passes |
| 1.M2 Plugin | S1, S2 | Against the fake backend: import, check (drift, pre-flight, port), resync, Execute, vMCP, export and re-import work, with the bearer refreshed and no other host contacted. The §4.3 behaviors hold with the plugin disabled. |
| 1.M3 Agents | The examples change (§5.3), or store-published agent images | `claude_agent` runs a skill script in a pod |
| 1.M4 End to end | The local cluster e2e with **rossoctl `main`**; a Kind run of §9.4 | Demo steps 1–8 pass, with no rossoctl diff |

### Stage 2: promote store code tools

| Milestone | Contents | Exit criterion |
|---|---|---|
| 2.M1 Bundle and runner | The bundle format, the runner, conformance cases (§13.3–13.4) | The store's executor and the runner give the same result on every conformance case |
| 2.M2 Promotion | `/promote`, status, GC (§13.5) | Promotion deploys through `POST /tools`, waits for Ready, imports, and creates the swapped skill version |
| 2.M3 Demo | §13.7 | An agent calls `walk_estimate` through AuthBridge, and the call shows in Phoenix as a tool span |

## 11. Open questions

1. **Name collisions.** The proposal is to prefix on collision only (`<tool>__<name>`). The alternative is always to prefix.
2. **Read-only marking without `/connect` annotations.** The proposal is the explicit `read_only` list on import. The alternative is a name heuristic (`get_`, `list_`, `search_`, `check_`) as a default the user confirms.
3. **Store credential.** Reuse `rossoctl-api` (zero setup), or an administrator-created dedicated client (better audit)? The proposal is the dedicated client for production and reuse for demos.
4. **`generic_agent` exec tool.** Is an in-pod script runner acceptable in the generic example?
5. **Runner dependencies (stage 2).** Store tools declare no Python package requirements (`ToolSchema.dependencies` lists other *tools*). The runner offers the standard library plus a fixed, documented set of packages. Should the store add a requirements field? That would be a core change, and it needs its own P-2 justification.
6. **Approval gate for promotion.** `rossoctl-approved`, a passing SAST outcome, or both?
7. **Bundle fetch authorization (stage 2).** A digest-addressed URL now, ServiceAccount tokens once the store verifies them?

## 12. Deferred: rossoctl-side enhancements (not planned)

These are listed so that a future proposal starts from them. Each must pass P-1, which none does today.

| Candidate | Store-side alternative in place | Why it is not justified now |
|---|---|---|
| Agent-create validation of required tools | `/check` pre-flight; exit code 3 at run time | Not required; low cost to use the alternative |
| Tool URL injection with the live port | Cluster DNS with the import port, `/check` and `/resync`, optional `envVars` pin | Port changes are rare and detectable |
| Console cards and chips for required tools | Store UI and `/check` | Cosmetic |
| `/connect` returning MCP annotations | The `read_only` list on import | One manual list per import |
| A dedicated store client in the chart | Reuse `apiOAuthSecret`, or a manual client | Values-only setup works |

## 13. Stage 2: promote store code tools to rossoctl tools

### 13.1 Goal

A user writes **code tools** in the store, composes a skill, and **promotes** its code tools. rossoctl then runs them as a native tool:
- a workload, the Service `<name>-mcp` and an `AgentRuntime` of type `tool`;
- listed in rossoctl's Tools, behind AuthBridge and the policy plugins;
- shared by every agent in the namespace.

The skill switches to proxies of the new tool, exactly as stage 1 imports any tool. Without promotion, a code tool still exports as a script that runs inside the agent's pod, as today.

### 13.2 Ownership: the store ships code and runner, rossoctl only deploys

| | skillberry-store (plugin) | rossoctl (unchanged) |
|---|---|---|
| Tool source, versions | owns them | — |
| The server that runs the code | **the runner image**, built and released from the store repo | — |
| Workload, Service, `AgentRuntime`, AuthBridge, catalog | requests them through `POST /api/v1/tools` | creates and manages them, as for any image-deployed tool |
| The skill | swaps the promoted code tools for proxies | — |

r4 put the runner and a new `POST /tools/from-store` endpoint in rossoctl. That is replaced, because the existing `POST /tools` already deploys any image with arbitrary env vars and Service ports (`CreateToolRequest`, `routers/tools.py:188-262`). The only new artifact is the runner image, which the store owns.

```
 skillberry-store (plugin)                          rossoctl-backend (unchanged)            team1
 /promote ── build bundle (by digest) ──┐
          ── POST /api/v1/tools ───────────────────▶ Deployment(runner image, env SBS_BUNDLE_URL,
             {name: <name>-<d8>, image: runner,       SBS_BUNDLE_DIGEST) + <name>-<d8>-mcp Svc
              envVars: [bundle URL, digest],          + AgentRuntime(type=tool)  ──────────▶ runner pod
              servicePorts: [8000]}                                                          │ GET bundle by digest
          ── poll GET /tools/{ns}/{name} ◀── readyStatus                                     ▼  from the store
          ── /import (stage 1) ── new skill version: code tools → proxies          serves streamable /mcp
```

### 13.3 The bundle (plugin)

`GET /plugins/rossoctl-tools/bundles/{digest}` returns a deterministic JSON document. The plugin builds it from the store API (`get_skill`, `get_tool`, `get_tool_module`). **Core gets no bundle export.**

```jsonc
{
  "schema": "skillberry.ai/tool-bundle/v1",
  "skill": {"uuid": "…", "name": "night-out-planner"},
  "tools": [{"uuid": "…", "name": "walk_estimate", "description": "…",
             "params": {…}, "returns": {…}, "programming_language": "python",
             "module_name": "walk_estimate.py", "module": "<source>",
             "dependencies": ["<uuid>", …]}],        // other bundle tools, as in ToolSchema
  "modules": {"<uuid>": {"name": "…", "module_name": "…", "module": "<source>"}},  // dependency closure
  "digest": "sha256:…"                                 // over the canonical JSON without this field
}
```

- **Code tools only.** `mcp` tools are skipped.
- **Pinned.** The bundle is uuid-pinned, and its digest is the URL. Later edits in the store produce a new bundle.
- **Limit:** 900 KiB.
- **Execution semantics**, the store's own:
  - the dependency modules and the tool module are loaded;
  - the function named `name` is called with keyword arguments validated against `params`;
  - the return value is JSON-serialized into one text item;
  - an exception becomes `isError` with its message;
  - each call runs in its own subprocess with a timeout.
- **Specification and conformance:** `plugins/skillberry-plugin-rossoctl-tools/docs/tool-bundle-v1.md` and conformance cases, run against both the store's `FileExecutor` (local mode) and the runner.

### 13.4 The runner image (plugin)

`plugins/skillberry-plugin-rossoctl-tools/runner/` (Dockerfile; Python package `skillberry_tool_runner`; extra `runner`, which brings `mcp`). It is published as `ghcr.io/skillberry-ai/skillberry-tool-runner:<version>` and is configurable through `ROSSOCTL_RUNNER_IMAGE`.

- At start it fetches `SBS_BUNDLE_URL`, verifies `SBS_BUNDLE_DIGEST`, and refuses to start on a mismatch.
- It serves the bundle's tools as **streamable-HTTP MCP at `/mcp`**, in stateless mode, on `$PORT` (default 8000). rossoctl's `POST /tools` sets `PORT` from the first Service port's `targetPort` (`_build_tool_env_vars`, `routers/tools.py:359`).
- Each call runs `python -I` in a subprocess with a timeout (`SBS_TOOL_TIMEOUT`, default 60 s).
- Non-root, read-only root filesystem except `/tmp`.
- The standard library plus a documented set of packages (§11 Q5).

### 13.5 `/promote`, status and garbage collection (plugin)

`POST /plugins/rossoctl-tools/promote` (`skills:create`), with body `{skill, namespace, name, tools?: [uuid], authBridgeEnabled?: true}`, returns 202 `{promotion_id}`. Its steps:

1. Build the bundle, and compute its digest `d8` (the first 8 hex digits).
2. Call `POST /api/v1/tools` with:
   - `name: "<name>-<d8>"` (≤ 59 characters, so `-mcp` still fits a DNS label);
   - `containerImage: ROSSOCTL_RUNNER_IMAGE`;
   - `envVars: [{SBS_BUNDLE_URL: "{ROSSOCTL_STORE_URL}/plugins/rossoctl-tools/bundles/<digest>"}, {SBS_BUNDLE_DIGEST}]`;
   - `servicePorts: [{port: 8000, targetPort: 8000}]`;
   - `description: "From skillberry-store skill <skill> (<digest>)"`;
   - `authBridgeEnabled`.

   If the tool already exists (409) with the same digest, continue; the call is idempotent.
3. Poll `GET /tools/{ns}/<name>-<d8>` until `readyStatus` is `Ready`, or abort on `Failed` or a timeout (`ROSSOCTL_PROMOTE_TIMEOUT`, default 300 s).
4. Run `/import` on the new tool, with `promoted_from` and `bundle_digest` recorded in each endpoint.
5. Create a new skill version in which each promoted code tool is replaced by its proxy.

`GET …/promote/{id}` reports `{state: bundling|deploying|waiting|importing|done|failed, detail, tool?, skill_uuid?}`. The state is kept in memory, and the operation is idempotent to re-run.

**Revisions without downtime.** Each digest is its own rossoctl tool, so agents on an older skill version keep working. `POST …/promote/gc {namespace, name}` deletes `<name>-*` revisions that no head skill version references, through the existing `DELETE /tools/{ns}/{name}`.

**`ROSSOCTL_STORE_URL`** is the store's in-cluster URL, as reachable from tool namespaces, for example `http://skillberry-store.rossoctl-system.svc.cluster.local:8000`. It is set through `extraEnv`.

### 13.6 Identity and policy

- The runner pod gets AuthBridge like any tool. Agents reach it through the stage 1 proxy and their own AuthBridge.
- The bundle fetch follows the skill-fetch path (tool namespace → store), with the digest as the capability for now (§8, §11 Q7).

### 13.7 Demo extension

A code tool in `night-out-planner`:

```python
def walk_estimate(distance_km: float, weathercode: int, temperature_f: float) -> dict:
    """Estimate walking minutes and a go/no-go for walking to a restaurant."""
```

1. Run `/promote` with `team1` and `night-out-utils`. rossoctl's Tools lists `night-out-utils-<d8>`, described as coming from the store.
2. The skill's new version replaces `walk_estimate`'s code with a proxy, and `rossoctl-tools.json` lists three tools.
3. Chat: "Is it walkable to Trattoria di Mare tonight?" Phoenix shows the tool span from the runner pod.

---

## 14. Implementation guide

Where §1–§13 and this section differ, this section wins. Both yield to §0.1.

### 14.1 Settled decisions

| # | Decision |
|---|---|
| D1 | rossoctl is not changed in any stage (P-1). Any rossoctl change needs a written P-1 justification and the owner's approval first. |
| D2 | Core changes are exactly C1–C3 (P-2). Anything else goes in the plugin. A new core change needs a written P-2 justification first. |
| D3 | Core never contains `rossoctl` or `svc.cluster.local` outside tests (enforced by a test). |
| D4 | The store ACL is assumed `disabled`. Write the plugin so that `delegated` only changes which token is sent. |
| D5 | Stage 2 deploys only through rossoctl's existing `POST /api/v1/tools`, with the store-owned runner image. |
| D6 | Open questions take the proposal in §11. |

### 14.2 Repositories and working rules

| Repo | Base | Notes |
|---|---|---|
| skillberry-store | `main` | Branch `rossoctl-skills` (fork `erezh16/skillberry-store`) |
| rossoctl | `main` | **No branch needed.** It is used read-only for tests and the demo. |
| rossoctl/examples | `main` | Branch `rossoctl-skills` for §5.3 |

Store rules:
- activate the venv (`source .venv/bin/activate`) before every `make` or `python` command;
- run tests with `make test`;
- finish with `make lint`, and apply what it recommends;
- commit at each significant step.

### 14.3 Core interfaces

**C1** — `modules/file_executor.py`: `mcp_call_result_to_dict(result)` → `{"error": text}` when `isError`, else `{"return value": joined_text, "structured"?: …}`.

**C2** — `modules/tool_providers.py`:

```python
@dataclass
class CallResult: text: str; is_error: bool = False; structured: Any = None
@dataclass
class ProviderContext: env_id: Optional[str] = None; bearer: Optional[str] = None

class ToolProvider(Protocol):
    name: str
    export_version: str
    def validate(self, endpoint: dict) -> None: ...
    async def call_tool(self, packaging_params: dict, arguments: dict, ctx: ProviderContext) -> CallResult: ...
    def export_module(self, tool: dict) -> Optional[str]: ...

def register_provider(provider, owner_slug) -> None
def get_provider(name) -> Optional[ToolProvider]          # None if unknown or owner inactive
def set_active_resolver(resolver) -> None                 # injected by PluginLoader
def validate_mcp_packaging(packaging_format, packaging_params) -> None
async def execute_via_provider(tool, parameters, ctx) -> dict   # FileExecutor-shaped result
def export_module_for(tool) -> Optional[str]              # provider.export_module, or None
def export_cache_token(tools) -> tuple                    # sorted (name, export_version) of active backing providers
```

Wiring:
- **Execution:** `ToolsService.execute` and vMCP `_HandlerToolSource.execute` dispatch tools that have `endpoint`. `ToolProviderUnavailable` maps to 422.
- **Validation:** schema and `ToolsService.create`/`update` call `validate_mcp_packaging`.
- **Export:** `SkillsService.gather_export_inputs` replaces the module with `export_module_for(tool)` when that returns text.
- **npx:** `tools/publish.py` `cache_key` appends `export_cache_token(tools)`.
- **Plugins:** `PluginBase.register_tool_provider`; `PluginLoader` registers providers and injects the resolver.

**C3** — `tools/anthropic/contributions.py`:
- `ExportContribution(files, metadata, skill_md_section)`, `ExportContributor`;
- `ImportClaim(claimed_paths, tools)`, `ImportClaimer`;
- registration through `PluginBase.register_export_contributor` and `register_import_claimer`;
- `ContributionError` naming the plugin.

The exporter merges contributions after core content; the importer offers files to claimers first (`extras=` on `import_from_anthropic_skill`). Contributed sections are wrapped in the generated markers. `extra.frontmatter_metadata` is kept.

### 14.4 Plugin interfaces

```
plugins/skillberry-plugin-rossoctl-tools/
  src/skillberry_plugin_rossoctl_tools/
    plugin.py       backend.py     provider.py
    proxy.py        # the streamable-HTTP proxy generator (moved from core r4 mcp_proxy.py)
    export.py       # contributor: frontmatter, rossoctl-tools.json, Tools section
    importer.py     # claimer
    check.py        # drift and per-namespace pre-flight
    promote.py      bundle.py      # stage 2
  runner/           # stage 2: Dockerfile + skillberry_tool_runner
  deploy/reservation-tool.yaml
  docs/tool-bundle-v1.md
  tests/  (unit, e2e, cluster/run-local-demo.sh)
```

- **Provider:** `RossoctlProvider.export_module(tool)` returns `proxy.render(tool, steps(pp))`, unless `ROSSOCTL_PROXY_EXPORT=false`. `export_version = proxy.GENERATOR_VERSION`.
- **Contributor:** for skills with rossoctl tools, it returns `rossoctl-tools.json`, `metadata.rossoctl-requires-tools`, and a SKILL.md section built from `proxy.render_tools_section(tools)` plus the rossoctl note.
- **Routes:** as in §4.2.6. `/check` returns:

  ```json
  {"tools": [{"uuid", "name", "rossoctl_tool", "drift", "live_sha256", "stored_sha256", "error"?}],
   "namespaces": {"team1": [{"tool", "present", "ready", "port", "port_matches"}]}}
  ```

- **`/import`:** accepts `read_only` per tool reference, which sets `extra.mcp_annotations.readOnlyHint` for the named MCP tools.

### 14.5 rossoctl

Nothing to implement. The local cluster e2e runs rossoctl's backend from `main`.

### 14.6 Tests that define "done"

**Stage 1:**
- **Core, with no plugins installed:**
  - `tests/modules/test_tool_providers.py`: validator matrix, gating, 422, vMCP error, `export_module` dispatch, cache token;
  - `tests/tools/test_contributions.py`;
  - `tests/test_core_has_no_rossoctl.py`;
  - golden export tests unchanged.
- **Plugin:**
  - unit tests (backend, provider, proxy, export, claimer, check, resync);
  - e2e (a) and (b) of §7.4;
  - the cluster e2e (c) against rossoctl `main`.
- **Examples:** unit tests for skill linking and `run_skill_script`.

**Stage 2:**
- the bundle determinism and digest;
- the conformance cases, on both executors;
- the runner e2e: the image, or the package run locally, served through FastMCP;
- `/promote` against the fake backend (`POST /tools`, polling, import, swap, idempotent 409);
- GC;
- a cluster e2e that promotes `walk_estimate`.

### 14.7 Order of work

1. Core C1–C3 and the no-plugin CI job (§15 lists what to change from r4).
2. Plugin: proxy generator, provider `export_module`, contributor (with the Tools section), claimer, `/check` with pre-flight, `/import` with `read_only`.
3. The examples change (§5.3).
4. The cluster e2e and the demo guide against rossoctl `main`. Then `make test` and `make lint`.
5. Stage 2: the bundle, the runner, `/promote`, GC, the demo extension.

---

## 15. Migration from the r4 implementation

The branches built for r4 change as follows.

**rossoctl (`erezh16/rossoctl`, branch `rossoctl-skills`).** Drop all six commits:
- the flag;
- the required-tools annotation, validation and injection;
- the UI;
- the Helm plugin config and OAuth job;
- the installer flag and reservation-tool script;
- the docs.

Reset the branch to `main`, or delete it. Nothing replaces these in rossoctl.

**skillberry-store, core:**

| r4 code | r5 action |
|---|---|
| `modules/file_executor.py` (C1) | Keep |
| `modules/tool_providers.py` | Keep the registry, validator, dispatch and 422. Remove `list_tools`/`proxy_resolution` from the protocol. Add `export_version`, `export_module`, `export_module_for` and `export_cache_token`. |
| `tools/anthropic/mcp_proxy.py` | **Move to the plugin** as `proxy.py`. Core keeps no generator. |
| `tools/anthropic/exporter.py` (proxy wiring, Tools section, placement) | Replace `apply_mcp_proxies` with `export_module_for`. Remove the core Tools section and `extra.tools_section`; the plugin's contribution supplies the section. Keep the contribution and generated-marker code (C3). |
| `services/skills_service.py` | `gather_export_inputs` uses `export_module_for`. Keep the claimer wiring and `frontmatter_metadata`. |
| `tools/publish.py` | Replace `MCP_PROXY_GENERATOR_VERSION` and `_active_proxy_providers` with `export_cache_token`. |
| `services/field_selection.py`, `fast_api/skills_api.py` (computed fields) | **Remove** |
| `docs/config-env-vars.md` (`SBS_MCP_PROXY_EXPORT`, `MCP_PROXY_*`) | Move to the plugin README as `ROSSOCTL_PROXY_EXPORT`, and the proxy's run-time variables |
| `tests/tools/test_mcp_proxy.py`, `tests/e2e/test_mcp_proxy_export.py` | Move to the plugin's tests. Core keeps a test that a provider's `export_module` is used. |
| `tests/services/test_computed_fields.py` | Remove |
| `CHANGELOG.md` | Keep C1. Add C2 and C3 entries. |

**skillberry-store, plugin:**
- add `proxy.py`, the provider's `export_module` and `export_version`, and the Tools section in the contributor;
- add `/check` pre-flight and `port_matches`;
- extend `/resync` to port changes;
- add `read_only` on `/import`;
- remove the computed-field registration;
- add `deploy/reservation-tool.yaml`;
- move the demo harness to `tests/cluster/`, running rossoctl `main`.

**Demo guide (`docs/demos/rossoctl-night-out-planner.md`):** rewrite to use the values of §6.2, with no rossoctl branch. Remove the steps that showed rossoctl's flag, annotations, cards and injected env; add the `/check` pre-flight step.

**rossoctl/examples:** unchanged from r4 (§5.3).
