# Health and readiness probes

Skillberry Store exposes two probes. They run the same checks and always agree
on the assessment — what differs is the **status code**, because the two kinds of
caller want opposite things from one.

| Endpoint | Answers | Status while booting | Point this at |
| --- | --- | --- | --- |
| `GET /health` | "Is this process alive?" | **200** | Render health check path, Kubernetes `livenessProbe`, ALB/ELB target group, `docker HEALTHCHECK`, uptime monitors |
| `GET /health/ready` | "Can it serve content requests yet?" | **503** | Kubernetes `readinessProbe`, load-balancer pool membership, test harnesses that must not assert before the store is warm |

Both are unauthenticated in every access-control mode (`_ALWAYS_UNAUTH_PATHS` in
[`access_control/config.py`](../src/skillberry_store/access_control/config.py)) —
a platform health check has no token to offer, and a user who cannot sign in yet
is exactly the one whose deployment is being probed.

## Why `/health` must never fail during a boot

A platform health check reads a non-2xx as "this instance is broken, replace it".
If the probe fails *because the store is still initialising*, the platform kills
the instance mid-boot, the replacement enters the same window, and the service
restart-loops without any boot ever getting to finish. Render on the free plan
shows this clearly: several restarts before one finally sticks.

So `/health` reports liveness in the status code and **progress in the body**:

```console
$ curl -s http://localhost:8000/health | jq
{
  "status": "healthy",
  "stage": "initializing",
  "checks": {
    "tool": true,
    "snippet": true,
    "skill": true,
    "vmcp": true,
    "vnfs": true,
    "encoder_warmup": false
  },
  "uptime_seconds": 4.182
}
```

`200` means "do not restart me". `stage` means "but do not expect semantic
search yet". Keeping those two answers in separate fields is the whole design.

### Stages

| `stage` | Meaning |
| --- | --- |
| `initializing` | At least one startup gate is still open. Normal and transient — **not** a failure. |
| `operational` | Every gate is closed. Semantic search and content requests are served in full. |

`checks` names the individual gates so a boot that is taking too long can be
diagnosed rather than just observed. The slow one is almost always
`encoder_warmup`: the ~80 MB ONNX sentence-encoder model being loaded (and, on
an image built without egress to HuggingFace, downloaded). The container image
pre-seeds those weights at build time — see the `encoder_cache_dir` note in the
[Dockerfile](../Dockerfile) — so a deployment running the published image should
reach `operational` quickly.

`uptime_seconds` is measured from a monotonic clock, so it cannot go backwards if
the host clock is stepped.

## Readiness

`GET /health/ready` returns `200` with `"status": "ready"` only once `stage` is
`operational`. Until then it returns **`503 Service Unavailable`** with a
`Retry-After: 5` header and the same assessment under `detail`:

```console
$ curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health/ready
503
```

503 rather than 500: a store that has not finished booting is *temporarily
unable to serve*, which an orchestrator should act on by waiting. 500 says
"server fault", which many act on by restarting — the loop above.

Readiness gates on the description stores and on the encoder warmup. It
deliberately does **not** gate on CLI artifact preparation: a store whose `sbs`
download is not stamped yet is fully functional for everything else, and gating
on it would hold a healthy store out of the load balancer over a download
convenience (see [`new_cli.md` §5.3](design/new_cli.md)).

## Configuring a platform

### Render

Set the service's **Health Check Path** to `/health`.

```yaml
# render.yaml
services:
  - type: web
    name: skillberry-store
    runtime: image
    image:
      url: <your-registry>/skillberry-store:latest
    healthCheckPath: /health          # NOT /health/ready
    envVars:
      - key: SBS_PUBLIC_URL
        value: https://<your-service>.onrender.com
```

With `/health/ready` as the path, Render polls an endpoint that answers `503`
throughout a normal boot and restarts the instance before it can warm up. With
`/health`, the first poll after the port opens succeeds, and the store is left
alone to finish initialising.

Two things worth knowing beyond the probe:

* **The port is closed until the app object is built.** Uvicorn binds only after
  `SBS.__init__` has loaded the access-control config, initialised the object
  handlers, discovered plugins, audited RBAC and mounted the Control MCP — about
  3 s on a warm developer machine, longer on a shared-CPU free instance. No HTTP
  probe can answer during that window; the platform sees a closed socket, not an
  error. This is within Render's port-detection window, but it is the reason a
  health check alone cannot make a boot look instant.
* **Memory.** Peak RSS after initialisation plus encoder warmup measures ~414 MB
  on an empty store. Render's free plan caps at 512 MB, so a store with real
  content can be OOM-killed — which also presents as "restarted several times".
  Check the instance's memory metrics before concluding the probe is at fault.

### Kubernetes

```yaml
livenessProbe:
  httpGet: { path: /health, port: 8000 }
  # Generous, because this probe's only job is to catch a wedged process.
  initialDelaySeconds: 10
  periodSeconds: 20
  failureThreshold: 3
readinessProbe:
  httpGet: { path: /health/ready, port: 8000 }
  initialDelaySeconds: 5
  periodSeconds: 5
```

### Docker

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD curl -fsS http://localhost:8000/health || exit 1
```

## Consuming the stage from a client

A client that needs semantic search polls `/health` and waits for
`stage == "operational"` rather than keying off the status code:

```js
const res = await fetch('/health');
const { stage } = await res.json();
if (stage === 'operational') enableSemanticSearch();
```

From the CLI:

```bash
sbs health          # the stage-aware liveness payload
sbs health-ready    # strict readiness; non-zero exit until operational
```

## Payload compatibility

The fields are additive. `/health` still carries `"status": "healthy"`, and a
ready `/health/ready` still carries `"status": "ready"` alongside its `checks` —
so consumers that only read the status code or that one field are unaffected.

The one behavioural change is the readiness **status code while initialising**:
it was `500`, it is now `503`. Anything asserting specifically on 500 (rather
than on "not 200") needs updating.
