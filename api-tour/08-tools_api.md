# 08 — tools_api.py

CRUD, module source, execution and search for tools (Python functions the store can run).

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/tools/` | Creates a new tool along with its module file. |
| GET | `/tools/` | Lists tools, with optional filter, sort, pagination and field projection. |
| GET | `/tools/{uuid_or_name}` | Returns one tool's metadata, looked up by UUID or name. |
| GET | `/tools/{uuid_or_name}/module` | Returns the tool's module file content. |
| PUT | `/tools/{uuid_or_name}/module` | Replaces the tool's module file content. |
| DELETE | `/tools/{uuid_or_name}` | Deletes a tool from the store. |
| PUT | `/tools/{uuid_or_name}` | Updates an existing tool's metadata. |
| POST | `/tools/{uuid_or_name}/execute` | Runs the tool with the parameters supplied. |
| GET | `/facets/tools` | Returns the distinct tags, namespaces and states across all tools. |
| GET | `/search/tools` | Semantic search: returns tools similar to a search term. |
| POST | `/tools/add` | Adds a tool from a Python file, reading its parameters from the docstring. |
| POST | `/tools/add_code` | Adds a tool from Python source sent as a string (MCP-friendly). |

## Live call

```console
$ curl -s "http://localhost:8000/tools/?limit=2&fields=name,description,state"
{"items":[{"name":"_load_schema","description":"Function _load_schema","state":"approved"},
 {"name":"redlining","description":"Validator for tracked changes in Word documents. ...","state":"approved"}],
 "total":350,"offset":0,"limit":2}
```

Status: 200. The response is wrapped and the second description shortened here. The store holds 350 tools.
