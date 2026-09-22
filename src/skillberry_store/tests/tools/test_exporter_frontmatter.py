# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""YAML-safety of the emitted ``SKILL.md`` frontmatter (docs/design/npx.md §5.4).

Every row in ``ROUND_TRIP_CASES`` broke the old string-interpolated
frontmatter: the first two made ``yaml.safe_load`` raise (so the consuming CLI
dropped the skill with no diagnostic), and the third parsed *successfully* to a
silently truncated description, which is the dangerous one.
"""

from __future__ import annotations

import pytest
import yaml

from skillberry_store.tools.anthropic.exporter import (
    generate_skill_md,
    render_frontmatter,
    skill_description,
)


def _frontmatter(content: str) -> dict:
    """Parse the leading ``---`` block of ``content`` the way the CLI does."""
    lines = content.split("\n")
    assert lines[0].strip() == "---", content
    end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    parsed = yaml.safe_load("\n".join(lines[1:end]))
    assert isinstance(parsed, dict)
    return parsed


ROUND_TRIP_CASES = [
    "Line one.\nLine two: with colon.\n- bullet",
    "Use when: reviewing code",
    '"quoted" #hash @at',
    "Plain and ordinary.",
    "trailing spaces   ",
    "unicode ✅ and emoji 🎉",
    "a: b: c: d",
    "- starts like a list item",
    "*starts like an alias",
    "{looks: like, a: flow mapping}",
]


@pytest.mark.parametrize("description", ROUND_TRIP_CASES)
def test_description_round_trips(description):
    """Whatever goes in comes back out byte-identical through a real parser."""
    skill = {"name": "demo", "description": description}
    fm = _frontmatter(generate_skill_md(skill, True, []))
    assert fm["description"] == description
    assert fm["name"] == "demo"


@pytest.mark.parametrize("name", ["PDF Forms", "weird: name", "name #with hash"])
def test_name_round_trips(name):
    fm = _frontmatter(generate_skill_md({"name": name, "description": "d"}, True, []))
    assert fm["name"] == name


def test_long_description_gains_no_newline():
    """``width`` must stay effectively infinite — folding reflows the text."""
    description = "word " * 400  # 2000 characters
    fm = _frontmatter(generate_skill_md({"name": "d", "description": description}, True, []))
    assert "\n" not in fm["description"]
    assert fm["description"] == description


def test_none_description_yields_fallback_not_the_string_none():
    """``description: None`` used to reach the file as the literal ``"None"``."""
    fm = _frontmatter(generate_skill_md({"name": "demo", "description": None}, True, []))
    assert fm["description"] == "Skill: demo"


@pytest.mark.parametrize("value", [None, "", "   "])
def test_empty_descriptions_yield_fallback(value):
    assert skill_description({"name": "demo", "description": value}) == "Skill: demo"


def test_missing_description_yields_fallback():
    fm = _frontmatter(generate_skill_md({"name": "demo"}, True, []))
    assert fm["description"] == "Skill: demo"


def test_name_override_emits_the_slug():
    """The well-known path emits the slug so it matches the install directory."""
    content = generate_skill_md(
        {"name": "PDF Forms", "description": "Fill forms."},
        True,
        [],
        name_override="pdf-forms",
    )
    fm = _frontmatter(content)
    assert fm["name"] == "pdf-forms"
    assert fm["description"] == "Fill forms."


def test_existing_callers_keep_the_raw_name():
    fm = _frontmatter(generate_skill_md({"name": "PDF Forms", "description": "x"}, True, []))
    assert fm["name"] == "PDF Forms"


def test_license_line_still_emitted_for_a_license_snippet():
    snippets = [{"tags": ["file:LICENSE.txt"], "content": "terms"}]
    fm = _frontmatter(generate_skill_md({"name": "d", "description": "x"}, True, snippets))
    assert fm["license"] == "Proprietary. LICENSE.txt has complete terms"


def test_no_license_line_without_a_license_snippet():
    snippets = [{"tags": ["file:README.md"], "content": "hi"}]
    fm = _frontmatter(generate_skill_md({"name": "d", "description": "x"}, True, snippets))
    assert "license" not in fm


def test_body_is_emitted_when_there_is_no_file_structure():
    content = generate_skill_md({"name": "demo", "description": "Does things."}, False, [])
    assert "# demo\n" in content
    assert "Does things.\n" in content
    assert _frontmatter(content)["description"] == "Does things."


def test_body_is_absent_when_there_is_a_file_structure():
    content = generate_skill_md({"name": "demo", "description": "Does things."}, True, [])
    assert content.count("demo") == 1  # only the frontmatter `name:` line


def test_key_order_is_preserved():
    """``sort_keys=False``: name first, then description, then license."""
    rendered = render_frontmatter({"name": "a", "description": "b", "license": "c"})
    assert rendered.splitlines()[1:4] == ["name: a", "description: b", "license: c"]


def test_render_frontmatter_ends_with_a_blank_line():
    assert render_frontmatter({"name": "a"}).endswith("---\n\n")
