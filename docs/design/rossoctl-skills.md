# Skills that use rossoctl tools — Design

Status: **Proposal (revision 4)**
Owner: skillberry-store, with matching changes in rossoctl
Scope: Stage 5 of [rossoctl-integration.md](../rossoctl-integration.md#staged-improvement), "Skills that use rossoctl tools", extended to tools that *become* rossoctl tools. The integration doc's sketch is the section [Stage 1: store skills that include rossoctl tools through proxy scripts](../rossoctl-integration.md#stage-1-store-skills-that-include-rossoctl-tools-through-proxy-scripts).

Revision notes:
* r4 — **rossoctl hosts every rossoctl tool; the store only works with proxies, and only through rossoctl's backend API** (§4.0). Skill composition exists only in the store, so the store's plugin uses rossoctl as a server: it lists, calls and imports tools through rossoctl's `/tools` API (stage 1), and asks rossoctl to create native tools from store code (stage 3). Consequences: the store needs **no streamable-HTTP MCP support** (no client, no `mcp` bump, no vMCP transport); core's resolver hook becomes a *provider* hook that delegates listing and execution to a plugin; the plugin authenticates to rossoctl with a client-credentials Keycloak client. **Stage 2 (streamable vMCP) is removed from this design** and kept only as an independent, optional store improvement (§12). Stage 3 is rewritten: rossoctl owns the generic **bundle runner** image, and the store only exports a bundle (§13).
* r3 — Staged the feature (rossoctl tools, streamable vMCP, promotion to native tools).
* r2 — Split the store side into rossoctl-agnostic core changes and an optional `rossoctl-tools` plugin (§4.0); a standalone store behaves as today (§4.3).
* r1 — Initial proposal; re-verified against the latest rossoctl, operator, cortex and CLI.

## 0. Stages

| Stage | What a user gets | skillberry-store | rossoctl | Sections |
|---|---|---|---|---|
| **1. Skills from existing rossoctl tools** | In the store, compose a skill from tools already deployed in rossoctl. Agents get them through the skill as runnable proxy scripts, and rossoctl validates, wires and shows the dependency. | Core: `isError` fix, provider hook, runnable proxy export, export/import hooks, opt-in computed fields. Plugin `rossoctl-tools`: backend client, provider, import. | Flag `toolSkills`: annotations, agent-create validation and URL injection, UI, optional auto-routes, Helm (plugin config, service credential), examples. | §1–§9 |
| **2. *(removed)*** | Streamable-HTTP serving from the store's vMCP is not needed by this feature. | Optional, independent store improvement; not part of this design. | — | §12 |
| **3. Skills whose tools become native rossoctl tools** | Write code tools in the store, compose a skill, and promote them: rossoctl deploys them as a native tool (AuthBridge, policy, catalog, shared by agents), and the skill switches to proxies of it. | Core: `export-mcp-bundle`. Plugin: `/promote`. | Flag `toolSkillsPublish`: a generic **bundle-runner** image (rossoctl-owned), `POST /tools/from-store`, UI. | §13 |

**Who owns what.** The store owns composition, versions and tool *source*. rossoctl owns every running tool, including the server code inside it: example tools bring their own MCP server; promoted store tools run in rossoctl's bundle runner, the same pattern as its `simulation-harness` image for simulated tools.

**Dependencies.** Stage 1 stands alone. Stage 3 needs stage 1's plugin (backend client, `/import`).

Unless a section says otherwise, §1–§9 describe stage 1.

**Implementers: start with §14 (Implementation guide).** It fixes the remaining decisions, gives exact interfaces and file locations, defines "done", and orders the work. Where it differs from earlier sections, §14 wins.

Sources inspected (latest `main` of each repo on 2026-10-21):

| Repo | Commit | Notes |
|---|---|---|
| skillberry-store (this repo) | `70137bb` | |
| `rossoctl/rossoctl` | `1cac9f3f` (2026-10-19) | The 12 commits since `44ec9139`, the revision the integration doc used, change docs only. Every backend, UI, chart and script line reference below was re-verified at `1cac9f3f`. |
| `rossoctl/operator` | `3b92d6c` (2026-10-14) | A dependency bump only since `10dd076`. |
| `rossoctl/cortex` | `main` | AuthBridge plugin catalogue (`docs/plugin-catalog.md`) |
| `rossoctl/rossoctl-cli` | `main` | The `rossoctl` CLI (`cmd/agents_import.go`, `cmd/tools_import.go`) |
| `rossoctl/examples` | `f1fc5df2` (2026-10-13) | The tools and agents used here (`weather_tool`, `reservation_tool`, `claude_agent`, `generic_agent`) were last changed between March and July 2026. |

In rossoctl, backend paths are relative to `rossoctl/backend/app/`, UI paths to `rossoctl/ui-v2/src/`, and chart paths to `charts/rossoctl/`, as in the integration doc. A local rossoctl checkout older than `#2388` (for example `c027bb8e`, 2026-08-09) still has the monolithic `routers/agents.py`, so its line numbers will not match. rossoctl's docs were restructured in #2530 (`docs/concepts/{core,experiments}/`, `docs/workloads/`, `docs/get-started/`, `docs/reference/`), and there is no `docs/demos/` directory any more.

---

## 1. Problem (stage 1)

Today a skillberry-store skill cannot use a rossoctl tool, for four reasons (integration doc, "Why this does not yet work for rossoctl"):

1. **Exported proxies are not runnable.** For a `packaging_format: "mcp"` tool, `export-anthropic` writes the bodiless stub that `mcp_content_from_manifest` generates (`fast_api/server_utils.py:54-95`) to `scripts/<tool>.py`. The script has no body and no entry point, so an agent that receives it cannot call anything.
2. **No way to reach them.** The store's MCP client is SSE-only (`modules/file_executor.py:16-17, 389-443`; mcp-importer), while rossoctl tools serve **streamable HTTP** at `http://<tool>-mcp.<ns>.svc.cluster.local:<port>/mcp`. rossoctl's backend, however, already lists and invokes any tool (`POST /tools/{ns}/{name}/connect`, `/invoke`), so the store can work through rossoctl instead of adding a transport (§4.0).
3. **Location is per-namespace.** One fixed `packaging_params.mcp_url` cannot serve a skill shared across namespaces.
4. **No declared dependency.** Nothing in the skill package says "I need tool X", so rossoctl cannot validate it, wire it, or show it.

On the rossoctl side, an agent gets **one** MCP tool, through `mcpToolName` → `MCP_URL` (`services/agent_env_defaults.py:19-27, 93-102`, and only behind `agent_import_defaults`). It gets **any number** of skills through `request.skills`. Several tools therefore mean hand-written `envVars`, with no instructions on when and how to use each one.

## 2. Goals and non-goals

### Goals

- **G0. The store stays independent.** skillberry-store keeps running standalone exactly as today. Core changes are useful without rossoctl and never reference it; everything rossoctl-specific is in an optional plugin (§4.0).
- **G1. Runnable tool proxies in skills.** A store skill that aggregates MCP-backed tools exports one self-contained, standard-library-only CLI per tool. Any MCP tool gets this in core; with the plugin, the CLI calls the live rossoctl tool.
- **G2. Late binding.** The same skill works in every rossoctl namespace that runs the tools. The endpoint is resolved at run time, never baked into the export.
- **G3. Declared dependencies.** The skill package declares the rossoctl tools it needs, in a human-readable form (SKILL.md) and a machine-readable form (frontmatter plus a JSON file).
- **G4. rossoctl acts on the declaration**, behind a feature flag:
  - show required tools on the skill;
  - validate them at agent creation;
  - inject resolved URLs;
  - optionally add AuthBridge outbound routes.
- **G5. Importing rossoctl tools into the store.** The `rossoctl-tools` plugin creates proxy tools, plus an optional skill skeleton, from live rossoctl tools, through rossoctl's backend API.
- **G6. Store-side execution and evaluation.** The store can execute these tools (`/tools/{id}/execute`, vMCP) for testing, evaluation and dreaming. rossoctl performs the call (`/invoke`); the store never connects to tool pods.
- **G7. No credentials in the proxy.** Auth is AuthBridge's job on the agent's egress.

### Non-goals (stage 1)

- **N0.** Tools that *become* rossoctl tools: that is stage 3 (§13). Any MCP transport work in the store: not needed (§4.0, §12).
- **N1.** Operator or CRD changes. No `Skill` CRD, and no `AgentRuntime.spec.tools`; see stage 2 of the integration doc. The operator is untouched.
- **N2.** Store ACL enforcement. The store is assumed to run in `disabled` mode (§8 covers `delegated`).
- **N3.** An SSE transport inside the exported proxy. Legacy SSE `mcp` tools (`mcp_url` only) keep today's stub export (§4.1.3).
- **N4.** Fixing version consistency (integration stage 1) or immutability (stage 2). §7.2 notes where this feature depends on stage 1.

## 3. Overview

```
 rossoctl-system                                            team1 (tenant namespace)
 ┌─────────────────────────────┐   REST /api/v1/tools   ┌──────────────────────┐   MCP     ┌───────────────────────┐
 │ skillberry-store            │  (list, connect,       │ rossoctl-backend     │──────────▶│ weather-tool-mcp      │
 │  rossoctl-tools plugin ─────┼─ invoke, from-store) ─▶│  (Keycloak roles)    │           │ reservation-tool-mcp  │
 │  · imports tools as proxies │◀── tool schemas ───────│                      │           │ (rossoctl-hosted)     │
 │  · export-anthropic:        │                        │  autosync ───────────┼─┐         └───────────▲───────────┘
 │    SKILL.md + Tools section │◀─ GET /skills/?fields= │  agent create:       │ │                     │ tools/call
 │    rossoctl-tools.json      │   …,_rossoctl_         │   validate tools,    │ │ skill    ┌──────────┴───────────┐
 │    scripts/<tool>.py proxy  │   required_tools ──────┼─▶ inject URLs        │ │ refs     │ agent pod            │
 └──────────────┬──────────────┘                        └──────────────────────┘ └────────▶ │  scripts/*.py proxy  │
                │ zip (init-container fetch)                                                │  AuthBridge (egress) │
                └──────────────────────────────────────────────────────────────────────────▶└──────────────────────┘
```

The store never opens a connection to a tool pod: the backend does that on its behalf (import, check, store-side execution), and agents do it themselves through the proxy scripts.

The pieces are independent. They compose like this:

- **Store core alone** (G0): no rossoctl anywhere, and no behavior change. The hooks are inert without a plugin.
- **Store with the `rossoctl-tools` plugin active** (G1–G3, G5–G6): the store reaches rossoctl only through its backend API. Any agent that can run scripts can use the exported skill, even with rossoctl's flag off; the proxy falls back to cluster-DNS resolution.
- **rossoctl flag on** (G4): validation, UI, explicit URL injection and auth routes.
- **Example agent change** (§5.6): an agent framework that can run skill scripts.

---

## 4. skillberry-store changes

### 4.0 Principle and split

**rossoctl hosts every rossoctl tool. skillberry-store only ever works with proxies of them, and reaches rossoctl only through rossoctl's backend API.**

- Composing skills happens only in skillberry-store; rossoctl has no skill-composition surface (no skill update API, no CLI skill commands, a file-upload-only UI). So the user is in the store when they compose, and the store's `rossoctl-tools` plugin uses rossoctl **as a server**:
  - stage 1: it lists and calls existing rossoctl tools through rossoctl's API, and imports them as proxies;
  - stage 3: it asks rossoctl to create a native tool from a bundle of store code tools, then imports that tool as proxies, exactly as in stage 1.
- The store never serves tools to rossoctl, and never opens connections to tool pods. Agents call tools directly, through the exported proxy scripts, their own AuthBridge, and the tool's AuthBridge; the store is not on that path.

Because the store's only peer is rossoctl's REST API, **skillberry-store needs no streamable-HTTP MCP support at all**: no MCP client changes, no `mcp` version bump, no vMCP transport work. The exported proxy does speak streamable HTTP, but it is a generated standard-library script that runs in the agent's pod, not store code.

skillberry-store and rossoctl remain independent projects, so the store-side work is split by one rule:

> **Core** gets only changes that are useful to any store user and that have no knowledge of rossoctl. That means no rossoctl names, URLs, env vars, file formats, Kubernetes conventions or code paths.
> **Everything that knows rossoctl lives in the `rossoctl-tools` plugin**, and it takes effect only while that plugin is installed **and** active (`PluginLoader.is_active`, `plugins/loader.py:167-172`).

| Concern | Core (rossoctl-agnostic) | `rossoctl-tools` plugin |
|---|---|---|
| Error results | `isError` reported as an error on the existing MCP path (§4.1.1) | — |
| Remote tools | A *provider* extension point: a tool whose `endpoint.provider` names a plugin is listed and executed by that plugin (§4.1.2) | provider `rossoctl`: calls rossoctl's `/connect` and `/invoke` |
| Exported proxy | Generic runnable stdlib MCP CLI with data-driven endpoint resolution (§4.1.3) | adds the rossoctl resolution steps |
| Skill package additions | Extension point for extra files, frontmatter `metadata` and a SKILL.md section (§4.1.4) | `rossoctl-tools.json`, `metadata.rossoctl-requires-tools`, "Tools (rossoctl)" note |
| Import of a package | Extension point to claim files (§4.1.4) | recognizes `rossoctl-tools.json` |
| Derived skill fields | Extension point for opt-in computed fields (§4.1.5) | `_rossoctl_required_tools` |
| Code-tool bundle | Export of a skill's code tools as a portable, pinned bundle (§13.3) | sends it to rossoctl (stage 3) |
| Talking to rossoctl | — | backend URL, service credential, `/import`, `/check`, `/resync`, `/promote` (§4.2) |

The direction of dependency is one-way: the plugin depends on core's extension points, and core never imports, names or special-cases the plugin. Removing the plugin package leaves a store that works with every object it holds, including objects the plugin created (§4.3).

### 4.1 Core changes (no rossoctl dependency)

Each item below is justified for a standalone store, with no rossoctl in the picture.

#### 4.1.1 Correct error results

**Standalone value.** This is a bug fix on the store's existing (SSE) MCP path. `execute_mcp_tool` (`file_executor.py:389-443`) returns `content[0].text` and ignores `isError`, so a failing remote tool looks successful. The fix:
- an `isError: true` result is returned as an execution error;
- every text content item is joined, and `structuredContent` is returned when present.

**Behavior change for every user:** a remote tool that reports an error now produces an error response where it used to produce `{"return value": …}`. This goes in `CHANGELOG.md`.

#### 4.1.2 Provider-backed tools: the opaque `endpoint` block and providers

**Standalone value.** It lets a plugin own how a remote tool is reached, instead of the store connecting to a fixed `mcp_url` itself. Any plugin can use it, for example one that fronts a corporate tool gateway. Core defines the mechanism and ships **no** provider.

```jsonc
"packaging_format": "mcp",
"packaging_params": {
  "mcp_tool_name": "get_weather",          // required (unchanged)
  "mcp_url": "http://…/sse",               // optional when "endpoint" is present (today's SSE path)
  "endpoint": {                            // optional, opaque to core
    "provider": "<name>",                  // e.g. "rossoctl" — a string, never interpreted by core
    "...": "provider-specific keys"        // stored and passed through verbatim
  },
  "input_schema_sha256": "…"               // optional; sha256 of the canonical inputSchema at import
}
```

**Validator** (`ToolSchema.validate_packaging_params`, `:116-144`, and a shared `validate_mcp_packaging()` that `ToolsService.create` and `update` also call, so plugin-created tools are checked too):
- `mcp_tool_name` is required and must match `^[A-Za-z0-9_.\-]{1,128}$`.
- At least one of `mcp_url` and `endpoint` is required.
- `endpoint.provider` must match `^[a-z][a-z0-9-]{0,31}$`.
- `endpoint` must be a JSON object of at most 4 KiB.

Core validates the shape only, never the contents.

**Provider registry** (`src/skillberry_store/modules/tool_providers.py`, new):

```python
class ToolProvider(Protocol):
    name: str
    async def list_tools(self, endpoint: dict) -> list[dict]: ...                     # {name, description, input_schema, annotations?}
    async def call_tool(self, packaging_params: dict, arguments: dict, ctx) -> CallResult: ...
    def proxy_resolution(self, packaging_params: dict) -> list[dict]: ...           # export steps (§4.1.3)
    def validate(self, endpoint: dict) -> None: ...                                 # raise ValueError
```

- `register_provider()` is called by a plugin at load.
- Lookup goes through `PluginLoader.is_active(owner_slug)`, just as event handlers are skipped for inactive plugins (`plugins/events.py`, `_enabled_resolver`). A disabled plugin's provider is therefore invisible.
- `validate` runs on create and update when the provider is active. When it is not, only the generic shape check applies, so a store without the plugin can still hold, copy and export the tool.
- **Execution dispatch** (`FileExecutor`, vMCP, `get_mcp_tools`): a tool with `endpoint` is executed by its active provider's `call_tool`, with the request's `ExecutionContext` (`ctx`). Without an active provider, the store returns **422** `"Tool '<name>' needs provider '<provider>', which is not available in this store (no active plugin provides it)."` It never falls back to guessing a URL. A tool with only `mcp_url` keeps today's SSE path.

#### 4.1.3 Runnable MCP proxy on export

**Standalone value.** Today an exported `mcp` tool is a bodiless stub that cannot run anywhere (`mcp_content_from_manifest`, `server_utils.py:54-95`). After this change, a remote MCP tool exports as a working CLI for whatever executes the skill: Claude Code, `npx skills add` installs, vNFS users. The generator is pure text generation; the store itself makes no MCP calls here.

- **Which tools:** tools with `packaging_format == "mcp"` and an `endpoint` block. Tools with only an SSE `mcp_url` keep today's stub (N3).
- **Where:** `SkillsService.gather_export_inputs` (`services/skills_service.py:732-779`), the single bundling point for `export-anthropic`, npx publish (`tools/publish.py:579`) and vNFS / skill-optimizer. It sets `tool_modules[name] = render_mcp_proxy_cli(tool, steps)` (new: `tools/anthropic/mcp_proxy.py`). `get_module` is unchanged.
- **Generic CLI contract:**
  - `python3 scripts/<tool>.py --<prop> …`, `--json '{…}'`, `-` (stdin), `--describe`, `--check` (schema hash), `--raw`.
  - Exit codes: 0 ok, 1 tool `isError`, 2 usage, 3 resolution or transport error, 4 schema drift.
  - Standard library only (`urllib` + `json`), Python ≥ 3.8.
  - No credentials are embedded. `MCP_PROXY_AUTH_HEADER` is read from the environment if set.
- **Resolution steps are data, not code paths in core.** The generated script carries an ordered list of resolution steps in its embedded spec. The interpreter for step *kinds* is fixed and generic:

  | Step kind | Meaning |
  |---|---|
  | `env` | `{"var": "X"}`: use `$X` if set |
  | `template` | `{"template": "http://{a}-x.{b}:{c}/mcp", "vars": {"b": {"env": "B", "file": "/path", "default": "…"}}}`: format a URL from env, file or default values |
  | `url` | `{"url": "…"}`: a literal URL |

  The active provider's `proxy_resolution()` returns the step list. Without an active provider, the list is just `[env MCP_PROXY_<TOOL>_URL]`, and the script exits 3 with `"set MCP_PROXY_<TOOL>_URL"`.
- **Protocol in the script (streamable HTTP, revisions 2025-03-26 / 2025-06-18):**
  1. `POST initialize`, accepting a JSON or SSE response; record `Mcp-Session-Id`.
  2. `POST notifications/initialized`.
  3. `POST tools/call`. Server-initiated requests (sampling, elicitation) get JSON-RPC error `-32601`.
  4. Best-effort `DELETE` of the session.

  Optional `traceparent`/`tracestate` come from `TRACEPARENT`/`TRACESTATE`. The generator emits this protocol as text; it is tested by running generated scripts against a test MCP server (§7.4), not by store runtime code.
- **Code-generation safety.** Manifest values are never interpolated into Python source. The spec is serialized with `json.dumps(ensure_ascii=True)` into a raw literal. The docstring line uses only `mcp_tool_name`, which the validator restricts. A hostile-input unit test checks the result with `ast.parse` plus execution.
- **SKILL.md usage text.** A generic "Tools" section is generated from the schemas (`python3 scripts/<tool>.py --city <string>`, the exit-code legend, a read-only marker from `readOnlyHint`). It appears only when the skill has at least one proxy-exported tool, so **all other exports stay byte-identical to today's**. The skill's `extra.tools_section: append | prepend | omit` controls it.
- **Kill switch:** `SBS_MCP_PROXY_EXPORT=false` emits stubs as today.
- **npx cache key:** add `MCP_PROXY_GENERATOR_VERSION` to the `modified_at` key (`publish.py:448-473`).

#### 4.1.4 Export and import contribution hooks

**Standalone value.** These are general extension points for any plugin that wants to add metadata to skill packages, such as a policy plugin adding a manifest, or a provenance plugin adding an attestation file.

- **`ExportContributor`**, called by `_build_file_structure` (`exporter.py:320-377`) with the skill, tools and snippets. It may return:
  - extra files (paths must be new; collisions are an error, not a merge);
  - frontmatter `metadata` entries (string→string, merged into the existing `metadata` such as npx's `sbs_name`; key collisions are an error);
  - a SKILL.md section (appended after core's Tools section).
- **`ImportClaimer`**, called by `import_from_anthropic_skill` (`importer.py:536-648`) before the default classification. It may claim specific files and return the store objects to create for them. Unclaimed files go through today's path.
- Both are registered by plugins and consulted only while the owning plugin is active. With no contributor active, export output is exactly today's. A contributor that raises is logged, and the export or import fails with 500 naming the plugin. It never silently produces a partial package.

`parse_skill_metadata` (`importer.py:74-125`) also starts keeping frontmatter `metadata` instead of dropping it. That is a standalone fix, and it makes round trips lossless for any metadata.

#### 4.1.5 Opt-in computed skill fields

**Standalone value.** This generalizes the existing `_npx_install` pattern (`services/field_selection.py`, `OPT_IN_ONLY_FIELDS`, `:227-229`; `fast_api/publish_api.py:542-600`) into a hook.

- A plugin registers `ComputedSkillField(name, compute(skill_dict, tools) -> Any)`.
- The name must start with `_`. It is added to `OPT_IN_ONLY_FIELDS["skill"]`, so **no preset returns it**. It is computed only when a caller names it explicitly in `fields=`.
- `GET /skills/` and `GET /skills/{id}` without the explicit field do no extra work.
- An unknown or inactive field name in `fields=` is rejected with 400 by the existing validation (`parse_fields_spec`), so a client learns that the store lacks the capability.

#### 4.1.6 What core does *not* get

- No streamable-HTTP MCP client or server, no `mcp` version bump, no vMCP transport changes.
- No `rossoctl` string, env var, URL template, `svc.cluster.local` default, ServiceAccount-file read, credential handling, or `rossoctl-tools.json` parsing.
- No new default-preset field on skills: `GET /skills/` responses are unchanged for every client.
- No change to the SSE path's behavior, apart from the §4.1.1 error fix.

### 4.2 The `rossoctl-tools` plugin

`plugins/skillberry-plugin-rossoctl-tools/`:
- the same layout as `skillberry-plugin-mcp-importer`;
- slug `rossoctl-tools`, type `IMPORTER`;
- entry point `skillberry_plugin_rossoctl_tools.plugin:SkillberryPluginRossoctlTools`;
- optional extra `plugin-rossoctl-tools`, included in `plugins-all`, with **no** MCP dependency (it uses `httpx` only);
- **disabled** in the repo's `plugin_config.json`, like `mcp-importer` and `simulate`.

`is_enabled()` returns True when a backend URL is configured. The plugin is *active* only when the deployment's plugin config enables it, which rossoctl's chart does (§6.2). At load it only registers its provider, contributor, claimer and computed field; it makes no network calls until a route is invoked or a provider-backed tool is executed.

#### 4.2.1 Talking to rossoctl: backend URL and credential

All calls go to rossoctl's backend REST API (`/api/v1`, Service `rossoctl-backend:8000` in `rossoctl-system`). The plugin never connects to tool pods.

| rossoctl endpoint | Used for | Role |
|---|---|---|
| `GET /tools?namespace=` (`routers/tools.py:744`) | discovery (UI picker in the store) | viewer |
| `GET /tools/{ns}/{name}` (`:864`) | existence, readiness (`readyStatus`), port (`service.ports[0].port`) | viewer |
| `POST /tools/{ns}/{name}/connect` (`:2436`) | `list_tools`: `{name, description, input_schema}` per MCP tool | operator |
| `POST /tools/{ns}/{name}/invoke` (`:2516`) | `call_tool`: `{tool_name, arguments}` → `{result: {content, isError}}` | operator |
| `POST /tools/from-store` (new, §13.5) | stage 3 promotion | operator |

**Credential (`disabled` store ACL).** rossoctl's backend requires a Keycloak bearer with the roles above (`core/auth.py`, `require_roles`), except when rossoctl itself runs with `ENABLE_AUTH=false`. The plugin uses the **client-credentials** grant with a dedicated Keycloak client:
- rossoctl's chart already has the mechanism: the `api-oauth-secret` job registers a service-account client, assigns it a realm role, and writes `CLIENT_ID`, `CLIENT_SECRET` and `TOKEN_ENDPOINT` into a Secret (`templates/api-oauth-secret-job.yaml`, `rossoctl/auth/api-oauth-secret/register_api_client.py:427-435`; values `apiOAuthSecret`, role `rossoctl-operator`).
- §6.2 adds a second instance for the store: client `skillberry-store-tools`, role `rossoctl-operator`, Secret `skillberry-store-rossoctl-credentials`, mounted into the store pod.
- The plugin reads `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT`, caches the access token until shortly before expiry, and retries once on 401.
- In `delegated` mode the plugin forwards the calling user's token instead (§8.2).

**Configuration** (plugin env):

| Env | Default | Meaning |
|---|---|---|
| `ROSSOCTL_BACKEND_URL` | — (plugin inactive without it) | e.g. `http://rossoctl-backend.rossoctl-system.svc.cluster.local:8000` |
| `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT` | — | Client-credentials grant; omitted when rossoctl runs without auth |
| `ROSSOCTL_ALLOWED_NAMESPACES` | `""` (any) | Comma list of namespaces the plugin may target |
| `ROSSOCTL_TIMEOUT` | `30` | Seconds per backend call |

**SSRF.** The only outbound destination is `ROSSOCTL_BACKEND_URL`, set by the deployment. Tool and namespace names are validated as DNS-1123 labels and go into URL *paths* on that host only.

#### 4.2.2 Provider `rossoctl`

The `endpoint` block it owns:

```jsonc
"endpoint": {
  "provider": "rossoctl",
  "tool": "weather-tool",        // rossoctl tool (workload) name, DNS-1123 label
  "namespace": "team1",          // namespace imported from (store-side default)
  "port": 8000,                  // Service port reported by GET /tools/{ns}/{name}
  "path": "/mcp",
  "gateway_prefix": "weather_",  // optional
  "promoted_from": "<uuid>",     // stage 3 only: the store code tool this proxy replaced
  "bundle_digest": "sha256:…"    // stage 3 only
}
```

- `validate`: `tool` and `namespace` are DNS-1123 labels (≤ 63), `port` is in 1..65535, `path` starts with `/`.
- `list_tools`: `POST /tools/{ns}/{tool}/connect`.
- `call_tool`: `POST /tools/{ns}/{tool}/invoke`; maps `result.content` and `result.isError` to core's `CallResult`, and backend 502/504 to a transport error. This is how the store **executes** rossoctl tools for testing, evaluation and dreaming: rossoctl makes the call, under rossoctl's authorization.
- `proxy_resolution` returns this step list, using core's generic step kinds. It is what the *agent's* proxy uses at run time, inside the cluster:
  1. `env ROSSOCTL_TOOL_<TOOL>_URL`, which rossoctl injects when its flag is on (§5.3);
  2. `template` with `ROSSOCTL_MCP_GATEWAY_URL` (plus `gateway_prefix` as the tool name) when that env var is set;
  3. `template` `http://{tool}-mcp.{ns}.svc.cluster.local:{port}{path}`. `ns` comes from `ROSSOCTL_NAMESPACE`, else the file `/var/run/secrets/kubernetes.io/serviceaccount/namespace`, else the import namespace. `port` comes from `ROSSOCTL_TOOL_<TOOL>_PORT`, else the import port.

  Timeout: `ROSSOCTL_TOOL_TIMEOUT`, default 60 s.

#### 4.2.3 Export contributor

The contributor adds the following only to skills that contain at least one tool with `endpoint.provider == "rossoctl"`:

- frontmatter `metadata.rossoctl-requires-tools: "reservation-tool,weather-tool"`;
- `rossoctl-tools.json` (`schema: skillberry.ai/rossoctl-tools/v1`; per tool `{tool, port, path, mcp_tools: [{name, script, schema_sha256, read_only}]}`);
- a short "Tools (rossoctl)" note under core's Tools section: tools resolve in the agent's namespace, and exit 3 means the tool is not deployed there.

#### 4.2.4 Import claimer

It claims `rossoctl-tools.json` and the scripts it lists. For each script it creates an `mcp` tool with an `endpoint` block rebuilt from the JSON and the spec embedded in the script; the generated runtime is discarded. This makes "export → edit in git → re-import" lossless, and lets the demo skill live in `rossoctl/examples`.

#### 4.2.5 Routes

| Route | Marker | Behavior |
|---|---|---|
| `GET /plugins/rossoctl-tools/tools?namespace=` | `@requires("tools","list")` | Pass-through of rossoctl's `GET /tools`, for the store UI's tool picker. |
| `POST /plugins/rossoctl-tools/import` | `@requires("tools","create")` | `{namespace, tools: [{name, gateway_prefix?}], skill?: {name, description, instructions?, tags?}, tags?}`. For each rossoctl tool: `GET /tools/{ns}/{name}` (must exist; the port comes from its Service), then `/connect`. Creates one `mcp` tool per MCP tool: `params` from `input_schema`, `extra.mcp_annotations` when available (§11, question 6), `input_schema_sha256`, tags `mcp` and `rossoctl-tool:<tool>`. Optionally creates the skill, with `instructions` as a `file:SKILL.md` snippet. |
| `POST /plugins/rossoctl-tools/check` | `@requires("tools","list")` | Schema-hash drift per tool, via `/connect`. No writes. |
| `POST /plugins/rossoctl-tools/resync` | `@requires("tools","create")` | A new version (same name, new uuid) per drifted tool, plus a new skill version. |
| `POST /plugins/rossoctl-tools/promote` | `@requires("skills","create")` | Stage 3 (§13.4). |
| `GET /plugins/rossoctl-tools/promote/{id}` | `@requires("skills","get")` | Stage 3 promotion status. |

#### 4.2.6 Computed field `_rossoctl_required_tools`

- Registered through §4.1.5.
- Value: the sorted, de-duplicated `endpoint.tool` over the skill's tools whose provider is `rossoctl`.
- Opt-in only: rossoctl's autosync names it explicitly in a CSV allowlist, `GET /skills/?fields=uuid,name,description,version,tags,_rossoctl_required_tools` (§5.2.1).
- A store without the plugin answers 400 for that field. rossoctl treats that as "no dependencies known" and falls back to its old behavior (§6.5).

### 4.3 Behavior without the plugin

| Situation | Without `rossoctl-tools` installed or active |
|---|---|
| Store start, all existing APIs, UI | Unchanged. Nothing rossoctl-related loads. |
| `GET /skills/` (any preset) | Unchanged response; no extra computation |
| Export of a skill with no MCP tools | Byte-identical to today's |
| Export of a skill with SSE `mcp` tools (`mcp_url` only) | Today's stub, unchanged |
| Export of a skill with rossoctl-backed tools (created earlier by the plugin, or copied from another store) | Succeeds. Proxies get only the `env MCP_PROXY_<TOOL>_URL` step and exit 3 naming it until it is set. No `rossoctl-tools.json`, no rossoctl frontmatter. |
| Execute or vMCP on such a tool | 422 naming the missing provider (§4.1.2) |
| Create or update such a tool | Accepted with the generic shape check; provider-specific validation is skipped |
| Import of a package that contains `rossoctl-tools.json` | Imported by today's rules: the JSON as a document snippet, the scripts as `code` tools. Logged at info: "rossoctl-tools.json not claimed: plugin not active". |
| `fields=_rossoctl_required_tools` | 400 (unknown field) |
| `/plugins/rossoctl-tools/*` | 404 (the loader guard) |
| `GET /skills/{id}/export-mcp-bundle` (stage 3, core) | Works; the bundle is a generic export (§13.3) |

### 4.4 Store work summary

| # | Item | Layer | Files | Size |
|---|---|---|---|---|
| S1 | `isError` handling | core | `modules/file_executor.py`, `CHANGELOG.md` | S |
| S2 | `endpoint` block, shared validator, provider registry and dispatch, 422 path | core | `schemas/tool_schema.py`, `services/tools_service.py`, `modules/tool_providers.py` (new), `modules/file_executor.py`, `modules/vmcp_server.py` | M |
| S3 | Proxy generator with generic resolution steps, Tools section, kill switch | core | `tools/anthropic/mcp_proxy.py` (new), `services/skills_service.py`, `tools/anthropic/exporter.py`, `tools/publish.py` | M |
| S4 | Export contributor and import claimer hooks; keep frontmatter `metadata` | core | `tools/anthropic/exporter.py`, `tools/anthropic/importer.py`, `plugins/base.py` | S |
| S5 | Computed opt-in skill fields hook | core | `services/field_selection.py`, `services/skills_service.py`, `fast_api/skills_api.py` | S |
| S6 | `rossoctl-tools` plugin: backend client and credential, provider, contributor, claimer, computed field, stage 1 routes | plugin | `plugins/skillberry-plugin-rossoctl-tools/` (new), `pyproject.toml` extra, `plugin_config.json` | M |
| S7 | Docs | both | `docs/config-env-vars.md` (core vars), plugin README (plugin vars) | S |

S1–S5 are the stage 1 core changes. Each is useful without rossoctl, none of them names it, and none adds an MCP transport. Stage 3 adds one more core item (P1, §13.7).

---

## 5. rossoctl changes

### 5.1 Feature flag `toolSkills`

This follows the `dreaming` and `externalSkills` pattern end to end.

| Layer | Change |
|---|---|
| `charts/rossoctl/values.yaml` (`featureFlags:`, :9-45) | `toolSkills: false`, commented "Skills from skillberry-store that call rossoctl tools through bundled proxy scripts. Requires skills, externalSkills and a store image ≥ <ver>." |
| `templates/ui.yaml` (:154-169) | `ROSSOCTL_FEATURE_FLAG_TOOL_SKILLS: "{{ .Values.featureFlags.toolSkills }}"` |
| `core/config.py` (:79-137) | `rossoctl_feature_flag_tool_skills: bool = False`, plus two settings: `rossoctl_tool_skills_enforce: bool = False` (reject agents whose required tools are missing; otherwise warn) and `rossoctl_tool_skills_auto_routes: bool = False` (§5.4). |
| `routers/config.py` (`FeatureFlagsResponse` :28, mapping :80-94) | `toolSkills: bool` |
| `hooks/useFeatureFlags.ts` | `toolSkills: data.toolSkills === true` |
| Startup check (`main.py`) | If `tool_skills` is on but `skills` or `external_skills` is off, log a warning and treat `tool_skills` as off, so effective flags are always consistent. `/config/features` reports the effective value. |

Local ConfigMap skills (§5.2.3) only need `skills`. The store path needs `externalSkills`.

### 5.2 Recording a skill's required tools

#### 5.2.1 Autosync (`services/skill_autosync.py`)

- A new constant, `SKILL_REQUIRED_TOOLS_ANNOTATION = "rossoctl.io/required-tools"`, holds a JSON list such as `["reservation-tool","weather-tool"]`.
- `_fetch_registry_skills` (:98-107) adds `?fields=uuid,name,description,version,tags,_rossoctl_required_tools` when the flag is on. These are the fields autosync reads; a CSV allowlist cannot contain a preset name. If the store answers 400 (no `rossoctl-tools` plugin, or an old store), autosync retries once without the field, logs a warning, and caches "unsupported" until the next config change.
- `_create_autosync_skill` (:151-198) and `_patch_skill` (:201-220) set it from the store's `_rossoctl_required_tools` field (§4.2.6), when the flag is on.
- `_skill_signature` (:52-67) includes `sorted(_rossoctl_required_tools)`, so a dependency change re-patches the ConfigMap.
- When the flag is off, the annotation is neither written nor removed. Turning the flag on backfills it at the next poll, because the signature changes.

#### 5.2.2 Manually registered external skills (`routers/skills.py`, `create_external_skill`)

- For `registryType == "skillberry"`, after the SSRF check, it does one `GET {registryUrl}/skills/{name}?fields=uuid,name,_rossoctl_required_tools` and copies the value into the annotation.
- A 400 for that field (store without the plugin) leaves the annotation unset. This is the compatibility path for old stores and stores without the plugin.

#### 5.2.3 Local ConfigMap skills

- A local skill may include a `rossoctl-tools.json` data key, using the same schema as §4.2.3.
- When the flag is on, `routers/skills.py` create and update parse it and set the same annotation.
- This lets hand-authored rossoctl skills that bundle their own proxy scripts (or copies of exported ones) use the same wiring.

### 5.3 Agent creation (`routers/agents_create.py`, and the Shipwright finalize path)

After external skill resolution (`agents_create.py:116-123`) and **before** `apply_agent_import_defaults` (:131), when `settings.rossoctl_feature_flag_tool_skills` is on:

```python
required = _collect_required_tools(kube, request.namespace, request.skills)   # new, agents_skills.py
#   -> {tool_name: [skill_cm_name, ...]} from the rossoctl.io/required-tools annotation
missing  = [t for t in required if not _tool_service_exists(kube, request.namespace, t)]
if missing and settings.rossoctl_tool_skills_enforce:
    raise HTTPException(422, detail={"missingTools": missing, "requiredBy": {...}})
warnings += [f"Skill(s) {required[t]} require tool '{t}', not found in {ns}" for t in missing]
request = _inject_tool_urls(request, kube, required.keys() - set(missing))
```

**`_tool_service_exists`** looks for Service `<tool>-mcp` (`TOOL_SERVICE_SUFFIX`) in the namespace. That is the same object `get_mcp_tool_url` relies on.

**`_inject_tool_urls`** appends `ROSSOCTL_TOOL_<TOOL>_URL = get_mcp_tool_url(tool, ns, kube)` (`agent_env_defaults.py:19-27`). This removes the port guess (§4.2.2, step 1), because rossoctl looks up the Service's real port.
- These go **before** user `envVars` in the precedence chain of `_build_env_vars` (`agents_manifests.py:82-138`), so a user override still wins.
- Also `ROSSOCTL_NAMESPACE = <ns>`, for agents whose ServiceAccount token is not mounted.
- When the MCP Gateway component is enabled and the tool has an `MCPServerRegistration`, rossoctl could inject `ROSSOCTL_MCP_GATEWAY_URL` instead. That is out of scope for this revision and listed in §11.

**Workload annotation.** `rossoctl.io/required-tools` is the union, in JSON, set next to `rossoctl.io/skills` in all four manifest builders (`agents_manifests.py:394, 595, 741, 849`). It gives `kubectl` visibility, and the UI and tool-delete checks use it.

**Response.** `CreateAgentResponse` (`agents_models.py:306`) gains `warnings: List[str] = []`, and the UI shows them.

**`mcpToolName` is untouched.** An agent can still have a native MCP tool. The skill's generated Tools section tells the model to prefer the skill's scripts (integration caveat: "an agent that also has the tool as a native MCP server sees it twice").

**The same block runs in `finalize_shipwright_build`**, which has its own `skills` and `outboundRoutes` fields (`agents_models.py:400, 414`).

### 5.4 AuthBridge outbound routes (optional, `rossoctl_tool_skills_auto_routes`)

rossoctl currently has **two** places for outbound token-exchange routes:

| Mechanism | Scope | Written by | Format |
|---|---|---|---|
| ConfigMap `authproxy-routes`, key `routes.yaml` | **Namespace-wide**, shared by every agent in the namespace | The backend, `_ensure_authproxy_routes` (`agents_authbridge.py:257-274`), from `CreateAgentRequest.outboundRoutes` (`agents_models.py:22-27`: `host`, `target_audience`, `token_scopes`) | YAML list |
| `AgentRuntime.spec.auth.outbound[]` | **Per agent** | Nobody in the backend yet. The backend's `_build_agentruntime_manifest` (`agents_manifests.py:250-314`) sets only `type`, `targetRef`, the bridge, mTLS and TLS modes, `pluginPreset`, `plugins` and `onError`. | `{destination: {host \| hostRegex}, audiences: [..]}` (operator `api/v1alpha1/agentruntime_types.go:214-255`, operator #510). The webhook renders it into a per-agent ConfigMap, `authbridge-routes-<cr>`, which **replaces** the namespace one for that pod (`internal/webhook/injector/pod_mutator.go:1313-1447`, #517). |

**Auto-routes use the per-agent field.** When AuthBridge is enabled for the agent and `rossoctl_tool_skills_auto_routes` is on, the backend adds one entry per present required tool to the agent's `AgentRuntime`:

```yaml
spec:
  auth:
    outbound:
      - destination: {host: weather-tool-mcp.team1.svc.cluster.local}
        audiences: ["<tool audience>"]
```

`<tool audience>` is read from the tool's `rossoctl.io/audience` annotation when set, else derived from the tool's SPIFFE ID (`spiffe://<td>/ns/<ns>/sa/<tool>`). The operator registers each workload with Keycloak under its SPIFFE ID (docs `security/authbridge.md`, "Client registration"). This is the per-agent shape the operator already supports, so:

- the namespace-wide `authproxy-routes` ConfigMap is not touched. Today it is **overwritten** on every agent create that passes `outboundRoutes`, so two agents in one namespace clobber each other; that is a separate existing bug, unaffected here.
- the user's own `outboundRoutes` must be carried into `spec.auth.outbound` too. The per-agent ConfigMap *replaces* the namespace one for that pod, so an agent with auto-routes would otherwise lose its namespace routes. The backend converts each `OutboundRoute` to `{destination: {host}, audiences: [target_audience]}` and merges by host, with the user's entry winning.

**Constraints**, from the operator's field documentation:
- `spec.auth.outbound` takes effect only when the namespace uses SPIFFE client auth (`authBridge.clientAuthType: federated-jwt`). In other modes the backend does not write it, and the UI says so.
- Only `audiences[0]` is used (`pod_mutator.go:1385-1394`).

**Default off.** With the default `passthrough` outbound policy (`values.yaml:347`), calls reach tools unchanged and work for the unauthenticated example tools. Routes matter only once tools validate inbound tokens (the inbound `jwt-validation` plugin on the tool, which tool pods get with `injectTools`).

**Visibility for policy plugins.** The proxy's traffic is plain MCP JSON-RPC over HTTP to `/mcp`. The opt-in AuthBridge plugins that act on tool calls therefore see proxy calls exactly as they see native MCP calls from the agent: `mcp-parser` (default path glob `/mcp`), then `ibac`, `sparc`, `opa` and `cpex` (cortex `docs/plugin-catalog.md`, `docs/mcp-parser-plugin.md`). Intent-based access and tool-call validation therefore apply to skill-mediated calls with no change.

### 5.5 UI

| Page | Change (gated on `features.toolSkills`) |
|---|---|
| `SkillDetailPage.tsx` | A "Required Tools" card after "Registry Information" (`:211`). Each tool shows present/ready in the current namespace, from the new endpoint below, and links to the tool's page. |
| `ImportAgentPage.tsx` ("Linked Skills", `:1061-1085`) | Chips per skill listing the required tools, with a warning icon on missing ones. Below the list: "This agent will get N tools through its skills." When auto-routes is on, a read-only preview of the routes that will be added, next to the outbound routes table (`:1190-1210`). Shows `warnings` from the create response. |
| `AgentDetailPage.tsx` | A "Tools via skills" list from the workload annotation, next to the agent-card skills (`:788-800`). |
| Tool delete dialog (`ToolDetailPage`/catalog) | "Used by skills X, Y in agents A, B", from a label scan of workloads' `rossoctl.io/required-tools`. A warning only. |
| `types/index.ts` (`Skill`, `:421-433`) | `requiredTools?: string[]` |

**New backend endpoint:** `GET /api/v1/skills/{namespace}/{name}/tool-requirements`.
- `ROLE_VIEWER`; returns 404 when the flag is off.
- Returns `[{tool, present, ready, url}]`, where `ready` comes from the tool Deployment's ready replicas.
- The `Skill` and `SkillDetail` models expose `requiredTools` parsed from the annotation.

### 5.6 Example agents (`rossoctl/examples`)

The feature needs an agent that **runs** skill scripts. Of the examples, only `claude_agent` and the sandbox agent can. `generic_agent` inlines skill files into the prompt but has no exec tool (`graph.py:109-200, 229-285`).

1. **`claude_agent`** (primary demo agent): Claude Code discovers skills under `~/.claude/skills/<name>/SKILL.md`, but rossoctl mounts them at `/app/skills/<cm>` and lists them in `SKILL_FOLDERS`.
   - Add a small startup step in `agent.py`: for each `SKILL_FOLDERS` entry that has a `SKILL.md`, symlink it to `$HOME/.claude/skills/<basename>`. `$HOME` is `/home/agent` and writable.
   - Progressive disclosure then works natively: only names and descriptions are in context until the skill is used.
   - The image needs python3 on `PATH` for the scripts. The image has uv-managed CPython under `/opt/uv/python`; add a `python3` symlink to `/usr/local/bin` in the Dockerfile.
2. **`generic_agent`** (secondary, works with Ollama): add an opt-in LangGraph tool, `run_skill_script(skill, script, args: list[str])`, enabled by `SKILL_SCRIPTS_ENABLED=true`. It:
   - resolves `script` strictly under `<skill folder>/scripts/` (realpath check, no `..`);
   - runs `python3 <script> *args` with no shell, a timeout, and an output cap;
   - returns stdout, stderr and the exit code.

   The skill text is already in the prompt, so the model knows the CLI.

Neither change touches rossoctl's backend.

### 5.7 rossoctl work summary

| # | Item | Files | Size |
|---|---|---|---|
| R1 | Flag plumbing (Helm → settings → `/config/features` → hook) | `values.yaml`, `templates/ui.yaml`, `core/config.py`, `routers/config.py`, `hooks/useFeatureFlags.ts`, `main.py` | S |
| R2 | Required-tools annotation from autosync, external and local skills (explicit `fields=…,_rossoctl_required_tools`, with a 400 fallback) | `services/skill_autosync.py`, `routers/skills.py`, `core/constants.py` | S |
| R3 | Agent-create validation, URL injection, workload annotation, warnings | `routers/agents_create.py`, `agents_finalize.py`, `agents_skills.py`, `agents_manifests.py`, `agents_models.py` | M |
| R4 | Optional auto-routes into `AgentRuntime.spec.auth.outbound`, carrying the user's `outboundRoutes` along | `routers/agents_manifests.py` (`_build_agentruntime_manifest`), `routers/agents_create.py` | S |
| R5 | `tool-requirements` endpoint and UI cards and chips | `routers/skills.py`, `SkillDetailPage.tsx`, `ImportAgentPage.tsx`, `AgentDetailPage.tsx`, `types/index.ts` | M |
| R6 | Helm: store plugin config, backend URL and service credential for the store, image tag | `templates/skillberry-store.yaml`, new `templates/skillberry-store-plugins-cm.yaml`, a second `api-oauth-secret` job instance, `values.yaml` | S |
| R10 | `/connect` returns MCP tool annotations (`readOnlyHint` etc.) | `routers/tools.py` (`MCPToolSchema`) | S |
| R7 | Kind installer `--with-tool-skills`; reservation-tool deploy script | `scripts/kind/setup-rossoctl.sh`, `.github/scripts/operator/73-deploy-reservation-tool.sh` (new) | S |
| R8 | Example agents | `rossoctl/examples`: `a2a/claude_agent`, `a2a/generic_agent` | S |
| R9 | Tests and docs | `rossoctl/backend/tests/` (next to `test_skill_autosync.py`, `test_agents_external_skills.py`), `rossoctl/ui-v2/e2e/`, a "Skills that call tools" section in `docs/concepts/experiments/skills.md`, a cross-link from `docs/workloads/deploy-a-tool.md` ("Connect an agent") | M |

The **required** rossoctl surface for an end-to-end feature is R1, R2, R3, R6 and R8. R3 is the only one with logic. The store uses rossoctl's existing `/tools` endpoints as they are; R10 is optional (without it, imported tools are marked as writes).

---

## 6. Deployment and integration

### 6.1 Topology

```
rossoctl-system:  rossoctl-backend, rossoctl-ui, skillberry-store (+ rossoctl-tools plugin), keycloak, phoenix…
team1:            weather-tool (+ -mcp Svc, AgentRuntime type=tool), reservation-tool (same), night-out agent
```

- The store reaches **only rossoctl's backend** (`rossoctl-backend.rossoctl-system:8000`), with a client-credentials token. The backend reaches the tools for import, checks and store-side execution.
- Agents reach tools in their own namespace: agent → AuthBridge egress → tool.
- No new Services, routes or CRDs are needed. A NetworkPolicy can deny the store pod any egress to tenant namespaces.

### 6.2 Helm values

```yaml
featureFlags:
  skills: true
  externalSkills: true
  toolSkills: true            # NEW; ineffective unless the two above are true

components:
  skillberryStore:
    enabled: true

skillberryStore:
  image:
    tag: "<store release with this feature>"   # today "0.2.0" (values.yaml:76)
  plugins:                    # NEW → ConfigMap skillberry-store-plugins + SKILLBERRY_PLUGIN_CONFIG
    disabled: [anthropic-skill-generator, creator, dedupe, doc_generator, evaluator,
               mcp-importer, security, simulate, skill-optimizer]
    # rossoctl-tools is enabled automatically when featureFlags.toolSkills is true,
    # and added to `disabled` otherwise; ask-runspace likewise follows featureFlags.dreaming.
  rossoctlTools:              # NEW → plugin env (ROSSOCTL_BACKEND_URL is set from the release automatically)
    allowedNamespaces: ""     # e.g. "team1,team2"
    credential:               # NEW → a second api-oauth-secret job instance (see below)
      clientId: skillberry-store-tools
      secretName: skillberry-store-rossoctl-credentials
      role: rossoctl-operator

toolSkills:                   # NEW → backend env
  enforce: false              # ROSSOCTL_TOOL_SKILLS_ENFORCE
  autoRoutes: false           # ROSSOCTL_TOOL_SKILLS_AUTO_ROUTES
```

**Template changes**
- **`templates/skillberry-store-plugins-cm.yaml`** (new) renders `{"disabled": [...], "owners": {}}` from `skillberryStore.plugins.disabled`, adjusted by the two flag rules in the comment.
- **`templates/skillberry-store.yaml`** mounts that file and sets `SKILLBERRY_PLUGIN_CONFIG`. Today the chart has no plugin key, so every installed plugin is enabled (missing file = all enabled, `plugins/config.py:29-70`). The new ConfigMap makes the store's plugin set follow rossoctl's flags. The same change fixes `dreaming`'s "requires the ask-runspace plugin" prerequisite, which today is only a comment.
- **Store credential.** A second instance of the existing `api-oauth-secret` job (`templates/api-oauth-secret-job.yaml`, `rossoctl/auth/api-oauth-secret/register_api_client.py`) registers the Keycloak client `skillberry-store-tools` with the service-account role `rossoctl-operator`, and writes `CLIENT_ID`, `CLIENT_SECRET` and `TOKEN_ENDPOINT` to `skillberry-store-rossoctl-credentials`. `templates/skillberry-store.yaml` maps them to `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT`. It is rendered when `toolSkills` is on and Keycloak is enabled; with `ui.auth.enabled=false` (rossoctl's `ENABLE_AUTH=false`) the credential is skipped and the backend accepts unauthenticated calls. The role is broad (any namespace); `allowedNamespaces` narrows what the plugin will target, and per-namespace roles arrive with §8.
- **Store image.** It must be built with the plugin (`PLUGIN_EXTRAS` containing `plugin-rossoctl-tools`, or `plugins-all`). The default `Dockerfile` builds core only. The published `ghcr.io/skillberry-ai/skillberry-store` image must be built with `plugins-all` (`make docker-build-full`), or at least with `plugin-rossoctl-tools`. The store's release checklist gains "rossoctl-tools in image".

### 6.3 Kind installer

`scripts/kind/setup-rossoctl.sh` gets:
- `--with-tool-skills`, which implies `--with-skills` and adds `--set featureFlags.toolSkills=true` to `ROSSOCTL_FLAGS` (:1282-1286).
- A `--with-tool-skills` entry in `docs/reference/install-options.md`, next to `--with-skills`.
- With `--with-examples`, the reservation tool is also deployed: `73-deploy-reservation-tool.sh`, a copy of `72-deploy-weather-tool.sh` with image `ghcr.io/rossoctl/examples/reservation_tool:latest`, Service `reservation-tool-mcp`, port 8000, and an `AgentRuntime` with `type: tool`.

The installer already sets `operator-chart.featureGates.injectTools=true` (:1290), so tool pods get AuthBridge. With the default passthrough policy, unauthenticated calls still reach them, just as rossoctl's own `/tools/{ns}/{name}/connect` does today with `headers={}` (`routers/tools.py:2462`).

```bash
SKILLBERRY_STORE_TAG=<ver> scripts/kind/setup-rossoctl.sh --with-all --with-tool-skills --with-examples
```

### 6.4 OpenShift

The same Helm values apply. Two differences:
- Tool images come from the internal registry (the `72-*` script's OpenShift branch).
- The store pod runs under the restricted SCC, with no change: the proxy runs in agent pods, and the plugin makes only outbound HTTP to the backend.

### 6.5 Compatibility matrix

| Store | rossoctl `toolSkills` | Result |
|---|---|---|
| old, or new without the plugin | off / on | `_rossoctl_required_tools` is rejected (400). Annotations stay unset; agents are created as today. No behavior change. |
| new with the plugin | off | Skills export runnable proxies. Agents that can run scripts use them through cluster-DNS resolution, with the port embedded at import. That fallback breaks if the tool is redeployed on another port, such as the 9090 console default; with the flag on, rossoctl injects the real port. No UI, validation or URL injection. |
| new with the plugin | on | Full feature. |

---

## 7. Cross-cutting concerns

### 7.1 Security

- **The proxy only acts as a client.** Policy stays with the tool and the agent's AuthBridge chain: `token-exchange`, plus the opt-in `opa`, `ibac` and `sparc` plugins, which see proxy calls through `mcp-parser` (§5.4), plus the tool's own authorization. A skill cannot reach a tool the pod's egress cannot reach.
- **The store holds an operator credential.** It lives in a Secret mounted only into the store pod, is never written to store objects or exports, and the token is cached in memory only.
- **The exported zip holds no secrets.** The core generator embeds only `mcp_tool_name`, the input schema, its hash and the resolution steps. It refuses `packaging_params` or `endpoint` keys named `headers`, `auth*`, `token*` or `*secret*`, which §8.2 reserves for store-side use.
- **Code generation** is injection-safe by construction (§4.1.3).
- **Write tools.** `read_only: false` is surfaced in SKILL.md and `rossoctl-tools.json`. The generated Tools section tells the model to ask for explicit user confirmation before calling a non-read-only tool. The demo skill reinforces this (§9.3). It is guidance, not enforcement. Enforcement belongs to AuthBridge (`ibac` judges a write against the user's request; `opa` can deny write tools by name through `input.mcp`) or to the tool.
- **Plugin SSRF.** The plugin's only outbound destination is the deployment-configured `ROSSOCTL_BACKEND_URL`; tool and namespace names are DNS-1123 labels used in URL paths, optionally bounded by `allowedNamespaces` (§4.2.1).
- **Store-side execution goes through rossoctl.** The store calls `/invoke` with its service credential, so rossoctl's authorization and logging apply, but in `disabled` store mode anyone who can call the store can execute a rossoctl tool through it with the store's operator-role identity. This is the "open API" problem from the integration doc, which already holds for every store tool today. §8 closes it. Until then, keep `allowedNamespaces` tight, and treat the store's credential as an operator credential.

### 7.2 Versions and drift

- **Autosync records the oldest version** (integration doc, Skill versions). `_rossoctl_required_tools` would be read from that version too, while agents fetch the head. **This feature should land after integration stage 1** (`heads=true`, `?version=`), or R2 should use `GET /skills/{name}` (the head) for `_rossoctl_required_tools`. Until stage 1, the fallback is an extra GET per changed skill.
- **Schema drift.** The live tool's schema can change after import:
  - `--check` and the plugin's `/check` detect it (`schema_sha256`);
  - `/resync` creates new versions;
  - at run time the proxy does not block on drift, because the server is authoritative. A call with bad arguments surfaces as the tool's own error (exit 1).
- **A tool missing at run time** gives exit 3, with the URL and resolution step, so the agent can report "reservation-tool is not deployed in team1" rather than hallucinating.

### 7.3 Observability

- The example tools emit OTEL spans (`weather_tool.py:63-90, 153-168`), and Phoenix shows `execute_tool` spans for proxy calls with no proxy change.
- **Trace propagation.** The proxy forwards `TRACEPARENT` / `TRACESTATE` from its environment as `traceparent`/`tracestate` headers when they are set. The `run_skill_script` tool (§5.6) sets them from the current span, so tool spans join the agent's trace.
- This matters for dreaming: trajectories then show which skill script hit which tool.

### 7.4 Testing

**Store** (`make test`, `make lint`). Core and plugin are tested separately:
- **Core-only run.** The `test` extra pulls in `plugins-all` (`pyproject.toml`), so CI adds a job that installs `skillberry-store[dev,pytest]` with **no** plugins and runs the core suite. It asserts:
  - every §4.3 row;
  - no rossoctl string in core: a test greps `src/skillberry_store/` (excluding tests) for `rossoctl` and `svc.cluster.local` and fails on a hit;
  - golden exports are byte-identical for skills without MCP tools.
- **Plugin tests** live in `plugins/skillberry-plugin-rossoctl-tools/tests/`, as for every other plugin.
- Unit:
  - validator matrix;
  - generator safety (§4.1.3);
  - argparse from schema (all JSON types);
  - SSE and JSON response parsing against recorded frames;
  - SKILL.md and frontmatter rendering;
  - `rossoctl-tools.json`;
  - `_rossoctl_required_tools` derivation, and the 400 when the plugin is inactive;
  - the provider registry ignores inactive plugins; the 422 for a tool whose provider is missing;
  - round-trip import.
- Integration (marker `integration`):
  - (a) **Generated proxies.** A FastMCP `streamable-http` server is started in-process as a *test fixture* only (the `mcp` package is already a test dependency; store runtime code does not use it). Export a skill with a test provider, unzip it, and run each proxy with `python3 -I` in a subprocess; assert stdout and exit codes 0, 1 and 3.
  - (b) **Plugin against a fake rossoctl backend.** A small FastAPI app implements `GET /tools/{ns}/{name}`, `/connect`, `/invoke` and the token endpoint with recorded responses. Run `/import`, `/check`, store-side Execute and vMCP through the plugin; assert the bearer is sent and refreshed on 401, and that no other host is contacted.
- Exporter golden tests: skills without rossoctl tools export byte-identical zips before and after the change.

**rossoctl**:
- Backend unit:
  - autosync annotation and signature;
  - `_collect_required_tools`;
  - enforce versus warn;
  - env precedence (a user `envVars` value overrides an injected URL);
  - auto-routes render `spec.auth.outbound` (merged with the user's `outboundRoutes`), and only under `federated-jwt`;
  - flag-off is a no-op.
- UI Playwright: chips and cards appear only with the flag on.
- Kind e2e: the §9 script without an LLM. Create the agent, exec into the pod, run `python3 /app/skills/night-out-planner/scripts/search_restaurants.py --city Boston`, and assert the JSON contains `Trattoria di Mare`.

---

## 8. Future: store ACL in `delegated` mode

This section assumes the extended ACL from the integration doc ([Future integration based on extended access control](../rossoctl-integration.md#future-integration-based-on-extended-access-control)):
- `delegated` **verifies** Keycloak JWTs and Kubernetes ServiceAccount tokens;
- tenants are `ns:<ns>` and `user:<sub>`, and realm roles are groups;
- `scope.namespaces` is enforced with intersection semantics, and untagged objects count as `default`.

The agent-side proxy path is **unchanged**: it never talks to the store. The changes are on the store-side paths and the metadata path.

### 8.1 Placement of imported tools and skills

- The plugin tags each tool it imports from namespace X with `namespace:X`, and gives the skill the same tag by default. Under scope enforcement:
  - only subjects scoped to X (or to tags the author adds) can see and export the skill;
  - adding `namespace:Y` to share it is a guarded tag write that requires a grant in Y (integration doc, design point 3).
- **Composite closure.** A skill shared to Y must not silently include tools bound to a namespace Y cannot use. The plugin's `/check` gains a placement check:
  - a warning when `skill.namespaces ⊄ ⋃ tool.namespaces`;
  - the store's export applies the closure rule the ACL design picks (design point 5).
- The `endpoint.namespace` the plugin records is only the namespace store-side execution targets. Agents still resolve their own namespace, so sharing to Y works if Y runs the same tools.

### 8.2 The plugin calls rossoctl as the caller

**Store side.** In delegated mode `POST /plugins/rossoctl-tools/import` arrives with the caller's verified token. The plugin-identity design ([plugin-identity.md](plugin-identity.md), P3) says store calls during the request run as the calling tenant, so the tools and skill are created as `user:<sub>` / `ns:<ns>`.

**rossoctl side.** The plugin already talks to rossoctl only through its backend (§4.2.1), so delegated mode changes *which token* it sends, not the path:
- **User-initiated calls** (`/import`, `/check`, `/resync`, `/promote`, Execute from the store UI) forward the caller's Keycloak bearer to rossoctl. rossoctl then authorizes the user, not the store: an operator in `team1` can import from `team1`, and rossoctl's audit names the user.
- **Autonomous calls** (an evaluator or dream run, plugin-identity P1) use the plugin's owner identity. That is the client-credentials token of §4.2.1, scoped down once rossoctl has per-namespace roles.
- The service credential remains only as the fallback for autonomous work; it no longer backs user requests.
- `/connect` does not return MCP annotations (`readOnlyHint`) today (`MCPToolSchema`, `routers/tools.py:328-333`); §11 question 6 covers the one-line backend addition.

### 8.3 Store-side execution and evaluation

- `POST /tools/{id}/execute` and vMCP calls on rossoctl-backed tools require `execute` on the tool, scoped to the tool's namespace. In the integration doc's mapping, `tool-runner` is bound with `scope.namespaces: [X, default]`.
- **The rossoctl call carries identity.** The provider's `call_tool` (§4.1.2) receives the request's `ExecutionContext` (tool_execution design, G2) and forwards the caller's token to `/invoke`. rossoctl's AuthBridge on the tool side then sees a call made by the backend on behalf of that user. A plugin-owned autonomous run, such as the evaluator or a dream, uses its owner's identity under P1.
- **Credentials never go into `packaging_params`.** The generator's refusal list (§7.1) already enforces this for exports.
- **Dreaming.** A dream run that evaluates a rossoctl-tool skill against live tools does so as the triggering user, with a short-lived token limited to `execute` in the agent's namespace (integration doc, Dreaming, stage 3). Tool calls the dream run makes are then attributable and bounded.

### 8.4 Metadata path

- Autosync reads `_rossoctl_required_tools` through `GET /skills/` **as the namespace**, with a projected ServiceAccount token per stage 3. Visibility filtering removes skills the namespace cannot see. That is correct: rossoctl should not record dependencies of skills it may not fetch.
- `_rossoctl_required_tools` is derived from tool manifests. The derivation must not leak the *names* of tools the subject cannot see. The derived field is computed with the PEP's view of the tools, and a hidden tool appears as `"<hidden>"`, so the dependency is visible without disclosure.
- The init-container fetch of the zip carries the pod's token (stage 3). No change for this feature.

### 8.5 rossoctl side under delegated

- No change to agent-create validation (§5.3). It uses Kubernetes objects, not the store.
- `create_external_skill`'s one GET (§5.2.2) carries the user's token, forwarded as for the UI catalog path.
- `tool-requirements` (§5.5) stays a rossoctl-only Kubernetes read.

### 8.6 Delta checklist

| Area | Change in `delegated` mode |
|---|---|
| Core | Opt-in computed fields and providers run under the request's subject (the existing PEP), so nothing plugin-specific is needed in core |
| Plugin import | Runs as the caller; forwards the caller's bearer to rossoctl; tags `namespace:<ns>` |
| Plugin `/check`, `/resync` | Same identity; placement-closure warning |
| Provider `call_tool` | Forwards the caller's bearer to `/invoke`; the service credential is used only for autonomous runs |
| `execute` verb | Scoped to the tool's namespace |
| `_rossoctl_required_tools` (plugin) | Computed under the subject's visibility; hidden tools masked |
| Exported proxy | None |
| rossoctl backend | Accepts user tokens forwarded by the store (no change: it already validates Keycloak JWTs); forward the user token on the external-skill GET; autosync uses its namespace token |

---

## 9. Demo: "night-out-planner"

### 9.1 What it shows

1. **One skill gives an agent two rossoctl tools.**
   - The demo agent has **no** `mcpToolName` and no `MCP_URLS`. Its only configuration is the skill.
   - The baseline needs more: the `weather_service` agent can be given one tool (`MCP_URL`) and cannot book, and `generic_agent` needs hand-written `MCP_URLS`.
2. **The skill says when and how to use the tools.** It encodes tool knowledge that a raw MCP listing does not carry:
   - the weather tool returns raw Open-Meteo `current_weather` JSON, with a numeric WMO `weathercode` and °F;
   - it reports **current** conditions only, not a forecast;
   - `place_reservation` is a write;
   - availability slots follow a fixed shape.
3. **The same skill works in another namespace** that runs the tools, with no change (late binding).
4. **rossoctl's gated UX:** required-tools chips, a missing-tool warning, injected URLs.
5. **Policy stays with the tool.** Phoenix traces show the tool spans, and the AuthBridge sidecar sits on the path.
6. *(Optional)* The store executes the same tools for evaluation, and dreaming can improve the instructions.

### 9.2 Tools used (native rossoctl examples, no API keys)

| rossoctl tool | Source | MCP tools used | External deps |
|---|---|---|---|
| `weather-tool` | `rossoctl/examples/mcp/weather_tool`, image `ghcr.io/rossoctl/examples/weather_tool:latest`, deployed by `--with-examples` (`72-deploy-weather-tool.sh`) | `get_weather(city: str)` → JSON `{temperature (°F), windspeed, winddirection, weathercode, is_day, time}`, or `"City X not found"` | Internet (Open-Meteo, no key) |
| `reservation-tool` | `rossoctl/examples/mcp/reservation_tool`, a UI preset in `ImportToolPage.tsx`; new `73-deploy-reservation-tool.sh` | `search_restaurants(city, cuisine?, date_time?, party_size?, price_tier?, distance_km?)`, `check_availability(restaurant_id, date_time, party_size)`, `place_reservation(restaurant_id, date_time, party_size, name, phone, email, notes?)` (write), `cancel_reservation(reservation_id, reason?)` (write, destructive), `list_reservations(user_id)` | None: deterministic `MockProvider`. Boston, New York, San Francisco and Austin restaurants. Lunch slots 11:30–14:00, dinner 17:00–21:00; about 70% available, deterministic per (restaurant, time, party). |

If the cluster has no internet access, the weather tool returns "temporarily unavailable". The skill handles that and continues (§9.3). The demo therefore still runs fully offline, with the reservation path intact.

### 9.3 The skill

The store objects:
- 6 `mcp` tools (`get_weather`, `search_restaurants`, `check_availability`, `place_reservation`, `cancel_reservation`, `list_reservations`), all with `endpoint.provider: rossoctl`;
- 1 instructions snippet, tagged `file:SKILL.md`;
- the skill `night-out-planner`, tagged `rossoctl-approved`.

The exported package:

```
night-out-planner/
  SKILL.md                      # frontmatter + instructions below + generated "Tools (rossoctl)" section
  rossoctl-tools.json           # weather-tool, reservation-tool
  scripts/get_weather.py
  scripts/search_restaurants.py
  scripts/check_availability.py
  scripts/place_reservation.py
  scripts/cancel_reservation.py
  scripts/list_reservations.py
```

The instructions snippet (authored; the frontmatter and the Tools section are generated):

```markdown
---
name: night-out-planner
description: Plan and book a restaurant evening in a city, taking current weather into account.
  Use when the user wants dinner/lunch suggestions, table availability, or to book or cancel
  a reservation.
metadata:
  rossoctl-requires-tools: "reservation-tool,weather-tool"
---

# Night-out planner

## Procedure
1. **Collect** city, date + time (convert to ISO 8601 local, e.g. `2026-10-22T19:00:00`),
   party size, cuisine and budget (price tier 1–4). Ask only for what is missing; city and
   party size are required.
2. **Weather** — run `get_weather.py --city "<city>"`.
   - The result is *current* conditions as JSON. It is **not a forecast**: if the booking is
     not for today, say so and do not predict the weather for that day.
   - Read `weathercode` (WMO): 0 clear · 1–3 cloudy · 45/48 fog · 51–57 drizzle · 61–67 rain ·
     71–77 snow · 80–82 showers · 95–99 thunderstorm. `temperature` is °F.
   - For tonight with code ≥ 51 or temperature < 45 °F: prefer restaurants with
     `distance_km` ≤ 2 and mention an umbrella or coat. If the tool is unavailable, say so in
     one line and continue — weather never blocks a booking.
3. **Search** — `search_restaurants.py --city … [--cuisine …] [--price_tier …] [--party_size …]`.
   Present at most 3 options: name, cuisine, price tier as `$`, rating, address.
4. **Availability** — `check_availability.py --restaurant_id … --date_time … --party_size …`
   for the user's pick. If the requested slot is taken, offer the nearest available slots
   within ±60 minutes, then the next option from step 3. Dinner slots run 17:00–21:00.
5. **Confirm before writing.** `place_reservation` and `cancel_reservation` change state.
   Show a one-line summary (restaurant, time, party, name) and wait for an explicit "yes".
   Collect name, phone and email first; never invent them.
6. **Book** — `place_reservation.py …`. Report the **confirmation code** and reservation id.
7. To review or cancel: `list_reservations.py --user_id <email or phone>`, then
   `cancel_reservation.py --reservation_id … --reason …` (confirm first).

## Rules
- Use these scripts, not other tools, for weather and reservations.
- Exit code 3 means a rossoctl tool is not reachable in this namespace: report which tool
  (from the error) and stop that step; do not fabricate results.
- Party size > 8: tell the user to call the restaurant (`phone` from the search result).
```

### 9.4 Demo script

This assumes Kind and `SKILLBERRY_STORE_TAG` set to a store build with this feature:

```bash
scripts/kind/setup-rossoctl.sh --with-all --with-tool-skills --with-examples   # weather + reservation in team1
```

Steps:

1. **Import the tools into the store.** In the store UI, the plugin action lists team1's tools (fetched from rossoctl's `GET /tools`); pick both. Or with curl:

   ```bash
   STORE=http://skillberry-store.localtest.me:8080
   curl -sX POST $STORE/plugins/rossoctl-tools/import -H 'content-type: application/json' -d '{
     "namespace": "team1",
     "tools": [{"name": "weather-tool"}, {"name": "reservation-tool"}],
     "skill": {"name": "night-out-planner",
               "description": "Plan and book a restaurant evening in a city, taking current weather into account.",
               "instructions": "<§9.3 snippet>", "tags": ["rossoctl-approved"]}}'
   ```

   Alternative: keep the skill in git (`rossoctl/examples/skills/night-out-planner/` with `rossoctl-tools.json`) and run `POST /skills/import-anthropic`, which the plugin's import claimer (§4.2.4) supports.

2. **Store-side check** (shows the store can evaluate against live tools): in the store UI, open tool `search_restaurants` → Execute `{"city":"Boston","cuisine":"Italian"}`. The result lists Trattoria di Mare and Bella Napoli. The call went store → rossoctl `/invoke` → tool; the backend log shows it, under the `skillberry-store-tools` client.

3. **rossoctl picks it up.** Within 30 s autosync creates the `night-out-planner` skill in team1. On **Skills → night-out-planner**, the "Required Tools" card shows `weather-tool ✓ ready`, `reservation-tool ✓ ready`.

4. **Create the agent.** In **Agents → Import Agent**, choose `claude_agent` (image `ghcr.io/rossoctl/examples/claude_agent`, env `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_BASE_URL`), namespace team1, and check `night-out-planner` under Linked Skills.
   - The chips read "weather-tool, reservation-tool". There is no MCP tool selection.
   - The same step from the CLI (rossoctl-cli has no `--skills` flag; `--additionalParameterJSON` merges keys into the create request):

     ```bash
     rossoctl agents import from-image --name night-out \
       --containerImage ghcr.io/rossoctl/examples/claude_agent:latest \
       --envVar ANTHROPIC_AUTH_TOKEN=$TOKEN --envVar ANTHROPIC_BASE_URL=$LITELLM \
       --additionalParameterJSON '{"skills": ["night-out-planner"]}'
     rossoctl agents wait night-out --timeout 5m
     ```

   - Then show the pod spec:

   ```bash
   kubectl -n team1 get deploy night-out -o jsonpath='{.spec.template.spec.containers[0].env}' | jq
   #  ROSSOCTL_TOOL_WEATHER_TOOL_URL=http://weather-tool-mcp.team1.svc.cluster.local:8000/mcp
   #  ROSSOCTL_TOOL_RESERVATION_TOOL_URL=…   SKILL_FOLDERS=/app/skills/night-out-planner
   kubectl -n team1 get deploy night-out -o jsonpath='{.metadata.annotations.rossoctl\.io/required-tools}'
   ```

5. **Chat.** "Plan dinner for 4 in Boston tonight at 7pm, Italian, mid-range." Expected trajectory:
   - `get_weather.py` → "it's 52 °F and drizzling (code 53); suggesting places close to the center";
   - `search_restaurants.py` → 2 options;
   - `check_availability.py` → if 19:00 is taken (the mock's slots are deterministic, so pick a restaurant and time that are taken during demo prep), the agent offers the nearest free slots;
   - the user picks one, and the agent asks for name, phone and email, then for confirmation;
   - `place_reservation.py` → confirmation code.

   Then "What reservations do I have?" → `list_reservations.py`.

6. **Observe.** In Phoenix, the agent trace contains the `execute_tool` spans from weather-tool and reservation-tool (§7.3). `kubectl -n team1 logs deploy/night-out -c authbridge` shows the egress passing through the sidecar.

7. **The negative and portability cases:**
   - Create team2 without the reservation tool, and try to create the same agent there. The UI warns "night-out-planner requires reservation-tool, not found in team2". With `toolSkills.enforce=true` the create is rejected (422).
   - Deploy the reservation tool in team2: the same skill, unchanged, now works there.

8. *(Optional: baseline.)* Ask the stock `weather_service` agent (single `MCP_URL`) the same question. It can report weather but cannot book. That is the single-tool limit the skill removes.

9. *(Optional: dreaming, with `featureFlags.dreaming=true`.)* After a few chats, trigger a dream on the agent. The store's ask-runspace run reads the trajectories and creates `night-out-planner` v2. For example, it adds "always pass `party_size` to `search_restaurants`" after seeing large-party misses. Autosync rolls v2 out to the next pod.

### 9.5 Demo assets to add

| Asset | Where |
|---|---|
| `night-out-planner/` skill folder (SKILL.md instructions, `rossoctl-tools.json`) | `rossoctl/examples/skills/night-out-planner/` |
| `73-deploy-reservation-tool.sh` | rossoctl `.github/scripts/operator/` |
| Demo doc | rossoctl `docs/concepts/experiments/skills.md`, a new section "Skills that call tools" (the docs have no demos directory since #2530) |
| Kind e2e (no LLM, step 4 plus `kubectl exec` of a proxy) | rossoctl `rossoctl/tests/e2e/common/` |
| Store tests: generated proxies against a FastMCP test fixture; plugin against a fake rossoctl backend | `src/skillberry_store/tests/e2e/test_mcp_proxy_export.py`, `plugins/skillberry-plugin-rossoctl-tools/tests/` |

---

## 10. Rollout plan

### Stage 1: skills from existing rossoctl tools

| Milestone | Contents | Exit criterion |
|---|---|---|
| **1.M1: Store core** | S1–S5, S7 (core part); core-only CI job | Generated proxies call a FastMCP test fixture with exit codes as specified; golden exports unchanged; core-only suite green with no plugins installed; no MCP runtime dependency added |
| **1.M2: `rossoctl-tools` plugin** | S6, S7 (plugin part) | Against a fake backend: import, check, Execute and vMCP go through `/connect` and `/invoke` with a refreshed bearer and contact no other host. Against Kind team1: the same with the real backend. The §4.3 behaviors hold with the plugin disabled. |
| **1.M3: rossoctl flag and wiring** | R1–R3, R6 | Flag off: no diff in created manifests. Flag on: env plus annotation; warn and enforce; the store credential is provisioned |
| **1.M4: UX and agents** | R5, R7, R8, R10 | Demo steps 3–7 pass by hand |
| **1.M5: Optional** | R4 (auto-routes); gateway resolution; trace propagation | Token-exchanged call to an inbound-validating tool |
| Before GA | Integration stage 1 (heads/version) | `_rossoctl_required_tools` matches the version agents run (§7.2) |

### Stage 3: promote store tools to native rossoctl tools

| Milestone | Contents | Exit criterion |
|---|---|---|
| **3.M1: Bundle contract** | P1 (spec, schema, conformance cases) | The store's own executor passes the conformance cases; the spec is reviewed by both projects |
| **3.M2: rossoctl runner and endpoint** | Q1–Q3 | The runner passes the same conformance cases; `from-store` deploys a bundle, reports Ready, and rolls back on failure; two replicas serve calls interchangeably |
| **3.M3: Store promotion** | P2 | `/promote` drives the flow end to end and creates the swapped skill version; a store without rossoctl is unaffected |
| **3.M4: Catalog and demo** | Q4, §13.8 | `walk_estimate` is called by the agent through AuthBridge, and visible in Phoenix as a tool span |

## 11. Open questions

### Stage 1

1. **Tool naming collisions.** Two rossoctl tools can both expose, say, `search`, and store tool names become script names. Proposal: the importer prefixes on collision only (`reservation-tool__search`). The alternative is always prefixing, which is uglier but stable.
2. **Audience source for auto-routes** (§5.4). The operator registers each tool's Keycloak client under its SPIFFE ID, so the default is derivable. Should a tool be able to override it with an annotation, and should rossoctl's tool creation record it?
3. **Gateway resolution.** Should rossoctl inject `ROSSOCTL_MCP_GATEWAY_URL` when a tool is registered with the MCP Gateway, so calls go through it rather than direct? That would trade a direct path for the gateway's future authn/authz. rossoctl's docs (`docs/concepts/experiments/mcp-gateway.md`) say the gateway address "will become the default value of `MCP_URL` after the gateway is stable". When that happens, step 2 of the plugin's resolution order (§4.2.2) becomes the normal path, and `gateway_prefix` must match the tool's `MCPServerRegistration.spec.prefix`.
4. **Operator surfacing.** Should the operator, behind `SkillDiscovery`, copy `rossoctl.io/required-tools` into `AgentRuntime.status.linkedTools` the way it copies `linkedSkills`? That is a cheap step towards integration stage 2's `spec.requires.tools`.
5. **`generic_agent` exec tool.** Is an in-pod script runner acceptable in the generic example, or should script-running stay limited to `claude_agent` and the sandbox agents?
6. **Tool annotations through `/connect`.** `MCPToolSchema` (`routers/tools.py:328-333`) has `name`, `description` and `input_schema` only. Adding `annotations` (R10) lets the store mark read-only tools; without it every imported tool is treated as a write in the generated Tools section.
7. **Store credential scope.** The store's client-credentials client holds `rossoctl-operator` in every namespace. Is that acceptable until delegated mode (§8), or should rossoctl add a narrower role (for example `tools:connect,invoke` only)?

### Stage 3

8. **Dependency installation.** Installing `dependencies` with uv at pod start needs package-index access from the tenant namespace and slows cold start. The alternative is a Shipwright build of a generated source tree on top of the runner, which gives an immutable image per promotion. Proposal: start-time install first, a `mode: runtime | build` option later.
9. **Approval gate.** Which store state qualifies a tool for promotion: `rossoctl-approved`, a passing SAST outcome, or both? Should rossoctl enforce it in `from-store`, or only display it?
10. **One workload per skill or per tool.** Per skill (the proposal) gives fewer workloads and one AuthBridge identity. Per tool allows finer policy and independent scaling.
11. **Lifecycle.** When the store gets a newer version of a promoted code tool, should rossoctl roll the existing workload in place (the proposal), or deploy a new revision side by side so skill versions can pin one?
12. **Bundle spec ownership.** The spec lives in the store repo and the runner in rossoctl. Is a shared conformance test suite enough to keep them in step, or should the spec move to a neutral repository?

---

## 12. Stage 2 (removed): streamable-HTTP vMCP

Revision 3 had a stage 2 that let the store's vMCP servers serve streamable HTTP. It is **not part of this design any more**:
- Stage 1 does not need it: the store reaches rossoctl through its backend's REST API (§4.0), and agents reach tools directly.
- Stage 3 does not need it: rossoctl owns the server that runs promoted tools (§13.2).

A streamable vMCP transport may still be a worthwhile standalone store improvement, for clients such as Claude Code or the MCP Inspector, since SSE is deprecated in the MCP spec. If pursued, it gets its own design document, and nothing in this feature depends on it. The facts gathered for it remain valid: `VirtualMcpServer._start_server` serves `sse_app()` on `127.0.0.1` (`modules/vmcp_server.py:732-797`); the installed SDK provides `FastMCP.streamable_http_app()` with `streamable_http_path`, `stateless_http` and `json_response`; and the vMCP port carries no authentication, so a non-loopback bind would need a guard.

---

## 13. Stage 3: skills whose tools become native rossoctl tools

### 13.1 Goal

A user writes **code tools** in the store (Python, as today) and composes a skill from them, possibly together with existing rossoctl tools (stage 1). The user then **promotes** the skill's code tools. rossoctl deploys them as a **native rossoctl tool**: a workload in the namespace with an `-mcp` Service and an `AgentRuntime` of `type: tool`. It is listed in rossoctl's **Tools**, reached through AuthBridge, subject to the AuthBridge policy plugins, shared by every agent in the namespace, and needs no dependencies in the agent's image. The store then replaces the promoted code tools in the skill with proxies of the new rossoctl tool, exactly as stage 1 imports any rossoctl tool.

Without promotion, a code tool still exports as `scripts/<tool>.py` and runs inside the agent's pod. That is today's behavior and stays the default. Promotion is worth it when a tool should be shared by several agents, go through AuthBridge policy, hold credentials or dependencies that should not live in agent images, or scale and be audited on its own.

### 13.2 Ownership: rossoctl runs the tools, the store ships the source

The same split as stage 1 (§0, "Who owns what"):

| | skillberry-store | rossoctl |
|---|---|---|
| Tool source, schemas, versions | owns them; exports them as a **bundle** | stores the bundle it is given |
| Server that runs the code | — | a generic **bundle-runner** image, owned and released by rossoctl |
| Workload, Service, `AgentRuntime`, AuthBridge, catalog | — | creates and manages them |
| The skill | swaps promoted code tools for proxies | — |

rossoctl already does this for simulated tools: one generic image, `simulation_harness_image` (`core/config.py:90-91`), deployed as a StatefulSet, a Service and an adopting `AgentRuntime` (`routers/simulation.py`, `services/simulation_manifests.py`), behind `POST /simulation/tools` returning **202** and a status endpoint. A bundle runner fits the same mould, so promotion needs **no per-tool image build**.

```
 skillberry-store                                rossoctl-backend                         team1
 ┌───────────────────────────┐  (1) POST /tools/from-store   ┌──────────────────┐ (2) ConfigMap(bundle)  ┌──────────────────────┐
 │ rossoctl-tools plugin     │──── {ns, name, bundle} ──────▶│ validate bundle  │──── Deployment ───────▶│ <name> pod           │
 │  /promote                 │◀─── 202 {status url} ─────────│                  │     (bundle-runner)    │  bundle-runner       │
 │                           │  (3) poll GET /tools/{ns}/{n} │                  │     <name>-mcp Svc     │  streamable /mcp     │
 │                           │◀─── Ready ────────────────────│                  │     AgentRuntime tool  │  AuthBridge sidecar  │
 │  (4) /import (stage 1)    │──── /connect ────────────────▶│ ──── MCP ────────┼───────────────────────▶└──────────────────────┘
 │  (5) new skill version:   │                               └──────────────────┘
 │      code tools → proxies │
 └───────────────────────────┘
```

The store pushes the bundle in the request body, so rossoctl needs no store URL, no fetch and no SSRF check for this flow.

### 13.3 Store core: `export-mcp-bundle` (no rossoctl knowledge)

**Standalone value.** A portable, pinned export of a skill's code tools that any runtime can execute. Core under rule §4.0.

`GET /skills/{id}/export-mcp-bundle`, `@requires("skills","get")` like `export-anthropic`. A deterministic JSON document:

```jsonc
{
  "schema": "skillberry.ai/mcp-bundle/v1",
  "skill":  {"uuid": "…", "name": "night-out-planner", "version": "…"},
  "tools": [{
    "uuid": "…", "name": "walk_estimate", "description": "…",
    "params": {…},            // JSON Schema of the inputs (ToolParamsSchema)
    "returns": {…},
    "programming_language": "python",
    "dependencies": ["…"],    // declared dependencies (ToolSchema.dependencies)
    "module_name": "walk_estimate.py",
    "module": "<source text>"
  }],
  "skipped": [{"uuid": "…", "name": "get_weather", "reason": "packaging_format mcp"}],
  "digest": "sha256:…"        // over the canonical JSON without this field
}
```

- **Code tools only.** `mcp` tools are listed in `skipped[]`; they already run elsewhere.
- **uuid-pinned.** Later edits in the store do not change an exported bundle.
- **Size.** The store refuses bundles over 900 KiB (413), the practical limit for a ConfigMap.

**The bundle is the contract between the projects**, so its spec is versioned and lives in the store repo (`docs/specs/mcp-bundle-v1.md`), with a JSON Schema. The spec fixes, beyond the fields above, the **execution semantics** the runner must reproduce so a tool behaves the same in the store and in rossoctl:
- **Invocation:** the module is loaded, and the function named `name` is called with keyword arguments taken from the MCP `arguments` object, validated against `params`.
- **Result:** the return value is JSON-serialized into one text content item; a raised exception becomes `isError: true` with the exception message.
- **Dependencies:** installed from `dependencies` into an isolated environment before the first call.
- **Isolation:** each call runs in a subprocess with a timeout; no state is shared between calls.

These match the store's own execution contract (`tool_execution.md`: subprocess default, data-passed parameters, typed errors), so the store can validate a bundle by running its own executor against it, and the runner can be tested against the same conformance cases (§13.7).

### 13.4 Store plugin `rossoctl-tools`: `/promote`

`POST /plugins/rossoctl-tools/promote`, `@requires("skills","create")`:
- Body: `{skill, namespace, name, tools?: [uuid], replicas?, authBridgeEnabled?}`.
- Steps:
  1. Build the bundle (`export-mcp-bundle`), restricted to `tools` when given.
  2. `POST {ROSSOCTL_BACKEND_URL}/api/v1/tools/from-store` with the bundle and options (§13.5). Expect 202.
  3. Poll `GET /api/v1/tools/{ns}/{name}` every 5 s until its `readyStatus` field is `Ready` (success) or `Failed` (abort), with a timeout (`ROSSOCTL_PROMOTE_TIMEOUT`, default 600 s, because dependency installation can be slow). Other values (`Progressing`, `Not Ready`, and a 404 while the workload is being created) mean keep polling. The values come from `_get_workload_status` (`routers/tools.py:431-475`).
  4. Run `/import` (§4.2.5) against the new tool. It yields one `mcp` tool with `endpoint.provider: rossoctl` per promoted code tool, with `endpoint.promoted_from` and `endpoint.bundle_digest` set.
  5. Create a **new skill version** (same name, new uuid). In it, each promoted code tool is replaced by its proxy; every other tool and snippet is unchanged.
- Returns `{promotion_id}` immediately (202); `GET /plugins/rossoctl-tools/promote/{id}` reports the step and the result.
- The original code tools stay in the store as the source of truth. Editing one and promoting again produces a new bundle, a new tool revision (§13.5), and a new skill version.

### 13.5 rossoctl: bundle runner and `from-store` (flag `toolSkillsPublish`)

**Flag.** `featureFlags.toolSkillsPublish: false` in `values.yaml`, rendered to `ROSSOCTL_FEATURE_FLAG_TOOL_SKILLS_PUBLISH`. It requires `toolSkills` (§5.1) and is reported by `/config/features`.

**The bundle-runner image** (`ghcr.io/rossoctl/bundle-runner`, a new rossoctl component, setting `bundle_runner_image` next to `simulation_harness_image`):
- reads `/bundle/bundle.json`, checks `schema` and `digest`, and refuses to start on a mismatch;
- installs each tool's `dependencies` with uv into a venv on an `emptyDir`, then reports ready (`/readyz`);
- serves the tools as **streamable-HTTP MCP at `/mcp`**, port 8000, in **stateless** mode, so any replica can answer any request;
- runs each call in a subprocess with a timeout, per the bundle spec (§13.3);
- runs as non-root with a read-only root filesystem; only `/tmp` and the venv are writable.

It is a FastMCP app of a few hundred lines. Like the simulation harness, it is rossoctl's to build, test and release.

**Endpoint.** `POST /api/v1/tools/from-store`, `ROLE_OPERATOR`, **202**:
- Body: `{namespace, name, bundle, replicas?, authBridgeEnabled?, outboundRoutes?, envVars?}`, reusing `CreateToolRequest` fields (`routers/tools.py:188-262`).
- Validates the bundle against the v1 schema and its digest (422 on failure). Then it creates:
  - ConfigMap `<name>-bundle` with `bundle.json`;
  - a Deployment with the bundle-runner image, the bundle mounted read-only, `emptyDir`s for the venv and `/tmp`, readiness `/readyz`, `replicas` (default 1), and annotations `rossoctl.io/source=skillberry-store`, `rossoctl.io/registry-skill`, `rossoctl.io/bundle-digest`;
  - the ServiceAccount, the Service `<name>-mcp` on port 8000, and an `AgentRuntime` with `type: tool` targeting the workload.

  This is the same sequence as image-deploy tool creation (`create_tool`, `routers/tools.py:1612+`) and the simulated-tool router. Failures roll back the created resources.
- Status: the existing `GET /tools/{ns}/{name}`, field `readyStatus` (`Ready`, `Progressing`, `Failed`, `Not Ready`).

**Revisions.** Promoting the same name again with a different digest patches the ConfigMap and the Deployment (rolling update). Agents keep working: the stateless runner and the Service hide the rollout, and the skill version that references the tool is unchanged. Side-by-side revisions are open question 11.

**Availability.** rossoctl creates tools with `replicas: 1` today (`routers/tools.py:1318, 1480`) and ships no autoscaler or disruption budget for them. `replicas` is exposed on `from-store`, and stateless serving makes more than one replica safe. An HPA or PDB is a rossoctl-wide tool concern, not specific to this feature.

**UI.**
- Tool catalog: tools with `rossoctl.io/source=skillberry-store` show a "From skill store" badge with the skill name and bundle digest.
- Promotion starts from the store's UI (the plugin's action), because composition happens there. rossoctl's UI needs no promotion form.

**Identity and policy.**
- The tool pod gets AuthBridge like any tool (`injectTools`). Inbound `jwt-validation` applies when enabled.
- Agents reach it through the stage 1 proxy and their own AuthBridge, so auto-routes (§5.4) cover it.
- The pod's network egress is whatever the namespace allows. Tools that call external services need the same NetworkPolicy and egress decisions as any rossoctl tool. Dependency installation needs a package index (open question 8).

### 13.6 Delegated mode (§8) for stage 3

- `/promote` forwards the calling user's token to `from-store`, so rossoctl authorizes the user (operator in that namespace), and its audit names them.
- The bundle is built under the caller's store identity, so a user can promote only tools they can see.
- The new skill version is created as the caller (plugin identity P3).

### 13.7 Work items

| # | Item | Layer | Files | Size |
|---|---|---|---|---|
| P1 | `export-mcp-bundle`, bundle spec v1 and JSON Schema, conformance cases | store core | `services/skills_service.py`, `fast_api/skills_api.py`, `docs/specs/mcp-bundle-v1.md` (new) | S |
| P2 | `/promote` and status | store plugin | `plugins/skillberry-plugin-rossoctl-tools/` | S |
| Q1 | Flag and settings (`toolSkillsPublish`, `bundle_runner_image`) | rossoctl | `values.yaml`, `templates/ui.yaml`, `core/config.py`, `routers/config.py`, `useFeatureFlags.ts` | S |
| Q2 | Bundle-runner image: FastMCP, stateless, subprocess per call, uv install, conformance tests | rossoctl | new component, e.g. `rossoctl/bundle-runner/` | M |
| Q3 | `POST /tools/from-store` with rollback | rossoctl | `routers/tools.py` or a new `routers/tools_from_store.py`; manifest builders in `services/` | M |
| Q4 | Catalog badge, tests, docs | rossoctl | `ToolCatalogPage.tsx`, `ToolDetailPage.tsx`, `backend/tests/`, `docs/workloads/deploy-a-tool.md` ("From the skill store") | S |

The store adds one core item (P1) and one plugin route (P2), and **no server code**.

### 13.8 Demo extension

Add a code tool to `night-out-planner` in the store:

```python
def walk_estimate(distance_km: float, weathercode: int, temperature_f: float) -> dict:
    """Estimate walking minutes and a go/no-go for walking to a restaurant."""
```

It uses the standard library only, so dependency installation is trivial. Then:
1. Add the tool to the skill, and tag it `rossoctl-approved`.
2. In the store UI: **night-out-planner → Promote to rossoctl**, namespace team1, name `night-out-utils`.
3. rossoctl's **Tools** now lists `night-out-utils`, with a "From skill store" badge. `rossoctl tools get night-out-utils` shows it Ready.
4. The skill's new version replaces the code of `walk_estimate` with a proxy, and its `rossoctl-tools.json` lists three tools: `weather-tool`, `reservation-tool`, `night-out-utils`. Autosync rolls it out; the Required Tools card shows all three.
5. Chat: "Is it walkable from the hotel to Trattoria di Mare tonight?" The agent calls `walk_estimate` through the proxy. Phoenix shows the tool span from the `night-out-utils` pod, and its AuthBridge sidecar log shows the inbound call.

---

## 14. Implementation guide

This section is for an implementer starting from this document alone. It fixes the decisions earlier sections leave implicit, gives exact interfaces, and orders the work. Where §1–§13 and this section differ, **this section wins**.

### 14.1 Settled decisions (do not reopen while implementing)

| # | Decision | Why |
|---|---|---|
| D1 | rossoctl hosts every rossoctl tool. The store only holds proxies, and talks to rossoctl only through its backend REST API (`/api/v1`). | §4.0. Composition exists only in the store. |
| D2 | The store gets **no** MCP transport work: no streamable client, no `mcp` version bump, no vMCP change. | §4.0, §12. |
| D3 | Every rossoctl-aware line of store code is in the `rossoctl-tools` plugin. Core never contains the string `rossoctl` or `svc.cluster.local` outside tests. | User requirement; enforced by a test (§14.6). |
| D4 | Stage 2 is dropped. Do not implement anything from §12. | §12. |
| D5 | The store ACL is assumed `disabled`. Write the plugin so `delegated` (§8) only changes which token is sent; do not implement §8. | Task scope. |
| D6 | rossoctl's skill dependency field is the opt-in computed field `_rossoctl_required_tools`, never a preset field. | §4.1.5, §4.2.6. |
| D7 | Stage 3 is not started until stage 1 is merged in both repos. | §0. |
| D8 | Open questions (§11) take the **proposal** stated in each; where none is stated: Q1 prefix on collision only; Q6 implement R10; Q7 use `rossoctl-operator`; Q10 one workload per promotion (a skill's selected tools); Q11 roll in place; Q12 spec in the store repo plus shared conformance cases. | Unblocks implementation. |

### 14.2 Repositories, branches and working rules

| Repo | Local path | Base branch | Notes |
|---|---|---|---|
| skillberry-store | `/home/erezh/work/skillberry/isolated/skillberry-store` | `main` (this document is on `sbs_opt`) | Branch `feat/rossoctl-tools-stage1` from `main`, and cherry-pick this document's commits. |
| rossoctl | `/home/erezh/work/rossoctl/rossoctl` | `origin/main` | The local checkout is **stale** (`c027bb8e`, before the router split #2388) and has an uncommitted change in `charts/rossoctl/values.yaml`. Do not work on it as-is: `git stash`, `git fetch`, then branch `feat/tool-skills` from `origin/main` (verified at `1cac9f3f`). |
| rossoctl/examples | not cloned locally | `main` | Clone `https://github.com/rossoctl/examples` for R8 and the demo skill folder. |

Store working rules (from the maintainer's standing instructions):
- Run `source .venv/bin/activate` before **every** `make`, `python` or `pytest` command, including one-off probes.
- Commit after each significant change, not in one batch. End commit messages with the attribution line the session requests.
- Finish with `make test`, then `make lint`, and apply what lint reports. `make test` runs the whole suite including e2e (about 10 minutes); run it in the background.
- `make lint` is `black --check` over `src/skillberry_store/{modules,tools,fast_api,utils}` only. Format edited files under those paths with `black`. Do not reformat `services/admin_service.py` (pre-existing drift).

### 14.3 Store core: exact interfaces (stage 1)

**C1. `isError` fix** — `src/skillberry_store/modules/file_executor.py`, inner `execute_mcp_tool` in `execute_python_file_in_mcp_server` (≈ lines 382-545).
- Today: returns `_return_value.content[0].text`, and the caller wraps it as `{"return value": "<text>"}` (`:545`).
- Change: if `_return_value.isError` is true, return `{"error": "<joined text>"}`. Otherwise join all `TextContent.text` items with `"\n"`; when `structuredContent` is present, return `{"return value": "<joined text>", "structured": <structuredContent>}`.
- `ToolsService.execute` (`services/tools_service.py:238-243`) already maps `{"error": …}` to `RuntimeError` → HTTP 500 at `POST /tools/{id}/execute`. Keep that.
- Tests: extend `src/skillberry_store/tests/modules/test_file_executor.py` with a mocked `ClientSession` returning `isError=True`, multi-item content, and `structuredContent`.

**C2. Provider hook** — new `src/skillberry_store/modules/tool_providers.py`:

```python
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

@dataclass
class CallResult:
    text: str                          # joined text content
    is_error: bool = False
    structured: Optional[Any] = None

@dataclass
class ProviderContext:
    env_id: Optional[str] = None       # from the Skillberry-Context header, as today
    bearer: Optional[str] = None       # caller's raw bearer, when present (delegated mode, §8)

class ToolProvider(Protocol):
    name: str                                            # matches endpoint.provider
    def validate(self, endpoint: Dict[str, Any]) -> None: ...                       # raise ValueError
    async def list_tools(self, endpoint: Dict[str, Any], ctx: ProviderContext) -> List[Dict[str, Any]]: ...
    async def call_tool(self, packaging_params: Dict[str, Any], arguments: Dict[str, Any],
                        ctx: ProviderContext) -> CallResult: ...
    def proxy_resolution(self, packaging_params: Dict[str, Any]) -> List[Dict[str, Any]]: ...

def register_provider(provider: ToolProvider, owner_slug: str) -> None: ...
def get_provider(name: str) -> Optional[ToolProvider]: ...   # None if unknown or owner inactive
def set_active_resolver(resolver: Optional[Callable[[str], bool]]) -> None: ...
```

- Model it on `plugins/events.py`: module-level registry, `_owner_by_name`, and an injected `set_active_resolver`. In `PluginLoader.__init__` (`plugins/loader.py`, next to `plugin_events.set_enabled_resolver`, ≈ line 46) call `tool_providers.set_active_resolver(self.is_active)`.
- Plugins register at construction or in `set_store_api`; the loader passes the slug. Simplest: add `PluginBase.register_tool_provider(provider)` that records the provider on the plugin, and have the loader call `tool_providers.register_provider(p, slug)` for each after loading (`loader.py`, ≈ line 100, after `set_store_api`).
- **Dispatch.** Add one function used by every execution path:

  ```python
  async def execute_via_provider(tool: dict, parameters: dict, ctx: ProviderContext) -> dict:
      """Returns the same dict shape FileExecutor returns: {"return value": …} or {"error": …}."""
  ```

  Call it before constructing a `FileExecutor` when `tool["packaging_format"] == "mcp"` and `"endpoint"` is in `packaging_params`, in:
  - `ToolsService.execute` (`services/tools_service.py:200-215`);
  - `_HandlerToolSource.execute` (`modules/vmcp_server.py:131-170`).

  Unknown or inactive provider: raise `ToolProviderUnavailable(provider, tool_name)` (new, in `services/exceptions.py`). Map it to **422** in `fast_api/tools_api.py` `execute_tool` (add an `except` before the `RuntimeError` branch), and to an MCP error result in vMCP.
- **Validator.** In `schemas/tool_schema.py`, replace the body of `validate_packaging_params` (`:116-144`) with a call to a new module-level `validate_mcp_packaging(packaging_format, packaging_params)` implementing §4.1.2. Call the same function from `ToolsService.create` (`:258+`, before writing) and `update`. When `get_provider(endpoint["provider"])` is not None, also call its `validate`.
- **Do not** route `get_mcp_tools` (`fast_api/server_utils.py:125`) through providers: nothing in the store calls it for provider-backed tools.

**C3. Proxy generator** — new `src/skillberry_store/tools/anthropic/mcp_proxy.py`:

```python
MCP_PROXY_GENERATOR_VERSION = "1"
def render_mcp_proxy_cli(tool: dict, steps: list[dict]) -> str: ...
def default_steps(tool: dict) -> list[dict]:   # [{"kind": "env", "var": f"MCP_PROXY_{TOOL}_URL"}]
def render_tools_section(tools: list[dict]) -> str: ...   # SKILL.md "## Tools" markdown
```

- `<TOOL>` in env names: `mcp_tool_name` upper-cased, every non-alphanumeric character replaced by `_`.
- The script is the fixed runtime text (a module constant, `_RUNTIME`) plus one line `_SPEC = json.loads(r'''<json>''')`. Spec keys: `name` (`mcp_tool_name`), `description`, `input_schema` (= the tool's `params`), `input_schema_sha256`, `read_only`, `steps`.
- Step objects: `{"kind": "env", "var": "X"}`, `{"kind": "url", "url": "…"}`, `{"kind": "template", "template": "…", "vars": {"name": {"env": "E", "file": "/p", "default": "d"}}}`. A `template` step is skipped (falls through) if any var resolves to empty. If no step yields a URL: exit 3 with the list of tried steps.
- Gateway-style steps may also carry `"tool_name": "<name>"`, which overrides `_SPEC["name"]` for the `tools/call` when that step wins (used for `gateway_prefix`).
- argparse: one `--<prop>` per top-level property of `input_schema.properties`; `string`→`str`, `integer`→`int`, `number`→`float`, `boolean`→`--<prop>/--no-<prop>` (`argparse.BooleanOptionalAction`), anything else → JSON literal parsed with `json.loads`. Plus `--json`, positional `-` for stdin JSON, `--describe`, `--check`, `--raw`, `-v`.
- `--check`: call `tools/list`, find the tool, compute `sha256` of `json.dumps(inputSchema, sort_keys=True, separators=(",", ":"))`, compare to `input_schema_sha256`; exit 4 on mismatch. The same canonicalization is used when the plugin computes the hash at import.
- Wiring: in `SkillsService.gather_export_inputs` (`services/skills_service.py:732-779`), for each tool with `packaging_format == "mcp"` and an `endpoint` block, unless env `SBS_MCP_PROXY_EXPORT` is `false`: `steps = provider.proxy_resolution(pp) if provider else default_steps(tool)`, then `tool_modules[tool["name"]] = render_mcp_proxy_cli(tool, steps)`. Tools without `endpoint` are untouched.
- `generate_skill_md` / `_build_file_structure` (`tools/anthropic/exporter.py:92-143, 320-377`): when at least one proxy was rendered, append `render_tools_section(...)` to SKILL.md, honoring `skill["extra"].get("tools_section", "append")`.
- `tools/publish.py:448-473`: include `MCP_PROXY_GENERATOR_VERSION` in the cache key.

**C4. Contribution hooks** — `src/skillberry_store/tools/anthropic/contributions.py` (new), registry pattern as C2:

```python
@dataclass
class ExportContribution:
    files: Dict[str, str] = field(default_factory=dict)        # relative path -> text
    metadata: Dict[str, str] = field(default_factory=dict)     # frontmatter metadata entries
    skill_md_section: str = ""

class ExportContributor(Protocol):
    def contribute(self, skill: dict, tools: list[dict], snippets: list[dict]) -> Optional[ExportContribution]: ...

class ImportClaimer(Protocol):
    def claim(self, files: Dict[str, str]) -> Optional["ImportClaim"]: ...
    # ImportClaim: claimed_paths: set[str]; tools: list[tuple[dict, str, str]]  (data, module_content, module_filename)
```

- Exporter: call active contributors inside `_build_file_structure` after core content is built; raise `ValueError` on a path or metadata-key collision (→ 500 with the plugin slug in the message).
- Importer: in `import_from_anthropic_skill` (`tools/anthropic/importer.py:536`), before classification, offer the file map to active claimers; remove claimed paths; create the returned tools via the service; continue with the rest.
- `parse_skill_metadata` (`importer.py:74-125`): keep the frontmatter `metadata` mapping in the returned dict, and store it on the imported skill's `extra["frontmatter_metadata"]`.

**C5. Computed fields** — `services/field_selection.py`:
- Add `register_computed_field(object_type, name, compute, owner_slug)`; names must start with `_`. Registration adds `name` to `SKILL_FIELD_TAGS` with an **empty** tag set and to `OPT_IN_ONLY_FIELDS["skill"]` (`:227-229`), exactly like `_npx_install` (`:164`).
- In the skills list and get paths (`fast_api/skills_api.py`, around `attach_npx_fields` calls at `:196, :266`), after the service returns items, for each requested computed field whose owner is active, set `item[name] = compute(item, tools_of(item))`. If the owner is inactive, `parse_fields_spec` must reject the name with 400, the same as an unknown field. Implement this by having `parse_fields_spec` consult the active resolver for registered computed fields.

### 14.4 Plugin `rossoctl-tools`: exact interfaces (stage 1)

**Layout** (copy `plugins/skillberry-plugin-mcp-importer/`):

```
plugins/skillberry-plugin-rossoctl-tools/
  pyproject.toml          # name skillberry-plugin-rossoctl-tools, deps: httpx>=0.28 (already a store dep)
  README.md
  src/skillberry_plugin_rossoctl_tools/
    __init__.py
    plugin.py             # SkillberryPluginRossoctlTools(PluginBase)
    backend.py            # RossoctlBackend: token cache + HTTP calls
    provider.py           # RossoctlProvider (C2)
    export.py             # contributor (C4) + rossoctl-tools.json + computed field (C5)
    importer.py           # claimer (C4)
    promote.py            # stage 3 only
  tests/
    fake_backend.py       # FastAPI app emulating rossoctl endpoints
    test_backend.py test_provider.py test_export.py test_import_route.py
```

- Entry point: `[project.entry-points."skillberry_store.plugins"] rossoctl-tools = "skillberry_plugin_rossoctl_tools.plugin:SkillberryPluginRossoctlTools"`.
- Root `pyproject.toml`: add extra `plugin-rossoctl-tools = ["skillberry-plugin-rossoctl-tools>=0.1.0"]`, add it to `plugins-all`, and add the `[tool.uv.sources]` editable path. Add `"rossoctl-tools"` to `plugin_config.json` `disabled`.
- Routes use `from skillberry_store.plugins.base import requires` with the `@requires(...)` decorator **above** `@router.post(...)`, as in mcp-importer.
- `PluginMetadata(name="rossoctl Tools", version="0.1.0", description="Use rossoctl tools in skills, through rossoctl's backend", plugin_type=PluginType.IMPORTER)`.
- `is_enabled()`: `bool(os.getenv("ROSSOCTL_BACKEND_URL"))`. `get_status_message()`: `"Disabled: set ROSSOCTL_BACKEND_URL"` or `"Ready"`.
- UI: `get_ui_config()` with one action, "Import rossoctl tools", endpoint `/plugins/rossoctl-tools/import`, params schema mirroring the request model below (same shape as mcp-importer's `get_ui_config`).

**`backend.py` — `RossoctlBackend`:**
- `base_url` from `ROSSOCTL_BACKEND_URL` (strip trailing `/`; requests go to `{base_url}/api/v1/...`).
- Token: if `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET` and `ROSSOCTL_TOKEN_ENDPOINT` are all set, `POST TOKEN_ENDPOINT` form `grant_type=client_credentials&client_id=…&client_secret=…`; cache `access_token` until `expires_in - 30` seconds. If they are unset, send no `Authorization` header (rossoctl with `ENABLE_AUTH=false`). If `ctx.bearer` is set, send that instead (delegated mode).
- On 401: drop the cached token, fetch a new one, retry once.
- Timeout `ROSSOCTL_TIMEOUT` (default 30 s). Map connection errors and 502/504 to `RossoctlUnavailable`; 404 to `RossoctlNotFound`; 401/403 after retry to `RossoctlForbidden`.
- `ROSSOCTL_ALLOWED_NAMESPACES`: if non-empty, reject other namespaces with `ValueError` before any call.
- Methods and the rossoctl responses they rely on (verified at `1cac9f3f`):

| Method | rossoctl call | Response used |
|---|---|---|
| `list_tools(ns)` | `GET /api/v1/tools?namespace={ns}` | `{"items": [{"name", "namespace", "description", "status", "labels", "createdAt", "workloadType"}]}` (`models/responses.py:39-54`) |
| `get_tool(ns, name)` | `GET /api/v1/tools/{ns}/{name}` | `{"metadata", "spec", "status", "readyStatus": "Ready"\|"Progressing"\|"Failed"\|"Not Ready", "workloadType", "service": {"name", "type", "clusterIP", "ports": [{"port", …}]} \| null}` (`routers/tools.py:864-935`) |
| `connect(ns, name)` | `POST /api/v1/tools/{ns}/{name}/connect` (no body) | `{"tools": [{"name", "description", "input_schema"}]}` (`:2436-2513`, `MCPToolSchema :328-333`); with R10 also `"annotations"` |
| `invoke(ns, name, tool, args)` | `POST /api/v1/tools/{ns}/{name}/invoke` body `{"tool_name", "arguments"}` | `{"result": {"content": [{"type": "text", "text"} \| {"type": "data", "data"}], "isError": bool}}` (`:2516-2600`); 502/504 when the tool is unreachable |
| `create_from_store(...)` | `POST /api/v1/tools/from-store` (stage 3, new) | 202 `{"success", "name", "namespace", "message"}` |

**`provider.py` — `RossoctlProvider` (`name = "rossoctl"`):**
- `validate`: §4.2.2 rules.
- `list_tools(endpoint, ctx)`: `backend.connect(ns, tool)` → list of `{name, description, input_schema, annotations}`.
- `call_tool(pp, args, ctx)`: `backend.invoke(ns, tool, pp["mcp_tool_name"], args)`; `text` = newline-joined `text` items (`data` items JSON-encoded); `is_error` = `result.isError`.
- `proxy_resolution(pp)`: the three steps of §4.2.2, in the step format of C3:

```python
T = re.sub(r"[^A-Za-z0-9]", "_", ep["tool"]).upper()
[
  {"kind": "env", "var": f"ROSSOCTL_TOOL_{T}_URL"},
  {"kind": "template", "template": "{gw}", "vars": {"gw": {"env": "ROSSOCTL_MCP_GATEWAY_URL"}},
   "tool_name": f'{ep.get("gateway_prefix","")}{pp["mcp_tool_name"]}'},   # only if gateway_prefix set
  {"kind": "template", "template": "http://{tool}-mcp.{ns}.svc.cluster.local:{port}{path}",
   "vars": {"tool": {"default": ep["tool"]},
            "ns": {"env": "ROSSOCTL_NAMESPACE",
                   "file": "/var/run/secrets/kubernetes.io/serviceaccount/namespace",
                   "default": ep["namespace"]},
            "port": {"env": f"ROSSOCTL_TOOL_{T}_PORT", "default": str(ep["port"])},
            "path": {"default": ep.get("path", "/mcp")}}},
]
```

  rossoctl injects `ROSSOCTL_TOOL_<T>_URL` using the **same** `<T>` rule (§5.3); both sides must use `re.sub(r"[^A-Za-z0-9]", "_", name).upper()`.

**Routes** (request models in `plugin.py`):

```python
class ImportToolRef(BaseModel):  name: str; gateway_prefix: Optional[str] = None
class ImportSkill(BaseModel):    name: str; description: str; instructions: Optional[str] = None; tags: List[str] = []
class ImportRequest(BaseModel):  namespace: str; tools: List[ImportToolRef]; skill: Optional[ImportSkill] = None; tags: List[str] = []
class CheckRequest(BaseModel):   skill: Optional[str] = None; tool_uuids: List[str] = []
```

- `POST /import` → `{"tools": [{"uuid", "name", "rossoctl_tool"}], "skill_uuid": str|null, "errors": [{"tool", "detail"}]}`. Per rossoctl tool: `get_tool` (404 → error entry); port = `service.ports[0].port`, else 8000; `connect`; for each MCP tool create a store tool:
  - `data = {name, description, params: {"type": "object", "properties": input_schema.properties, "required": input_schema.required or []}, packaging_format: "mcp", programming_language: "python", tags: ["mcp", f"rossoctl-tool:{tool}", f"namespace:{ns}", *tags], extra: {"mcp_annotations": annotations or {}}, packaging_params: {"mcp_tool_name": name, "endpoint": {...§4.2.2...}, "input_schema_sha256": sha}}`
  - module: `mcp_content_from_manifest(data)` (`fast_api/server_utils.py:54`), filename `f"{name}.py"`;
  - name collisions with an existing store tool imported from a *different* rossoctl tool: prefix `f"{tool}__{name}"` (D8, Q1).
  - With `skill`: `create_skill({"name", "description", "tool_uuids": [...], "tags": ["rossoctl-approved"?…] + skill.tags})`; if `instructions`, first `create_snippet({"name": f"{skill.name}-instructions", "content": instructions, "tags": ["file:SKILL.md"]})` and add its uuid to `snippet_uuids`.
- `POST /check` → `{"tools": [{"uuid", "name", "drift": bool, "live_sha256", "stored_sha256", "error"?}]}`.
- `POST /resync` → for each drifted tool, `create_tool` with the **same name** and a new uuid (the store makes it the head, `tools_service.py:301-304`); then a new skill version with the same name and the new uuids. Returns the new uuids.
- `GET /tools?namespace=` → pass-through of `list_tools`.

**`export.py`:** contributor per §4.2.3; `rossoctl-tools.json` exactly:

```json
{"schema": "skillberry.ai/rossoctl-tools/v1",
 "tools": [{"tool": "weather-tool", "port": 8000, "path": "/mcp",
            "mcp_tools": [{"name": "get_weather", "script": "scripts/get_weather.py",
                           "schema_sha256": "…", "read_only": true}]}]}
```

`read_only` = `extra.mcp_annotations.readOnlyHint is True`; sort tools by `tool`, `mcp_tools` by `name`. Computed field `_rossoctl_required_tools`: sorted unique `endpoint.tool` of the skill's tools with `endpoint.provider == "rossoctl"`.

### 14.5 rossoctl changes: exact interfaces (stage 1)

All paths relative to `rossoctl/backend/app/` unless noted. Line numbers are at `1cac9f3f`.

| Item | Change |
|---|---|
| R1 flag | `core/config.py`: `rossoctl_feature_flag_tool_skills: bool = False`, `rossoctl_tool_skills_enforce: bool = False`, `rossoctl_tool_skills_auto_routes: bool = False`. `routers/config.py`: `toolSkills` in `FeatureFlagsResponse` and `get_feature_flags`. `charts/rossoctl/values.yaml` `featureFlags.toolSkills: false`; `charts/rossoctl/templates/ui.yaml` env `ROSSOCTL_FEATURE_FLAG_TOOL_SKILLS` next to `…_SIMULATED_TOOLS` (`:168`), plus `ROSSOCTL_TOOL_SKILLS_ENFORCE`/`…_AUTO_ROUTES` from `toolSkills.enforce`/`.autoRoutes`. UI `hooks/useFeatureFlags.ts`: `toolSkills`. In `main.py`, if the flag is on and `skills` or `external_skills` is off, log a warning and force it off. |
| R2 annotation | `core/constants.py`: `SKILL_REQUIRED_TOOLS_ANNOTATION = "rossoctl.io/required-tools"`. `services/skill_autosync.py`: `_fetch_registry_skills` (`:98`) — when the flag is on, request `?fields=uuid,name,description,version,tags,_rossoctl_required_tools`; on HTTP 400 retry once without `fields` and remember "unsupported" in a module variable until the autosync config changes. `_skill_signature` (`:52`) — add `"required_tools": sorted(skill.get("_rossoctl_required_tools") or [])`. `_create_autosync_skill` (`:151`), `_patch_skill` (`:201`) — set the annotation to `json.dumps(sorted(...))` when the field is present. `routers/skills.py` `create_external_skill` (`:591`) — the one GET per §5.2.2. |
| R3 agent create | `routers/agents_skills.py`: add `collect_required_tools(kube, ns, skills) -> Dict[str, List[str]]` reading the annotation from each skill ConfigMap (match by name or display-name as `_load_agent_skill_summaries` does, `:54`). `routers/agents_create.py` after `:123`: compute, check `<tool>-mcp` Services with `kube.get_service`, raise 422 or collect warnings, append env vars `ROSSOCTL_TOOL_<T>_URL = get_mcp_tool_url(tool, ns, kube)` and `ROSSOCTL_NAMESPACE = ns` to `request.envVars` **only where the user did not set them**. `routers/agents_manifests.py`: set `rossoctl.io/required-tools` next to `rossoctl.io/skills` at `:394, :595, :741, :849`. `routers/agents_models.py` `CreateAgentResponse` (`:306`): `warnings: List[str] = []`. Repeat the block in `routers/agents_finalize.py` `finalize_shipwright_build` (`:90`, skill handling at `:293-320`). |
| R5 UI | §5.5; endpoint `GET /api/v1/skills/{ns}/{name}/tool-requirements` in `routers/skills.py`, viewer role, 404 when the flag is off. |
| R6 Helm | §6.2: new `templates/skillberry-store-plugins-cm.yaml`; in `templates/skillberry-store.yaml` mount it and set `SKILLBERRY_PLUGIN_CONFIG`, set `ROSSOCTL_BACKEND_URL=http://rossoctl-backend.{{ .Values.ui.namespace }}.svc.cluster.local:8000`, and map the credential Secret keys `CLIENT_ID`, `CLIENT_SECRET`, `TOKEN_ENDPOINT` to `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT`; add a second job from `templates/api-oauth-secret-job.yaml` with `CLIENT_ID=skillberry-store-tools`, `SECRET_NAME=skillberry-store-rossoctl-credentials`, the role `rossoctl-operator`, rendered when `toolSkills` and `keycloak.enabled`. |
| R7 installer | §6.3. |
| R8 examples | §5.6, in `rossoctl/examples`. |
| R10 annotations | `routers/tools.py`: `MCPToolSchema` gains `annotations: Optional[dict] = None`; in `connect_to_tool` set it from `tool.annotations.model_dump(exclude_none=True)` when present. |

### 14.6 Tests that define "done" (stage 1)

Store, core (must pass with **no plugins installed**):
- `tests/modules/test_tool_providers.py`: registry respects the active resolver; dispatch returns 422 for an unavailable provider; validator matrix.
- `tests/tools/test_mcp_proxy.py`: generator safety (hostile descriptions through `ast.parse` and execution); argparse for every JSON type; step resolution order; exit codes; SSE-framed and JSON responses parsed from recorded bytes.
- `tests/e2e/test_mcp_proxy_export.py` (marker `integration`): start a FastMCP `streamable-http` fixture server on a free port (test-only use of `mcp`), register a **test provider** whose `proxy_resolution` returns a `url` step to it, export, unzip into a temp dir, run each proxy with `python3 -I` in a subprocess; assert exit codes 0, 1, 3.
- `tests/test_core_has_no_rossoctl.py`: walk `src/skillberry_store/` excluding `tests/` and `contrib/`; fail on `rossoctl` or `svc.cluster.local`.
- Golden export: existing `tests/tools/test_exporter_archive.py` cases unchanged.
- CI: a job that installs `skillberry-store[dev,pytest]` (no `plugins-all`) and runs `pytest src/skillberry_store/tests -m "not integration"` plus the two files above.

Store, plugin (`plugins/skillberry-plugin-rossoctl-tools/tests/`):
- `fake_backend.py`: FastAPI app with `GET /api/v1/tools`, `GET /api/v1/tools/{ns}/{name}`, `POST …/connect`, `POST …/invoke`, `POST /token`; records requests. Run with `httpx.ASGITransport` (no network).
- Assert: bearer sent; one retry after a 401; no host other than the backend contacted; import creates the tool shapes of §14.4; `_rossoctl_required_tools` correct; contributor output matches the JSON above; Execute via the store returns the fake's text; `isError` maps to 500.

rossoctl:
- `rossoctl/backend/tests/test_skill_autosync.py`: the `fields` request, the 400 fallback, the signature, the annotation.
- `rossoctl/backend/tests/test_agents_external_skills.py` (or a new `test_tool_skills.py`): enforce/warn, env injection without overriding user values, workload annotation, flag off is a no-op.

### 14.7 Stage 3 interfaces

Implement only after stage 1 is merged (D7).

**Store core P1.** `GET /skills/{uuid_or_name}/export-mcp-bundle` in `fast_api/skills_api.py` next to `export-anthropic` (`:601-630`), `@requires("skills", "get")`, returns `application/json` per §13.3. Service method `SkillsService.export_mcp_bundle(uuid_or_name, tool_uuids=None) -> dict` reusing `gather_export_inputs`. Canonical JSON for the digest: `json.dumps(obj_without_digest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`, digest `"sha256:" + hexdigest`. Spec file `docs/specs/mcp-bundle-v1.md` and `docs/specs/mcp-bundle-v1.schema.json`. Conformance cases in `docs/specs/mcp-bundle-v1-conformance/` (one bundle plus expected `tools/call` results per case: success, raised exception → `isError`, missing argument → `isError`, dependency import), runnable by the store's executor and by the rossoctl runner.

**Store plugin P2.** `POST /plugins/rossoctl-tools/promote` body `{skill: str, namespace: str, name: str, tools: List[str] = [], replicas: int = 1, authBridgeEnabled: bool = True}` → 202 `{"promotion_id"}`; `GET /plugins/rossoctl-tools/promote/{id}` → `{"state": "bundling"|"deploying"|"waiting"|"importing"|"done"|"failed", "detail", "tool"?, "skill_uuid"?}`. Run the steps of §13.4 in an `asyncio` task; keep state in memory (lost on restart is acceptable for v1; the operation is idempotent to re-run because `from-store` patches in place).

**rossoctl Q2 runner.** New directory `rossoctl/bundle-runner/` (Dockerfile, `pyproject.toml`, `bundle_runner/`). FastMCP (`mcp.server.fastmcp.FastMCP`), `stateless_http=True`, `streamable_http_path="/mcp"`, port 8000. Register one tool per bundle entry with the entry's `name`, `description` and `params` as input schema. Each call: `subprocess.run([venv_python, "-I", "-c", RUNNER_STUB], input=json.dumps({"module": …, "function": name, "arguments": …}), timeout=TOOL_TIMEOUT)`; the stub prints one JSON line `{"ok": true, "value": …}` or `{"ok": false, "error": …}`. `/readyz` returns 200 after `uv pip install` of the union of `dependencies` into `/venv` succeeds. Settings: `bundle_runner_image: str = "ghcr.io/rossoctl/bundle-runner:latest"` in `core/config.py` next to `simulation_harness_image` (`:90-91`).

**rossoctl Q3 endpoint.** New `routers/tools_from_store.py`, included with the `/api/v1` prefix in `main.py` behind `rossoctl_feature_flag_tool_skills_publish`, modeled on `routers/simulation.py:426-512`: validate (422), `kube.ensure_service_account`, create ConfigMap, Deployment (labels as `services/simulation_manifests.py:200-240`, **without** `rossoctl.io/type`, which the operator stamps), Service `<name>-mcp` port 8000, `AgentRuntime` `type: tool`; 409 if a workload of that name exists **and** lacks `rossoctl.io/source=skillberry-store` (otherwise patch in place); on `ApiException` delete what was created and return 502; return 202.

### 14.8 Order of work

1. Store C1 (`isError`), commit.
2. Store C2 (provider hook, validator, dispatch, 422), commit.
3. Store C3 (proxy generator and its tests, including the integration test with a test provider), commit.
4. Store C4 and C5 (hooks), commit.
5. Core-only CI job and `test_core_has_no_rossoctl.py`, commit.
6. Plugin: `backend.py` + fake backend tests; `provider.py`; routes; `export.py`; `importer.py`; packaging. Commit per file group.
7. `make test`, `make lint`; open the store PR.
8. rossoctl R1 → R2 → R3 → R10 → R6 → R5 → R7; backend tests with each; open the rossoctl PR.
9. `rossoctl/examples` R8 and the `night-out-planner` skill folder; open the examples PR.
10. Kind end-to-end per §9.4 with `SKILLBERRY_STORE_IMAGE`/`SKILLBERRY_STORE_TAG` pointing at a store image built with `make docker-build-full`.
11. Stage 3 (§14.7), only after 7–10 are merged.

### 14.9 Things that will look wrong but are intended

- The store's exported proxy speaks streamable HTTP while the store itself does not. The proxy is generated text that runs in agent pods (D2).
- Store-side Execute of a rossoctl tool needs rossoctl's backend and credential even though the tool URL is known. Calls must go through rossoctl (D1).
- `_rossoctl_required_tools` is absent from every default `GET /skills/` response. It is opt-in by design (D6).
- Tools created by the plugin have a stub `module_name` file. It exists only because existing code (`vmcp_server.py:131-170`, `tools_service.py:305-306`) expects one; provider dispatch never reads it.
- `mcp_url` is optional when `endpoint` is present; existing SSE tools keep `mcp_url` and the old path untouched.
