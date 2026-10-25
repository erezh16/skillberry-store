"""The plugin inside a full in-process store, against the fake rossoctl backend.

Covers /import, /check, /resync, /tools, store-side Execute, export
(rossoctl-tools.json, frontmatter, proxy steps), the computed field, the
import claimer round trip, and the behavior with the plugin disabled.
"""

import io
import json
import zipfile

from skillberry_store.tools.anthropic.mcp_proxy import canonical_schema_sha256, extract_spec

from rossoctl_fake_backend import SEARCH_SCHEMA, WEATHER_SCHEMA

SKILL = {
    "name": "night-out-planner",
    "description": "Plan and book a restaurant evening.",
    "instructions": "# Night-out planner\n\nUse the scripts.",
    "tags": ["rossoctl-approved"],
}


def _import(client, tools=("weather-tool", "reservation-tool"), skill=SKILL):
    body = {"namespace": "team1", "tools": [{"name": t} for t in tools]}
    if skill:
        body["skill"] = skill
    r = client.post("/plugins/rossoctl-tools/import", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _export(client, name="night-out-planner"):
    r = client.get(f"/skills/{name}/export-anthropic")
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def test_tools_passthrough(store_client):
    r = store_client.get("/plugins/rossoctl-tools/tools", params={"namespace": "team1"})
    assert r.status_code == 200
    assert {i["name"] for i in r.json()["items"]} >= {"weather-tool", "reservation-tool"}


def test_import_creates_proxy_tools_and_skill(store_client, fake):
    out = _import(store_client)
    assert out["errors"] == []
    assert [t["name"] for t in out["tools"]] == ["get_weather", "search_restaurants", "place_reservation"]
    weather = store_client.get("/tools/get_weather", params={"fields": "full"}).json()
    assert weather["packaging_format"] == "mcp"
    assert weather["packaging_params"] == {
        "mcp_tool_name": "get_weather",
        "endpoint": {"provider": "rossoctl", "tool": "weather-tool", "namespace": "team1", "port": 8000, "path": "/mcp"},
        "input_schema_sha256": canonical_schema_sha256(WEATHER_SCHEMA),
    }
    assert weather["params"]["required"] == ["city"]
    assert set(weather["tags"]) >= {"mcp", "rossoctl-tool:weather-tool", "namespace:team1"}
    assert weather["extra"]["mcp_annotations"] == {"readOnlyHint": True}
    skill = store_client.get("/skills/night-out-planner", params={"fields": "wide"}).json()
    assert skill["uuid"] == out["skill_uuid"]
    assert len(skill["tool_uuids"]) == 3 and len(skill["snippet_uuids"]) == 1
    assert "rossoctl-approved" in skill["tags"]
    # Only the backend and the token endpoint were contacted.
    assert {r["host"] for r in fake.requests} == {"rossoctl-backend.test", "keycloak.test"}
    assert all(r["auth"] for r in fake.requests if r["host"] == "rossoctl-backend.test")


def test_import_errors(store_client):
    r = store_client.post("/plugins/rossoctl-tools/import", json={"namespace": "team1", "tools": [{"name": "missing-tool"}]})
    assert r.status_code == 404
    r = store_client.post("/plugins/rossoctl-tools/import", json={"namespace": "team1", "tools": [{"name": "unreachable-tool"}]})
    assert r.status_code == 502
    r = store_client.post("/plugins/rossoctl-tools/import", json={"namespace": "Bad_NS", "tools": [{"name": "x"}]})
    assert r.status_code == 400
    out = _import(store_client, tools=("weather-tool", "missing-tool"), skill=None)
    assert [t["name"] for t in out["tools"]] == ["get_weather"]
    assert out["errors"][0]["tool"] == "missing-tool"


def test_import_prefixes_on_collision_only(store_client):
    _import(store_client, tools=("reservation-tool",), skill=None)
    out = _import(store_client, tools=("other-search", "reservation-tool"), skill=None)
    names = [t["name"] for t in out["tools"]]
    assert names[0] == "other_search__search_restaurants"
    # reservation-tool's own tools keep their names on re-import.
    assert "search_restaurants" in names
    other = store_client.get("/tools/other_search__search_restaurants", params={"fields": "full"}).json()
    assert other["packaging_params"]["mcp_tool_name"] == "search_restaurants"
    assert other["packaging_params"]["endpoint"]["port"] == 9090


def test_store_side_execute_goes_through_invoke(store_client, fake):
    _import(store_client, skill=None)
    r = store_client.post("/tools/search_restaurants/execute", json={"city": "Boston", "cuisine": "Italian"})
    assert r.status_code == 200, r.text
    assert "Trattoria di Mare" in r.json()["return value"]
    invoke = [x for x in fake.requests if x["path"].endswith("/invoke")]
    assert invoke and invoke[-1]["path"] == "/api/v1/tools/team1/reservation-tool/invoke"
    r = store_client.post("/tools/get_weather/execute", json={"city": "Atlantis"})
    assert r.status_code == 500
    assert "City Atlantis not found" in r.json()["detail"]


def test_export_package(store_client):
    _import(store_client)
    zf = _export(store_client)
    names = set(zf.namelist())
    assert {
        "night-out-planner/SKILL.md",
        "night-out-planner/rossoctl-tools.json",
        "night-out-planner/scripts/get_weather.py",
        "night-out-planner/scripts/search_restaurants.py",
        "night-out-planner/scripts/place_reservation.py",
    } <= names
    md = zf.read("night-out-planner/SKILL.md").decode()
    assert "rossoctl-requires-tools: reservation-tool,weather-tool" in md
    assert "# Night-out planner" in md
    assert "## Tools" in md and "### Tools (rossoctl)" in md
    doc = json.loads(zf.read("night-out-planner/rossoctl-tools.json"))
    assert doc == {
        "schema": "skillberry.ai/rossoctl-tools/v1",
        "tools": [
            {
                "tool": "reservation-tool",
                "port": 8000,
                "path": "/mcp",
                "mcp_tools": [
                    {"name": "place_reservation", "script": "scripts/place_reservation.py",
                     "schema_sha256": doc["tools"][0]["mcp_tools"][0]["schema_sha256"], "read_only": False},
                    {"name": "search_restaurants", "script": "scripts/search_restaurants.py",
                     "schema_sha256": canonical_schema_sha256(SEARCH_SCHEMA), "read_only": False},
                ],
            },
            {
                "tool": "weather-tool",
                "port": 8000,
                "path": "/mcp",
                "mcp_tools": [
                    {"name": "get_weather", "script": "scripts/get_weather.py",
                     "schema_sha256": canonical_schema_sha256(WEATHER_SCHEMA), "read_only": True},
                ],
            },
        ],
    }
    spec = extract_spec(zf.read("night-out-planner/scripts/get_weather.py").decode())
    assert spec["steps"][0]["var"] == "ROSSOCTL_TOOL_WEATHER_TOOL_URL"
    assert "svc.cluster.local" in spec["steps"][-1]["template"]


def test_computed_field(store_client):
    _import(store_client)
    r = store_client.get("/skills/", params={"fields": "uuid,name,description,version,tags,_rossoctl_required_tools"})
    assert r.status_code == 200, r.text
    (skill,) = [s for s in r.json() if s["name"] == "night-out-planner"]
    assert skill["_rossoctl_required_tools"] == ["reservation-tool", "weather-tool"]
    assert set(skill) <= {"uuid", "name", "description", "version", "tags", "_rossoctl_required_tools"}
    assert "tool_uuids" not in skill  # widened only to compute, then stripped
    r = store_client.get("/skills/night-out-planner", params={"fields": "uuid,name,_rossoctl_required_tools"})
    assert r.json() == {"uuid": skill["uuid"], "name": "night-out-planner",
                        "_rossoctl_required_tools": ["reservation-tool", "weather-tool"]}
    r = store_client.get("/skills/", params={"fields": "full"})
    assert all("_rossoctl_required_tools" not in s for s in r.json())


def test_check_and_resync(store_client, fake):
    out = _import(store_client)
    r = store_client.post("/plugins/rossoctl-tools/check", json={"skill": "night-out-planner"})
    assert r.status_code == 200, r.text
    assert [t["drift"] for t in r.json()["tools"]] == [False, False, False]
    # The live weather tool gains a parameter.
    fake.tools["team1/weather-tool"]["mcp"][0]["input_schema"] = {
        **WEATHER_SCHEMA, "properties": {**WEATHER_SCHEMA["properties"], "units": {"type": "string"}}
    }
    report = store_client.post("/plugins/rossoctl-tools/check", json={"skill": "night-out-planner"}).json()
    drift = {t["name"]: t["drift"] for t in report["tools"]}
    assert drift == {"get_weather": True, "search_restaurants": False, "place_reservation": False}
    r = store_client.post("/plugins/rossoctl-tools/resync", json={"skill": "night-out-planner"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [t["name"] for t in body["tools"]] == ["get_weather"]
    new_skill = store_client.get(f"/skills/{body['skill_uuid']}", params={"fields": "wide"}).json()
    old_weather = next(t["uuid"] for t in out["tools"] if t["name"] == "get_weather")
    assert body["tools"][0]["uuid"] in new_skill["tool_uuids"]
    assert old_weather not in new_skill["tool_uuids"]
    head = store_client.get("/tools/get_weather", params={"fields": "full"}).json()
    assert "units" in head["params"]["properties"]
    again = store_client.post("/plugins/rossoctl-tools/check", json={"skill": new_skill["uuid"]}).json()
    assert not any(t["drift"] for t in again["tools"])


def test_reimport_of_exported_package_is_lossless(store_client):
    _import(store_client)
    data = _export(store_client)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for name in data.namelist():
            out.writestr(name.replace("night-out-planner/", "night-out-copy/"), data.read(name))
    # Rename the skill so the copy is a new skill.
    zf = zipfile.ZipFile(io.BytesIO(buf.getvalue()))
    files = {n: zf.read(n) for n in zf.namelist()}
    files["night-out-copy/SKILL.md"] = files["night-out-copy/SKILL.md"].replace(b"name: night-out-planner", b"name: night-out-copy")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        for n, c in files.items():
            out.writestr(n, c)
    r = store_client.post(
        "/skills/import-anthropic",
        data={"source_type": "zip"},
        files={"zip_file": ("night-out-copy.zip", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["tools_created"] == 3
    copy = store_client.get("/skills/night-out-copy", params={"fields": "full"}).json()
    by_name = {t["name"]: t for t in copy["tools"]}
    assert set(by_name) == {"get_weather", "search_restaurants", "place_reservation"}
    assert by_name["get_weather"]["packaging_params"]["endpoint"] == {
        "provider": "rossoctl", "tool": "weather-tool", "namespace": "team1", "port": 8000, "path": "/mcp"
    }
    assert by_name["get_weather"]["extra"]["mcp_annotations"] == {"readOnlyHint": True}
    # No duplicate generated sections and no stray JSON snippet.
    snippet_names = {s["name"] for s in copy["snippets"]}
    assert "rossoctl-tools" not in snippet_names
    re_export = _export(store_client, "night-out-copy")
    md = re_export.read("night-out-copy/SKILL.md").decode()
    assert md.count("## Tools\n") == 1 and md.count("### Tools (rossoctl)") == 1
    assert md.count("rossoctl-requires-tools") == 1
    assert json.loads(re_export.read("night-out-copy/rossoctl-tools.json")) == json.loads(
        data.read("night-out-planner/rossoctl-tools.json")
    )


def test_plugin_disabled_behaviour(store_client):
    """§4.3: with the plugin inactive the store still holds and exports the tools."""
    _import(store_client)
    store_client.loader.set_enabled("rossoctl-tools", False)
    assert store_client.post("/plugins/rossoctl-tools/import", json={"namespace": "team1", "tools": [{"name": "x"}]}).status_code == 404
    r = store_client.post("/tools/get_weather/execute", json={"city": "Boston"})
    assert r.status_code == 422 and "needs provider 'rossoctl'" in r.json()["detail"]
    r = store_client.get("/skills/", params={"fields": "name,_rossoctl_required_tools"})
    assert r.status_code == 400
    zf = _export(store_client)
    names = zf.namelist()
    assert "night-out-planner/rossoctl-tools.json" not in names
    md = zf.read("night-out-planner/SKILL.md").decode()
    assert "rossoctl-requires-tools" not in md and "Tools (rossoctl)" not in md
    spec = extract_spec(zf.read("night-out-planner/scripts/get_weather.py").decode())
    assert spec["steps"] == [{"kind": "env", "var": "MCP_PROXY_GET_WEATHER_URL"}]
    # Creating such a tool is still accepted (generic shape check only).
    r = store_client.get("/tools/get_weather", params={"fields": "full"})
    assert r.status_code == 200
    store_client.loader.set_enabled("rossoctl-tools", True)
