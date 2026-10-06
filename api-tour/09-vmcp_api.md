# 09 — vmcp_api.py

Virtual MCP servers: each is an MCP server created on the fly that exposes one store skill on its own port.

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/vmcp_servers/` | Creates a new virtual MCP server. |
| GET | `/facets/vmcp_servers` | Returns the distinct tags, namespaces and states across all VMCP servers. |
| GET | `/vmcp_servers/` | Lists VMCP servers, with optional filter, sort, pagination and field projection. |
| GET | `/vmcp_servers/{uuid_or_name}` | Returns one VMCP server's metadata, looked up by UUID or name. |
| DELETE | `/vmcp_servers/{uuid_or_name}` | Deletes a VMCP server from the store. |
| PUT | `/vmcp_servers/{uuid_or_name}` | Updates an existing VMCP server's metadata. |
| POST | `/vmcp_servers/{uuid_or_name}/start` | Starts or restarts a VMCP server. |
| GET | `/search/vmcp_servers` | Semantic search over VMCP servers. |

## Live call

```console
$ curl -s "http://localhost:8000/vmcp_servers/?limit=2"
{"items":[],"total":0,"offset":0,"limit":2}
```

Status: 200. No VMCP servers are defined on this instance.
