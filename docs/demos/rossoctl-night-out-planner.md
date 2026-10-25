# Demo: a skill that gives an agent two rossoctl tools ("night-out-planner")

This guide shows how to prepare and run the demo from
[docs/design/rossoctl-skills.md §9](../design/rossoctl-skills.md#9-demo-night-out-planner):

- You compose one skill in skillberry-store from two tools that already run in rossoctl:
  `weather-tool` and `reservation-tool`.
- You give the skill to an agent. The agent gets both tools through the skill's scripts, plus
  instructions for when and how to use each tool.
- The agent needs no `MCP_URL` and no tool selection.

There are two ways to run the demo. Pick the one that fits:

| | [A. Kind cluster](#a-kind-cluster-full-demo) | [B. One machine, no containers](#b-one-machine-no-containers) |
|---|---|---|
| Needs | Docker or Podman, Kind, Helm, `kubectl` | Python 3.11, `uv`, `curl` |
| Shows | Everything, including a live chat with an LLM agent, the rossoctl UI and Phoenix traces | The whole data path without an LLM: import, store-side execution, autosync, agent creation with injected URLs, and the skill's scripts called the way the agent calls them |
| Time | About 30 minutes (first install) | About 5 minutes |

Both use the same code: the `rossoctl-skills` branches of
[skillberry-store](https://github.com/erezh16/skillberry-store/tree/rossoctl-skills) and
[rossoctl](https://github.com/erezh16/rossoctl/tree/rossoctl-skills), plus a `rossoctl-skills`
branch of [rossoctl/examples](https://github.com/rossoctl/examples) for the example agents.

---

## What you will see

1. **Import.** The store's `rossoctl-tools` plugin reads the two tools from rossoctl's backend. It
   creates six proxy tools (`get_weather`, `search_restaurants`, `check_availability`,
   `place_reservation`, `cancel_reservation`, `list_reservations`) and the skill
   `night-out-planner`.
2. **Store-side execution.** Executing `search_restaurants` in the store returns *Trattoria di Mare*
   and *Bella Napoli*. The call went store → rossoctl `/invoke` → tool. The store never connects to
   the tool pod.
3. **rossoctl picks the skill up.** Within about 30 seconds, autosync creates the skill in `team1`
   with the annotation `rossoctl.io/required-tools: ["reservation-tool","weather-tool"]`. The
   **Required Tools** card shows both tools as ready.
4. **Agent creation.** Create an agent with only the skill. rossoctl injects
   `ROSSOCTL_TOOL_WEATHER_TOOL_URL`, `ROSSOCTL_TOOL_RESERVATION_TOOL_URL` and `ROSSOCTL_NAMESPACE`,
   and it annotates the workload with the required tools.
5. **The agent uses the tools.** The skill's `scripts/*.py` call the tools over MCP. They are
   standard-library Python and contain no credentials.
6. **Negative and portability cases.**
   - In `team2`, which has no reservation tool, agent creation warns. With
     `toolSkills.enforce=true` it is rejected with 422.
   - The script for the missing tool exits with code 3 and names the tool.
   - After you deploy the tool in `team2`, the same unchanged skill works there.

---

## Prepare the code (both ways)

```bash
mkdir -p ~/demo && cd ~/demo
git clone -b rossoctl-skills https://github.com/erezh16/skillberry-store.git
git clone -b rossoctl-skills https://github.com/erezh16/rossoctl.git
git clone https://github.com/rossoctl/examples.git rossoctl-examples
```

For the example agents, check out the examples branch that contains the skill-script support. It
adds Claude Code skill discovery to `claude_agent` and an opt-in `run_skill_script` tool to
`generic_agent`. Until that branch is published, apply the commit from the PR.

The skill's instructions, from [design §9.3](../design/rossoctl-skills.md#93-the-skill), are in
[`rossoctl-night-out-planner/instructions.md`](rossoctl-night-out-planner/instructions.md):

```bash
cp ~/demo/skillberry-store/docs/demos/rossoctl-night-out-planner/instructions.md ~/demo/instructions.md
```

---

## A. Kind cluster (full demo)

### A.1 Build a store image with the plugin

The rossoctl chart deploys the store from an image. That image must contain the `rossoctl-tools`
plugin. The default `Dockerfile` builds core only.

```bash
cd ~/demo/skillberry-store
python3 -m venv .venv && source .venv/bin/activate
make docker-build-full          # the all-plugins variant, tagged :<version>-full and :latest-full
docker images | grep skillberry-store            # note the repository name it printed
docker tag <repository>:latest-full skillberry-store:rossoctl-skills
```

### A.2 Install rossoctl with tool-skills

```bash
cd ~/demo/rossoctl
kind create cluster --name rossoctl          # or let the installer create it
kind load docker-image skillberry-store:rossoctl-skills --name rossoctl

SKILLBERRY_STORE_IMAGE=skillberry-store SKILLBERRY_STORE_TAG=rossoctl-skills \
  scripts/kind/setup-rossoctl.sh --with-all --with-tool-skills --with-examples \
  --build-images --skip-cluster
```

- `--with-tool-skills` turns on `featureFlags.toolSkills`, and with it `skills` and `externalSkills`.
  It also enables the store's `rossoctl-tools` plugin and points the plugin at the backend.
- `--with-examples` deploys `weather-tool` and `reservation-tool` in `team1`.
- `--build-images` builds the backend and UI from this branch.

Check:

```bash
kubectl -n team1 get deploy weather-tool reservation-tool
kubectl -n rossoctl-system get pods -l app=skillberry-store
curl -s http://rossoctl-ui.localtest.me:8080/api/v1/config/features | jq .toolSkills   # true
```

The store's plugin config is the ConfigMap `skillberry-store-plugins`:

```bash
kubectl -n rossoctl-system get cm skillberry-store-plugins -o jsonpath='{.data.plugins\.json}'
# rossoctl-tools must NOT be in "disabled"
```

When the installer enabled Keycloak (`--with-all` includes SPIRE, so UI auth is on), the job
`skillberry-store-oauth-secret-job` creates the store's credential:

```bash
kubectl -n rossoctl-system get secret skillberry-store-rossoctl-credentials
```

### A.3 Prepare the deterministic parts

The reservation tool's mock data is deterministic for each restaurant, time and party size. For
Trattoria di Mare (`rest_001`), party of 4:
- 19:00 is **taken**;
- 19:30 and 20:00 are **free**.

This gives the "slot taken, offer the nearest one" moment of the chat. Check it before the demo:

```bash
STORE=http://skillberry-store.localtest.me:8080        # the store API through the gateway
```

If the store's API is not exposed through the gateway, port-forward it:

```bash
kubectl -n rossoctl-system port-forward svc/skillberry-store 8000:8000 &
STORE=http://localhost:8000
```

### A.4 Run the demo

**Step 1. Import the tools into the store, together with the skill.**

```bash
python3 - "$STORE" ~/demo/instructions.md <<'PY'
import json, sys, urllib.request
store, instr = sys.argv[1], open(sys.argv[2]).read()
body = {"namespace": "team1",
        "tools": [{"name": "weather-tool"}, {"name": "reservation-tool"}],
        "skill": {"name": "night-out-planner",
                  "description": "Plan and book a restaurant evening in a city, taking current weather into account. Use when the user wants dinner/lunch suggestions, table availability, or to book or cancel a reservation.",
                  "instructions": instr, "tags": ["rossoctl-approved"]}}
req = urllib.request.Request(f"{store}/plugins/rossoctl-tools/import",
                             json.dumps(body).encode(), {"content-type": "application/json"})
print(json.dumps(json.load(urllib.request.urlopen(req)), indent=1))
PY
```

You get six tools and a `skill_uuid`. In the store UI, the plugin card **rossoctl Tools →
Import rossoctl tools** does the same.

**Step 2. Store-side check.** In the store UI, open the tool `search_restaurants` → **Execute** with
`{"city": "Boston", "cuisine": "Italian"}`. Or use curl:

```bash
curl -s -X POST $STORE/tools/search_restaurants/execute -H 'content-type: application/json' \
  -d '{"city":"Boston","cuisine":"Italian"}' | jq -r '."return value"' | jq '.[].name'
```

The backend log shows the call, made with the store's `skillberry-store-tools` client:

```bash
kubectl -n rossoctl-system logs deploy/rossoctl-backend | grep reservation-tool/invoke | tail -1
```

**Step 3. rossoctl picks the skill up.** Wait about 30 s. Then:

```bash
kubectl -n team1 get cm night-out-planner \
  -o jsonpath='{.metadata.annotations.rossoctl\.io/required-tools}'; echo
# ["reservation-tool", "weather-tool"]
```

In the UI, open **Skills → night-out-planner**. The **Required Tools** card shows
`reservation-tool Ready` and `weather-tool Ready`.

**Step 4. Create the agent.** In **Agents → Import Agent**:
- namespace `team1`;
- image `ghcr.io/rossoctl/examples/claude_agent:latest`, built from the examples branch;
- env `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_BASE_URL` for your LiteLLM or Anthropic endpoint;
- check `night-out-planner` under **Linked Skills**.

The chips under the skill read `reservation-tool`, `weather-tool`, and the line below says *"This
agent will get 2 tools through its skills"*. Leave the MCP tool empty. Or, from the CLI:

```bash
rossoctl agents import from-image --name night-out \
  --containerImage ghcr.io/rossoctl/examples/claude_agent:latest \
  --envVar ANTHROPIC_AUTH_TOKEN=$TOKEN --envVar ANTHROPIC_BASE_URL=$LITELLM \
  --additionalParameterJSON '{"skills": ["night-out-planner"]}'
rossoctl agents wait night-out --timeout 5m
```

Show what rossoctl did:

```bash
kubectl -n team1 get deploy night-out -o json | jq -r '
  .spec.template.spec.containers[0].env[] | select(.name|test("^ROSSOCTL_|SKILL_FOLDERS")) | "\(.name)=\(.value)"'
#  ROSSOCTL_TOOL_RESERVATION_TOOL_URL=http://reservation-tool-mcp.team1.svc.cluster.local:8000/mcp
#  ROSSOCTL_TOOL_WEATHER_TOOL_URL=http://weather-tool-mcp.team1.svc.cluster.local:8000/mcp
#  ROSSOCTL_NAMESPACE=team1
#  SKILL_FOLDERS=/app/skills/night-out-planner
kubectl -n team1 get deploy night-out -o jsonpath='{.metadata.annotations.rossoctl\.io/required-tools}'; echo
```

The LLM-free check from the design (§7.4): run one proxy inside the pod.

```bash
kubectl -n team1 exec deploy/night-out -c agent -- \
  python3 /app/skills/night-out-planner/scripts/search_restaurants.py --city Boston | grep -c "Trattoria di Mare"
```

**Step 5. Chat.** In the agent's chat, send:

> Plan dinner for 4 in Boston tomorrow at 7pm, Italian, mid-range.

The expected trajectory:
1. `get_weather.py`. The agent says the result is current conditions, not a forecast, because the
   booking is for tomorrow.
2. `search_restaurants.py`, then two or three options.
3. `check_availability.py`. 19:00 at Trattoria di Mare is taken, so the agent offers 19:30 or 20:00.
4. The agent asks for name, phone and email, then for an explicit "yes".
5. `place_reservation.py`, then a confirmation code.

Then ask: *"What reservations do I have?"* The agent runs `list_reservations.py`.

**Step 6. Observe.**
- In Phoenix, the agent's trace contains the tools' `execute_tool` spans.
- `kubectl -n team1 logs deploy/night-out -c authbridge` shows the calls passing through the sidecar.

**Step 7. The negative and portability cases.**

`team2` is created by the installer. Its weather tool is not needed for this step.

1. Create the same agent in `team2`, where there is no reservation tool:
   - The UI shows the chip `reservation-tool (not in team2)`.
   - The create response, and the agent page, warn: *"Skill(s) night-out-planner require tool
     'reservation-tool', not found in team2"*.
   - With `--set toolSkills.enforce=true`, the create is rejected with 422.
2. Deploy the tool in `team2` and create the agent again. The same skill, unchanged, now works there:

   ```bash
   .github/scripts/operator/73-deploy-reservation-tool.sh team2
   ```

**Step 8 (optional): baseline.** Ask the stock `weather-service` agent the same question. It has a
single `MCP_URL`, so it can report the weather but cannot book a table.

---

## B. One machine, no containers

This variant runs the real code, unchanged, on one machine:
- the store and the rossoctl backend run from source;
- the two example tools run as local processes;
- a [kwok](https://kwok.sigs.k8s.io/) control plane supplies a real Kubernetes API server, without
  kubelets.

rossoctl creates real Deployments, Services and ConfigMaps in it. Instead of an agent pod, the
script fetches the skill exactly as the agent's init container does. It then runs the skill's
scripts with the environment that rossoctl wrote into the agent's Deployment.

It needs no LLM, container runtime or cluster admin rights. It does not show the UI or a chat.

### B.1 One-time setup

```bash
cd ~/demo
# Store
(cd skillberry-store && python3 -m venv .venv && source .venv/bin/activate \
   && make install-requirements ODEPS=test SKIPOPT=1)
# rossoctl backend
(cd rossoctl/rossoctl/backend && uv sync --extra dev)
# Example tools
(cd rossoctl-examples/mcp/weather_tool && uv sync --frozen)
(cd rossoctl-examples/mcp/reservation_tool && uv sync --frozen)
# kwok (a real kube-apiserver with no kubelets)
curl -fsSL -o kwokctl https://github.com/kubernetes-sigs/kwok/releases/download/v0.7.0/kwokctl-linux-amd64
chmod +x kwokctl
KWOK_WORKDIR=$PWD/kwok ./kwokctl create cluster --name demo --runtime binary
KWOK_WORKDIR=$PWD/kwok ./kwokctl get kubeconfig --name demo > kubeconfig
# The AgentRuntime CRD from rossoctl's operator chart
(cd rossoctl/charts/rossoctl && helm dependency build . \
   && tar xzf charts/operator-chart-*.tgz -C /tmp)
KUBECONFIG=$PWD/kubeconfig kubectl apply -f /tmp/operator-chart/crds/
```

Ports used:

| Port | Used by |
|---|---|
| 8000 | the store |
| 8010 | the rossoctl backend |
| 18001 | weather-tool |
| 18002 | reservation-tool |
| 8080 | a small gateway that routes `<tool>.localtest.me:8080` to the tools, as rossoctl's gateway does |

`*.localtest.me` resolves to `127.0.0.1`. The weather tool calls Open-Meteo on the internet. Without
internet access, it returns *"temporarily unavailable"*, and the rest of the demo still works.

### B.2 Run it

The script
[`rossoctl-night-out-planner/run-local-demo.sh`](rossoctl-night-out-planner/run-local-demo.sh)
runs every step of A.4 that does not need an LLM:

```bash
cd ~/demo/skillberry-store
ROSSOCTL_REPO=~/demo/rossoctl EXAMPLES_REPO=~/demo/rossoctl-examples KUBECONFIG=~/demo/kubeconfig \
  docs/demos/rossoctl-night-out-planner/run-local-demo.sh
```

The output of a run is in
[`sample-output.txt`](rossoctl-night-out-planner/sample-output.txt).

It prints each step:

1. the tools start;
2. rossoctl backend features `toolSkills: true`;
3. the store with the plugin `rossoctl-tools enabled - Ready`;
4. import, with 6 tools;
5. store-side `search_restaurants → ['Trattoria di Mare', 'Bella Napoli']`, through the backend's
   `/invoke`;
6. the autosynced ConfigMap with `rossoctl.io/required-tools`, and the tool-requirements endpoint;
7. agent creation, with the injected `ROSSOCTL_TOOL_*_URL`, the workload annotation and the
   skill-fetch init container;
8. the agent side: the skill unpacked under `/app/skills/night-out-planner`, then search, weather,
   availability (19:00 taken, 19:30 free), booking with a confirmation code, and the reservation
   list;
9. `team2`: a warning for the missing tool, exit code 3 from its script, then the same skill working
   after the tool is deployed there.

Logs are in `/tmp/demo/run/logs/`. Set `WORK=<dir>` to put them elsewhere. A second run needs a
fresh cluster state: `kubectl delete ns team1 team2` first.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `POST /plugins/rossoctl-tools/import` returns 404 | The plugin is not active. Check `GET /plugins/`: the `rossoctl-tools` status must be `Ready`, and `ROSSOCTL_BACKEND_URL` must be set. Check that `skillberry-store-plugins` does not list it under `disabled` (`featureFlags.toolSkills=true`). Check that the store image includes the plugin (`-full` image or `PLUGIN_EXTRAS=plugin-rossoctl-tools`). |
| Import returns 403 | The store has no valid credential. Check that `skillberry-store-rossoctl-credentials` exists and that the job `skillberry-store-oauth-secret-job` succeeded. |
| Import returns 502 for a tool | The backend cannot reach the tool's MCP server. Check that the tool pod is Ready, and run `curl $BACKEND/api/v1/tools/team1/<tool>/connect -X POST`. |
| The skill in rossoctl has no `rossoctl.io/required-tools` | Check that `featureFlags.toolSkills` is on (`/api/v1/config/features`). Check whether the backend logged *"does not provide _rossoctl_required_tools"*: if it did, the store lacks the plugin. Autosync then retries only after its config changes. |
| A script exits with 3 | The tool is not reachable from the agent's namespace. The message names the URL it tried. Deploy the tool there, or set `ROSSOCTL_TOOL_<TOOL>_URL`. |
| A script exits with 1 | The tool returned an error, for example *"City X not found"*. The message is on stderr. |
| Agent creation warns about a missing tool | Expected in a namespace without the tool. Use `toolSkills.enforce=true` to reject instead. |
