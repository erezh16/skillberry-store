# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""Unit tests for the well-known (npx) publishing layer — docs/design/npx.md §6.3.

Everything here runs against a stub service so the slug / HEAD / digest / cache
contracts are exercised without an app or a store on disk. The HTTP surface is
covered by ``tests/fast_api/test_publish_api.py``.
"""

from __future__ import annotations

import hashlib
import io
import logging
import secrets
import zipfile

import pytest
import yaml

from skillberry_store.tools import publish as pub


# --------------------------------------------------------------------------- #
# A stub SkillsService: name_cache + get + gather_export_inputs, nothing else.
# --------------------------------------------------------------------------- #
class _NameCache:
    def __init__(self, heads):
        self._heads = dict(heads)

    def get_all_names(self):
        return set(self._heads)

    def get_head(self, name):
        return self._heads.get(name)


class _Handler:
    def __init__(self, heads):
        self.name_cache = _NameCache(heads)


class FakeService:
    """Minimal stand-in for ``SkillsService`` over in-memory dicts."""

    def __init__(self, skills, tools=None, snippets=None, modules=None):
        self.skills = {s["uuid"]: s for s in skills}
        self.tools = {t["uuid"]: t for t in (tools or [])}
        self.snippets = {s["uuid"]: s for s in (snippets or [])}
        self.modules = dict(modules or {})
        # Later definitions of a name win, mirroring HEAD-per-name.
        self.handler = _Handler({s["name"]: s["uuid"] for s in skills})
        self.gather_calls = 0

    def get(self, uuid, fields=None):
        if uuid not in self.skills:
            raise KeyError(uuid)
        return dict(self.skills[uuid])

    def gather_export_inputs(self, uuid):
        self.gather_calls += 1
        skill = dict(self.skills[uuid])
        tools = [dict(self.tools[u]) for u in skill.get("tool_uuids") or []]
        snippets = [dict(self.snippets[u]) for u in skill.get("snippet_uuids") or []]
        return skill, tools, snippets, dict(self.modules)


def make_skill(uuid, name, description="A skill.", created="2024-01-01", **extra):
    skill = {
        "uuid": uuid,
        "name": name,
        "description": description,
        "created_at": created,
        "modified_at": created,
        "tool_uuids": [],
        "snippet_uuids": [],
        "tags": [],
        "state": "approved",
    }
    skill.update(extra)
    return skill


@pytest.fixture(autouse=True)
def _clean_cache():
    pub.get_cache().clear()
    yield
    pub.get_cache().clear()


# --------------------------------------------------------------------------- #
# to_slug / is_valid_slug (§4.2)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "name,expected",
    [
        ("pdf-forms", "pdf-forms"),
        ("PDF Forms", "pdf-forms"),
        ("pdf_forms", "pdf-forms"),
        ("pdf--forms", "pdf-forms"),
        ("  PDF   Forms  ", "pdf-forms"),
        ("-leading-and-trailing-", "leading-and-trailing"),
        ("Release Notes (v2)!", "release-notes-v2"),
        ("MiXeD_CaSe Thing", "mixed-case-thing"),
        ("émoji 🎉 skill", "moji-skill"),
        ("123", "123"),
        ("", ""),
        ("!!!", ""),
        ("---", ""),
        ("_", ""),
        (None, ""),
        ("a" * 80, "a" * 64),
    ],
)
def test_to_slug(name, expected):
    assert pub.to_slug(name) == expected


@pytest.mark.parametrize(
    "name",
    ["a" * 64 + " tail", "a" * 63 + " tail", "a" * 62 + " tail", "a" * 100],
)
def test_to_slug_never_ends_in_a_hyphen_at_the_cap(name):
    """A 64-char cut landing on a hyphen must not leave an edge hyphen."""
    slug = pub.to_slug(name)
    assert len(slug) <= 64
    assert pub.is_valid_slug(slug)


@pytest.mark.parametrize("slug", ["a", "pdf-forms", "a1-b2-c3", "123"])
def test_is_valid_slug_accepts_cli_legal_names(slug):
    assert pub.is_valid_slug(slug)


@pytest.mark.parametrize("slug", ["", "-a", "a-", "a--b", "A", "a_b", "a b", "a" * 65])
def test_is_valid_slug_rejects_everything_the_cli_would(slug):
    assert not pub.is_valid_slug(slug)


def test_every_slug_to_slug_produces_is_cli_valid():
    for name in ["PDF Forms", "pdf--forms", "_x_", "a" * 80, "Release Notes (v2)!"]:
        slug = pub.to_slug(name)
        if slug:
            assert pub.is_valid_slug(slug), slug


# --------------------------------------------------------------------------- #
# assign_slugs — collisions and stability (§5.6)
# --------------------------------------------------------------------------- #
def test_assign_slugs_gives_each_skill_a_unique_slug():
    skills = [
        make_skill("u1", "PDF Forms", created="2024-01-01"),
        make_skill("u2", "pdf-forms", created="2024-02-01"),
        make_skill("u3", "pdf_forms", created="2024-03-01"),
    ]
    assigned = pub.assign_slugs(skills)
    assert len(assigned) == 3
    assert assigned["pdf-forms"]["uuid"] == "u1"  # oldest keeps the bare slug
    assert all(pub.is_valid_slug(s) for s in assigned)


def test_oldest_created_at_keeps_the_bare_slug():
    skills = [
        make_skill("zzz", "PDF Forms", created="2024-01-01"),
        make_skill("aaa", "pdf-forms", created="2024-06-01"),
    ]
    assigned = pub.assign_slugs(skills)
    assert assigned["pdf-forms"]["uuid"] == "zzz"


def test_uuid_breaks_a_created_at_tie():
    skills = [
        make_skill("bbbb", "PDF Forms", created="2024-01-01"),
        make_skill("aaaa", "pdf-forms", created="2024-01-01"),
    ]
    assigned = pub.assign_slugs(skills)
    assert assigned["pdf-forms"]["uuid"] == "aaaa"


def test_adding_a_third_colliding_skill_shifts_no_existing_slug():
    """The §5.6 stability rule: a shifted slug orphans an installed copy."""
    first = make_skill("u1", "PDF Forms", created="2024-01-01")
    second = make_skill("u2", "pdf_forms", created="2024-02-01")
    before = {s["uuid"]: k for k, s in pub.assign_slugs([first, second]).items()}

    # A newcomer whose uuid sorts *below* both existing ones.
    third = make_skill("0000", "pdf-forms", created="2024-09-01")
    after = {s["uuid"]: k for k, s in pub.assign_slugs([first, second, third]).items()}

    assert after["u1"] == before["u1"]
    assert after["u2"] == before["u2"]
    assert after["0000"] not in before.values()


def test_a_suffixed_slug_never_displaces_a_bare_one():
    """A skill literally named ``pdf-forms-a3f1`` still gets that bare slug."""
    colliding = [
        make_skill("a3f1aaaa", "PDF Forms", created="2024-06-01"),
        make_skill("u0", "pdf forms", created="2024-01-01"),
        make_skill("u9", "PDF-Forms-a3f1", created="2024-07-01"),
    ]
    assigned = pub.assign_slugs(colliding)
    assert assigned["pdf-forms-a3f1"]["uuid"] == "u9"
    assert assigned["pdf-forms"]["uuid"] == "u0"
    assert len(assigned) == 3


def test_empty_slug_is_skipped_and_logged(caplog):
    with caplog.at_level(logging.WARNING, logger="skillberry_store.tools.publish"):
        assigned = pub.assign_slugs([make_skill("u1", "!!!"), make_skill("u2", "ok")])
    assert list(assigned) == ["ok"]
    assert "has no valid slug" in caplog.text


def test_assign_slugs_respects_the_64_char_cap_on_suffixed_slugs():
    long_name = "x" * 70
    skills = [
        make_skill("u1", long_name, created="2024-01-01"),
        make_skill("u2", long_name.upper(), created="2024-02-01"),
    ]
    assigned = pub.assign_slugs(skills)
    assert len(assigned) == 2
    assert all(len(s) <= 64 and pub.is_valid_slug(s) for s in assigned)


# --------------------------------------------------------------------------- #
# head_skills — HEAD only (§5.5)
# --------------------------------------------------------------------------- #
def test_head_skills_returns_one_entry_per_name():
    """Three ``create``s of one name leave three objects but one HEAD."""
    v1 = make_skill("u1", "demo", created="2024-01-01")
    v2 = make_skill("u2", "demo", created="2024-02-01")
    v3 = make_skill("u3", "demo", created="2024-03-01")
    service = FakeService([v1, v2, v3])  # last wins in the name cache
    heads = pub.head_skills(service)
    assert [h["uuid"] for h in heads] == ["u3"]
    assert heads[0]["uuid"] == service.handler.name_cache.get_head("demo")


def test_head_skills_skips_an_unreadable_head(caplog):
    service = FakeService([make_skill("u1", "ok")])
    service.handler.name_cache._heads["ghost"] = "missing-uuid"
    with caplog.at_level(logging.WARNING, logger="skillberry_store.tools.publish"):
        heads = pub.head_skills(service)
    assert [h["uuid"] for h in heads] == ["u1"]
    assert "Skipping skill ghost" in caplog.text


def test_is_head():
    head = make_skill("u2", "demo")
    service = FakeService([make_skill("u1", "demo"), head])
    assert pub.is_head(service, head)
    assert not pub.is_head(service, make_skill("u1", "demo"))
    assert not pub.is_head(service, {"uuid": "u1"})


def test_index_is_built_from_heads_not_every_revision():
    """A store with three revisions publishes one entry, at the HEAD uuid."""
    service = FakeService(
        [make_skill(f"u{i}", "demo", created=f"2024-0{i}-01") for i in (1, 2, 3)]
    )
    assigned = pub.assign_slugs(pub.head_skills(service))
    assert list(assigned) == ["demo"]
    assert assigned["demo"]["uuid"] == "u3"


# --------------------------------------------------------------------------- #
# namespaces / authorized_skills (§4.3.9)
# --------------------------------------------------------------------------- #
def test_namespaces_of():
    skill = make_skill("u1", "a", tags=["namespace:data-eng", "python", "namespace:x"])
    assert pub.namespaces_of(skill) == ["data-eng", "x"]
    assert pub.namespaces_of(make_skill("u2", "b")) == []


def test_authorized_skills_with_no_acl_returns_every_head():
    service = FakeService([make_skill("u1", "a"), make_skill("u2", "b")])
    got = pub.authorized_skills(service, None, None)
    assert {s["uuid"] for s in got} == {"u1", "u2"}


def test_authorized_skills_filters_by_namespace_scope():
    service = FakeService(
        [
            make_skill("u1", "a", tags=["namespace:data-eng"]),
            make_skill("u2", "b", tags=["namespace:other"]),
            make_skill("u3", "c"),
        ]
    )
    got = pub.authorized_skills(service, None, None, scope="ns:data-eng")
    assert [s["uuid"] for s in got] == ["u1"]


def test_authorized_skills_consults_the_pdp_and_denies(monkeypatch):
    """§4.3.9: the filter must be *called*, not assumed redundant."""
    from skillberry_store.access_control import pdp

    calls = []

    def spy(subject, resource, verb, cfg):
        calls.append((resource, verb))
        return pdp.Decision(allowed=False, reason="nope")

    monkeypatch.setattr(pdp, "authorize", spy)
    service = FakeService([make_skill("u1", "a")])
    cfg = type("Cfg", (), {"mode": "standalone"})()
    assert pub.authorized_skills(service, pdp.Subject(tenant_id="t"), cfg) == []
    assert calls == [("skills", "list")]


def test_authorized_skills_allows_when_the_pdp_allows(monkeypatch):
    from skillberry_store.access_control import pdp

    calls = []

    def spy(subject, resource, verb, cfg):
        calls.append((resource, verb))
        return pdp.Decision(allowed=True, reason="ok")

    monkeypatch.setattr(pdp, "authorize", spy)
    service = FakeService([make_skill("u1", "a")])
    cfg = type("Cfg", (), {"mode": "standalone"})()
    got = pub.authorized_skills(service, pdp.Subject(tenant_id="t"), cfg)
    assert [s["uuid"] for s in got] == ["u1"]
    assert calls == [("skills", "list")]


def test_authorized_skills_denies_an_absent_subject_under_acl():
    cfg = type("Cfg", (), {"mode": "standalone"})()
    assert pub.authorized_skills(FakeService([make_skill("u1", "a")]), None, cfg) == []


# --------------------------------------------------------------------------- #
# SBS_PUBLISH_NAMESPACES (§4.3)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw,expected",
    [
        (None, None),
        ("", None),
        ("   ", None),
        (",,", None),
        ("data-eng", ["data-eng"]),
        (" data-eng , web ", ["data-eng", "web"]),
    ],
)
def test_allowed_namespaces(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv(pub.NAMESPACES_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(pub.NAMESPACES_ENV_VAR, raw)
    assert pub.allowed_namespaces() == expected


def test_the_namespace_allowlist_restricts_which_scopes_resolve(monkeypatch):
    service = FakeService(
        [
            make_skill("u1", "a", tags=["namespace:data-eng"]),
            make_skill("u2", "b", tags=["namespace:seed"]),
        ]
    )
    monkeypatch.setenv(pub.NAMESPACES_ENV_VAR, "data-eng")
    assert [s["uuid"] for s in pub.authorized_skills(service, None, None, "ns:data-eng")] == [
        "u1"
    ]
    assert pub.authorized_skills(service, None, None, "ns:seed") == []


def test_the_namespace_allowlist_does_not_affect_other_scopes(monkeypatch):
    service = FakeService([make_skill("u1", "a", tags=["namespace:seed"])])
    monkeypatch.setenv(pub.NAMESPACES_ENV_VAR, "data-eng")
    assert len(pub.authorized_skills(service, None, None, "*")) == 1


# --------------------------------------------------------------------------- #
# The tri-state master switch and the per-skill flag (§5.12)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("flag", [True, False, None])
@pytest.mark.parametrize(
    "master,expected_for",
    [
        # `true`/`false` ignore the skill's own flag — that is what makes them
        # useful: one edit publishes or withdraws the whole store.
        ("true", lambda flag: True),
        ("false", lambda flag: False),
        # `selective` consults it, and an unset flag means NOT published.
        ("selective", lambda flag: flag is True),
    ],
)
def test_skill_publishable_truth_table(master, expected_for, flag):
    skill = make_skill("u1", "demo", npx_publish=flag)
    assert pub.skill_publishable(skill, master) is expected_for(flag)


def test_skill_publishable_treats_a_missing_key_as_not_published():
    """A manifest written before the field existed is not silently published."""
    skill = make_skill("u1", "demo")
    skill.pop("npx_publish", None)
    assert pub.skill_publishable(skill, "selective") is False
    assert pub.skill_publishable(skill, "true") is True


def test_skill_publishable_is_independent_of_every_other_gate():
    """Not entangled with lifecycle state, tags or the internal marker — those
    are separate concerns and conflating them would re-create the invisible
    second visibility rule §4.3.1 argues against."""
    skill = make_skill("u1", "demo", npx_publish=True, state="new", tags=[pub.INTERNAL_TAG])
    assert pub.skill_publishable(skill, "selective") is True


# --------------------------------------------------------------------------- #
# The per-skill internal opt-out (§4.3)
# --------------------------------------------------------------------------- #
def test_is_internal_reads_an_ordinary_tag():
    assert pub.is_internal(make_skill("u1", "a", tags=[pub.INTERNAL_TAG]))
    assert not pub.is_internal(make_skill("u2", "b"))
    assert not pub.is_internal(make_skill("u3", "c", tags=["other"]))


# --------------------------------------------------------------------------- #
# normalise_description (§1.3, §5.3 #2/#3)
# --------------------------------------------------------------------------- #
def test_normalise_description_passes_ordinary_text_through():
    assert pub.normalise_description("Fill PDF forms.", "d") == "Fill PDF forms."


@pytest.mark.parametrize("value", [None, "", "   ", 42, [], {}])
def test_normalise_description_substitutes_a_fallback(value):
    assert pub.normalise_description(value, "pdf-forms") == "Skill: pdf-forms"


def test_normalise_description_truncates_to_the_v2_limit():
    got = pub.normalise_description("word " * 500, "d")
    assert len(got) <= pub.MAX_DESCRIPTION_CHARS
    assert not got.endswith(" ")


def test_normalise_description_truncates_on_a_word_boundary():
    text = "a" * 600 + " " + "b" * 600
    got = pub.normalise_description(text, "d")
    assert got == "a" * 600


def test_normalise_description_ignores_a_word_boundary_too_early_to_help():
    """A boundary in the first half would throw away more than it saves."""
    text = "a" * 100 + " " + "b" * 2000
    got = pub.normalise_description(text, "d")
    assert len(got) == pub.MAX_DESCRIPTION_CHARS
    assert got == text[: pub.MAX_DESCRIPTION_CHARS]


def test_normalise_description_handles_a_single_unbroken_word():
    got = pub.normalise_description("z" * 2000, "d")
    assert got == "z" * pub.MAX_DESCRIPTION_CHARS


# --------------------------------------------------------------------------- #
# Archive safety (§5.7, §5.3 #5)
# --------------------------------------------------------------------------- #
def test_safe_archive_paths_passes_clean_input_through():
    files = {"SKILL.md": b"x", "scripts/a.py": b"y"}
    assert pub.safe_archive_paths(files) is files


def test_safe_archive_paths_raises_naming_every_offender():
    with pytest.raises(pub.UnsafeArchivePathError) as excinfo:
        pub.safe_archive_paths({"SKILL.md": b"x", "../a": b"y", "/b": b"z"})
    assert excinfo.value.paths == ["../a", "/b"]


def test_check_archive_limits_rejects_too_many_files():
    files = {f"f{i}.txt": b"x" for i in range(pub.MAX_ARCHIVE_FILES + 1)}
    with pytest.raises(pub.ArchiveTooLargeError, match="file cap"):
        pub.check_archive_limits(files)


def test_check_archive_limits_rejects_an_oversized_archive():
    files = {"big.bin": b"x" * (pub.MAX_ARCHIVE_BYTES + 1)}
    with pytest.raises(pub.ArchiveTooLargeError, match="byte cap"):
        pub.check_archive_limits(files)


def test_check_archive_limits_accepts_a_normal_archive():
    files = {"SKILL.md": b"x" * 1000}
    assert pub.check_archive_limits(files) is files


def test_offending_file_tags_names_the_tag_the_operator_wrote():
    snippets = [{"tags": ["file:../../escape.txt"], "content": "x"}]
    tools = [{"tags": ["file:ok.py"], "name": "t"}]
    assert pub.offending_file_tags(
        ["../../escape.txt"], tools, snippets, "demo"
    ) == ["file:../../escape.txt"]


# --------------------------------------------------------------------------- #
# build_entry (§6.3)
# --------------------------------------------------------------------------- #
def _service_with_files():
    skill = make_skill(
        "u1",
        "PDF Forms",
        description="Fill and flatten PDF forms.",
        tool_uuids=["t1"],
        snippet_uuids=["s1"],
    )
    tools = [
        {
            "uuid": "t1",
            "name": "fill",
            "tags": ["file:scripts/fill.py"],
            "modified_at": "2024-01-01",
            "programming_language": "python",
        }
    ]
    snippets = [
        {
            "uuid": "s1",
            "name": "spec",
            "tags": ["file:reference/spec.md"],
            "content": "# spec",
            "modified_at": "2024-01-01",
        }
    ]
    return FakeService([skill], tools, snippets, {"fill": "print('fill')"})


def test_build_entry_produces_a_root_level_skill_md():
    entry = pub.build_entry(_service_with_files(), "u1", "pdf-forms")
    with zipfile.ZipFile(io.BytesIO(entry.payload)) as zf:
        names = zf.namelist()
        assert "SKILL.md" in names
        assert "scripts/fill.py" in names
        assert "reference/spec.md" in names
        assert not any(n.startswith("PDF Forms/") for n in names)


def test_build_entry_emits_the_slug_as_the_frontmatter_name():
    """§5.8 #1: ``name: PDF Forms`` in a ``pdf-forms/`` directory is a bug."""
    entry = pub.build_entry(_service_with_files(), "u1", "pdf-forms")
    with zipfile.ZipFile(io.BytesIO(entry.payload)) as zf:
        content = zf.read("SKILL.md").decode()
    body = content.split("---")[1]
    parsed = yaml.safe_load(body)
    assert parsed["name"] == "pdf-forms"
    assert parsed["description"] == "Fill and flatten PDF forms."


def _frontmatter_of(entry):
    with zipfile.ZipFile(io.BytesIO(entry.payload)) as zf:
        return yaml.safe_load(zf.read("SKILL.md").decode().split("---")[1])


def test_build_entry_records_the_sbs_name_when_it_differs_from_the_slug():
    """The frontmatter ``name`` is the slug, so the store's own name would
    otherwise be lost from the file an agent (or a re-import) reads."""
    entry = pub.build_entry(_service_with_files(), "u1", "pdf-forms")
    assert _frontmatter_of(entry)["metadata"] == {"sbs_name": "PDF Forms"}


def test_build_entry_omits_metadata_when_the_name_already_is_the_slug():
    service = FakeService([make_skill("u1", "demo")])
    assert "metadata" not in _frontmatter_of(pub.build_entry(service, "u1", "demo"))


def test_an_internal_tagged_skill_emits_metadata_internal():
    """The CLI hides such a skill from a multi-entry install list unless
    INSTALL_INTERNAL_SKILLS=1 — a per-skill opt-out (§1.4, §4.3)."""
    service = FakeService([make_skill("u1", "demo", tags=[pub.INTERNAL_TAG])])
    assert _frontmatter_of(pub.build_entry(service, "u1", "demo"))["metadata"] == {
        "internal": True
    }


def test_an_internal_tagged_skill_is_still_published():
    """It has to be, or its own per-skill install URL would stop working."""
    service = FakeService([make_skill("u1", "demo", tags=[pub.INTERNAL_TAG])])
    assert pub.publishable_entry(service, "u1", "demo") is not None


def test_build_entry_digest_matches_the_payload():
    entry = pub.build_entry(_service_with_files(), "u1", "pdf-forms")
    assert entry.digest == "sha256:" + hashlib.sha256(entry.payload).hexdigest()
    assert entry.digest.startswith("sha256:")
    assert len(entry.digest) == len("sha256:") + 64


def test_build_entry_preserves_identity_and_normalises_the_description():
    service = FakeService([make_skill("u1", "Demo", description="  ")])
    entry = pub.build_entry(service, "u1", "demo")
    assert (entry.uuid, entry.name, entry.slug) == ("u1", "Demo", "demo")
    assert entry.description == "Skill: Demo"


def test_index_entry_url_is_relative_and_carries_no_token():
    entry = pub.build_entry(_service_with_files(), "u1", "pdf-forms")
    item = entry.index_entry()
    assert item == {
        "name": "pdf-forms",
        "description": "Fill and flatten PDF forms.",
        "type": "archive",
        "url": "pdf-forms.zip",
        "digest": entry.digest,
    }


def test_build_index_shape_matches_the_v2_schema_literal():
    entry = pub.build_entry(_service_with_files(), "u1", "pdf-forms")
    index = pub.build_index([entry])
    assert index["$schema"] == (
        "https://schemas.agentskills.io/discovery/0.2.0/schema.json"
    )
    assert len(index["skills"]) == 1


def test_build_entry_raises_on_an_unsafe_file_tag():
    skill = make_skill("u1", "demo", snippet_uuids=["s1"])
    snippets = [
        {
            "uuid": "s1",
            "tags": ["file:../../escape.txt"],
            "content": "x",
            "modified_at": "2024-01-01",
        }
    ]
    service = FakeService([skill], snippets=snippets)
    with pytest.raises(pub.UnsafeArchivePathError) as excinfo:
        pub.build_entry(service, "u1", "demo")
    assert excinfo.value.tags == ["file:../../escape.txt"]


def test_publishable_entry_excludes_an_unsafe_skill_with_a_warning(caplog):
    skill = make_skill("u1", "demo", snippet_uuids=["s1"])
    snippets = [
        {
            "uuid": "s1",
            "tags": ["file:../../escape.txt"],
            "content": "x",
            "modified_at": "2024-01-01",
        }
    ]
    service = FakeService([skill], snippets=snippets)
    with caplog.at_level(logging.WARNING, logger="skillberry_store.tools.publish"):
        assert pub.publishable_entry(service, "u1", "demo") is None
    assert "demo" in caplog.text
    assert "file:../../escape.txt" in caplog.text


def test_publishable_entry_returns_the_entry_for_a_good_skill():
    assert pub.publishable_entry(_service_with_files(), "u1", "pdf-forms") is not None


def test_publishable_entry_applies_no_state_gate():
    """§4.3.1: a ``state: new`` draft is publishable — it is already visible."""
    service = FakeService([make_skill("u1", "draft", state="new")])
    assert pub.publishable_entry(service, "u1", "draft") is not None


def test_publishable_entry_swallows_a_missing_skill(caplog):
    with caplog.at_level(logging.WARNING, logger="skillberry_store.tools.publish"):
        assert pub.publishable_entry(FakeService([]), "nope", "nope") is None


# --------------------------------------------------------------------------- #
# Cache (§4.4, §5.8 #2/#3)
# --------------------------------------------------------------------------- #
def test_repeat_builds_hit_the_cache():
    service = _service_with_files()
    first = pub.build_entry(service, "u1", "pdf-forms")
    second = pub.build_entry(service, "u1", "pdf-forms")
    assert first is second
    assert service.gather_calls == 2  # gathering is cheap; zipping is not


def test_touching_nothing_does_not_change_the_digest():
    service = _service_with_files()
    assert (
        pub.build_entry(service, "u1", "pdf-forms").digest
        == pub.build_entry(service, "u1", "pdf-forms").digest
    )


def test_mutating_the_skill_changes_the_digest():
    service = _service_with_files()
    before = pub.build_entry(service, "u1", "pdf-forms").digest
    service.skills["u1"]["description"] = "Changed."
    service.skills["u1"]["modified_at"] = "2024-05-05"
    assert pub.build_entry(service, "u1", "pdf-forms").digest != before


def test_mutating_a_tool_changes_the_digest():
    service = _service_with_files()
    before = pub.build_entry(service, "u1", "pdf-forms").digest
    service.modules["fill"] = "print('changed')"
    service.tools["t1"]["modified_at"] = "2024-05-05"
    assert pub.build_entry(service, "u1", "pdf-forms").digest != before


def test_mutating_a_snippet_changes_the_digest():
    service = _service_with_files()
    before = pub.build_entry(service, "u1", "pdf-forms").digest
    service.snippets["s1"]["content"] = "# changed"
    service.snippets["s1"]["modified_at"] = "2024-05-05"
    assert pub.build_entry(service, "u1", "pdf-forms").digest != before


def test_a_none_modified_at_rebuilds_every_call():
    """§5.8 #3: a ``None`` key that never changes would never invalidate."""
    service = FakeService([make_skill("u1", "demo")])
    service.skills["u1"]["modified_at"] = None
    assert pub.cache_key(service.skills["u1"], [], []) is None
    first = pub.build_entry(service, "u1", "demo")
    second = pub.build_entry(service, "u1", "demo")
    assert first is not second
    assert first.digest == second.digest  # same content, still deterministic


def test_cache_key_is_none_when_a_tool_has_no_timestamp():
    skill = make_skill("u1", "demo")
    assert pub.cache_key(skill, [{"modified_at": None}], []) is None
    assert pub.cache_key(skill, [{"modified_at": "2024-01-01"}], []) is not None


def test_cache_by_digest_serves_the_exact_bytes_after_an_edit():
    """The race-closing lookup behind ``?digest=`` (§5.14 option B)."""
    service = _service_with_files()
    old = pub.build_entry(service, "u1", "pdf-forms")
    service.skills["u1"]["description"] = "Changed."
    service.skills["u1"]["modified_at"] = "2024-05-05"
    new = pub.build_entry(service, "u1", "pdf-forms")
    assert new.digest != old.digest

    cache = pub.get_cache()
    assert cache.by_digest("u1", "pdf-forms", old.digest).payload == old.payload
    assert cache.by_digest("u1", "pdf-forms", new.digest).payload == new.payload


def test_cache_by_digest_refuses_a_digest_belonging_to_another_skill():
    service = _service_with_files()
    entry = pub.build_entry(service, "u1", "pdf-forms")
    assert pub.get_cache().by_digest("other", "pdf-forms", entry.digest) is None
    assert pub.get_cache().by_digest("u1", "other-slug", entry.digest) is None


def test_cache_by_digest_misses_an_unknown_digest():
    service = _service_with_files()
    pub.build_entry(service, "u1", "pdf-forms")
    assert pub.get_cache().by_digest("u1", "pdf-forms", "sha256:" + "0" * 64) is None


def test_cache_evicts_rather_than_growing_without_limit():
    """§5.8 #2: a miss is cheap, a leak is not — the bound must actually bind."""
    cache = pub.ArtifactCache(max_bytes=4096)
    # Incompressible payloads, so the bound is reached in a handful of entries
    # rather than depending on how well deflate does on repeated bytes.
    blobs = [secrets.token_hex(8192) for _ in range(10)]
    service = FakeService(
        [make_skill(f"u{i}", f"s{i}", snippet_uuids=[f"n{i}"]) for i in range(10)],
        snippets=[
            {
                "uuid": f"n{i}",
                "tags": [f"file:blob{i}.bin"],
                "content": blobs[i],
                "modified_at": "2024-01-01",
            }
            for i in range(10)
        ],
    )
    for i in range(10):
        pub.build_entry(service, f"u{i}", f"s{i}", cache=cache)
    assert cache.size() < 10
    # One entry is always retained: evicting the archive just built would make
    # the immediately-following artifact fetch a guaranteed miss.
    assert cache.size() >= 1


def test_cache_clear_resets_accounting():
    cache = pub.ArtifactCache()
    pub.build_entry(_service_with_files(), "u1", "pdf-forms", cache=cache)
    assert cache.size() == 1
    cache.clear()
    assert (cache.size(), cache.total_bytes()) == (0, 0)


# --------------------------------------------------------------------------- #
# The install command (§4.3.5)
# --------------------------------------------------------------------------- #
def test_npx_install_command_shape():
    assert pub.npx_install_command("https://store.example.com", "TOKEN") == (
        "npx skills add https://store.example.com/pub/TOKEN -y -a claude-code"
    )


def test_npx_install_command_honours_the_agent_picker():
    assert pub.npx_install_command("https://s", "T", agent="cursor").endswith(
        "-y -a cursor"
    )


def test_npx_install_command_never_emits_a_double_slash():
    assert "//pub/" not in pub.npx_install_command("https://store.example.com/", "T")


def test_npx_install_url_shape():
    assert pub.npx_install_url("http://localhost:8000/", "pdf-forms") == (
        "http://localhost:8000/pub/pdf-forms"
    )


def test_install_command_carries_no_env_var_prefix():
    """Resolves §9 Q7 against prefixing, and keeps the command portable.

    `VAR=1 cmd` is POSIX shell syntax, so a prefix would make the command we
    hand out fail outright in PowerShell and cmd.exe — and it would protect only
    this one invocation, not the `npx skills update` runs that follow. The
    telemetry opt-out is documented as an exported `DO_NOT_TRACK=1` instead.
    """
    command = pub.npx_install_command("https://s", "T")
    assert command.startswith("npx skills add ")
    assert "=" not in command.split(" ", 1)[0]
    assert "DISABLE_TELEMETRY" not in command
    assert "DO_NOT_TRACK" not in command


def test_install_command_pins_an_agent_and_takes_all_skills():
    """``-y`` without ``-a`` installs into ~75 agent directories (§4.3.1)."""
    command = pub.npx_install_command("https://s", "T")
    assert " -y " in command
    assert " -a " in command
    assert " -s " not in command  # no per-skill flag: the URL names the skill
    assert " -g" not in command  # a per-skill token is safe in a lockfile
