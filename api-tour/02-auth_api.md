# 02 — auth_api.py

Session login, logout and identity endpoints for the access-control layer.

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/auth/login` | Checks a username and password against the bcrypt hashes in `access_control_config.yaml` and returns a bearer session token (12h TTL by default). |
| POST | `/auth/logout` | Revokes the bearer token on the request. Idempotent: it returns 200 even when the token is missing or unknown. |
| GET | `/auth/whoami` | Returns the caller's identity and the roles bound to it, computed from the bindings loaded when the request arrives. |

## Live call

```console
$ curl -s http://localhost:8000/auth/whoami
{"detail":"auth_disabled"}
```

Status: **503**. This instance runs with access control off, so the auth endpoints answer `auth_disabled`.
