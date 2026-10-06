# 05 — publish_api.py

Per-skill discovery endpoints for `npx skills add`. Both are on the ACL unauthenticated allow-list and left out of `/openapi.json`. Every failure returns 404.

| Method | Path | What it does |
|--------|------|--------------|
| GET | `/pub/{ref}/.well-known/agent-skills/index.json` | Discovery index (schema 0.2.0) for the scope `{ref}` resolves to: a capability token in `standalone` mode, a plaintext scope or bare skill slug in `disabled` mode. |
| GET | `/pub/{ref}/.well-known/agent-skills/{slug}.zip` | One skill's archive within `{ref}`'s scope. Optional `?digest=` serves exactly those bytes from cache, or 404. |

## Live call

Access control is off on this instance, so a bare skill slug (`pdf`) works as `{ref}`:

```console
$ curl -s http://localhost:8000/pub/pdf/.well-known/agent-skills/index.json
{"$schema":"https://schemas.agentskills.io/discovery/0.2.0/schema.json","skills":[{"name":"pdf",
"description":"Use this skill whenever the user wants to do anything with PDF files. ...",
"type":"archive","url":"pdf.zip","digest":"sha256:185eb0aa48dc6d60e7ab95771430c6d96ddd73698fa0f334436e3d6adb101b5b"}]}
```

Status: 200. The response is one line of JSON, wrapped and with the description shortened here.
