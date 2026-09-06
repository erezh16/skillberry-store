# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0
"""Unit tests for the rich login banner: markup, style, degradation.

Covers docs/design/login-banner.md §§4-6 and §8. Parsing only — no HTTP, no UI.

Three invariants carry most of the weight here, and each has its own section
below:

* **No construct can raise or drop text.** An unknown attribute, a rejected
  colour, an unbalanced marker — every one of them keeps the operator's words
  and loses only the decoration.
* **Nothing off the allow-list reaches the tree.** In particular no
  ``javascript:`` href, no ``data:text/html`` image, and no colour that is not a
  ``#hex`` or a named colour.
* **The tree always degrades to plain text**, which is what keeps ``sbs login``
  and the ``GET /auth/whoami`` 401 plain no matter what the UI does (§8).
"""

from __future__ import annotations

import json
import logging
import textwrap

import pytest

from skillberry_store.access_control.config import (
    LOGIN_INFO_MAX_CHARS,
    LOGIN_INFO_MAX_LINES,
    load_config,
)
from skillberry_store.access_control.login_banner import (
    BANNER_MAX_IMAGES,
    BANNER_MAX_LINES,
    BANNER_MAX_NODES,
    PAYLOAD_VERSION,
    build_banner,
    parse_message,
    parse_style,
)


def _spans(message: str):
    """Every span of a one-block message, as the dicts the payload carries."""
    blocks = parse_message(message, "test.yaml")
    return [span.to_dict() for block in blocks for span in block.spans]


def _plain(message: str, style=None) -> str:
    banner = build_banner(message, style, "test.yaml")
    assert banner is not None, f"nothing renderable in {message!r}"
    return banner.to_plain_text()


def _write(tmp_path, contents: str) -> str:
    path = tmp_path / "acl.yaml"
    path.write_text(textwrap.dedent(contents))
    return str(path)


def _rich_config(message: str, extra: str = "") -> str:
    """A standalone config with ``message`` as a rich login banner."""
    return f"""
        mode: standalone
        standalone:
          users: []
          login_info:
            enabled: true
            format: rich
{textwrap.indent(textwrap.dedent(extra), "            ") if extra else ""}
            message: |
{textwrap.indent(message, "              ")}
        """


# --------------------------------------------------------------------------- #
# Inline markup (§4.2)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message, expected",
    [
        ("**bold**", {"kind": "text", "text": "bold", "bold": True}),
        ("__bold__", {"kind": "text", "text": "bold", "bold": True}),
        ("*ital*", {"kind": "text", "text": "ital", "italic": True}),
        ("_ital_", {"kind": "text", "text": "ital", "italic": True}),
        ("~~gone~~", {"kind": "text", "text": "gone", "strike": True}),
        ("`code`", {"kind": "text", "text": "code", "mono": True}),
    ],
    ids=["bold-stars", "bold-unders", "ital-star", "ital-under", "strike", "code"],
)
def test_each_emphasis_marker_sets_its_own_flag(message, expected):
    assert _spans(message) == [expected]


def test_mark_sets_both_halves_of_the_pair():
    """A highlight must set a foreground too, or it can land dark-on-dark."""
    (span,) = _spans("==look==")
    assert span["text"] == "look"
    assert span["bg"] and span["color"]


def test_nested_markers_merge_their_styles():
    (span,) = _spans("**[big]{size=2xl color=red}**")
    assert span == {
        "kind": "text",
        "text": "big",
        "bold": True,
        "color": "red",
        "size": "2xl",
    }


def test_the_inner_style_wins_over_the_outer_one():
    (span,) = _spans("[[inner]{color=blue}]{color=red}")
    assert span["color"] == "blue"


def test_an_attribute_span_carries_every_flag_and_value():
    (span,) = _spans(
        "[x]{color=#ffd166 bg=navy glow=gold size=xl bold italic underline "
        "strike mono caps pill animate=blink}"
    )
    assert span == {
        "kind": "text",
        "text": "x",
        "animate": "blink",
        "bg": "navy",
        "bold": True,
        "caps": True,
        "color": "#ffd166",
        "glow": "gold",
        "italic": True,
        "mono": True,
        "pill": True,
        "size": "xl",
        "strike": True,
        "underline": True,
    }


def test_a_code_span_is_opaque_to_markup():
    """Otherwise a banner could not show the markup syntax it documents."""
    assert _spans("`**not bold**`") == [
        {"kind": "text", "text": "**not bold**", "mono": True}
    ]


def test_escapes_produce_literal_characters():
    assert _spans(r"\*not italic\* and \[not a span\]") == [
        {"kind": "text", "text": "*not italic* and [not a span]"}
    ]


def test_underscores_inside_a_word_are_not_emphasis():
    """`login_info_message` must not come out half-italic."""
    assert _spans("see login_info_message here") == [
        {"kind": "text", "text": "see login_info_message here"}
    ]


@pytest.mark.parametrize(
    "message",
    ["**unclosed", "[label", "[label](https://x", "~~open"],
    ids=["bold", "bracket", "paren", "strike"],
)
def test_an_unclosed_construct_keeps_every_character(message):
    rendered = "".join(s.get("text", "") for s in _spans(message))
    assert rendered == message


def test_a_bare_label_is_not_a_construct():
    """`[LIVE DEMO]` written for effect must survive as typed."""
    assert _spans("[LIVE DEMO]") == [{"kind": "text", "text": "[LIVE DEMO]"}]


@pytest.mark.parametrize(
    "name, glyph", [("rocket", "🚀"), ("star", "⭐"), ("warning", "⚠️")]
)
def test_known_icon_shortcodes_resolve_to_a_glyph(name, glyph):
    (span,) = _spans(f":{name}:")
    assert span["text"] == glyph


def test_an_unknown_shortcode_stays_literal_text():
    assert _spans(":not-an-icon:") == [{"kind": "text", "text": ":not-an-icon:"}]


def test_an_icon_is_recognised_mid_line_not_only_in_column_zero():
    (span,) = _spans("hi :star: there")
    assert span["text"] == "hi ⭐ there"


# --------------------------------------------------------------------------- #
# Links and images (§4.3)
# --------------------------------------------------------------------------- #


def test_a_bracket_link_carries_its_target():
    assert _spans("[text](https://example.com/a)") == [
        {"kind": "link", "text": "text", "href": "https://example.com/a"}
    ]


def test_a_bare_url_is_autolinked_and_trailing_punctuation_stays_text():
    assert _spans("Visit https://example.com/a. Done") == [
        {"kind": "text", "text": "Visit "},
        {
            "kind": "link",
            "text": "https://example.com/a",
            "href": "https://example.com/a",
        },
        {"kind": "text", "text": ". Done"},
    ]


def test_a_link_can_carry_style_attributes_too():
    (span,) = _spans("[go](https://example.com){bold color=#8ee6ff}")
    assert span["href"] == "https://example.com"
    assert span["bold"] is True and span["color"] == "#8ee6ff"


def test_an_image_inside_link_text_becomes_a_clickable_logo():
    (span,) = _spans("[![logo](https://e.com/l.png)](https://e.com)")
    assert span == {
        "kind": "image",
        "src": "https://e.com/l.png",
        "alt": "logo",
        "href": "https://e.com",
    }


def test_an_image_height_is_clamped_into_range():
    (span,) = _spans("![l](https://e.com/l.png){height=9999}")
    assert span["height"] == 256


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:x",
        "//evil.example.com/x",
    ],
    ids=["js", "js-mixed-case", "data-html", "vbscript", "protocol-relative"],
)
def test_a_rejected_link_scheme_never_becomes_an_href(url, caplog):
    """The text is kept; only the target is dropped."""
    with caplog.at_level(logging.WARNING):
        spans = _spans(f"[click]({url})")
    assert spans == [{"kind": "text", "text": "click"}]
    assert not any("href" in s for s in spans)
    assert "login banner" in caplog.text


def test_a_root_relative_link_is_allowed():
    """A banner pointing at the deployment's own docs is a normal thing to want."""
    (span,) = _spans("[docs](/docs)")
    assert span["href"] == "/docs"


@pytest.mark.parametrize(
    "src",
    [
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "data:text/html;base64,PHA+eDwvcD4=",
        "data:image/png,notbase64!!",
        "javascript:alert(1)",
    ],
    ids=["svg", "html", "not-base64", "js"],
)
def test_a_rejected_image_source_never_becomes_a_src(src):
    assert not any(s.get("src") for s in _spans(f"![alt]({src})"))


def test_a_raster_data_uri_image_is_allowed():
    src = "data:image/png;base64,iVBORw0KGgo="
    (span,) = _spans(f"![dot]({src})")
    assert span["src"] == src


def test_images_beyond_the_cap_are_dropped(caplog):
    many = " ".join(
        f"![i{n}](https://e.com/{n}.png)" for n in range(BANNER_MAX_IMAGES + 3)
    )
    with caplog.at_level(logging.WARNING):
        images = [s for s in _spans(many) if s["kind"] == "image"]
    assert len(images) == BANNER_MAX_IMAGES
    assert "images" in caplog.text


# --------------------------------------------------------------------------- #
# Blocks (§4.4)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "line, kind",
    [
        ("# Title", "h1"),
        ("## Sub", "h2"),
        ("### Small", "h3"),
        ("> Quoted", "quote"),
        ("- Item", "li"),
        ("* Item", "li"),
        ("Plain", "p"),
        ("---", "rule"),
    ],
)
def test_each_block_prefix_selects_its_kind(line, kind):
    (block,) = parse_message(line, "test.yaml")
    assert block.kind == kind


def test_a_blank_line_becomes_one_spacer_however_many_were_typed():
    blocks = parse_message("a\n\n\n\nb", "test.yaml")
    assert [b.kind for b in blocks] == ["p", "spacer", "p"]


def test_trailing_spacers_are_trimmed():
    """They would render as dead space under the last line."""
    blocks = parse_message("a\n\n\n", "test.yaml")
    assert [b.kind for b in blocks] == ["p"]


def test_a_trailing_attribute_group_applies_to_the_whole_line():
    (block,) = parse_message("Centered and teal {align=center color=teal}", "test.yaml")
    assert block.align == "center"
    assert block.spans[0].to_dict()["color"] == "teal"
    assert block.spans[0].to_dict()["text"] == "Centered and teal"


def test_a_span_attribute_group_at_the_end_of_a_line_stays_inline():
    """`]` before `{` is what distinguishes a span's group from a block's."""
    (block,) = parse_message("plain then [styled]{color=red}", "test.yaml")
    assert block.align is None
    assert [s.to_dict().get("color") for s in block.spans] == [None, "red"]


def test_a_block_attribute_is_inherited_by_every_span_in_the_line():
    (block,) = parse_message("**a** and *b* {color=gold}", "test.yaml")
    assert all(s.to_dict()["color"] == "gold" for s in block.spans)


def test_a_heading_marker_is_not_confused_with_a_colour_literal():
    (block,) = parse_message("[x]{color=#fff}", "test.yaml")
    assert block.kind == "p"
    assert block.spans[0].to_dict()["color"] == "#fff"


# --------------------------------------------------------------------------- #
# Attribute and value validation (§5)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "value", ["#fff", "#ffff", "#ffd166", "#ffd166cc", "GOLD", " gold "]
)
def test_accepted_colour_spellings(value):
    (span,) = _spans(f"[x]{{color={value}}}")
    assert span["color"] == value.strip().lower()


@pytest.mark.parametrize(
    "value",
    [
        "rgb(255,0,0)",
        "url(https://e.com/x.png)",
        "notacolour",
        "#12345",
        "expression(alert(1))",
        "red;position:fixed",
    ],
    ids=["rgb", "url", "unknown-name", "bad-hex", "expression", "injection"],
)
def test_a_rejected_colour_is_dropped_and_the_text_kept(value, caplog):
    with caplog.at_level(logging.WARNING):
        spans = _spans(f"[keep me]{{color={value}}}")
    assert spans == [{"kind": "text", "text": "keep me"}]
    assert "color" in caplog.text


def test_an_unknown_attribute_is_dropped_and_the_text_kept(caplog):
    with caplog.at_level(logging.WARNING):
        spans = _spans("[keep me]{colour=red position=fixed}")
    assert spans == [{"kind": "text", "text": "keep me"}]
    assert "unknown attribute" in caplog.text


def test_an_unknown_size_or_animation_is_dropped():
    (span,) = _spans("[x]{size=enormous animate=explode}")
    assert "size" not in span and "animate" not in span


def test_a_flag_can_be_switched_off_explicitly():
    (span,) = _spans("[[x]{bold}]{bold=false}")
    # The inner `bold` still wins; the point is that `bold=false` parses.
    assert span["bold"] is True
    (other,) = _spans("[x]{bold=false}")
    assert "bold" not in other


def test_repeated_warnings_are_emitted_once(caplog):
    with caplog.at_level(logging.WARNING):
        parse_message("\n".join(f"[l{n}]{{colour=red}}" for n in range(6)), "test.yaml")
    unknown = [r for r in caplog.records if "unknown attribute" in r.getMessage()]
    assert len(unknown) == 1


# --------------------------------------------------------------------------- #
# The style mapping (§6)
# --------------------------------------------------------------------------- #


def test_a_full_style_block_round_trips():
    style = parse_style(
        {
            "gradient": ["#1b1141", "#4c1d72", "#0d1b3e"],
            "gradient_angle": 130,
            "text_color": "#f3edff",
            "border_color": "gold",
            "border_width": 2,
            "border_style": "dashed",
            "radius": 14,
            "padding": "lg",
            "align": "center",
            "font_size": "lg",
            "shadow": "lg",
            "glow": "#ffd166",
            "icon": "rocket",
            "image": "https://e.com/logo.png",
            "image_height": 40,
            "animate": ["pulse-border", "shimmer"],
        },
        "test.yaml",
    ).to_dict()
    assert style["gradient"] == ["#1b1141", "#4c1d72", "#0d1b3e"]
    assert style["icon"] == "🚀"
    assert style["animate"] == ["pulse-border", "shimmer"]
    assert style["border_style"] == "dashed"


def test_a_non_mapping_style_is_dropped_without_raising(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_style("gold", "test.yaml").to_dict() == {}
        assert parse_style(["gold"], "test.yaml").to_dict() == {}


def test_an_unknown_style_key_is_dropped(caplog):
    with caplog.at_level(logging.WARNING):
        style = parse_style({"onclick": "x", "background": "navy"}, "test.yaml")
    assert style.to_dict() == {"background": "navy"}
    assert "unknown style key" in caplog.text


def test_numeric_style_values_are_clamped_not_rejected():
    style = parse_style(
        {"radius": 999, "border_width": -4, "gradient_angle": 400}, "test.yaml"
    )
    assert (style.radius, style.border_width, style.gradient_angle) == (32, 0, 360)


def test_a_one_stop_gradient_is_not_a_gradient(caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_style({"gradient": ["navy"]}, "test.yaml").gradient == []


def test_a_gradient_drops_unusable_stops_and_caps_at_three():
    style = parse_style(
        {"gradient": ["navy", "rgb(1,2,3)", "gold", "#fff", "teal"]}, "test.yaml"
    )
    assert style.gradient == ["navy", "gold", "#fff"]


def test_a_gradient_may_be_written_as_a_string():
    assert parse_style({"gradient": "navy, gold"}, "test.yaml").gradient == [
        "navy",
        "gold",
    ]


def test_animate_accepts_a_bare_name_a_string_list_and_a_yaml_list():
    for value in ("shimmer", "shimmer, float", ["shimmer", "float"]):
        assert parse_style({"animate": value}, "test.yaml").animate[0] == "shimmer"


def test_an_unknown_animation_is_dropped_and_the_rest_kept():
    style = parse_style({"animate": ["shimmer", "explode", "float"]}, "test.yaml")
    assert style.animate == ["shimmer", "float"]


def test_a_literal_emoji_is_accepted_as_an_icon():
    assert parse_style({"icon": "🎉"}, "test.yaml").icon == "🎉"


@pytest.mark.parametrize("value", ["not-an-icon", "a whole sentence of text", 42])
def test_a_bad_icon_is_dropped(value, caplog):
    with caplog.at_level(logging.WARNING):
        assert parse_style({"icon": value}, "test.yaml").icon is None


def test_a_rejected_style_image_is_dropped():
    assert parse_style({"image": "javascript:alert(1)"}, "test.yaml").image is None


# --------------------------------------------------------------------------- #
# Caps (§5)
# --------------------------------------------------------------------------- #


def test_nodes_beyond_the_cap_are_dropped_with_one_warning(caplog):
    with caplog.at_level(logging.WARNING):
        blocks = parse_message(
            "\n".join(["word"] * (BANNER_MAX_NODES + 50)), "test.yaml"
        )
    nodes = len(blocks) + sum(len(b.spans) for b in blocks)
    assert nodes <= BANNER_MAX_NODES
    assert len([r for r in caplog.records if "nodes" in r.getMessage()]) == 1


def test_the_rich_line_cap_is_larger_than_the_plain_one(tmp_path, caplog):
    """Markup spends lines on presentation before any of them reach the reader."""
    assert BANNER_MAX_LINES > LOGIN_INFO_MAX_LINES
    message = "\n".join(f"line {n}" for n in range(LOGIN_INFO_MAX_LINES + 5))
    with caplog.at_level(logging.WARNING):
        cfg = load_config(_write(tmp_path, _rich_config(message)))
    # All of them survive into the banner...
    assert len(cfg.login_info_banner.blocks) == LOGIN_INFO_MAX_LINES + 5
    # ...while the plain text the CLI sees is still capped where it always was.
    assert len(cfg.login_info.split("\n")) == LOGIN_INFO_MAX_LINES


# --------------------------------------------------------------------------- #
# Plain-text degradation — the CLI contract (§8)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "message, expected",
    [
        ("**bold** and *italic*", "bold and italic"),
        ("~~struck~~ and ==marked==", "struck and marked"),
        ("`code`", "`code`"),
        ("[caps]{caps}", "CAPS"),
        ("# Heading", "Heading"),
        ("- Item", "- Item"),
        ("> Quoted", "> Quoted"),
        ("---", "---"),
        (":rocket: go", "🚀 go"),
        ("[label](https://e.com/a)", "label (https://e.com/a)"),
        ("[https://e.com/a](https://e.com/a)", "https://e.com/a"),
        ("[e.com/a](https://e.com/a)", "https://e.com/a"),
        ("![alt text](https://e.com/l.png)", "alt text"),
        ("[styled]{color=red size=3xl pill glow=gold}", "styled"),
    ],
    ids=[
        "emphasis",
        "strike-mark",
        "code-keeps-backticks",
        "caps-uppercases",
        "heading",
        "bullet",
        "quote",
        "rule",
        "icon",
        "link-with-label",
        "link-that-is-its-url",
        "link-whose-label-is-the-bare-host",
        "image-alt",
        "styling-vanishes",
    ],
)
def test_the_plain_degradation_of_every_construct(message, expected):
    assert _plain(message) == expected


def test_the_degraded_text_carries_no_markup_characters():
    """What a terminal is shown must not look like source."""
    plain = _plain(
        "# [!!!]{color=#ff7b7b size=lg} Store [LIVE DEMO]{bg=gold pill bold caps}\n"
        "## [**Visit us** and *drop us a star!*]{color=#ffe9a8}"
    )
    assert plain == "!!! Store LIVE DEMO\nVisit us and drop us a star!"
    for ch in ("{", "}", "**", "]("):
        assert ch not in plain


def test_a_configured_escape_sequence_still_cannot_reach_a_terminal(tmp_path):
    """The control-character strip runs before the markup parser, not instead.

    The escapes are YAML's own (a raw ESC byte is not legal in a YAML scalar at
    all), matching how ``test_login_info.py`` writes the same case.
    """
    cfg = load_config(
        _write(
            tmp_path,
            """
            mode: standalone
            standalone:
              users: []
              login_info:
                enabled: true
                format: rich
                message: "[red]{color=red} \\e[31mnope\\a"
            """,
        )
    )
    assert cfg.login_info_banner is not None
    for ch in cfg.login_info:
        assert not ("\x00" <= ch <= "\x1f" or "\x7f" <= ch <= "\x9f"), repr(ch)
    # The bytes that would have been the escape sequence survive as visible text.
    assert "[31mnope" in cfg.login_info


def test_the_style_block_never_leaks_into_the_plain_text(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            _rich_config(
                "Hello",
                """
                style:
                  gradient: ["navy", "gold"]
                  icon: rocket
                """,
            ),
        )
    )
    assert cfg.login_info == "Hello"
    assert cfg.login_info_banner.style.icon == "🚀"


# --------------------------------------------------------------------------- #
# Resolution through the config (§4.1, §11)
# --------------------------------------------------------------------------- #


def test_the_default_format_is_plain_and_leaves_no_banner(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            """
            mode: standalone
            standalone:
              users: []
              login_info:
                enabled: true
                message: "**not bold** here"
            """,
        )
    )
    assert cfg.login_info == "**not bold** here"
    assert cfg.login_info_banner is None


def test_an_unknown_format_falls_back_to_plain_with_a_warning(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        cfg = load_config(
            _write(
                tmp_path,
                """
                mode: standalone
                standalone:
                  users: []
                  login_info:
                    enabled: true
                    format: rish
                    message: "**text**"
                """,
            )
        )
    assert cfg.login_info == "**text**"
    assert cfg.login_info_banner is None
    assert "format" in caplog.text


def test_a_style_block_under_format_plain_is_ignored_quietly(tmp_path, caplog):
    """Staging a style before switching format is the same pattern as `enabled`."""
    with caplog.at_level(logging.WARNING):
        cfg = load_config(
            _write(
                tmp_path,
                """
                mode: standalone
                standalone:
                  users: []
                  login_info:
                    enabled: true
                    message: "plain"
                    style:
                      background: navy
                """,
            )
        )
    assert (cfg.login_info, cfg.login_info_banner) == ("plain", None)
    assert not caplog.records


def test_the_gate_still_governs_a_rich_banner(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            """
            mode: standalone
            standalone:
              users: []
              login_info:
                enabled: false
                format: rich
                message: "# Big"
            """,
        )
    )
    assert (cfg.login_info, cfg.login_info_banner) == (None, None)


def test_disabled_mode_drops_a_rich_banner_too(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            """
            mode: disabled
            standalone:
              users: []
              login_info:
                enabled: true
                format: rich
                message: "# Big"
            """,
        )
    )
    assert (cfg.login_info, cfg.login_info_banner) == (None, None)


def test_a_rich_message_with_nothing_renderable_falls_back_to_plain(tmp_path, caplog):
    with caplog.at_level(logging.WARNING):
        cfg = load_config(_write(tmp_path, _rich_config("   ")))
    assert (cfg.login_info, cfg.login_info_banner) == (None, None)


def test_an_image_only_banner_has_no_plain_text_but_still_renders(tmp_path):
    """The honest split: the UI has something to draw, a terminal does not."""
    cfg = load_config(
        _write(tmp_path, _rich_config("![](https://e.com/logo.png)")),
    )
    assert cfg.login_info is None
    assert cfg.login_info_banner is not None


def test_a_broken_style_block_never_stops_standalone_from_booting(tmp_path):
    cfg = load_config(
        _write(
            tmp_path,
            _rich_config(
                "Hello",
                """
                style: "not a mapping"
                """,
            ),
        )
    )
    assert cfg.mode == "standalone"
    assert cfg.login_info == "Hello"
    assert cfg.login_info_banner.style.to_dict() == {}


def test_the_rich_plain_text_is_still_capped_at_the_plain_char_limit(tmp_path):
    cfg = load_config(
        _write(tmp_path, _rich_config("x" * (LOGIN_INFO_MAX_CHARS + 500)))
    )
    assert len(cfg.login_info) == LOGIN_INFO_MAX_CHARS


# --------------------------------------------------------------------------- #
# The payload the UI reads (§7)
# --------------------------------------------------------------------------- #


def test_the_payload_is_json_serializable_and_versioned():
    banner = build_banner("# [Hi]{color=gold}", {"icon": "rocket"}, "test.yaml")
    payload = json.loads(json.dumps(banner.to_dict()))
    assert payload["version"] == PAYLOAD_VERSION
    assert payload["style"] == {"icon": "🚀"}
    assert payload["blocks"] == [
        {"kind": "h1", "spans": [{"kind": "text", "text": "Hi", "color": "gold"}]}
    ]


def test_an_empty_style_block_is_omitted_from_the_payload():
    payload = build_banner("Hi", None, "test.yaml").to_dict()
    assert "style" not in payload


def test_the_demo_config_ships_a_rich_banner_whose_plain_text_is_unchanged():
    """The shipped demo is the worked example, so pin what it promises.

    The message is deliberately written so that its degradation is exactly the
    four lines the config carried before the rich layer existed — which is the
    whole claim of §8 made concrete.
    """
    from pathlib import Path

    path = Path(__file__).resolve().parents[4] / "access_control_config.yaml"
    cfg = load_config(str(path))
    assert cfg.login_info_banner is not None
    assert cfg.login_info == (
        "!!! Skillberry Store LIVE DEMO !!!\n"
        "Visit us and drop us a star!\n"
        "https://github.com/skillberry-ai/skillberry-store\n"
        "Sign in as `skillberry` with password `skillberry`."
    )
