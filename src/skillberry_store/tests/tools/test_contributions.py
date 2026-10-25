"""Export contributor / import claimer hooks and kept frontmatter metadata."""

import io
import zipfile

import pytest

from skillberry_store.tools.anthropic import contributions
from skillberry_store.tools.anthropic.contributions import (
    ContributionError,
    ExportContribution,
    ImportClaim,
)
from skillberry_store.tools.anthropic.exporter import (
    GENERATED_BEGIN,
    _build_file_structure,
    export_skill_to_anthropic_format,
)
from skillberry_store.tools.anthropic.importer import (
    import_from_anthropic_skill,
    parse_skill_metadata,
)

SKILL = {"name": "planner", "description": "Plan things."}
SNIPPET = {"name": "i", "content": "# Planner\n\nDo it.", "tags": ["file:SKILL.md"]}


@pytest.fixture
def active():
    slugs = {"p1", "p2"}
    contributions.set_active_resolver(lambda s: s in slugs)
    registered = []

    def add(obj, slug, kind="contributor"):
        registered.append(obj)
        if kind == "contributor":
            contributions.register_export_contributor(obj, slug)
        else:
            contributions.register_import_claimer(obj, slug)

    yield slugs, add
    for obj in registered:
        contributions.unregister(obj)
    contributions.set_active_resolver(None)


class Contributor:
    def __init__(self, contribution=None, raises=None):
        self.contribution = contribution
        self.raises = raises

    def contribute(self, skill, tools, snippets):
        if self.raises:
            raise self.raises
        return self.contribution


def _md(files):
    return files["planner/SKILL.md"].decode()


def test_no_contributor_is_byte_identical():
    before = export_skill_to_anthropic_format(SKILL, [], [SNIPPET], {})
    after = export_skill_to_anthropic_format(SKILL, [], [SNIPPET], {})
    assert before == after
    md = zipfile.ZipFile(io.BytesIO(before)).read("planner/SKILL.md").decode()
    assert GENERATED_BEGIN not in md


def test_contribution_adds_files_metadata_and_section(active):
    _, add = active
    add(Contributor(ExportContribution(
        files={"extra.json": '{"a": 1}'},
        metadata={"acme-tools": "x,y"},
        skill_md_section="## Acme\n\nNote.",
    )), "p1")
    files = _build_file_structure(SKILL, [], [SNIPPET], {}, metadata={"sbs_name": "Planner"})
    assert files["planner/extra.json"] == b'{"a": 1}\n'
    md = _md(files)
    assert "  acme-tools: x,y" in md and "  sbs_name: Planner" in md
    assert md.index("Do it.") < md.index("## Acme")
    assert GENERATED_BEGIN in md


def test_inactive_contributor_is_ignored(active):
    slugs, add = active
    add(Contributor(ExportContribution(files={"extra.json": "{}"})), "p1")
    slugs.discard("p1")
    assert "planner/extra.json" not in _build_file_structure(SKILL, [], [SNIPPET], {})


def test_file_collision_fails(active):
    _, add = active
    add(Contributor(ExportContribution(files={"SKILL.md": "x"})), "p1")
    with pytest.raises(ContributionError, match="p1"):
        _build_file_structure(SKILL, [], [SNIPPET], {})


def test_metadata_collision_between_contributors_fails(active):
    _, add = active
    add(Contributor(ExportContribution(metadata={"k": "1"})), "p1")
    add(Contributor(ExportContribution(metadata={"k": "2"})), "p2")
    with pytest.raises(ContributionError, match="'k'"):
        _build_file_structure(SKILL, [], [SNIPPET], {})


def test_metadata_collision_with_caller_fails(active):
    _, add = active
    add(Contributor(ExportContribution(metadata={"sbs_name": "x"})), "p1")
    with pytest.raises(ContributionError):
        _build_file_structure(SKILL, [], [SNIPPET], {}, metadata={"sbs_name": "y"})


def test_raising_contributor_fails_export(active):
    _, add = active
    add(Contributor(raises=RuntimeError("boom")), "p1")
    with pytest.raises(ContributionError, match="Plugin 'p1' failed: boom"):
        _build_file_structure(SKILL, [], [SNIPPET], {})


def test_kept_frontmatter_metadata_is_exported_and_contribution_regenerates_it(active):
    _, add = active
    skill = {**SKILL, "extra": {"frontmatter_metadata": {"author": "me", "acme-tools": "old"}}}
    md = _md(_build_file_structure(skill, [], [SNIPPET], {}))
    assert "author: me" in md and "acme-tools: old" in md
    add(Contributor(ExportContribution(metadata={"acme-tools": "new"})), "p1")
    md = _md(_build_file_structure(skill, [], [SNIPPET], {}))
    assert "acme-tools: new" in md and "old" not in md


# --- import --------------------------------------------------------------


def _zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


PACKAGE = {
    "planner/SKILL.md": "---\nname: planner\ndescription: d\nmetadata:\n  acme-tools: x\n  n: 3\n  nested: {a: 1}\n---\n\n# Planner\n",
    "planner/acme.json": '{"tools": ["t"]}',
    "planner/scripts/t.py": "def t(a: int):\n    '''T.'''\n    return a\n",
}


def test_parse_skill_metadata_keeps_scalar_metadata():
    files = [{"name": "SKILL.md", "path": "SKILL.md", "content": PACKAGE["planner/SKILL.md"]}]
    meta = parse_skill_metadata(files)
    assert meta["metadata"] == {"acme-tools": "x", "n": 3}


class Claimer:
    def __init__(self):
        self.seen = None

    def claim(self, files):
        self.seen = dict(files)
        if "acme.json" not in files:
            return None
        return ImportClaim(
            claimed_paths={"acme.json", "scripts/t.py"},
            tools=[({"name": "t", "packaging_format": "code"}, "src", "t.py")],
        )


def test_claimer_claims_files_relative_to_skill_root(active):
    _, add = active
    claimer = Claimer()
    add(claimer, "p1", kind="claimer")
    extras = {}
    name, _, tools, snippets, _ = import_from_anthropic_skill("zip", _zip(PACKAGE), extras=extras)
    assert name == "planner"
    assert set(claimer.seen) == {"SKILL.md", "acme.json", "scripts/t.py"}
    assert tools == []  # the claimed script was not classified as a code tool
    assert {s.name for s in snippets} == {"SKILL"}
    (claim, slug), = extras["claims"]
    assert slug == "p1" and claim.tools[0][0]["name"] == "t"
    assert extras["frontmatter_metadata"] == {"acme-tools": "x", "n": 3}


def test_without_claimer_files_import_as_before(active):
    slugs, _ = active
    slugs.clear()
    extras = {}
    _, _, tools, snippets, _ = import_from_anthropic_skill("zip", _zip(PACKAGE), extras=extras)
    assert [t.name for t in tools] == ["t"]
    assert "acme" in {s.name for s in snippets}
    assert extras["claims"] == []


def test_claimer_unknown_path_fails(active):
    _, add = active

    class Bad:
        def claim(self, files):
            return ImportClaim(claimed_paths={"nope"})

    add(Bad(), "p1", kind="claimer")
    with pytest.raises(ContributionError, match="unknown"):
        import_from_anthropic_skill("zip", _zip(PACKAGE), extras={})


def test_generated_section_is_dropped_on_reimport(active):
    _, add = active
    add(Contributor(ExportContribution(skill_md_section="## Generated\n\nG.")), "p1")
    data = export_skill_to_anthropic_format(SKILL, [], [SNIPPET], {})
    _, _, _, snippets, _ = import_from_anthropic_skill("zip", data, extras={})
    (skill_md,) = [s for s in snippets if s.name == "SKILL"]
    assert "Generated" not in skill_md.content
    assert "Do it." in skill_md.content
