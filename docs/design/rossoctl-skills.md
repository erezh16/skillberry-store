# Skills that use rossoctl tools — Design

Status: **Proposal (revision 6)**
Owner: skillberry-store (the `rossoctl-tools` plugin). rossoctl is unchanged.
Scope: Stage 5 of [rossoctl-integration.md](../rossoctl-integration.md#staged-improvement), "Skills that use rossoctl tools", extended to store tools that *become* rossoctl tools. The integration doc's sketch is the section [Stage 1: store skills that include rossoctl tools through proxy scripts](../rossoctl-integration.md#stage-1-store-skills-that-include-rossoctl-tools-through-proxy-scripts).

Revision notes:
* **r6 — Each rossoctl tool becomes an ordinary store *code* tool, whose module is the proxy script.** The store executes it as any code tool, and exports it verbatim. As a result:
  - **C2 (tool-provider hook) is dropped.** Nothing in core needs to know a tool is remote.
  - **C3 (package contribution hooks) is dropped.** The dependency file and the generated Tools section are snippets that the plugin creates and keeps current (§4.2.5), and a proxy script re-imports as a working code tool with no claimer.
  - **Core is C1 only,** the `isError` fix, which this feature does not depend on.
  - Store-side calls go through rossoctl's `/invoke` when the script runs in the store process, with the credential read from the store's environment, never embedded (§4.2.3). In Docker execution mode the script cannot see that environment, and calls the tool directly instead (D7).
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
| skillberry-store core | C1 `isError` fix (§4.1). It is a bug fix in core's SSE path; this feature does not depend on it. | S |
| `rossoctl-tools` plugin | Everything else: backend client, proxy generator, the proxy code tools it creates, the skill snippets it maintains, import, check, resync, promotion, the stage 2 runner image | L |

### 0.2 Stages

| Stage | What a user gets | skillberry-store | rossoctl | Sections |
|---|---|---|---|---|
| **1. Skills from existing rossoctl tools** | In the store, compose a skill from tools already deployed in rossoctl. Agents get the tools through the skill as runnable proxy scripts. The store can execute the tools for testing and evaluation. | **No core change** (C1 is an independent fix). The plugin: backend client, proxy generator, proxy code tools, skill snippets, `/tools`, `/import`, `/check`, `/resync`. | **None.** Deployed with existing chart values (§6). | §1–§9 |
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

1. **Exported proxies are not runnable.** For a `packaging_format: "mcp"` tool, `export-anthropic` writes a bodiless stub (`mcp_content_from_manifest`, `fast_api/server_utils.py:54-95`) to `scripts/<tool>.py`. An agent that receives it cannot call anything. A **code** tool, by contrast, exports its module verbatim, and the store executes it; r6 builds on that.
2. **No way to reach the tools.** The store's MCP client is SSE-only (`modules/file_executor.py`). rossoctl tools serve streamable HTTP at `http://<tool>-mcp.<ns>.svc.cluster.local:<port>/mcp`. rossoctl's backend, however, already lists and invokes any tool (`POST /tools/{ns}/{name}/connect`, `/invoke`), so the store can work through it instead of adding a transport.
3. **Location is per namespace.** One fixed `packaging_params.mcp_url` cannot serve a skill shared across namespaces.
4. **No declared dependency.** Nothing in a skill package says "I need tool X".

On the rossoctl side, an agent gets one MCP tool through `mcpToolName` → `MCP_URL`, but any number of skills through `request.skills`. A skill that carries its tools as scripts therefore removes the single-tool limit, with no rossoctl change.

## 2. Goals and non-goals

### Goals

- **G0. rossoctl unchanged; no store core change for this feature.** The principles of §0.1.
- **G1. Runnable tool proxies in skills.** A skill that aggregates rossoctl tools exports one self-contained, standard-library-only CLI per MCP tool.
- **G2. Late binding.** The same skill works in every namespace that runs the tools. The endpoint is resolved in the agent's pod at run time, and is never baked into the export.
- **G3. Declared dependencies.** The package declares the rossoctl tools it needs, for people (SKILL.md) and for programs (frontmatter `metadata` and `rossoctl-tools.json`).
- **G4. Pre-flight checks from the store.** Before an agent is created, the store can report, per namespace, which of a skill's tools are deployed, ready and on the recorded port (`/check`). This replaces r4's rossoctl-side agent-create validation.
- **G5. Import.** The plugin creates proxy tools, and optionally a skill skeleton, from live rossoctl tools, through rossoctl's backend API.
- **G6. Store-side execution.** The store executes these tools (`/tools/{id}/execute`, vMCP) for testing, evaluation and dreaming, as it executes any code tool. In the store's local execution mode the proxy calls rossoctl's `/invoke`, so rossoctl makes the call under its authorization and audit, and the store never connects to tool pods. In Docker mode it calls the tool directly (D7).
- **G7. No credentials in the proxy.** Authentication on the agent's egress is AuthBridge's job.

### Non-goals

- **N0. Any change to rossoctl,** including its backend, UI, chart, installer, operator and docs. §12 keeps a list of candidates for the record. None is planned.
- **N1. Any change to store core for this feature,** including MCP transport work. The proxy speaks streamable HTTP, but it is generated text from the plugin, stored as an ordinary code tool's module.
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
 │  · proxy = code tool module │◀── GET /skills/ (as today) ──│  + skill fetch       │  │       ┌──────────┴───────────┐
 │  · snippets: Tools section, │                              └──────────────────────┘  └─────▶ │ agent pod            │
 │    rossoctl-tools.json      │──── zip (existing skill-fetcher init container) ──────────────▶│  scripts/*.py proxy  │
 └─────────────────────────────┘                                                                │  AuthBridge (egress) │
                                                                                                └──────────────────────┘
```

Every arrow into or out of rossoctl uses an endpoint or mechanism that exists today:
- **`/tools` REST:** `routers/tools.py:744, 864, 1609, 2435, 2516`.
- **Skill autosync:** `services/skill_autosync.py`, seeded by `templates/skillberry-autosync-configmap.yaml`.
- **The external-skill fetch:** `routers/agents_skills.py`, the `fetch-skill-*` init container, `GET {store}/skills/{name}/export-anthropic`.

How the pieces compose:
- **Store without the plugin:** no change to anything a user sees, except the independent `isError` fix (C1). Tools and skills the plugin created earlier keep working: they are ordinary code tools and snippets.
- **Store with the plugin:** stage 1 in full. Agents that can run scripts use the skills. rossoctl does not know the feature exists.
- **The example agent change (§5.3):** gives rossoctl's example agents the ability to run skill scripts.

---

## 4. skillberry-store changes

### 4.0 The split

All rossoctl knowledge lives in the plugin. Core code never contains the strings `rossoctl` or `svc.cluster.local` outside tests; a test enforces this (§14.6).

The plugin uses only interfaces core already offers to plugins: routes, UI actions, and the `StoreAPI` (`create_tool`, `create_snippet`, `create_skill`, `get_*`, `update_*`). Everything it creates is an ordinary store object:

| Concern | How it works, with no core change |
|---|---|
| A rossoctl tool in the store | One **code** tool per MCP tool (`packaging_format: "code"`, `programming_language: "python"`). Its module is the generated proxy script; its `params` come from the tool's live input schema. |
| Store-side execution | Core's code-tool executor calls the script's function. The function calls rossoctl's `/invoke` (local mode) or the tool directly (Docker mode), §4.2.3. |
| Export to agents | Core exports a code tool's module verbatim as `scripts/<tool>.py`. That file is the agent's CLI. |
| The dependency declaration and the usage text | Two plugin-maintained snippets: `file:rossoctl-tools.json`, and a `file:SKILL.md` snippet with the generated Tools section (§4.2.5) |
| Re-import of an exported package | Core's importer turns each script back into a working code tool (one top-level function); `rossoctl-tools.json` and the Tools section come back as snippets |
| Talking to rossoctl | The plugin's backend client and routes |

### 4.1 Core changes

#### C1. Correct error results (independent bug fix)

**Standalone value.** `execute_mcp_tool` returns `content[0].text` and ignores `isError`, so a failing remote tool looks successful to every store user with an SSE `mcp` tool.

**Change:**
- an `isError: true` result is returned as an execution error;
- all text items are joined with newlines;
- `structuredContent` is returned as `"structured"`.

This changes behavior: an error result is now HTTP 500 at `/tools/{id}/execute`. It is noted in `CHANGELOG.md`.

**Relation to this feature: none.** The feature uses code tools, not core's MCP path. C1 is kept because it was found and fixed during this work. It can land as its own pull request.

#### Not in core (r6)

| r4/r5 core item | Why it is not needed |
|---|---|
| C2 tool-provider hook (`packaging_params.endpoint`, provider registry, execute dispatch, 422, `export_module`, npx cache key) | A proxy code tool is executed and exported by core's existing code-tool paths. Nothing in core needs to know the tool is remote. |
| C3 export contributor and import claimer, the generated-section markers | The plugin writes its additions as real snippets (§4.2.5). Core's importer re-creates the proxy as a code tool without help. |
| C3 kept frontmatter `metadata` | Not needed by the feature: the dependency is declared in `rossoctl-tools.json` and the Tools section. It is a reasonable standalone fix, which can be proposed on its own merits. |
| Opt-in computed skill fields | Already dropped in r5 |

### 4.2 The `rossoctl-tools` plugin

`plugins/skillberry-plugin-rossoctl-tools/`:
- slug `rossoctl-tools`, type `IMPORTER`;
- optional extra `plugin-rossoctl-tools`, included in `plugins-all`;
- dependency `httpx` only. The stage 2 runner has its own extra (§13.4).

The *generated proxy scripts* depend on nothing: they use only the standard library, so they run in any agent image, and core's local executor installs nothing for them.

It is active when `ROSSOCTL_BACKEND_URL` is set and the store's plugin config does not disable it. It makes no network calls until a route is invoked. The proxy tools it created make calls when they are executed, with or without the plugin active.

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
| `ROSSOCTL_FORWARD_CALLER_TOKEN` | `false` | delegated mode (§8) |
| `ROSSOCTL_RUNNER_IMAGE`, `ROSSOCTL_STORE_URL` | see §13 | stage 2 |

**SSRF.** The only outbound destinations are `ROSSOCTL_BACKEND_URL` and the token endpoint. Tool and namespace names are validated as DNS-1123 labels and used only in URL paths.

#### 4.2.2 The proxy code tool

`/import` creates one store tool per MCP tool of each selected rossoctl tool:

```jsonc
{
  "name": "search_restaurants",              // the MCP tool name; on a collision, <tool>__<name>
  "description": "…",                        // from /connect
  "packaging_format": "code",
  "programming_language": "python",
  "module_name": "search_restaurants.py",
  "params": {…},                             // the live input schema, as ToolParamsSchema
  "tags": ["mcp", "rossoctl-proxy", "rossoctl-tool:reservation-tool", "namespace:team1"],
  "extra": {
    "rossoctl": {                            // plugin-owned metadata; core stores it opaquely
      "tool": "reservation-tool", "namespace": "team1", "port": 8000, "path": "/mcp",
      "mcp_tool_name": "search_restaurants",
      "input_schema_sha256": "…",
      "read_only": true,
      "generator_version": "1",
      "promoted_from": "<uuid>",             // stage 2 only
      "bundle_digest": "sha256:…"            // stage 2 only
    }
  }
}
```

**The module** is the generated script (§4.2.4). It holds a single top-level function, named like the store tool, with one keyword parameter per schema property. Its body runs a module-level `_main(...)`, which is defined *inside* the function's body (as a nested function) or after a `__main__` guard, so that:
- core's executor finds and calls the one function;
- core's importer finds exactly one tool in it on re-import (`extract_python_functions` takes top-level functions only);
- auto-detection of dependencies finds nothing to link (`detect_tool_dependencies` matches calls to other store tools by name, and the script calls none).

`extra.rossoctl` is the plugin's source of truth for `/check`, `/resync` and regeneration. The same values are embedded in the script, so the script never reads the store.

#### 4.2.3 Endpoint resolution

The script runs in two places and chooses its path at run time.

**In the store (store-side execution).** If `ROSSOCTL_BACKEND_URL` is in the process environment, the script calls `POST {ROSSOCTL_BACKEND_URL}/api/v1/tools/{ns}/{tool}/invoke` with `{tool_name, arguments}`. It gets a token from `ROSSOCTL_TOKEN_ENDPOINT` with `ROSSOCTL_CLIENT_ID`/`ROSSOCTL_CLIENT_SECRET` when those are set. `ns` is the import namespace.
- **Local execution** (`EXECUTE_PYTHON_LOCALLY=true`): core runs the code with `exec` in the store process, so the script sees the store's environment. Calls go through rossoctl, under its authorization and audit, and the store never connects to tool pods.
- **Docker execution** (core's default): core starts the container with only `PYTHONUNBUFFERED` set, so the backend variables are absent. The script falls through to the agent order below, which works only when the container can reach the tool's cluster address (D7).
- The credential is **never in the script**. It comes only from the environment of the process that runs it.

**In an agent pod.** rossoctl injects nothing, so the script resolves the tool itself, in this order:

1. **`ROSSOCTL_TOOL_<TOOL>_URL`**, if set. This is an override the user can set through the agent's existing `envVars`. `<TOOL>` is the tool name upper-cased, with every non-alphanumeric character replaced by `_`.
2. **`ROSSOCTL_MCP_GATEWAY_URL`**, only for tools imported with a `gateway_prefix`. The call then uses `<prefix><mcp_tool_name>`.
3. **`http://<tool>-mcp.<ns>.svc.cluster.local:<port><path>`**, the cluster address of the Service that `POST /tools` and the example deploy scripts create (`routers/tools.py:1548`).
   - `<ns>` comes from `ROSSOCTL_NAMESPACE`, else the pod's ServiceAccount namespace file (`/var/run/secrets/kubernetes.io/serviceaccount/namespace`, mounted for Deployment and StatefulSet agents), else the import namespace.
   - `<port>` comes from `ROSSOCTL_TOOL_<TOOL>_PORT`, else the import port.

Step 3 is the normal path, and it is what makes the skill portable across namespaces (G2). Its one weakness is a tool redeployed on a different port. `/check` detects that, and `/resync` fixes it (§4.2.6).

Agent pods do not set `ROSSOCTL_BACKEND_URL`, so they never take the store path. Setting it there would make the agent call rossoctl's backend instead of the tool, which needs an operator credential in the pod and is not supported.

#### 4.2.4 The proxy CLI (plugin)

**As a function** (store-side): `search_restaurants(city="Boston", cuisine="Italian")` returns the tool's text result, decoded as JSON when it is JSON. A tool error, or an unreachable tool, raises an exception, which core's executor reports as an execution error (HTTP 500 at `/tools/{id}/execute`, an error result over vMCP).

**As a CLI** (agents): `python3 scripts/<tool>.py --<prop> …`, with these options:
- `--json '{…}'`, or `-` to read the arguments from stdin;
- `--describe`, `--check` (schema hash), `--raw`, `-v`.

**Exit codes:**
- 0: success;
- 1: the tool returned `isError`;
- 2: usage error;
- 3: the endpoint does not resolve, or the transport failed;
- 4: the input schema drifted.

**Runtime.** Standard library only, Python ≥ 3.8. No credentials are embedded; `MCP_PROXY_AUTH_HEADER` is read from the environment when set, and `TRACEPARENT`/`TRACESTATE` are forwarded.

The store's executor generates a wrapper that calls the function and prints its result (`generate_wrapper_any_types`, `modules/file_executor.py`), and in local mode it `pip install`s any module the code imports that is missing. The proxy imports only the standard library, so nothing is installed.

**Protocol:** streamable HTTP, revisions 2025-03-26 and 2025-06-18.
1. `initialize`, accepting a JSON or an SSE response.
2. `notifications/initialized`.
3. `tools/call`. Server-initiated requests get the JSON-RPC error `-32601`.
4. A best-effort `DELETE` of the session.

**Generation safety.** Manifest values are never interpolated into code. The spec is embedded as JSON (`ensure_ascii`, `'` escaped) in a raw literal, and the docstring uses only `mcp_tool_name`. Keys named `headers`, `auth*`, `token*` or `*secret*` are refused.

#### 4.2.5 Skill contents: plugin-maintained snippets

When `/import` creates a skill, or when `/resync` updates one, the plugin also writes two snippets and links them to the skill (`snippet_uuids`). Core's exporter already turns a `file:<path>` snippet into that file, and appends a second `file:SKILL.md` snippet to the first:

| Snippet | Tag | Exported as | Content |
|---|---|---|---|
| `<skill>-rossoctl-tools` | `file:rossoctl-tools.json`, `rossoctl-generated` | `rossoctl-tools.json` | the dependency declaration, below |
| `<skill>-rossoctl-tools-usage` | `file:SKILL.md`, `rossoctl-generated` | appended to SKILL.md, after the author's instructions | the generated "Tools" section |

`rossoctl-tools.json`:

```json
{"schema": "skillberry.ai/rossoctl-tools/v1",
 "tools": [{"tool": "weather-tool", "port": 8000, "path": "/mcp",
            "mcp_tools": [{"name": "get_weather", "script": "scripts/get_weather.py",
                           "schema_sha256": "…", "read_only": true}]}]}
```

The Tools section contains:
- per tool, a usage line generated from the schema;
- a read-only marker, or "may change state: confirm with the user first";
- the exit-code legend;
- a rossoctl note: tools resolve in the agent's namespace, and exit 3 means the tool is not deployed there.

**Ordering.** The author's instructions snippet comes first in `snippet_uuids`, and the usage snippet last, so the generated section follows the instructions.

**Keeping them current.** The snippets are derived data. `/resync` regenerates them. So does `POST /plugins/rossoctl-tools/refresh {skill}`, which a user runs after adding or removing proxy tools by hand. Each refresh creates a new snippet version and a new skill version, like any edit. A user can delete the usage snippet to drop the generated section.

**Frontmatter.** r5's frontmatter key `metadata.rossoctl-requires-tools` is dropped: core generates SKILL.md's frontmatter, and the dependency is already declared in `rossoctl-tools.json` and the Tools section.

**Re-import** (`POST /skills/import-anthropic` of an exported package) needs nothing from the plugin:
- each proxy script becomes a working code tool again: one top-level function, its parameters, its docstring;
- `rossoctl-tools.json` comes back as a `file:rossoctl-tools.json` snippet;
- the SKILL.md body, including the Tools section, comes back as the `file:SKILL.md` snippet.

What is lost on re-import is the tools' `extra.rossoctl` metadata and their `rossoctl-*` tags. `POST /plugins/rossoctl-tools/adopt {skill}` restores them from the scripts' embedded spec and the JSON snippet, so `/check` and `/resync` work again. Without adopt, the tools still run.

**Read-only marking.** rossoctl's `/connect` does not return MCP annotations (`MCPToolSchema`, `routers/tools.py:328-333`), and r5 does not change that. The tools are therefore treated as writes unless told otherwise:
- `/import` accepts `read_only: ["get_weather", …]` per tool reference;
- the marking is stored in `extra.rossoctl.read_only` and can be changed with `/refresh`.

#### 4.2.6 Routes

| Route | Permission | Behavior |
|---|---|---|
| `GET /plugins/rossoctl-tools/tools?namespace=` | `tools:list` | Pass-through of rossoctl's `GET /tools`, for a picker in the store UI |
| `POST /plugins/rossoctl-tools/import` | `tools:create` | `{namespace, tools: [{name, gateway_prefix?, read_only?: [str]}], skill?: {name, description, instructions?, tags?}, tags?}`. For each rossoctl tool: `GET /tools/{ns}/{name}` (existence and port), then `/connect`. Creates one proxy **code** tool per MCP tool (§4.2.2). On a name collision with a tool of another rossoctl tool, the new tool is named `<tool>__<name>`. Optionally creates the skill, with `instructions` as the first `file:SKILL.md` snippet, then the two generated snippets (§4.2.5). |
| `POST /plugins/rossoctl-tools/check` | `tools:list` | `{skill? , tool_uuids?, namespaces?: [str]}`. **Schema drift** via `/connect`. **Pre-flight (G4)** for each namespace (default: the import namespace) and each rossoctl tool: `present`, `ready` (`readyStatus`), `port` and `port_matches`. No writes. |
| `POST /plugins/rossoctl-tools/resync` | `tools:create` | A new tool version (same name, new uuid, regenerated module) for each tool whose schema drifted, whose import-namespace port changed, or whose `generator_version` is old; regenerated snippets; a new skill version. |
| `POST /plugins/rossoctl-tools/refresh` | `skills:create` | `{skill, read_only?: [str]}`. Regenerates the two snippets from the skill's current proxy tools; a new skill version. |
| `POST /plugins/rossoctl-tools/adopt` | `tools:update` | `{skill}`. Restores `extra.rossoctl` and the tags of re-imported proxy tools from their embedded spec. |
| `POST /plugins/rossoctl-tools/promote`, `GET …/promote/{id}`, `GET …/bundles/{digest}`, `POST …/promote/gc` | see §13 | stage 2 |

### 4.3 Behavior without the plugin

Everything the plugin created is an ordinary store object, so a store without the plugin keeps working with it:

| Situation | Behavior without `rossoctl-tools` installed or active |
|---|---|
| Store start, APIs, UI | Unchanged |
| Export of a skill with proxy tools | Unchanged: the proxies and the two snippets are exported, and agents use them as before |
| Execute or vMCP on a proxy tool | Works, as a code tool. With `ROSSOCTL_BACKEND_URL` still in the store's environment (local mode) it goes through `/invoke`; otherwise it calls the tool directly. |
| Import of a package that contains proxies | Core's normal import: working code tools and snippets |
| `/plugins/rossoctl-tools/*` (import, check, resync, refresh, adopt, promote) | 404 |

### 4.4 Store work summary

| # | Item | Layer | Size |
|---|---|---|---|
| C1 | `isError` fix (independent) | core | S |
| S1 | Plugin: backend client and credential, proxy generator (function + CLI, store and agent paths), proxy code tools, the two snippets, `/tools`, `/import`, `/check` (drift and pre-flight), `/resync`, `/refresh`, `/adopt` | plugin | M |
| S2 | Docs: plugin README, the demo guide | plugin and docs | S |
| S3 | Stage 2: `/promote`, bundle route, GC, runner image (§13) | plugin | M |

### 4.5 Deployment of the store in rossoctl, with existing chart values

See §6. No chart change is needed.

### 4.6 r4's rossoctl changes and their store-side replacements

| r4 item (dropped) | What it gave | r5 replacement (no rossoctl change) | What is lost |
|---|---|---|---|
| R1 `toolSkills` flag | A gate for R2–R5 | Not needed. The plugin is gated by `ROSSOCTL_BACKEND_URL` and the store's plugin config. | — |
| R2 `rossoctl.io/required-tools` on skill ConfigMaps | rossoctl knew a skill's tools | The dependency lives in the package (`rossoctl-tools.json`, the Tools section) and is queried through the plugin's `/check` | rossoctl itself cannot show or act on it |
| R3 agent-create validation and warnings | A warning, or a 422, for a missing tool | `/check` with `namespaces` before creating the agent; exit code 3 at run time, naming the tool | No automatic check inside rossoctl's agent create |
| R3 URL injection | The real Service port in the pod | Resolution step 3, the import port, plus `/check`'s `port_matches` and `/resync`; or a manual `ROSSOCTL_TOOL_<TOOL>_URL` in the agent's `envVars` | A port change needs a resync before agents pick it up |
| R4 automatic AuthBridge routes | Per-agent outbound routes | The existing `outboundRoutes` field at agent create, when tools need token exchange; the default passthrough needs none | Manual |
| R5 console cards and chips | Visibility in the rossoctl UI | The store UI (plugin card, tool tags `rossoctl-tool:<name>`) and `/check` | Nothing shown in the rossoctl console |
| R6 Helm plugin config and store credential | Automatic setup | Existing values: `skillberryStore.image`, `skillberryStore.extraEnv`, `apiOAuthSecret` (§6.2) | One values file to write |
| R7 installer flag, reservation-tool script | Demo convenience | The console's existing Reservation Tool preset (`ImportToolPage.tsx:64`), or `kubectl apply` | — |
| R9 rossoctl docs and e2e | — | The demo guide and the e2e live in the store repo | — |
| R10 `/connect` annotations | Read-only tools detected automatically | The `read_only` option on `/import` and `/refresh` (§4.2.5) | A manual list |

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
    - name: EXECUTE_PYTHON_LOCALLY        # store-side calls through rossoctl /invoke (D7)
      value: "true"
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
- **Execution mode.** For store-side calls to go through rossoctl, add `EXECUTE_PYTHON_LOCALLY: "true"` to `extraEnv` (D7). Without it the store runs code tools in Docker, and proxy tools call the tool directly, which needs Docker in the store pod and network access to the tool namespaces.

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
- **The exported zip holds no secrets.** The generator refuses credential-like keys, and the script reads the store's credential only from the environment of the process that runs it (§4.2.3).
- **Local execution runs proxy code in the store process.** This is core's existing local mode, which runs every code tool with the store's privileges; proxy tools are no different. The proxy is plugin-generated, but it is an editable code tool: anyone with `tools:update` can change what it does with the store's credential. In `disabled` ACL mode that is anyone who can call the store, the same exposure every code tool already has in local mode.
- **Automatic tool runners.** Store plugins that execute code tools on their own (the evaluator, DAST with `DAST_LIVE=1`) would call live rossoctl tools, including writes. The plugin tags proxies `rossoctl-proxy`; operators should exclude that tag from automatic runs, or keep those plugins off in clusters where tools have side effects.
- **Write tools.**
  - The Tools section asks the model to confirm before running a tool that is not read-only.
  - This is guidance. Enforcement belongs to AuthBridge (`ibac`, `opa`) or to the tool.
- **Plugin SSRF.** Only the configured backend and token endpoint are contacted, and only with validated names.
- **Store-side execution goes through rossoctl** (local mode).
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

**Store core:** C1's error mapping, and a guard that core contains no `rossoctl` or `svc.cluster.local`. Core needs no other test for this feature; existing golden export tests must stay unchanged.

**Plugin unit tests:**
- the backend client against a fake rossoctl backend (`httpx` transport): bearer token, refresh on 401, only the backend host contacted;
- generator safety (hostile input through `ast.parse` and execution);
- the generated module as **core sees it**:
  - core's `extract_function_and_imports` finds one function with the schema's parameters;
  - `generate_wrapper_any_types` wraps and runs it;
  - `extract_python_functions` (re-import) finds exactly one tool;
  - `detect_tool_dependencies` finds none;
- argument parsing for every JSON type, JSON and SSE response frames, resolution order (store path first when `ROSSOCTL_BACKEND_URL` is set);
- the two snippets: content, ordering in the exported SKILL.md, refresh;
- `/check` (drift and pre-flight per namespace, including port mismatch), `/resync`, `/adopt`.

**Plugin e2e** (marker `integration`):
- (a) Generated proxies run as CLIs with `python3 -I` against a FastMCP `streamable-http` fixture, asserting exit codes 0, 1, 2 and 3.
- (b) The full store with the plugin, against the fake backend, **in local execution mode**: `/import`, then `POST /tools/{id}/execute` goes through the fake `/invoke`; export, re-import, `/adopt`.
- (c) The same store **in Docker mode**, or with `ROSSOCTL_BACKEND_URL` unset in the execution environment: Execute calls the FastMCP fixture directly.
- (d) The **local cluster e2e** (`plugins/skillberry-plugin-rossoctl-tools/tests/cluster/run-local-demo.sh`): rossoctl from `main` (unchanged) on a kwok control plane, the example tools, and the store from source. It runs every step of §9 that does not need an LLM.

**Kind e2e** (manual or nightly, in the store repo): §9 without an LLM, with `kubectl exec` into the agent pod to run a proxy.

**rossoctl: none.**

## 8. Future: store ACL in `delegated` mode

The agent-side proxy path is unchanged: it never talks to the store.

- **Placement.** Imported tools and the skill are tagged `namespace:<ns>`. Sharing a skill to another namespace is a guarded tag write. `/check` warns when a skill's namespaces are not covered by its tools' namespaces.
- **Identity toward rossoctl.**
  - User-initiated plugin routes (`/import`, `/check`, `/resync`, `/promote`) forward the caller's Keycloak bearer (`ROSSOCTL_FORWARD_CALLER_TOKEN=true`). rossoctl then authorizes and audits the user. **rossoctl needs no change:** it already validates Keycloak JWTs.
  - Store-side Execute of a proxy tool runs inside core's executor, which does not hand the caller's token to the code. It therefore uses the service credential. Forwarding the caller's identity to code tools would be a generic core feature (useful to any code tool that calls an API), and needs its own P-2 justification when delegated mode is implemented.
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

Six proxy code tools; three snippets (the author's instructions and the generated Tools section, both tagged `file:SKILL.md`, and `file:rossoctl-tools.json`); and the skill `night-out-planner`, tagged `rossoctl-approved`. The import marks `get_weather`, `search_restaurants`, `check_availability` and `list_reservations` read-only.

The exported package:

```
night-out-planner/
  SKILL.md                  # frontmatter + instructions + the generated Tools section (a snippet)
  rossoctl-tools.json
  scripts/{get_weather,search_restaurants,check_availability,place_reservation,cancel_reservation,list_reservations}.py
```

The instructions are in [`docs/demos/rossoctl-night-out-planner/instructions.md`](../demos/rossoctl-night-out-planner/instructions.md).

### 9.4 Demo script

Setup: rossoctl from `main`, unchanged, installed with the values of §6.2 and `--with-examples`; plus `reservation-tool` in `team1`.

1. **Import.** `POST /plugins/rossoctl-tools/import` with `team1`, both tools, the read-only list and the skill. You get six tools.
2. **Store-side check.** Execute `search_restaurants {"city": "Boston", "cuisine": "Italian"}`. It returns Trattoria di Mare. The store runs it as a code tool, and the backend log shows the `/invoke` it made (local execution mode).
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
| 1.M1 Core | C1 (independent), the no-rossoctl guard, the no-plugin CI job | Golden exports unchanged; the core-only suite green |
| 1.M2 Plugin | S1, S2 | Against the fake backend: import, check (drift, pre-flight, port), resync, refresh, Execute (local mode through `/invoke`; Docker mode direct), vMCP, export, re-import and adopt work, with the bearer refreshed and no other host contacted. The §4.3 behaviors hold with the plugin disabled. |
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
2. **Read-only marking without `/connect` annotations.** The proposal is the explicit `read_only` list on import and refresh. The alternative is a name heuristic (`get_`, `list_`, `search_`, `check_`) as a default the user confirms.
8. **Docker execution mode** (D7). Proxies in Docker mode call the tool directly, bypassing rossoctl's `/invoke`. The proposal is to document local mode as the supported mode for store-side execution of proxies. A generic core option to pass selected environment variables into the execution container would close the gap, and needs its own P-2 justification.
9. **Automatic runners.** Should the evaluator and DAST skip `rossoctl-proxy` tools by default? That is a change to those plugins, not to core.
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

- **Code tools only, excluding proxies.** `mcp` tools, and proxy code tools (tag `rossoctl-proxy`, or `extra.rossoctl` set), are skipped: they already run in rossoctl.
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
4. Run `/import` on the new tool, with `promoted_from` and `bundle_digest` recorded in each proxy's `extra.rossoctl`.
5. Create a new skill version in which each promoted code tool is replaced by its proxy code tool, and regenerate the two snippets.

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
| D2 | This feature needs no core change (P-2). C1 is an independent bug fix. Anything a new requirement would add to core needs a written P-2 justification and approval first. |
| D7 | Store-side execution of proxy tools is supported in core's **local** execution mode (`EXECUTE_PYTHON_LOCALLY=true`), where calls go through `/invoke`. In Docker mode the proxy calls the tool directly; that is documented, not prevented. |
| D8 | A rossoctl tool is stored as a **code** tool whose module is the proxy. Plugin metadata lives in `extra.rossoctl`; nothing else in the tool is special. |
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

### 14.3 Core

**C1** — `modules/file_executor.py`: `mcp_call_result_to_dict(result)` → `{"error": text}` when `isError`, else `{"return value": joined_text, "structured"?: …}`. Independent of the feature.

**Guard** — `tests/test_core_has_no_rossoctl.py`.

Nothing else. The plugin uses existing `PluginBase` and `StoreAPI` methods only.

### 14.4 Plugin interfaces

```
plugins/skillberry-plugin-rossoctl-tools/
  src/skillberry_plugin_rossoctl_tools/
    plugin.py       backend.py
    proxy.py        # the generator: one function per tool, CLI runtime, store and agent paths
    tools.py        # create/regenerate proxy code tools (extra.rossoctl), adopt
    snippets.py     # rossoctl-tools.json and the Tools section snippets
    check.py        # drift and per-namespace pre-flight
    promote.py      bundle.py      # stage 2
  runner/           # stage 2: Dockerfile + skillberry_tool_runner
  deploy/reservation-tool.yaml
  docs/tool-bundle-v1.md
  tests/  (unit, e2e, cluster/run-local-demo.sh)
```

- **Proxy:** `proxy.render(meta, schema, description) -> str` produces the module. `proxy.extract_spec(source)` reads the embedded spec without executing it (for `/adopt`).
- **Tools:** `create_proxy_tool(...)` calls `store.create_tool(data, module_content, module_filename)`; regeneration creates a new version (same name, new uuid).
- **Snippets:** `render_tools_json(tools)` and `render_tools_section(tools)`; `ensure_snippets(skill)` creates or replaces them and returns the new `snippet_uuids` order.
- **Routes:** as in §4.2.6. `/check` returns:

  ```json
  {"tools": [{"uuid", "name", "rossoctl_tool", "drift", "live_sha256", "stored_sha256", "error"?}],
   "namespaces": {"team1": [{"tool", "present", "ready", "port", "port_matches"}]}}
  ```

- **`/import`:** accepts `read_only` per tool reference, which sets `extra.rossoctl.read_only` for the named MCP tools.

### 14.5 rossoctl

Nothing to implement. The local cluster e2e runs rossoctl's backend from `main`.

### 14.6 Tests that define "done"

**Stage 1:**
- **Core:** C1's tests; `tests/test_core_has_no_rossoctl.py`; golden export tests unchanged; the suite green with no plugins installed.
- **Plugin:** the unit tests and e2e (a)–(c) of §7.4; the cluster e2e (d) against rossoctl `main`.
- **Examples:** unit tests for skill linking and `run_skill_script`.

**Stage 2:**
- the bundle determinism and digest;
- the conformance cases, on both executors;
- the runner e2e: the image, or the package run locally, served through FastMCP;
- `/promote` against the fake backend (`POST /tools`, polling, import, swap, idempotent 409);
- GC;
- a cluster e2e that promotes `walk_estimate`.

### 14.7 Order of work

1. Core: revert everything except C1 and the guard; keep the no-plugin CI job (§15).
2. Plugin: the proxy generator (function + CLI), proxy code tools, snippets, `/import` with `read_only`, `/check` with pre-flight, `/resync`, `/refresh`, `/adopt`.
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

**skillberry-store, core:** revert to `main` except C1 and the guard.

| r4 code | r6 action |
|---|---|
| `modules/file_executor.py` (C1), `CHANGELOG.md` C1 entry | Keep (independent fix) |
| `tests/test_core_has_no_rossoctl.py`, the no-plugin CI job | Keep |
| `modules/tool_providers.py`, `services/exceptions.py` (`ToolProviderUnavailable`), provider wiring in `schemas/tool_schema.py`, `services/tools_service.py`, `fast_api/tools_api.py`, `modules/vmcp_server.py`, `plugins/base.py`, `plugins/loader.py` | **Revert** |
| `tools/anthropic/mcp_proxy.py` and its core tests | **Move to the plugin** as `proxy.py`, reworked to emit one top-level function plus the CLI runtime |
| `tools/anthropic/contributions.py`, exporter and importer hook wiring, generated markers, `frontmatter_metadata` | **Revert** (the frontmatter fix may be proposed separately) |
| `tools/publish.py` cache-key change | **Revert** |
| `services/field_selection.py`, `fast_api/skills_api.py` (computed fields) and their tests | **Revert** |
| `docs/config-env-vars.md` proxy variables | Move to the plugin README |

**skillberry-store, plugin:**
- the importer creates **code** tools whose module is the proxy, with `extra.rossoctl`;
- the provider, contributor, claimer and computed field are removed;
- `snippets.py` creates and refreshes the JSON and Tools-section snippets;
- add `/check` pre-flight and `port_matches`, the port case of `/resync`, `/refresh`, `/adopt`;
- add `read_only` on `/import`;
- add `deploy/reservation-tool.yaml`;
- move the demo harness to `tests/cluster/`, running rossoctl `main`.

**Demo guide (`docs/demos/rossoctl-night-out-planner.md`):**
- rewrite it to use the values of §6.2, including `EXECUTE_PYTHON_LOCALLY`, with no rossoctl branch;
- remove the steps that showed rossoctl's flag, annotations, cards and injected env;
- add the `/check` pre-flight step.

**rossoctl/examples:** unchanged from r4 (§5.3).
