# 07 — snippets_api.py

CRUD and search for snippets, the content files skills are built from.

| Method | Path | What it does |
|--------|------|--------------|
| POST | `/snippets/` | Creates a new snippet in the store. |
| GET | `/snippets/` | Lists snippets, with optional filter, sort, pagination and field projection. |
| GET | `/snippets/{uuid_or_name}` | Returns one snippet's metadata, looked up by UUID or name. |
| DELETE | `/snippets/{uuid_or_name}` | Deletes a snippet from the store. |
| PUT | `/snippets/{uuid_or_name}` | Updates an existing snippet's metadata and content. |
| GET | `/facets/snippets` | Returns the distinct tags, namespaces and states across all snippets. |
| GET | `/search/snippets` | Semantic search over snippets (`search_term`, `max_number_of_results`, `similarity_threshold`, ...). |

## Live call

```console
$ curl -s "http://localhost:8000/search/snippets?search_term=pdf&max_number_of_results=2"
[{"uuid":"405f52f5-b558-4478-925f-1183550d871d","version":"1.0.0","state":"approved",
  "tags":["file:reference.md","skill:pdf","md","anthropic","namespace:default"],"name":"reference",
  "description":"# PDF Processing Advanced Reference","content_type":"text/plain","similarity_score":0.6972935199737549},
 {"uuid":"7aac6624-6e93-4ca6-90ae-137cfd833dea","version":"1.0.0","state":"approved",
  "tags":["file:SKILL.md","skill:pdf","md","anthropic","namespace:default"],"name":"SKILL",
  "description":"# PDF Processing Guide","content_type":"text/plain","similarity_score":0.6228460073471069}]
```

Status: 200. The response is one line of JSON, wrapped here.
