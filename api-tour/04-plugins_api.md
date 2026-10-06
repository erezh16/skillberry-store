# 04 — plugins_api.py

Discover plugins and turn them on or off (RBAC resource `plugins`).

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/plugins/` | Lists every discovered plugin with its metadata (type, version, enabled, has_router/cli/ui). |
| GET | `/plugins/{plugin_name}` | Returns the details of one plugin; 404 if it isn't found. |
| PATCH | `/plugins/{plugin_name}` | Enables or disables a plugin (`{"enabled": bool}`), live and persisted. On enable it records the acting tenant as owner. |

## Live call

```console
$ curl -s http://localhost:8000/plugins/
[]
```

Status: 200. No plugins are installed on this instance.
