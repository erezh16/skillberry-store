# 06 — skills_api.py

CRUD, search and Anthropic-format import/export for skills.

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/skills/` | Creates a new skill in the store. |
| GET | `/skills/` | Lists skills, with optional filter, sort, pagination and field projection. |
| GET | `/skills/{uuid_or_name}` | Returns one skill's metadata, looked up by UUID or name. |
| DELETE | `/skills/{uuid_or_name}` | Deletes a skill, optionally cascading to the items it references. |
| PUT | `/skills/{uuid_or_name}` | Updates an existing skill's metadata. |
| GET | `/facets/skills` | Returns the distinct tags, namespaces and states across all skills. |
| GET | `/search/skills` | Semantic search: returns skills similar to a search term. |
| POST | `/skills/detect-anthropic-skills` | Finds child skill directories inside a parent directory. |
| POST | `/skills/import-anthropic` | Imports an Anthropic-format skill from a GitHub URL, ZIP file or local folder. |
| GET | `/skills/{uuid_or_name}/export-anthropic` | Exports a skill as an Anthropic-format ZIP. |

## Live call

```console
$ curl -s http://localhost:8000/facets/skills
{"tags":["anthropic","imported"],"namespaces":["default"],"states":["approved"]}
```

Status: 200.
