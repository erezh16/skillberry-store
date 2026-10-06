# 01 — admin_api.py

Admin, health and housekeeping endpoints (tag `admin`), registered directly on the app with no prefix.

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/admin/metrics` | Passes Prometheus metrics through as plain text, so the UI can read them without CORS problems (503 if the metrics server can't be reached). |
| DELETE | `/admin/purge-all` | Hard-deletes all data: stops VMCP servers, wipes the data directories and resets the vector indexes. This can't be undone. |
| GET | `/admin/backup` | Streams a `.json.zip` backup of all skills, tools, snippets, VMCP and vNFS servers. |
| POST | `/admin/restore` | Purges everything, then restores from an uploaded backup ZIP and restarts the approved servers. |
| GET | `/health` | Liveness probe. Always 200 once the server is listening; `stage`, `checks` and `uptime_seconds` in the body show boot progress. |
| GET | `/changes` | Returns the global mutation counter (`count`), which goes up on every create, update or delete. |
| GET | `/health/ready` | Readiness probe. 200 only after the description stores and semantic encoder are warm; 503 with `Retry-After` until then. |

## Live call

```console
$ curl -s http://localhost:8000/changes
{"count":674}
```

Status: 200.
