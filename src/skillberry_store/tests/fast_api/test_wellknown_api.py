"""Integration tests for the npx discovery endpoints — docs/design/npx.md §6.5.

Two deployment shapes, because they are the two that matter:

* ``mode: disabled`` — the bare dev checkout, where ``{ref}`` is the slug;
* ``mode: standalone`` running the **shipped** ``access_control_config.yaml.standalone``
  (the ``skillberry`` / ``skillberry-admin`` demo file), which is what proves the
  feature works on a deployment like the live demo — one that answers
  ``401 missing_authorization`` for ``GET /skills/`` today.

The standalone fixture uses the real file rather than a hand-written YAML, with
exactly one edit: ``npx_publish`` flipped on, because the shipped file
deliberately ships it off and the operator is meant to opt in (§5.12). Everything
else — the roles, the bindings, the allow-list — is the file as it ships, so a
change there that forgets the npx paths is caught here.

Several assertions mirror the CLI's own validators rather than our intent: what
matters is not that the index looks reasonable but that ``skills@1.6.0`` accepts
it, since every rejection is *silent* (§1.3, §1.4).
"""

from __future__ import annotations

import hashlib
import io
import re
import textwrap
import zipfile
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from skillberry_store.access_control import config as acl_config
from skillberry_store.access_control.sessions import SessionStore
from skillberry_store.tests.utils import clean_test_tmp_dir

REPO_ROOT = Path(__file__).resolve().parents[4]
STANDALONE_CFG = REPO_ROOT / "access_control_config.yaml.standalone"
DISABLED_CFG = REPO_ROOT / "access_control_config.yaml"

SCHEMA_V2 = "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
PUBLIC_URL = "http://store.test"

INDEX = "/pub/{ref}/.well-known/agent-skills/index.json"
ARTIFACT = "/pub/{ref}/.well-known/agent-skills/{slug}.zip"

# The convention's second index spelling, which the store deliberately does NOT
# route: the CLI probes it only after the first fails, and the first always
# answers. Kept here as the subject of an assertion, not as a URL we serve.
UNSERVED_ALIAS = "/pub/{ref}/.well-known/skills/index.json"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
def _build_client(cfg_path, monkeypatch, tmp_path) -> TestClient:
    """A fresh SBS() over a clean store, pointed at ``cfg_path``."""
    from skillberry_store.modules import object_handler
    from skillberry_store.services import registry
    from skillberry_store.fast_api.server import SBS

    monkeypatch.setenv("SBS_ACCESS_CONTROL_CONFIG", str(cfg_path))
    monkeypatch.setenv("SBS_PUBLIC_URL", PUBLIC_URL)
    monkeypatch.setenv("SBS_WELLKNOWN_SECRET", "test-publish-secret")
    monkeypatch.setenv(
        "SBS_WELLKNOWN_SECRET_FILE", str(tmp_path / "wellknown_secret.json")
    )
    acl_config.reset_config_cache()
    clean_test_tmp_dir()
    object_handler.clear_object_handlers()
    registry.clear_services()
    return TestClient(SBS())


def _teardown() -> None:
    from skillberry_store.modules import object_handler
    from skillberry_store.services import registry

    object_handler.clear_object_handlers()
    registry.clear_services()
    acl_config.reset_config_cache()
    from skillberry_store.tools.wellknown import get_cache

    get_cache().clear()


@pytest.fixture
def acl_client(tmp_path, monkeypatch):
    """``build(yaml_text)`` → a TestClient over a caller-supplied ACL config."""

    def build(yaml_text: str) -> TestClient:
        path = tmp_path / "acl.yaml"
        path.write_text(textwrap.dedent(yaml_text))
        return _build_client(path, monkeypatch, tmp_path)

    yield build
    _teardown()


@pytest.fixture
def disabled_client(tmp_path, monkeypatch):
    """The shipped ``mode: disabled`` config, which ships npx publishing ON."""
    client = _build_client(DISABLED_CFG, monkeypatch, tmp_path)
    yield client
    _teardown()


def _standalone_yaml(npx_publish: bool) -> str:
    """The shipped demo config, with only ``npx_publish`` changed."""
    source = STANDALONE_CFG.read_text()
    replacement = f"npx_publish: {'true' if npx_publish else 'false'}"
    updated, count = re.subn(r"^npx_publish:.*$", replacement, source, flags=re.M)
    assert count == 1, "access_control_config.yaml.standalone lost npx_publish"
    return updated


@pytest.fixture
def skillberry_demo_client(tmp_path, monkeypatch):
    """A TestClient running the *shipped* skillberry/skillberry-admin config.

    Uses the real file, edited only to opt in to npx publishing: if someone adds
    an endpoint to ``access_control_config.yaml.standalone`` without allow-listing
    the ``/pub/*`` paths, this fixture is what catches it.
    """
    path = tmp_path / "standalone.yaml"
    path.write_text(_standalone_yaml(npx_publish=True))
    client = _build_client(path, monkeypatch, tmp_path)
    yield client
    _teardown()


@pytest.fixture
def npx_off_client(tmp_path, monkeypatch):
    """The shipped demo config exactly as it ships — publishing off."""
    client = _build_client(STANDALONE_CFG, monkeypatch, tmp_path)
    yield client
    _teardown()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _auth(client: TestClient, tenant: str, groups=()) -> dict:
    token, _ = client.app.state.acl_sessions.mint(tenant, list(groups), 600)
    return {"Authorization": f"Bearer {token}"}


def _create_snippet(client, name, content, tags=None, headers=None):
    resp = client.post(
        "/snippets/",
        params={"name": name, "content": content, "tags": tags or []},
        headers=headers or {},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["uuid"]


def _create_skill(client, name, description="A skill.", headers=None, **params):
    resp = client.post(
        "/skills/",
        params={"name": name, "description": description, **params},
        headers=headers or {},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["uuid"]


def _update_skill(client, name, headers=None, **fields):
    """PUT a skill's manifest (a JSON body, unlike the query-param POST)."""
    resp = client.put(
        f"/skills/{name}", json={"name": name, **fields}, headers=headers or {}
    )
    assert resp.status_code == 200, resp.text


def _install_ref(client, skill_name, headers=None):
    """The ``{ref}`` from the skill's own ``_npx_install`` command."""
    resp = client.get(
        f"/skills/{skill_name}",
        params={"fields": "name,_npx_install"},
        headers=headers or {},
    )
    assert resp.status_code == 200, resp.text
    command = resp.json().get("_npx_install")
    if command is None:
        return None
    url = command.split(" add ", 1)[1].split(" ", 1)[0]
    assert url.startswith(f"{PUBLIC_URL}/pub/")
    return url.rsplit("/", 1)[1]


# --------------------------------------------------------------------------- #
# CLI-fidelity validators — mirrored from the published bundle, not invented
# --------------------------------------------------------------------------- #
_NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
_DIGEST_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


def _assert_cli_valid(index: dict) -> None:
    """Assert ``index`` passes every v0.2.0 rule in ``isValidSkillEntryV2``."""
    assert index["$schema"] == SCHEMA_V2
    assert isinstance(index["skills"], list)
    for entry in index["skills"]:
        name = entry["name"]
        assert _NAME_RE.match(name), name
        assert not name.startswith("-") and not name.endswith("-"), name
        assert "--" not in name, name
        assert isinstance(entry["description"], str) and entry["description"]
        assert len(entry["description"]) <= 1024
        assert entry["type"] in ("skill-md", "archive")
        assert isinstance(entry["url"], str) and entry["url"]
        assert _DIGEST_RE.match(entry["digest"]), entry["digest"]


def _assert_cli_installable(payload: bytes) -> dict:
    """Assert the archive is one the CLI will extract, and return its frontmatter."""
    assert len(payload) <= 50 * 1024 * 1024
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        names = zf.namelist()
        assert len(names) <= 1000
        assert "SKILL.md" in names, names
        for info in zf.infolist():
            path = info.filename
            assert not path.startswith("/"), path
            assert ".." not in path.split("/"), path
            assert "\\" not in path, path
            assert "\x00" not in path, path
            assert not (len(path) > 1 and path[1] == ":"), path
            mode = info.external_attr >> 16
            assert mode & 0o170000 != 0o120000, path  # no symlinks
        content = zf.read("SKILL.md").decode("utf-8")
    assert content.startswith("---\n")
    frontmatter = yaml.safe_load(content.split("---\n")[1])
    assert isinstance(frontmatter.get("name"), str) and frontmatter["name"]
    assert isinstance(frontmatter.get("description"), str) and frontmatter["description"]
    return frontmatter


# =========================================================================== #
# mode: disabled — the slug is the whole reference
# =========================================================================== #
def test_disabled_mode_publishes_by_slug(disabled_client):
    _create_skill(disabled_client, "PDF Forms", "Fill and flatten PDF forms.")
    resp = disabled_client.get(INDEX.format(ref="pdf-forms"))
    assert resp.status_code == 200, resp.text
    _assert_cli_valid(resp.json())
    assert [e["name"] for e in resp.json()["skills"]] == ["pdf-forms"]


def test_disabled_mode_index_is_json_never_html(disabled_client):
    """§5.3 #11: an HTML body is parsed as an index and silently yields zero."""
    _create_skill(disabled_client, "demo")
    for path in (INDEX.format(ref="demo"), INDEX.format(ref="no-such-skill")):
        resp = disabled_client.get(path)
        assert "application/json" in resp.headers["content-type"], path
        assert "text/html" not in resp.headers["content-type"], path


def test_disabled_mode_unknown_slug_is_a_json_404(disabled_client):
    resp = disabled_client.get(INDEX.format(ref="no-such-skill"))
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_disabled_mode_artifact_digest_matches_the_index(disabled_client):
    _create_skill(disabled_client, "demo", "Does things.")
    index = disabled_client.get(INDEX.format(ref="demo")).json()
    entry = index["skills"][0]
    resp = disabled_client.get(ARTIFACT.format(ref="demo", slug="demo"))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "sha256:" + hashlib.sha256(resp.content).hexdigest() == entry["digest"]
    _assert_cli_installable(resp.content)


def test_disabled_mode_supports_the_explicit_scope_spellings(disabled_client):
    """With no auth layer the ref *is* the scope; a bare slug is the shorthand."""
    _create_skill(disabled_client, "alpha", "A.")
    _create_skill(disabled_client, "beta", "B.", tags=["namespace:data-eng"])

    for ref, expected in [
        ("alpha", {"alpha"}),
        ("skill:alpha", {"alpha"}),
        ("ns:data-eng", {"beta"}),
        ("*", {"alpha", "beta"}),
    ]:
        resp = disabled_client.get(INDEX.format(ref=ref))
        assert resp.status_code == 200, (ref, resp.text)
        _assert_cli_valid(resp.json())
        assert {e["name"] for e in resp.json()["skills"]} == expected, ref


def test_disabled_mode_rejects_an_unknown_namespace(disabled_client):
    _create_skill(disabled_client, "alpha", "A.")
    assert disabled_client.get(INDEX.format(ref="ns:nope")).status_code == 404


def test_disabled_mode_install_command_is_slug_addressed(disabled_client):
    _create_skill(disabled_client, "PDF Forms", "Fill forms.")
    resp = disabled_client.get(
        "/skills/PDF Forms", params={"fields": "name,_npx_install"}
    )
    assert resp.json()["_npx_install"] == (
        f"npx skills add {PUBLIC_URL}/pub/pdf-forms -y -a claude-code"
    )


# =========================================================================== #
# mode: standalone, the shipped demo config (§6.5 #1-#27)
# =========================================================================== #
def test_01_fixture_really_enabled_acl(skillberry_demo_client):
    assert skillberry_demo_client.app.state.acl_cfg.mode == "standalone"
    assert skillberry_demo_client.app.state.acl_cfg.npx_publish is True


def test_02_the_store_is_genuinely_locked_down(skillberry_demo_client):
    resp = skillberry_demo_client.get("/skills/")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_authorization"


@pytest.fixture
def demo_with_skill(skillberry_demo_client):
    """The demo store with one skill, plus ``skillberry``'s install ref."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    snippet = _create_snippet(
        client, "spec", "# spec", tags=["file:reference/spec.md"], headers=admin
    )
    _create_skill(
        client,
        "PDF Forms",
        "Fill and flatten PDF forms.",
        headers=admin,
        snippet_uuids=[snippet],
    )
    ref = _install_ref(client, "PDF Forms", headers=_auth(client, "skillberry"))
    assert ref
    return client, ref


def test_03_index_reachable_with_no_token(demo_with_skill):
    client, ref = demo_with_skill
    resp = client.get(INDEX.format(ref=ref))
    assert resp.status_code == 200, resp.text
    assert "application/json" in resp.headers["content-type"]
    _assert_cli_valid(resp.json())


def test_04_the_second_index_spelling_is_deliberately_not_served(demo_with_skill):
    """The store answers one index URL, not two.

    The CLI's candidate list contains ``/.well-known/skills/index.json`` as well,
    but it is tried *sequentially* — only when the first candidate fails to parse.
    Since the first always answers, routing the second would be public surface no
    supported client ever reaches. This pins that decision: if someone re-adds the
    route, they have to come here and say why.

    A 404 is also a well-behaved answer in its own right — the CLI skips a
    non-2xx candidate silently — so nothing breaks for a client that does probe it
    first and then falls through to the spelling we serve.
    """
    client, ref = demo_with_skill
    assert client.get(INDEX.format(ref=ref)).status_code == 200
    resp = client.get(UNSERVED_ALIAS.format(ref=ref))
    assert resp.status_code == 404
    # Still JSON, never HTML: an HTML body would be parsed as an index and
    # silently yield zero skills (§5.3 #11).
    assert "application/json" in resp.headers["content-type"]


def test_04b_only_two_pub_routes_exist(skillberry_demo_client):
    """The whole public npx surface, enumerated — so growth is deliberate."""
    from skillberry_store.access_control.audit import walk_api_routes

    paths = sorted(
        w.path for w in walk_api_routes(skillberry_demo_client.app)
        if w.path.startswith("/pub/")
    )
    assert paths == [
        "/pub/{ref}/.well-known/agent-skills/index.json",
        "/pub/{ref}/.well-known/agent-skills/{slug}.zip",
    ]


def test_04c_the_pub_routes_are_absent_from_the_openapi_schema(skillberry_demo_client):
    """No generated SDK method and no ``sbs`` command for either route.

    Both the Python SDK (``openapi-generator-cli generate -i .../openapi.json``)
    and the ``sbs`` CLI (restish, over the same schema) are generated from
    ``/openapi.json``, so ``include_in_schema=False`` is what keeps these two out
    of both. They exist for npx and for nothing else: a generated
    ``get_wellknown_index(ref=...)`` would be a client method whose only correct
    argument is a capability token, and a `sbs` command for it would invite
    exactly the confusion that the token is not a session credential.

    Asserted rather than assumed, because the property rests on one keyword
    argument per route that nothing else would miss if it were dropped.
    """
    spec = skillberry_demo_client.app.openapi()
    assert [p for p in spec.get("paths", {}) if p.startswith("/pub")] == []

    # Nor may they carry the markers that name a CLI command or an MCP tool.
    from skillberry_store.access_control.audit import walk_api_routes

    for walked in walk_api_routes(skillberry_demo_client.app):
        if not walked.path.startswith("/pub/"):
            continue
        extra = walked.route.openapi_extra or {}
        assert walked.route.include_in_schema is False, walked.path
        assert "x-cli-name" not in extra, walked.path
        assert "x-mcp-tool" not in extra, walked.path


def test_05_artifact_reachable_with_no_token_and_digest_matches(demo_with_skill):
    client, ref = demo_with_skill
    entry = client.get(INDEX.format(ref=ref)).json()["skills"][0]
    resp = client.get(ARTIFACT.format(ref=ref, slug=entry["name"]))
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    assert "sha256:" + hashlib.sha256(resp.content).hexdigest() == entry["digest"]


def test_06_and_07_every_entry_and_artifact_satisfies_the_cli(demo_with_skill):
    client, ref = demo_with_skill
    index = client.get(INDEX.format(ref=ref)).json()
    _assert_cli_valid(index)
    for entry in index["skills"]:
        payload = client.get(ARTIFACT.format(ref=ref, slug=entry["name"])).content
        frontmatter = _assert_cli_installable(payload)
        # §5.8 #1: the frontmatter name is the slug, matching the directory the
        # CLI will create.
        assert frontmatter["name"] == entry["name"]


def test_08_unknown_artifact_is_a_json_404(demo_with_skill):
    client, ref = demo_with_skill
    resp = client.get(ARTIFACT.format(ref=ref, slug="no-such-skill"))
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}
    assert "application/json" in resp.headers["content-type"]


def test_09_the_feature_does_not_disturb_the_existing_tenant(demo_with_skill):
    client, ref = demo_with_skill
    assert client.get("/skills/", headers=_auth(client, "skillberry")).status_code == 200
    assert client.get(INDEX.format(ref=ref)).status_code == 200


def test_10_the_wellknown_route_did_not_widen_base_user(demo_with_skill):
    """The one that matters most: `base-user` still has no `skills:create`."""
    client, _ = demo_with_skill
    resp = client.post(
        "/skills/", params={"name": "sneaky"}, headers=_auth(client, "skillberry")
    )
    assert resp.status_code == 403


def test_11_a_per_skill_url_returns_exactly_that_one_skill(demo_with_skill):
    client, ref = demo_with_skill
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "Release Notes", "Notes.", headers=admin)

    index = client.get(INDEX.format(ref=ref)).json()
    assert [e["name"] for e in index["skills"]] == ["pdf-forms"]


def test_11b_both_tenants_see_the_same_skills(demo_with_skill):
    """Visibility is binary: `base-user` and `admin` see the same set (§4.3.1)."""
    client, _ = demo_with_skill
    seen = {
        tenant: {
            s["name"]
            for s in client.get(
                "/skills/", params={"fields": "narrow"}, headers=_auth(client, tenant)
            ).json()
        }
        for tenant in ("skillberry", "skillberry-admin")
    }
    assert seen["skillberry"] == seen["skillberry-admin"]


def test_12_with_publishing_off_no_route_exists(npx_off_client):
    client = npx_off_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)

    assert client.app.state.acl_cfg.npx_publish is False
    assert client.get(INDEX.format(ref="demo")).status_code == 404
    assert client.get("/pub/anything").status_code == 404
    paths = {getattr(r, "path", "") for r in client.app.routes}
    assert not any(p.startswith("/pub/") for p in paths), sorted(paths)
    # And the rest of the store is untouched.
    assert client.get("/skills/").status_code == 401
    assert client.get("/skills/", headers=admin).status_code == 200


def test_12b_with_publishing_off_no_install_command_is_emitted(npx_off_client):
    client = npx_off_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    resp = client.get("/skills/demo", params={"fields": "name,_npx_install"}, headers=admin)
    assert resp.status_code == 200
    assert "_npx_install" not in resp.json()


def test_13_a_draft_skill_is_publishable(skillberry_demo_client):
    """§4.3.1: no state gate. A `state: new` draft is already visible to every
    holder of `skills:list`, so excluding it would only misreport the store."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "draft", "A draft.", headers=admin, state="new")
    ref = _install_ref(client, "draft", headers=_auth(client, "skillberry"))
    index = client.get(INDEX.format(ref=ref)).json()
    assert [e["name"] for e in index["skills"]] == ["draft"]


def test_14_the_rbac_audit_passed(skillberry_demo_client):
    """``audit_rbac_coverage`` runs in ``SBS.__init__`` and refuses to boot on a
    marker/allow-list mismatch — so a constructed app *is* the assertion. Made
    explicit here because the well-known routes deliberately carry no marker."""
    from skillberry_store.access_control.audit import (
        audit_rbac_coverage,
        walk_api_routes,
    )

    app = skillberry_demo_client.app
    audit_rbac_coverage(app, app.state.acl_cfg)
    pub = [w for w in walk_api_routes(app) if w.path.startswith("/pub/")]
    assert pub, "the /pub routes should be registered"
    for walked in pub:
        extra = walked.route.openapi_extra or {}
        assert "x-rbac-resource" not in extra, walked.path
        assert walked.route.include_in_schema is False, walked.path


def test_15_learning_an_install_url_requires_a_session(skillberry_demo_client):
    client = skillberry_demo_client
    _create_skill(client, "demo", "D.", headers=_auth(client, "skillberry-admin"))
    resp = client.get("/skills/demo", params={"fields": "name,_npx_install"})
    assert resp.status_code == 401


def test_16_the_url_she_is_given_works_with_no_bearer(demo_with_skill):
    client, ref = demo_with_skill
    resp = client.get(INDEX.format(ref=ref))
    assert resp.status_code == 200
    assert len(resp.json()["skills"]) == 1
    assert resp.json()["skills"][0]["name"] == "pdf-forms"


def test_17_the_url_survives_rebuilding_the_session_store(demo_with_skill):
    """Derived, not session-backed: `SessionStore` is in-memory and the URL is
    replayed by every `npx skills update` (§4.3.3)."""
    client, ref = demo_with_skill
    client.app.state.acl_sessions = SessionStore()
    assert client.get(INDEX.format(ref=ref)).status_code == 200


def test_18_rotating_the_secret_yields_404_not_403(demo_with_skill):
    client, ref = demo_with_skill
    assert client.get(INDEX.format(ref=ref)).status_code == 200
    client.app.state.npx._secret = b"a-rotated-secret"
    resp = client.get(INDEX.format(ref=ref))
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_19_a_tenant_without_skills_list_gets_no_install_url(acl_client):
    client = acl_client(
        """
        mode: standalone
        npx_publish: true
        standalone:
          users:
            - username: getter
              tenant_id: getter
              password_hash: "$2b$12$notused"
            - username: lister
              tenant_id: lister
              password_hash: "$2b$12$notused"
        roles:
          - name: getter-only
            rules:
              - resources: [skills]
                verbs: [get, create]
          - name: full-reader
            rules:
              - resources: [skills]
                verbs: [list, get, create]
        bindings:
          - name: b-getter
            subjects: [{kind: tenant, name: getter}]
            roles: [getter-only]
          - name: b-lister
            subjects: [{kind: tenant, name: lister}]
            roles: [full-reader]
        """
    )
    _create_skill(client, "demo", "D.", headers=_auth(client, "lister"))

    # `lister` holds skills:list, so she gets a URL and it works.
    ref = _install_ref(client, "demo", headers=_auth(client, "lister"))
    assert ref and client.get(INDEX.format(ref=ref)).status_code == 200

    # `getter` may read the skill but not list, so no URL is offered.
    assert _install_ref(client, "demo", headers=_auth(client, "getter")) is None


def test_19b_a_token_stops_working_when_its_tenant_loses_skills_list(acl_client):
    """Re-authorized per request, not at issue time (§4.3.3)."""
    client = acl_client(
        """
        mode: standalone
        npx_publish: true
        standalone:
          users:
            - username: alice
              tenant_id: alice
              password_hash: "$2b$12$notused"
        roles:
          - name: reader
            rules:
              - resources: [skills]
                verbs: [list, get, create]
        bindings:
          - name: b-alice
            subjects: [{kind: tenant, name: alice}]
            roles: [reader]
        """
    )
    _create_skill(client, "demo", "D.", headers=_auth(client, "alice"))
    ref = _install_ref(client, "demo", headers=_auth(client, "alice"))
    assert client.get(INDEX.format(ref=ref)).status_code == 200

    client.app.state.acl_cfg.bindings.clear()
    assert client.get(INDEX.format(ref=ref)).status_code == 404


def test_20_the_token_is_never_resolvable_as_a_session_bearer(demo_with_skill):
    """§4.3.4: the PEP resolves bearers only through ``SessionStore``."""
    client, ref = demo_with_skill
    headers = {"Authorization": f"Bearer {ref}"}
    assert client.get("/skills/", headers=headers).status_code == 401
    assert client.get("/skills/PDF Forms", headers=headers).status_code == 401
    assert client.get("/auth/whoami", headers=headers).status_code == 401


def test_21_one_skills_token_cannot_open_another_skills_url(skillberry_demo_client):
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "alpha", "A.", headers=admin)
    _create_skill(client, "beta", "B.", headers=admin)
    base = _auth(client, "skillberry")
    ref_a = _install_ref(client, "alpha", headers=base)
    ref_b = _install_ref(client, "beta", headers=base)
    assert ref_a != ref_b

    assert {
        e["name"] for e in client.get(INDEX.format(ref=ref_a)).json()["skills"]
    } == {"alpha"}
    assert client.get(ARTIFACT.format(ref=ref_a, slug="beta")).status_code == 404
    assert client.get(ARTIFACT.format(ref=ref_a, slug="alpha")).status_code == 200


def test_22_the_artifact_url_is_relative_and_inherits_the_same_gate(demo_with_skill):
    client, ref = demo_with_skill
    index_path = INDEX.format(ref=ref)
    index = client.get(index_path).json()
    entry = index["skills"][0]

    # The index body carries no credential — only the URL the user pasted does.
    assert ref not in client.get(index_path).text
    assert entry["url"] == "pdf-forms.zip"

    # Resolved relative to the index URL, the artifact lands under /pub/{ref}/.
    resolved = index_path.rsplit("/", 1)[0] + "/" + entry["url"]
    assert resolved == ARTIFACT.format(ref=ref, slug="pdf-forms")
    assert client.get(resolved).status_code == 200

    # Fetching it under a different ref does not work.
    assert (
        client.get(ARTIFACT.format(ref="wrong-ref", slug="pdf-forms")).status_code == 404
    )


def test_23_every_visible_skill_has_a_working_install_url(skillberry_demo_client):
    """The product requirement and the security assertion at once: everything
    she can see she can install, and no URL returns anything else (§4.3.8)."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    for name in ("alpha", "Beta Skill", "gamma_delta"):
        _create_skill(client, name, f"{name} does things.", headers=admin)

    base = _auth(client, "skillberry")
    listing = client.get("/skills/", params={"fields": "narrow"}, headers=base).json()
    visible = {s["name"] for s in listing}
    assert visible == {"alpha", "Beta Skill", "gamma_delta"}

    installed = set()
    for name in visible:
        ref = _install_ref(client, name, headers=base)
        assert ref, name
        index = client.get(INDEX.format(ref=ref)).json()
        assert len(index["skills"]) == 1, name
        _assert_cli_valid(index)
        payload = client.get(
            ARTIFACT.format(ref=ref, slug=index["skills"][0]["name"])
        ).content
        installed.add(_assert_cli_installable(payload)["name"])

    # Each URL installed exactly one distinct skill, and nothing outside the
    # listing appeared.
    assert len(installed) == len(visible)


def test_24_a_superseded_version_offers_no_install_url(skillberry_demo_client):
    """An install URL always installs the HEAD, so offering one from an old
    version would hand out a command that installs something else (§5.10 #7)."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    old = _create_skill(client, "demo", "v1.", headers=admin)
    new = _create_skill(client, "demo", "v2.", headers=admin)
    assert old != new

    base = _auth(client, "skillberry")
    head = client.get(
        f"/skills/{new}", params={"fields": "uuid,_npx_install"}, headers=base
    ).json()
    superseded = client.get(
        f"/skills/{old}", params={"fields": "uuid,_npx_install"}, headers=base
    ).json()
    assert "_npx_install" in head
    assert "_npx_install" not in superseded


def test_24b_repeated_creates_publish_exactly_one_entry(skillberry_demo_client):
    """§5.5: ``list_all()`` would publish one entry per revision, all slugging
    identically. (An ``update`` would not exercise this — it mutates one uuid.)"""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    uuids = [_create_skill(client, "demo", f"v{i}.", headers=admin) for i in range(3)]

    ref = _install_ref(client, "demo", headers=_auth(client, "skillberry"))
    index = client.get(INDEX.format(ref=ref)).json()
    assert [e["name"] for e in index["skills"]] == ["demo"]

    from skillberry_store.services.registry import get_service

    head = get_service("skill").handler.name_cache.get_head("demo")
    assert head == uuids[-1]
    assert index["skills"][0]["description"] == "v2."


def test_25_a_current_digest_selector_serves_those_bytes(demo_with_skill):
    client, ref = demo_with_skill
    entry = client.get(INDEX.format(ref=ref)).json()["skills"][0]
    resp = client.get(
        ARTIFACT.format(ref=ref, slug=entry["name"]), params={"digest": entry["digest"]}
    )
    assert resp.status_code == 200
    assert "sha256:" + hashlib.sha256(resp.content).hexdigest() == entry["digest"]


def test_26_an_unknown_digest_selector_is_honoured_not_ignored(demo_with_skill):
    """A miss must 404 rather than quietly substituting the current bytes —
    that substitution is the very failure the selector exists to prevent."""
    client, ref = demo_with_skill
    resp = client.get(
        ARTIFACT.format(ref=ref, slug="pdf-forms"),
        params={"digest": "sha256:" + "0" * 64},
    )
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


def test_26b_a_superseded_digest_still_resolves_from_cache(demo_with_skill):
    """What makes §5.14 option B a one-line change later: the bytes a digest
    names stay fetchable after an edit, so the index/artifact race closes."""
    client, ref = demo_with_skill
    before = client.get(INDEX.format(ref=ref)).json()["skills"][0]

    _update_skill(
        client,
        "PDF Forms",
        headers=_auth(client, "skillberry-admin"),
        description="Now edited.",
    )

    after = client.get(INDEX.format(ref=ref)).json()["skills"][0]
    assert after["digest"] != before["digest"]

    old = client.get(
        ARTIFACT.format(ref=ref, slug="pdf-forms"), params={"digest": before["digest"]}
    )
    assert old.status_code == 200
    assert "sha256:" + hashlib.sha256(old.content).hexdigest() == before["digest"]


def test_27_without_a_digest_selector_an_edit_wins_the_race(demo_with_skill):
    """Documents accepted behaviour for §5.14 option A rather than pretending
    the window does not exist: with no selector the artifact serves *current*
    bytes, so an edit landing between the two fetches makes the CLI drop the
    skill. The user re-runs one command and it works."""
    client, ref = demo_with_skill
    published = client.get(INDEX.format(ref=ref)).json()["skills"][0]["digest"]

    _update_skill(
        client,
        "PDF Forms",
        headers=_auth(client, "skillberry-admin"),
        description="Edited mid-install.",
    )

    served = client.get(ARTIFACT.format(ref=ref, slug="pdf-forms")).content
    assert "sha256:" + hashlib.sha256(served).hexdigest() != published


# =========================================================================== #
# Namespace and global scopes (§4.3.9) — opt-in, not the default
# =========================================================================== #
def test_a_namespace_scoped_index_contains_only_that_namespace(skillberry_demo_client):
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "in-ns", "A.", headers=admin, tags=["namespace:data-eng"])
    _create_skill(client, "other-ns", "B.", headers=admin, tags=["namespace:web"])
    _create_skill(client, "no-ns", "C.", headers=admin)

    from skillberry_store.tools import publish_tokens as tokens

    publisher = client.app.state.npx
    ref = publisher.ref_for_scope(tokens.namespace_scope("data-eng"), "skillberry")
    index = client.get(INDEX.format(ref=ref)).json()
    _assert_cli_valid(index)
    assert {e["name"] for e in index["skills"]} == {"in-ns"}
    assert client.get(ARTIFACT.format(ref=ref, slug="in-ns")).status_code == 200
    assert client.get(ARTIFACT.format(ref=ref, slug="other-ns")).status_code == 404


def test_an_unknown_namespace_token_does_not_fall_back_to_everything(
    skillberry_demo_client,
):
    """The CLI's own ``WellKnownScopeNotFoundError`` exists to stop exactly this
    (§1.7); here the token simply does not resolve."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "alpha", "A.", headers=admin)

    from skillberry_store.tools import publish_tokens as tokens

    publisher = client.app.state.npx
    ref = publisher.ref_for_scope(tokens.namespace_scope("no-such-ns"), "skillberry")
    assert client.get(INDEX.format(ref=ref)).status_code == 404


def test_the_global_scope_publishes_every_visible_skill(skillberry_demo_client):
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    for name in ("alpha", "beta"):
        _create_skill(client, name, f"{name}.", headers=admin)

    from skillberry_store.tools import publish_tokens as tokens

    ref = client.app.state.npx.ref_for_scope(tokens.SCOPE_ALL, "skillberry")
    index = client.get(INDEX.format(ref=ref)).json()
    _assert_cli_valid(index)
    assert {e["name"] for e in index["skills"]} == {"alpha", "beta"}
    for entry in index["skills"]:
        assert client.get(ARTIFACT.format(ref=ref, slug=entry["name"])).status_code == 200


def test_a_global_token_for_an_unconfigured_tenant_does_not_resolve(
    skillberry_demo_client,
):
    from skillberry_store.tools import publish_tokens as tokens

    client = skillberry_demo_client
    _create_skill(client, "alpha", "A.", headers=_auth(client, "skillberry-admin"))
    # `plugin-user` is a virtual subject with no `standalone.users` entry, so it
    # is never a resolution candidate (§5.10 #4).
    ref = tokens.publish_token(
        client.app.state.npx.secret, "plugin-user", tokens.SCOPE_ALL
    )
    assert client.get(INDEX.format(ref=ref)).status_code == 404


def test_the_namespace_allowlist_restricts_which_namespaces_resolve(
    skillberry_demo_client, monkeypatch
):
    """``SBS_WELLKNOWN_NAMESPACES`` (§4.3): when set, only those namespaces
    publish, so a store can expose one pack without exposing the rest."""
    from skillberry_store.tools import publish_tokens as tokens
    from skillberry_store.tools.wellknown import NAMESPACES_ENV_VAR

    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "public", "A.", headers=admin, tags=["namespace:data-eng"])
    _create_skill(client, "private", "B.", headers=admin, tags=["namespace:secret"])

    publisher = client.app.state.npx
    open_ref = publisher.ref_for_scope(tokens.namespace_scope("data-eng"), "skillberry")
    shut_ref = publisher.ref_for_scope(tokens.namespace_scope("secret"), "skillberry")

    monkeypatch.setenv(NAMESPACES_ENV_VAR, "data-eng")
    assert {
        e["name"] for e in client.get(INDEX.format(ref=open_ref)).json()["skills"]
    } == {"public"}
    assert client.get(INDEX.format(ref=shut_ref)).json()["skills"] == []

    # A per-skill URL is unaffected: the allowlist scopes namespaces, not skills.
    per_skill = _install_ref(client, "private", headers=_auth(client, "skillberry"))
    assert client.get(INDEX.format(ref=per_skill)).status_code == 200


def test_an_internal_tagged_skill_is_marked_but_still_installable(disabled_client):
    """§4.3: the tag is a per-skill "don't offer this in a list" flag the CLI
    honours; the skill's own URL must keep working."""
    from skillberry_store.tools.wellknown import INTERNAL_TAG

    _create_skill(disabled_client, "hidden", "H.", tags=[INTERNAL_TAG])
    _create_skill(disabled_client, "shown", "S.")

    assert disabled_client.get(INDEX.format(ref="hidden")).status_code == 200
    payload = disabled_client.get(ARTIFACT.format(ref="hidden", slug="hidden")).content
    assert _assert_cli_installable(payload)  # frontmatter still valid

    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        frontmatter = yaml.safe_load(zf.read("SKILL.md").decode().split("---\n")[1])
    assert frontmatter["metadata"]["internal"] is True

    # It is still in a multi-entry index; hiding it is the client's job.
    index = disabled_client.get(INDEX.format(ref="*")).json()
    assert {e["name"] for e in index["skills"]} == {"hidden", "shown"}


def test_update_polls_are_counted_separately_from_installs(disabled_client):
    """§4.4: `npx skills update` sets X-Skills-Update-Check, so an operator can
    tell polling from real installs."""
    from skillberry_store.fast_api.wellknown_api import (
        UPDATE_CHECK_HEADER,
        wellknown_index_counter,
    )

    def count(label):
        return wellknown_index_counter.labels(update_check=label)._value.get()

    _create_skill(disabled_client, "demo", "D.")
    before_install, before_poll = count("false"), count("true")

    disabled_client.get(INDEX.format(ref="demo"))
    disabled_client.get(INDEX.format(ref="demo"), headers={UPDATE_CHECK_HEADER: "1"})

    assert count("false") == before_install + 1
    assert count("true") == before_poll + 1


def test_artifact_downloads_are_counted(disabled_client):
    from skillberry_store.fast_api.wellknown_api import wellknown_artifact_counter

    _create_skill(disabled_client, "demo", "D.")
    before = wellknown_artifact_counter._value.get()
    disabled_client.get(ARTIFACT.format(ref="demo", slug="demo"))
    assert wellknown_artifact_counter._value.get() == before + 1


# =========================================================================== #
# The `_npx_install` field's hygiene contract (§4.3.5)
# =========================================================================== #
@pytest.mark.parametrize("preset", ["minimal", "narrow", "wide", "full"])
def test_no_preset_ever_returns_an_install_command(skillberry_demo_client, preset):
    """``fields="full"`` is used internally; none of those paths should start
    returning a credential."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    for path in ("/skills/", "/skills/demo"):
        resp = client.get(path, params={"fields": preset}, headers=admin)
        assert resp.status_code == 200, (path, preset, resp.text)
        assert "_npx_install" not in resp.text, (path, preset)


def test_list_may_carry_install_commands_when_asked(skillberry_demo_client):
    """Each value grants only its own skill, so a per-skill token on `list` is
    self-limiting — which is what makes the scripted bulk install a one-liner
    (§4.3.8)."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    for name in ("alpha", "beta"):
        _create_skill(client, name, f"{name}.", headers=admin)

    resp = client.get(
        "/skills/", params={"fields": "name,_npx_install"}, headers=admin
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 2
    commands = {i["name"]: i["_npx_install"] for i in items}
    assert commands["alpha"] != commands["beta"]
    for name, command in commands.items():
        ref = command.split("/pub/", 1)[1].split(" ", 1)[0]
        index = client.get(INDEX.format(ref=ref)).json()
        assert [e["name"] for e in index["skills"]] == [name]


def test_asking_for_the_field_alone_returns_only_that_field(skillberry_demo_client):
    """The identity keys the command is composed from are widened in and
    narrowed out again, so the response shape stays the caller's choice."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    resp = client.get(
        "/skills/demo", params={"fields": "_npx_install"}, headers=admin
    )
    assert resp.status_code == 200
    assert set(resp.json()) == {"_npx_install"}


def test_explicitly_requested_identity_fields_are_not_stripped(skillberry_demo_client):
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    resp = client.get(
        "/skills/demo", params={"fields": "uuid,name,_npx_install"}, headers=admin
    )
    assert set(resp.json()) == {"uuid", "name", "_npx_install"}


def test_get_and_list_emit_byte_identical_commands(skillberry_demo_client):
    """One helper, every surface — so `sbs get-skill` and `sbs list-skills`
    cannot drift (§4.3.5)."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    one = client.get(
        "/skills/demo", params={"fields": "name,_npx_install"}, headers=admin
    ).json()["_npx_install"]
    many = client.get(
        "/skills/", params={"fields": "name,_npx_install"}, headers=admin
    ).json()[0]["_npx_install"]
    assert one == many


def test_the_agent_picker_changes_which_agent_is_pinned(skillberry_demo_client):
    """The UI sends its agent rather than rewriting the command, so the string
    has exactly one author (§4.3.5)."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)

    def command(agent=None):
        params = {"fields": "_npx_install"}
        if agent is not None:
            params["npx_agent"] = agent
        return client.get("/skills/demo", params=params, headers=admin).json()[
            "_npx_install"
        ]

    assert command().endswith("-y -a claude-code")
    assert command("cursor").endswith("-y -a cursor")
    assert command("codex").endswith("-y -a codex")


@pytest.mark.parametrize(
    "agent",
    ["", "Claude Code", "a; rm -rf /", "-rf", "x" * 60, "a b", "a$(whoami)"],
)
def test_an_unusable_agent_name_falls_back_rather_than_reaching_a_shell(
    skillberry_demo_client, agent
):
    """The command is pasted into a shell, so nothing arbitrary may reach it —
    and `-a` is never dropped, since `-y` with no agent installs into ~75
    directories (§4.3.1)."""
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    command = client.get(
        "/skills/demo",
        params={"fields": "_npx_install", "npx_agent": agent},
        headers=admin,
    ).json()["_npx_install"]
    assert command.endswith("-y -a claude-code"), command
    assert agent not in command or agent in ("", "-rf")


def test_the_agent_is_ignored_when_the_field_was_not_asked_for(skillberry_demo_client):
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    _create_skill(client, "demo", "D.", headers=admin)
    resp = client.get(
        "/skills/demo", params={"fields": "narrow", "npx_agent": "cursor"}, headers=admin
    )
    assert resp.status_code == 200
    assert "_npx_install" not in resp.json()


def test_an_unknown_fields_token_is_still_rejected(skillberry_demo_client):
    client = skillberry_demo_client
    admin = _auth(client, "skillberry-admin")
    resp = client.get("/skills/", params={"fields": "_npx_instal"}, headers=admin)
    assert resp.status_code == 400


def test_without_a_public_url_the_command_is_omitted_not_guessed(
    tmp_path, monkeypatch
):
    """§5.11 option 3: a wrong URL is worse than an absent one — but a directly
    reachable server may still derive one from the request."""
    from skillberry_store.fast_api.wellknown_api import NpxPublisher

    cfg = type("Cfg", (), {"mode": "disabled", "npx_publish": True})()
    publisher = NpxPublisher(cfg, public_url=None)
    assert publisher.base_url(None) is None
    assert publisher.install_command(None, object(), {"uuid": "u", "name": "n"}) is None

    publisher = NpxPublisher(cfg, public_url="https://store.example.com/")
    assert publisher.base_url(None) == "https://store.example.com"


def test_a_request_base_url_is_the_documented_fallback(disabled_client, monkeypatch):
    """Precedence 2 (§5.11): correct for a directly-reachable server."""
    _create_skill(disabled_client, "demo", "D.")
    disabled_client.app.state.npx.public_url = None
    resp = disabled_client.get("/skills/demo", params={"fields": "_npx_install"})
    assert resp.status_code == 200
    command = resp.json()["_npx_install"]
    assert "/pub/demo -y -a claude-code" in command
    assert command.startswith("npx skills add http")
    assert "//pub/" not in command


# =========================================================================== #
# Unpublishable skills (§5.7)
# =========================================================================== #
def test_a_skill_with_an_unsafe_file_tag_is_not_published(disabled_client, caplog):
    """One bad ``file:`` tag would abort extraction of the whole archive, so the
    skill is excluded and the operator told which tag did it."""
    import logging

    snippet = _create_snippet(
        disabled_client, "bad", "x", tags=["file:../../escape.txt"]
    )
    _create_skill(disabled_client, "unsafe", "U.", snippet_uuids=[snippet])
    _create_skill(disabled_client, "safe", "S.")

    with caplog.at_level(logging.WARNING, logger="skillberry_store.tools.wellknown"):
        resp = disabled_client.get(INDEX.format(ref="unsafe"))
    assert resp.status_code == 404
    assert "file:../../escape.txt" in caplog.text

    # A multi-entry index skips it and keeps serving the rest.
    index = disabled_client.get(INDEX.format(ref="*")).json()
    assert {e["name"] for e in index["skills"]} == {"safe"}


def test_a_name_with_no_valid_slug_is_not_published(disabled_client):
    _create_skill(disabled_client, "!!!", "No slug.")
    _create_skill(disabled_client, "ok", "Fine.")
    index = disabled_client.get(INDEX.format(ref="*")).json()
    assert {e["name"] for e in index["skills"]} == {"ok"}
    resp = disabled_client.get("/skills/!!!", params={"fields": "_npx_install"})
    assert resp.status_code == 200
    assert "_npx_install" not in resp.json()


def test_an_empty_namespace_index_is_an_empty_list_not_a_404(disabled_client):
    """`npx skills update` reads an emptied index as "deleted upstream" and
    offers to remove the local copy, which is the behaviour we want."""
    uuid = _create_skill(disabled_client, "only", "O.", tags=["namespace:temp"])
    assert disabled_client.get(INDEX.format(ref="ns:temp")).status_code == 200
    resp = disabled_client.delete(f"/skills/{uuid}")
    assert resp.status_code == 200, resp.text
    # The namespace no longer exists at all once its last skill is gone.
    assert disabled_client.get(INDEX.format(ref="ns:temp")).status_code == 404
