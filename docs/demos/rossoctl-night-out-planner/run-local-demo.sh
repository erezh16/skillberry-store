#!/usr/bin/env bash
# Runs the night-out-planner demo end to end WITHOUT Docker/Kind, on one host:
#
#   kwok control plane (a real kube-apiserver, no kubelets)  ← rossoctl backend (from source)
#   weather-tool + reservation-tool (from rossoctl/examples, run locally)
#   skillberry-store (from source, rossoctl-tools plugin active)
#   an "agent pod" = the skill fetched exactly as the skill-fetcher init container
#     does, then its scripts run with the env vars rossoctl injected into the
#     Deployment it created.
#
# Off cluster, rossoctl's backend computes tool URLs as
# http://<tool>.<domain>:8080 (the gateway route), so a small local gateway on
# :8080 routes <tool>.localtest.me (which resolves to 127.0.0.1) to the tool.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STORE_REPO=${STORE_REPO:-$(cd "$HERE/../../.." && pwd)}
ROSSOCTL_REPO=${ROSSOCTL_REPO:?set ROSSOCTL_REPO to a rossoctl checkout (rossoctl-skills branch)}
EXAMPLES_REPO=${EXAMPLES_REPO:?set EXAMPLES_REPO to a rossoctl/examples checkout}
export KUBECONFIG=${KUBECONFIG:-/tmp/kwok/kubeconfig}
WORK=${WORK:-/tmp/demo/run}
LOGS=$WORK/logs
STORE_PORT=8000      # the store keeps its default port
BACKEND_PORT=8010
WEATHER_PORT=18001
RESERVATION_PORT=18002
mkdir -p "$LOGS"

log() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
wait_http() { for _ in $(seq 1 120); do curl -fsS -o /dev/null "$1" 2>/dev/null && return 0; sleep 1; done; echo "timeout waiting for $1"; return 1; }
wait_tcp() { for _ in $(seq 1 120); do (exec 3<>/dev/tcp/127.0.0.1/"$1") 2>/dev/null && return 0; sleep 1; done; echo "timeout waiting for port $1"; return 1; }

# Each service runs in its own process group (set -m), so cleanup also stops
# the grandchildren (uv → python, make → uvicorn).
set -m
cleanup() { for p in ${PIDS:-}; do kill -- -"$p" 2>/dev/null || kill "$p" 2>/dev/null || true; done; }
trap cleanup EXIT
PIDS=""
for p in $STORE_PORT $BACKEND_PORT 8080 $WEATHER_PORT $RESERVATION_PORT; do
  if (exec 3<>/dev/tcp/127.0.0.1/"$p") 2>/dev/null; then echo "port $p is in use; stop whatever listens there first"; exit 1; fi
done

# ----------------------------------------------------------------------------
log "1. Tools: weather-tool and reservation-tool (rossoctl/examples)"
(cd "$EXAMPLES_REPO/mcp/weather_tool" && PORT=$WEATHER_PORT HOST=127.0.0.1 OTEL_SDK_DISABLED=true \
   uv run --frozen weather_tool.py >"$LOGS/weather.log" 2>&1) & PIDS="$PIDS $!"
(cd "$EXAMPLES_REPO/mcp/reservation_tool" && PORT=$RESERVATION_PORT HOST=127.0.0.1 \
   uv run --frozen reservation_tool.py >"$LOGS/reservation.log" 2>&1) & PIDS="$PIDS $!"
wait_tcp $WEATHER_PORT; wait_tcp $RESERVATION_PORT

# The cluster objects rossoctl looks at: Deployment + <tool>-mcp Service +
# AgentRuntime per tool, as .github/scripts/operator/72-/73-deploy-*.sh create
# them, with the Service port set to the local port.
deploy_tool() {  # ns name port
  kubectl apply -f - >/dev/null <<EOF
apiVersion: apps/v1
kind: Deployment
metadata:
  name: $2
  namespace: $1
  labels: {rossoctl.io/type: tool, protocol.rossoctl.io/mcp: "", rossoctl.io/transport: streamable_http, app.kubernetes.io/name: $2}
spec:
  replicas: 1
  selector: {matchLabels: {app.kubernetes.io/name: $2}}
  template:
    metadata: {labels: {rossoctl.io/type: tool, app.kubernetes.io/name: $2}}
    spec: {containers: [{name: mcp, image: ghcr.io/rossoctl/examples/${2//-/_}:latest}]}
---
apiVersion: v1
kind: Service
metadata:
  name: $2-mcp
  namespace: $1
  labels: {rossoctl.io/type: tool, app.kubernetes.io/name: $2}
spec:
  selector: {app.kubernetes.io/name: $2}
  ports: [{name: http, port: $3, targetPort: $3}]
---
apiVersion: agent.rossoctl.dev/v1alpha1
kind: AgentRuntime
metadata: {name: $2, namespace: $1}
spec: {type: tool, targetRef: {apiVersion: apps/v1, kind: Deployment, name: $2}}
EOF
}
# kwok has no kubelets: register a fake node that kwok's controller manages, so
# pods are scheduled and become Ready as on a real cluster.
kubectl apply -f - >/dev/null <<'NODE'
apiVersion: v1
kind: Node
metadata:
  name: kwok-node-0
  annotations: {kwok.x-k8s.io/node: fake}
  labels: {type: kwok, kubernetes.io/hostname: kwok-node-0}
NODE
for ns in team1 team2 rossoctl-system; do kubectl create ns $ns --dry-run=client -o yaml | kubectl apply -f - >/dev/null; done
kubectl label ns team1 team2 rossoctl-enabled=true --overwrite >/dev/null
deploy_tool team1 weather-tool $WEATHER_PORT
deploy_tool team1 reservation-tool $RESERVATION_PORT
deploy_tool team2 weather-tool $WEATHER_PORT        # team2 has no reservation tool
kubectl -n team1 wait --for=condition=Available deploy/weather-tool deploy/reservation-tool --timeout=90s >/dev/null
echo "team1: weather-tool, reservation-tool; team2: weather-tool only (Deployments Available)"

# ----------------------------------------------------------------------------
log "2. rossoctl backend (from source) with toolSkills on, auth off"
# Off-cluster, rossoctl reaches tools via http://<tool>.<domain>:8080; point the
# domain at a local gateway that routes <tool>.localtest.me:8080 → the tool port.
python3 - >"$LOGS/gateway.log" 2>&1 <<PY &
import asyncio
ROUTES = {"weather-tool": $WEATHER_PORT, "reservation-tool": $RESERVATION_PORT}
async def handle(reader, writer):
    head = await reader.readuntil(b"\r\n\r\n")
    host = next((l.split(b":",1)[1].strip().split(b":")[0].decode() for l in head.split(b"\r\n") if l.lower().startswith(b"host:")), "")
    port = ROUTES.get(host.split(".")[0])
    if not port:
        writer.write(b"HTTP/1.1 404 Not Found\r\ncontent-length: 0\r\n\r\n"); await writer.drain(); writer.close(); return
    ur, uw = await asyncio.open_connection("127.0.0.1", port)
    uw.write(head)
    async def pipe(r, w):
        try:
            while (d := await r.read(65536)): w.write(d); await w.drain()
        finally:
            w.close()
    await asyncio.gather(pipe(reader, uw), pipe(ur, writer), return_exceptions=True)
async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", 8080)
    async with srv: await srv.serve_forever()
asyncio.run(main())
PY
PIDS="$PIDS $!"
wait_tcp 8080
(cd "$ROSSOCTL_REPO/rossoctl/backend" && \
  ROSSOCTL_FEATURE_FLAG_SKILLS=true ROSSOCTL_FEATURE_FLAG_EXTERNAL_SKILLS=true \
  ROSSOCTL_FEATURE_FLAG_TOOL_SKILLS=true ENABLE_AUTH=false DOMAIN_NAME=localtest.me \
  SKILL_AUTOSYNC_INTERVAL=5 SKILL_REGISTRY_ALLOWED_HOSTS=127.0.0.1,localhost \
  uv run --frozen uvicorn app.main:app --host 127.0.0.1 --port $BACKEND_PORT >"$LOGS/backend.log" 2>&1) & PIDS="$PIDS $!"
wait_http "http://127.0.0.1:$BACKEND_PORT/api/v1/config/features"
curl -s "http://127.0.0.1:$BACKEND_PORT/api/v1/config/features" | python3 -c 'import json,sys; d=json.load(sys.stdin); print("features:", {k: d[k] for k in ("skills","externalSkills","toolSkills")})'

# ----------------------------------------------------------------------------
log "3. skillberry-store (from source) with the rossoctl-tools plugin active"
echo '{"disabled": [], "owners": {}}' > "$WORK/plugins.json"
(cd "$STORE_REPO" && source .venv/bin/activate && \
  SBS_BASE_DIR="$WORK/sbs" SKILLBERRY_PLUGIN_CONFIG="$WORK/plugins.json" \
  ROSSOCTL_BACKEND_URL="http://127.0.0.1:$BACKEND_PORT" ROSSOCTL_ALLOWED_NAMESPACES=team1,team2 \
  PROMETHEUS_METRICS_PORT=0 SBS_ACCESS_CONTROL_CONFIG="$STORE_REPO/access_control_config.yaml.disabled" \
  SBS_CLI_DOWNLOAD=off python -m skillberry_store.main >"$LOGS/store.log" 2>&1) & PIDS="$PIDS $!"
wait_http "http://127.0.0.1:$STORE_PORT/health/ready"
STORE=http://127.0.0.1:$STORE_PORT
curl -s "$STORE/plugins/" | python3 -c 'import json,sys; [print("plugin", p["slug"], "enabled" if p["enabled"] else "off", "-", p["status"]) for p in json.load(sys.stdin) if p["slug"]=="rossoctl-tools"]'

# rossoctl's autosync points at this store (the chart seeds the same ConfigMap).
kubectl apply -f - >/dev/null <<EOF
apiVersion: v1
kind: ConfigMap
metadata: {name: rossoctl-skill-autosync-config, namespace: rossoctl-system}
data: {enabled: "true", registry-type: skillberry, registry-url: "$STORE", sync-interval: "5", allowed-tags: "rossoctl-approved"}
EOF

# ----------------------------------------------------------------------------
log "4. Demo step 1: import the tools into the store, with the skill"
python3 - "$STORE" "${INSTRUCTIONS:-$HERE/instructions.md}" <<'PY' | tee "$WORK/import.json"
import json, sys, urllib.request
store, instr = sys.argv[1], open(sys.argv[2]).read()
body = {"namespace": "team1",
        "tools": [{"name": "weather-tool"}, {"name": "reservation-tool"}],
        "skill": {"name": "night-out-planner",
                  "description": "Plan and book a restaurant evening in a city, taking current weather into account. Use when the user wants dinner/lunch suggestions, table availability, or to book or cancel a reservation.",
                  "instructions": instr, "tags": ["rossoctl-approved"]}}
req = urllib.request.Request(f"{store}/plugins/rossoctl-tools/import", json.dumps(body).encode(), {"content-type": "application/json"})
out = json.load(urllib.request.urlopen(req, timeout=120))
print(json.dumps({"tools": [t["name"] for t in out["tools"]], "skill_uuid": out["skill_uuid"], "errors": out["errors"]}, indent=1))
PY

log "5. Demo step 2: store-side execution goes store → rossoctl /invoke → tool"
curl -s -X POST "$STORE/tools/search_restaurants/execute" -H 'content-type: application/json' \
  -d '{"city":"Boston","cuisine":"Italian"}' | python3 -c 'import json,sys; r=json.load(sys.stdin); names=[x["name"] for x in json.loads(r["return value"])]; print("search_restaurants →", names)'
grep -c '"POST /api/v1/tools/team1/reservation-tool/invoke' "$LOGS/backend.log" | sed 's/^/backend \/invoke calls: /'

log "6. Demo step 3: rossoctl autosync creates the skill with its required tools"
for _ in $(seq 1 60); do
  v=$(kubectl -n team1 get cm night-out-planner -o jsonpath='{.metadata.annotations.rossoctl\.io/required-tools}' 2>/dev/null || true)
  [[ -n "$v" ]] && break; sleep 2
done
echo "team1 ConfigMap night-out-planner rossoctl.io/required-tools = $v"
curl -s "http://127.0.0.1:$BACKEND_PORT/api/v1/skills/team1/night-out-planner/tool-requirements" | python3 -m json.tool

log "7. Demo step 4: create the agent (claude_agent image, skill only, no MCP tool)"
create() {  # ns name
  curl -s -X POST "http://127.0.0.1:$BACKEND_PORT/api/v1/agents" -H 'content-type: application/json' -d "{
    \"name\": \"$2\", \"namespace\": \"$1\", \"deploymentMethod\": \"image\",
    \"containerImage\": \"ghcr.io/rossoctl/examples/claude_agent:latest\",
    \"authBridgeEnabled\": false, \"spireEnabled\": false,
    \"skills\": [\"night-out-planner\"],
    \"envVars\": [{\"name\": \"ANTHROPIC_MODEL\", \"value\": \"sonnet\"}]}"
}
create team1 night-out | python3 -m json.tool
echo "--- Deployment env (rossoctl-injected) ---"
kubectl -n team1 get deploy night-out -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for e in d["spec"]["template"]["spec"]["containers"][0]["env"]:
    if e["name"].startswith(("ROSSOCTL_","SKILL_FOLDERS")): print(" ", e["name"], "=", e.get("value"))
print("  annotation rossoctl.io/required-tools =", d["metadata"]["annotations"].get("rossoctl.io/required-tools"))
print("  init containers:", [c["name"] for c in d["spec"]["template"]["spec"].get("initContainers", [])])'

log "8. The agent pod: fetch the skill like the init container, run its scripts with the injected env"
POD=$WORK/agent-pod; rm -rf "$POD"; mkdir -p "$POD/app/skills/night-out-planner"
curl -fsSL -o "$POD/skill.zip" "$STORE/skills/night-out-planner/export-anthropic"
(cd "$POD" && python3 -c 'import zipfile; zipfile.ZipFile("skill.zip").extractall("x")' && cp -r x/night-out-planner/. app/skills/night-out-planner/)
ls "$POD/app/skills/night-out-planner" "$POD/app/skills/night-out-planner/scripts"
# Env exactly as rossoctl wrote it into the Deployment. The backend runs off
# cluster here, so it injected its off-cluster tool URLs
# (http://<tool>.localtest.me:8080/mcp), which the local gateway serves.
eval "$(kubectl -n team1 get deploy night-out -o json | python3 -c '
import json,sys
d=json.load(sys.stdin)
for e in d["spec"]["template"]["spec"]["containers"][0]["env"]:
    if e["name"].startswith("ROSSOCTL_"):
        print("export %s=%r" % (e["name"], e["value"]))')"
env | grep -E "^ROSSOCTL_(TOOL_|NAMESPACE)" | sed "s/^/  /"
S=$POD/app/skills/night-out-planner/scripts
run() { echo "\$ python3 scripts/$*"; (cd "$POD/app/skills/night-out-planner" && python3 -I "scripts/$@"); echo "  (exit $?)"; }
set +e
run search_restaurants.py --city Boston --cuisine Italian | head -c 600; echo
run get_weather.py --city Boston
RID=$(cd "$POD/app/skills/night-out-planner" && python3 -I scripts/search_restaurants.py --city Boston --cuisine Italian | python3 -c 'import json,sys; print(json.load(sys.stdin)[0]["id"])')
echo "\$ python3 scripts/check_availability.py --restaurant_id $RID --date_time 2026-10-26T19:00:00 --party_size 4"
(cd "$POD/app/skills/night-out-planner" && python3 -I scripts/check_availability.py --restaurant_id "$RID" --date_time 2026-10-26T19:00:00 --party_size 4) | python3 -c "import json,sys; s=json.load(sys.stdin); print(\"  19:00 available:\", [x[\"available\"] for x in s if x[\"time\"].endswith(\"19:00:00\")], \"| free dinner slots:\", [x[\"time\"][11:16] for x in s if x[\"available\"] and x[\"time\"][11:13] >= \"17\"])"
run place_reservation.py --restaurant_id "$RID" --date_time 2026-10-26T19:30:00 --party_size 4 \
    --name "Ada Lovelace" --phone 555-0100 --email ada@example.com
run list_reservations.py --user_id ada@example.com | head -c 400; echo
set -e

log "9. Negative and portability cases"
echo "--- team2 (no reservation-tool): warn ---"
for _ in $(seq 1 30); do kubectl -n team2 get cm night-out-planner >/dev/null 2>&1 && break; sleep 2; done
create team2 night-out | python3 -c 'import json,sys; r=json.load(sys.stdin); print("success:", r.get("success"), "warnings:", r.get("warnings"))'
echo "--- the same skill's proxy in team2 for the missing tool: exit 3 ---"
(cd "$POD/app/skills/night-out-planner" && env -u ROSSOCTL_TOOL_RESERVATION_TOOL_URL ROSSOCTL_NAMESPACE=team2 ROSSOCTL_TOOL_TIMEOUT=3 \
   python3 -I scripts/search_restaurants.py --city Boston; echo "  (exit $?)") || true
echo "--- deploy reservation-tool in team2: the unchanged skill now resolves there ---"
deploy_tool team2 reservation-tool $RESERVATION_PORT
kubectl -n team2 delete deploy/night-out svc/night-out agentruntime/night-out --ignore-not-found --wait=true >/dev/null 2>&1 || true
create team2 night-out | python3 -c 'import json,sys; r=json.load(sys.stdin); print("success:", r.get("success"), "warnings:", r.get("warnings"))'
kubectl -n team2 get deploy night-out -o jsonpath='{.metadata.annotations.rossoctl\.io/required-tools}'; echo

log "Demo complete. Logs: $LOGS"
