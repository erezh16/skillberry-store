# Skills that use rossoctl tools — Design

Status: **Implemented, revision 8 ("option 0")**
Owner: skillberry-store, `rossoctl-tools` plugin. **rossoctl: no change. skillberry-store core: C1 only.**

Revision notes:
* **r8:** the design and implementation are rebuilt around **option 0** of [the pitch](../rossoctl-skills-pitch.md#rung-0-no-rossoctl-change-ship-the-wiring-with-the-skill).
  - The skill is delivered by rossoctl as today.
  - The rossoctl tools it needs reach the agent as **MCP tools**, configured on the agent with the existing `MCP_URLS` / `MCP_URL` env vars, in the same create request that links the skill.
  - The plugin produces that create request (the **deployment recipe**).

  Proxy scripts stay in the package for agents that run scripts (and any non-rossoctl host), but nothing depends on them. r1–r7 are superseded; their history is in git.
* r7: evidence on how rossoctl agents use skills (kept as §2). r6: rossoctl tools as store code tools. r5: the core design principles. r4 and earlier: rossoctl changes, all dropped.

---

## 0. Principles

These hold from design through implementation. A section that contradicts them is a defect.

- **P-1. No changes in rossoctl.** Change rossoctl only when absolutely required, or when the alternative is extremely costly in effort or runtime performance. r8 changes nothing in rossoctl: no backend, UI, chart, operator, installer or docs change.
- **P-2. Store changes live in the plugin.** Core gets only generic must-have changes with value outside rossoctl. r8's core change is **C1** (an `isError` bug fix, §5.1), and this feature doesn't depend on it.

Outside both repos, the feature needs no change in `rossoctl/examples` either: option 0 works with the stock `generic_agent`.

## 1. What a user gets

Compose a skill in skillberry-store from tools already deployed in rossoctl. Then create an agent in rossoctl with the skill **and** its tools in one step, with a request the store generates.

1. **Import**: `POST /plugins/rossoctl-tools/import` reads the selected tools from rossoctl's backend. It creates one store tool per MCP tool, and optionally the skill with its instructions.
2. **Test from the store**: `POST /tools/{id}/execute` runs any of those tools from the store. The call goes through rossoctl's `/invoke`, so the tool is never contacted directly.
3. **Recipe**: `POST /plugins/rossoctl-tools/recipe {skill, namespace, agent}` returns:
   - a ready-to-send `POST /api/v1/agents` body: `skills: [<skill>]`, `envVars: [MCP_URLS=…, MCP_TRANSPORT=…]` and the agent image;
   - the equivalent `rossoctl agents import` command;
   - a pre-flight check that every required tool is deployed and ready in that namespace.
4. **Deploy**: send the body to rossoctl (UI, CLI or API). rossoctl's existing skill fetch puts the skill in the pod, and the agent connects to the tools over MCP. The skill's instructions tell it when and how to use each one.

## 2. Why option 0 (evidence)

Checked at `rossoctl/rossoctl@1cac9f3f` and `rossoctl/examples@f1fc5df2`.

- **The rossoctl agent contract has tools, not skills.** "Bring your own agent" requires A2A over HTTP plus `LLM_*`, `MCP_URL` (one address: a tool or the MCP Gateway) and `MCP_URLS` (several, comma-separated). `SKILL_FOLDERS` is set by the skill fetch, but no agent is obliged to read it.
- **No example agent executes skill scripts:**
  - `generic_agent` pastes SKILL.md and the scripts into the system prompt, and its only tools are MCP tools from `MCP_URLS`;
  - the LangGraph siblings (`weather_service` and others) ignore skills and take one `MCP_URL`;
  - the AG2, CrewAI and plain-LLM agents ignore skills;
  - `claude_agent` could run scripts, but is never pointed at the skill and isn't a console preset.

  So a script in a skill is, today, text the model reads.
- **This isn't a security rule.** The docs treat code-running agents as supported (the Sandboxes page; the internal skill-execution e2e), and they enforce policy at the AuthBridge sidecar regardless of agent code.
- **Consequence:**
  - On a stock rossoctl agent today, a skill's tools work only as **MCP tools wired on the agent**. The skill contributes the instructions.
  - Option 0 does exactly that, with no change anywhere. The store generates the wiring together with the skill, so the two stay consistent.

## 3. Overview

```
 skillberry-store (rossoctl-system)                         rossoctl (unchanged)                     team1
 ┌──────────────────────────────────┐  GET/POST /api/v1/tools ┌─────────────────────┐   MCP   ┌──────────────────────┐
 │ rossoctl-tools plugin            │  (list, get, connect,   │ rossoctl-backend    │───────▶ │ weather-tool-mcp     │
 │  /import → store tools + skill   │──── invoke) ───────────▶│                     │         │ reservation-tool-mcp │
 │  /recipe → POST /agents body     │                         │ skill autosync      │         └─────────▲────────────┘
 │  /check  → drift, pre-flight     │◀── GET /skills/ ────────│ + skill fetch init  │                   │ MCP (MCP_URLS)
 └──────────────────────────────────┘                         │   container         │         ┌─────────┴────────────┐
          │ recipe (JSON / CLI)                               └──────────┬──────────┘         │ agent pod            │
          ▼                                                              │ skill zip          │  SKILL_FOLDERS: skill│
     user ── POST /api/v1/agents {skills, envVars: MCP_URLS} ───────────▶└───────────────────▶│  MCP_URLS: the tools │
                                                                                              │  AuthBridge (egress) │
                                                                                              └──────────────────────┘
```

## 4. Decisions

All decisions taken in r8, numbered for reference. The implementation follows them.

| # | Decision | Reason |
|---|---|---|
| D1 | **No rossoctl change; no `rossoctl/examples` change.** | P-1. Option 0 works with stock agents. |
| D2 | **Core: C1 only.** Everything else from r4–r7 core (provider hook, contribution hooks, computed fields, core proxy generator) is reverted. | P-2. Not needed by option 0. |
| D3 | **The binding between a skill and its tools is the deployment recipe**, generated by the plugin, not anything inside rossoctl. | Option 0. The recipe uses only existing `CreateAgentRequest` fields (`skills`, `envVars`, `containerImage`, `deploymentMethod`, …). |
| D4 | **Tool addresses in the recipe:**<br>- **`direct`** (default): `MCP_URLS` = `http://<tool>-mcp.<ns>.svc.cluster.local:<port>/mcp` per required tool, with the port read live from `GET /tools/{ns}/{tool}`, plus `MCP_TRANSPORT=streamable_http`.<br>- **`gateway`**: `MCP_URL` = the MCP Gateway address (`ROSSOCTL_MCP_GATEWAY_URL`, default `http://mcp-gateway-istio.gateway-system.svc.cluster.local:8080/mcp`). | `direct` works on every install; only `generic_agent` takes several URLs. `gateway` serves single-`MCP_URL` agents, but needs the tools registered with the gateway by an operator; the recipe says so. |
| D5 | **The recipe targets `generic_agent` by default** (`ghcr.io/rossoctl/examples/generic_agent:latest`, `deploymentMethod: image`). The image is overridable. In `gateway` mode any MCP agent works. | It is the only stock agent that reads skills and accepts `MCP_URLS`. |
| D6 | **The recipe never carries LLM settings or secrets.** It leaves `LLM_*` to the user and lists them in `todo`. | The store doesn't know the cluster's model setup, and must not handle its keys. |
| D7 | **Pre-flight is part of the recipe:**<br>- for each required tool: present, `readyStatus`, Service port;<br>- a missing tool is reported in `missing`;<br>- the body still includes the present tools. | It replaces agent-create validation in rossoctl (P-1). |
| D8 | **Required tools are recorded on the skill** in `extra.rossoctl.required_tools`, a sorted list of rossoctl tool names. They are derived from the skill's rossoctl-imported tools when absent. | The recipe needs them. `extra` is opaque to core, so no core change is needed. |
| D9 | **Store tools are `code` tools** whose module is a generated **proxy**. As a function, the store calls rossoctl `/invoke` with the plugin's credential, read from the store's environment. As a CLI, an agent can run it, speaking MCP to the tool's Service. | Store-side testing and evaluation without a core hook (P-2). The script is also usable by script-running agents and non-rossoctl hosts. No credentials are embedded. |
| D10 | **Store-side execution needs the store's local execution mode** (`EXECUTE_PYTHON_LOCALLY=true`). In Docker mode the proxy can't see the store's environment, and calls the tool directly. | Core runs Docker execution with an empty environment. A per-tool env passthrough would be a core change (P-2), so it's deferred. |
| D11 | **Proxy tools are tagged `rossoctl-proxy`.** Operators should exclude them from automatic runners (evaluator, DAST live). | Store-side execution of a write tool has real side effects. |
| D12 | **SKILL.md guidance for the MCP path.** `/import` appends a generated "Tools" section to the skill's instructions snippet:<br>- the MCP tool names (`direct`) and their gateway-prefixed names (`gateway`, when a prefix is set);<br>- read-only and write markers;<br>- "ask the user before calling a write tool".<br>Its first line also tells script-capable agents about `scripts/`. | The agent learns the tools from MCP; the skill adds *when* and *how*. `/import` writes it once; `/refresh` regenerates it. |
| D13 | **Read-only marking comes from the import request** (`read_only: [...]`, default: none). | rossoctl's `/connect` returns no MCP annotations, and that won't change (P-1). |
| D14 | **Name collisions:** prefix on collision only (`<tool>__<name>`). | Store tool names are global. |
| D15 | **`rossoctl-tools.json` is a `file:` snippet** in the skill: `{schema, tools: [{tool, port, path, mcp_tools: [...]}]}`. It declares the dependency in the package, for people and other tooling. | No core hook. A plain file in the export. |
| D16 | **Credential:** client-credentials (`ROSSOCTL_CLIENT_ID/SECRET/TOKEN_ENDPOINT`), or none when rossoctl runs without auth. Deployment: existing chart keys (`skillberryStore.extraEnv`, `apiOAuthSecret`). | No chart change (P-1). |
| D17 | **Stage 2 (promoting store code tools to rossoctl tools) is deferred.** The r6 approach remains valid: existing `POST /tools` and a store-owned runner. | Out of option 0's scope. |
| D18 | **The demo runs against unmodified rossoctl `main`**, and is scripted in the plugin's tests. | It proves D1. |

## 5. skillberry-store

### 5.1 Core: C1 (independent fix)

`execute_mcp_tool` returned `content[0].text` and ignored `isError`. Now:
- an `isError` result becomes `{"error": …}`, which gives HTTP 500 at `/tools/{id}/execute`;
- all text items are joined;
- `structuredContent` is returned as `structured`.

It is in CHANGELOG. It affects only SSE `mcp` tools, which option 0 does not use.

Also in core: `tests/test_core_has_no_rossoctl.py`, which fails if core source mentions `rossoctl` or `svc.cluster.local`, and the CI job that runs core tests with no plugins installed.

### 5.2 Plugin `rossoctl-tools`

`plugins/skillberry-plugin-rossoctl-tools/`, extra `plugin-rossoctl-tools` (in `plugins-all`), dependency `httpx`. It is active when `ROSSOCTL_BACKEND_URL` is set.

| Module | Role |
|---|---|
| `backend.py` | Client for rossoctl `/api/v1/tools`:<br>- endpoints `list`, `get`, `connect`, `invoke`;<br>- a cached client-credentials token, refreshed once on 401;<br>- DNS-1123 name checks and `ROSSOCTL_ALLOWED_NAMESPACES`. |
| `proxy.py` | Generates the proxy module (D9). One top-level function, so core's executor and importer each see exactly one tool. Its two paths:<br>- **store:** stdlib HTTP to `/invoke`;<br>- **CLI:** stdlib MCP over streamable HTTP. |
| `skill.py` | The generated Tools section (D12) and `rossoctl-tools.json` (D15); the required-tools list (D8). |
| `recipe.py` | Pre-flight and the agent-create body (D3–D7). |
| `plugin.py` | Routes and UI action. |

**Routes** (all under `/plugins/rossoctl-tools`):

| Route | Permission | Behavior |
|---|---|---|
| `GET /tools?namespace=` | `tools:list` | Pass-through of rossoctl's tool list |
| `POST /import` | `tools:create` | Body: `{namespace, tools: [{name, read_only?: [str], gateway_prefix?}], skill?: {name, description, instructions?, tags?}}`. Creates the proxy tools, and optionally the skill:<br>- an instructions snippet (author text + the Tools section);<br>- the `rossoctl-tools.json` snippet;<br>- `extra.rossoctl`. |
| `POST /recipe` | `skills:get` | Body: `{skill, namespace, agent?: {name?, image?, mode?: "direct" \| "gateway"}}` → `{request, cli, preflight: [...], missing: [...], todo: [...]}` |
| `POST /check` | `tools:list` | `{skill?, tool_uuids?, namespaces?}` → schema drift (via `/connect`) and per-namespace pre-flight |
| `POST /refresh` | `skills:create` | Regenerates the Tools section and `rossoctl-tools.json` from the skill's current tools (new snippet versions, new skill version) |

**Plugin env:**
- `ROSSOCTL_BACKEND_URL`;
- `ROSSOCTL_CLIENT_ID`, `ROSSOCTL_CLIENT_SECRET`, `ROSSOCTL_TOKEN_ENDPOINT`;
- `ROSSOCTL_ALLOWED_NAMESPACES`, `ROSSOCTL_TIMEOUT`;
- `ROSSOCTL_AGENT_IMAGE` (default D5), `ROSSOCTL_MCP_GATEWAY_URL` (default D4).

### 5.3 Behavior without the plugin

| Situation | Behavior |
|---|---|
| Skills and tools the plugin created | They are ordinary code tools and snippets. Export, import and store UI all work. |
| Execute of a proxy tool | Works if `ROSSOCTL_BACKEND_URL` (and the credential) is still in the store's environment. Otherwise it calls the tool directly, which fails outside the cluster. |
| `/plugins/rossoctl-tools/*` | 404 |

## 6. Deployment (existing chart values only)

```yaml
featureFlags: {skills: true, externalSkills: true}
components: {skillberryStore: {enabled: true}}
skillberryStore:
  image: {tag: "<release>-full"}            # built with plugins-all
  extraEnv:
    - {name: ROSSOCTL_BACKEND_URL, value: "http://rossoctl-backend.rossoctl-system.svc.cluster.local:8000"}
    - {name: EXECUTE_PYTHON_LOCALLY, value: "true"}     # D10
    # with ui.auth.enabled=true, reuse the existing api-oauth-secret client (D16):
    - {name: ROSSOCTL_CLIENT_ID,      valueFrom: {secretKeyRef: {name: rossoctl-api-oauth-secret, key: CLIENT_ID}}}
    - {name: ROSSOCTL_CLIENT_SECRET,  valueFrom: {secretKeyRef: {name: rossoctl-api-oauth-secret, key: CLIENT_SECRET}}}
    - {name: ROSSOCTL_TOKEN_ENDPOINT, valueFrom: {secretKeyRef: {name: rossoctl-api-oauth-secret, key: TOKEN_ENDPOINT}}}
  autosync: {allowedTags: "rossoctl-approved"}
apiOAuthSecret: {enabled: true}
```

## 7. Security

- **The recipe** holds no secrets (D6). The user adds `LLM_*`, typically as a `secretKeyRef`.
- **The agent reaches the tools over MCP through its AuthBridge sidecar**, like any MCP-wired rossoctl agent. With token exchange, the user adds `outboundRoutes` as today.
- **Proxy scripts** embed no credentials. In the store they read the plugin's credential from the store's environment. In agents they never see one.
- **Store-side Execute** runs as the store's service identity. In `disabled` store ACL mode, anyone who can call the store can invoke a rossoctl tool through it. Keep `ROSSOCTL_ALLOWED_NAMESPACES` tight (D11).

## 8. Testing

| Layer | Tests |
|---|---|
| Core | C1 unit tests; the no-rossoctl guard; golden exports unchanged; the full `make test` |
| Plugin unit | Backend client vs a fake rossoctl backend (token, 401 refresh, host allowlist); proxy generation (safety, one function, core parser compatibility); recipe (`direct`/`gateway`, ports, missing tools, no secrets); the Tools section and `rossoctl-tools.json`; `/check` |
| Plugin e2e | The in-process store with the plugin against the fake backend: import → Execute (through `/invoke`) → recipe → export; the proxy CLI against a FastMCP streamable-HTTP fixture |
| Cluster e2e | `tests/cluster/run-local-demo.sh`, which runs §9 on a kwok control plane with **rossoctl `main` unmodified**. It plays the agent as `generic_agent` would: MCP calls to the recipe's `MCP_URLS`. |

## 9. Demo: night-out-planner

1. **Deploy the tools:** `weather-tool` and `reservation-tool` in `team1` (console presets, or `--with-examples` plus the Reservation Tool preset).
2. **Import** both into the store, with the skill `night-out-planner` (`rossoctl-approved`) and the read-only list.
3. **Store-side test:** Execute `search_restaurants {"city": "Boston", "cuisine": "Italian"}` → Trattoria di Mare.
4. **Generate the recipe** for `team1`:
   - pre-flight shows both tools ready;
   - `request.envVars` has `MCP_URLS` with both Services;
   - `skills: ["night-out-planner"]`.
5. **Create the agent** by posting the recipe (adding `LLM_*`). Autosync has already created the skill in `team1`, so the fetch init container delivers it and `generic_agent` puts it in its prompt.
6. **Chat:** "Plan dinner for 4 in Boston tomorrow at 7pm, Italian." The agent calls `get_weather`, `search_restaurants` and `check_availability` (19:00 is taken, so it offers 19:30), asks for confirmation, then calls `place_reservation`.
7. **Portability:** the recipe for `team2` (no reservation tool) reports `missing: ["reservation-tool"]` before anything is created.

User guide: [docs/demos/rossoctl-night-out-planner.md](../demos/rossoctl-night-out-planner.md).

## 10. Later rungs

[The pitch](../rossoctl-skills-pitch.md) lists the next steps, cheapest first. Each one is opt-in and would get its own design:
1. agents that run skill scripts, where the proxies already in the package become usable;
2. a skill-runner agent type;
3. skills in the agent contract;
4. rossoctl acting on a skill's tool dependencies;
5. CRD support.

Stage 2, promotion (D17), is independent of these.
