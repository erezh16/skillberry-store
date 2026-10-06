# 03 — cli_api.py

Serves the native `sbs` CLI binary from the store. Unauthenticated in every access-control mode. The route is only registered when `SBS_CLI_DOWNLOAD` is on.

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/cli/download` | Returns the `sbs` executable for `?platform=<goos>-<goarch>` (detected from the request when omitted), as `format=raw` or `archive`. The sha256 comes back in `X-SBS-SHA256`. |
| HEAD | `/cli/download` | Same lookup with no body, so a client can read the sha256, version and size before downloading (not in the OpenAPI schema). |

## Live call

```console
$ curl -s "http://localhost:8000/cli/download?platform=linux-amd64"
{"detail":"not_bundled","platform":"linux-amd64"}
```

Status: **404**. The route exists (the response carries the `vary: Sec-CH-UA-Platform, ...` header the handler sets), but this instance has no CLI artifact bundled for `linux-amd64`.
