# 10 — vnfs_api.py

Virtual NFS servers: each exposes one store skill as a mountable file system over WebDAV (the default) or NFSv3. The API has the same shape as the VMCP one.

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/vnfs_servers/` | Creates a new virtual NFS server. |
| GET | `/facets/vnfs_servers` | Returns the distinct tags, namespaces and states across all vNFS servers. |
| GET | `/vnfs_servers/` | Lists vNFS servers, with optional filter, sort, pagination and field projection. |
| GET | `/vnfs_servers/{uuid_or_name}` | Returns one vNFS server's metadata, looked up by UUID or name. |
| DELETE | `/vnfs_servers/{uuid_or_name}` | Deletes a vNFS server from the store. |
| PUT | `/vnfs_servers/{uuid_or_name}` | Updates an existing vNFS server's metadata. |
| POST | `/vnfs_servers/{uuid_or_name}/start` | Starts or restarts a vNFS server. |
| GET | `/search/vnfs_servers` | Semantic search over vNFS servers. |

## Live call

```console
$ curl -s http://localhost:8000/facets/vnfs_servers
{"tags":[],"namespaces":[],"states":[]}
```

Status: 200. No vNFS servers are defined on this instance.
