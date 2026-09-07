"""Rich login banner: markup parsing, style validation, plain-text degradation.

The plain login message (``standalone.login_info.message`` with the default
``format: plain``) is a string and nothing more — see
docs/design/login-info.md. This module implements the ``format: rich`` layer
described in docs/design/login-banner.md: a small Markdown-flavoured markup
that an operator can use to make the sign-in banner impossible to miss, with
per-span colors, sizes, weights, links, images, icons and badges, plus a
``style:`` block for the banner's own chrome.

Three properties shape everything here.

**Operator text never reaches an HTML parsing context.** The markup is parsed
*here*, on the server, into a tree of validated nodes; every color, size, URL
scheme, attribute name and animation name is checked against an allow-list
before it can enter that tree. What ships to the browser is JSON, HTML-escaped
into a ``<meta>`` attribute, which the SPA renders as React elements. There is
no HTML passthrough, no ``dangerouslySetInnerHTML`` and no sanitizer to get
wrong — a construct we do not understand stays visible text.

**Nothing here can raise.** Every malformed value is warned about and dropped,
keeping the surrounding text, because a login banner must never be able to stop
a standalone server from booting (docs/design/access-control.md §5.2). The
parser has no error path: unbalanced markers and unknown constructs degrade to
literal text.

**The rich tree always degrades to plain text.** :meth:`LoginBanner.to_plain_text`
is what ``sbs login`` and the ``GET /auth/whoami`` 401 keep showing — the same
message, without the presentation. The CLI is deliberately unaware that any of
this exists (docs/design/login-banner.md §8).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Payload schema version, carried in the JSON the SPA reads. The renderer
# refuses a version it does not know rather than guessing at unknown keys, so
# an older bundle served by a newer server degrades to the plain message
# instead of half-rendering.
PAYLOAD_VERSION = 1

# Caps for the *rich* message. The plain caps (1024 chars / 10 lines) are
# unchanged and still apply to the degraded text the CLI receives, so a rich
# banner cannot lengthen what a terminal is shown. Markup and (optionally)
# base64 image data both eat into the budget, hence the larger allowance here.
BANNER_MAX_CHARS = 8192
BANNER_MAX_LINES = 40
# Blocks plus spans. Bounds both the injected HTML and the work the renderer
# does on a pre-authentication page.
BANNER_MAX_NODES = 400
BANNER_MAX_IMAGES = 4
BANNER_MAX_URL_CHARS = 4096

# --------------------------------------------------------------------------- #
# Allow-lists. Everything an operator can write resolves to one of these or is
# dropped: no value from the config becomes a CSS declaration verbatim.
# --------------------------------------------------------------------------- #

_HEX_COLOR_RE = re.compile(r"\A#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\Z")

# CSS named colors are a 148-entry list; this is the useful subset, which keeps
# the allow-list reviewable and matches what the UI re-validates against.
NAMED_COLORS = frozenset("""
    black white red green blue yellow orange purple pink cyan magenta gray grey
    gold silver teal navy lime maroon olive indigo violet crimson coral salmon
    khaki turquoise tomato brown beige ivory tan plum orchid seagreen skyblue
    steelblue slateblue firebrick darkred darkgreen darkblue midnightblue
    transparent
    """.split())

SIZES = ("sm", "md", "lg", "xl", "2xl", "3xl")
PADDINGS = ("sm", "md", "lg")
ALIGNMENTS = ("left", "center", "right")
SHADOWS = ("none", "sm", "md", "lg")
BORDER_STYLES = ("solid", "dashed", "dotted", "double")

# Motion. Everything is off unless the operator names it, and the stylesheet
# suppresses all of it under `prefers-reduced-motion: reduce`.
BANNER_ANIMATIONS = (
    "pulse-border",
    "shimmer",
    "float",
    "glow-breathe",
    "gradient-shift",
)
SPAN_ANIMATIONS = ("shimmer", "pulse", "blink", "glow")

# Bare flags inside `{...}`.
SPAN_FLAGS = ("bold", "italic", "underline", "strike", "mono", "caps", "pill")
# Colour-valued attributes.
_COLOR_ATTRS = ("color", "bg", "glow")

# `:name:` shortcodes. Resolved to a glyph rather than to an icon component so
# that the rich banner and the plain CLI text show the same thing, and so the
# renderer needs no icon font or sprite on a pre-authentication page.
#
# Most are emoji; `code` is the store's own `</>` wordmark, which is ASCII. That
# it renders as a glyph and not as markup is guaranteed the same way every other
# character in a message is — it is escaped into an attribute and then rendered
# as a React text child, never parsed as HTML.
ICONS = {
    "code": "</>",
    "rocket": "🚀",
    "star": "⭐",
    "sparkles": "✨",
    "fire": "🔥",
    "warning": "⚠️",
    "info": "ℹ️",
    "check": "✅",
    "cross": "❌",
    "lock": "🔒",
    "unlock": "🔓",
    "key": "🔑",
    "bell": "🔔",
    "party": "🎉",
    "wave": "👋",
    "point-right": "👉",
    "bulb": "💡",
    "book": "📖",
    "gear": "⚙️",
    "heart": "❤️",
    "thumbs-up": "👍",
    "eyes": "👀",
    "zap": "⚡",
    "crown": "👑",
    "gift": "🎁",
    "berry": "🫐",
    "robot": "🤖",
    "hammer": "🔨",
    "package": "📦",
    "link": "🔗",
    "mail": "📧",
    "clock": "⏰",
    "hourglass": "⏳",
    "shield": "🛡️",
    "trophy": "🏆",
    "magic": "🪄",
    "target": "🎯",
    "flag": "🚩",
    "megaphone": "📣",
    "construction": "🚧",
    "snowflake": "❄️",
    "rainbow": "🌈",
    "globe": "🌐",
}

# `==mark==` defaults. A highlight has to set both halves of the pair or it can
# land dark-on-dark; an explicit `{...}` on an inner span still wins.
_MARK_DEFAULTS = {"bg": "#ffe066", "color": "#212121"}

LINK_SCHEMES = ("https://", "http://", "mailto:")
# SVG is excluded deliberately: it is a document format, and while a browser
# will not run script for one loaded through <img>, allow-listing it buys
# nothing a raster format does not already give a login banner.
IMAGE_DATA_PREFIXES = (
    "data:image/png;base64,",
    "data:image/jpeg;base64,",
    "data:image/gif;base64,",
    "data:image/webp;base64,",
)

_ESCAPABLE = set("\\`*_~=[]{}()!<>#:-|")

# Bare URLs are turned into links so an operator does not have to write the
# bracket form for the common case. Trailing sentence punctuation is trimmed
# below rather than matched here.
_AUTOLINK_RE = re.compile(r"(?:https?://|mailto:)[^\s<>\"'`\]\[{}]+")
_AUTOLINK_TRIM = ".,;:!?)"

# No `\A`/`^`: `Pattern.match(text, i)` already anchors at `i`, while those
# anchors would assert the start of the whole line and so only ever match an
# icon in column zero.
_ICON_RE = re.compile(r":([a-z0-9][a-z0-9-]{0,30}):")

# `key=value` or a bare flag, space-separated, inside `{...}`. Values may be
# quoted so a color name or an animation list can contain no spaces without
# the operator having to think about it.
_ATTR_RE = re.compile(
    r"""([a-z_]+)              # key
        (?:\s*=\s*
            (?:"([^"]*)"|'([^']*)'|([^\s}]*))   # quoted or bare value
        )?""",
    re.VERBOSE,
)


class _Ctx:
    """Warning sink and node budget for one parse.

    Warnings are emitted once per distinct message: an operator who wrote
    ``{colour=red}`` on ten lines needs to be told once, and a log line per
    span would bury the rest of a startup log.
    """

    def __init__(self, where: str) -> None:
        self.where = where
        self.nodes = 0
        self.images = 0
        self._seen: set = set()

    def warn(self, template: str, *args: Any) -> None:
        key = template % args if args else template
        if key in self._seen:
            return
        self._seen.add(key)
        logger.warning("login banner in %s: %s", self.where, key)

    def take_node(self) -> bool:
        """Claim one node from the budget, warning once when it runs out."""
        if self.nodes >= BANNER_MAX_NODES:
            self.warn(
                "more than %d nodes; the rest of the message was dropped",
                BANNER_MAX_NODES,
            )
            return False
        self.nodes += 1
        return True

    def take_image(self) -> bool:
        if self.images >= BANNER_MAX_IMAGES:
            self.warn(
                "more than %d images; the rest were dropped as text",
                BANNER_MAX_IMAGES,
            )
            return False
        self.images += 1
        return True


# --------------------------------------------------------------------------- #
# Value validation
# --------------------------------------------------------------------------- #


def validate_color(raw: Any, ctx: _Ctx, key: str = "color") -> Optional[str]:
    """A ``#hex`` or allow-listed named color, lowercased, else ``None``."""
    if not isinstance(raw, str):
        ctx.warn("%s=%r is not a string; ignoring", key, raw)
        return None
    value = raw.strip().lower()
    if _HEX_COLOR_RE.match(value) or value in NAMED_COLORS:
        return value
    ctx.warn(
        "%s=%r is not a #hex value or a known color name; ignoring", key, raw.strip()
    )
    return None


def _validate_url(
    raw: Any, ctx: _Ctx, schemes: Sequence[str], kind: str
) -> Optional[str]:
    """Shared URL check: length, no control characters, allow-listed scheme.

    Root-relative paths (``/docs``) are accepted so a banner can point at the
    deployment's own pages; ``//host/x`` is not, because a protocol-relative
    URL is an off-site link wearing a local disguise.
    """
    if not isinstance(raw, str):
        ctx.warn("%s URL %r is not a string; ignoring", kind, raw)
        return None
    value = raw.strip()
    if not value:
        return None
    if len(value) > BANNER_MAX_URL_CHARS:
        ctx.warn("a %s URL exceeds %d characters; ignoring", kind, BANNER_MAX_URL_CHARS)
        return None
    if any(ch <= "\x20" or ch in '"<>\\' or "\x7f" <= ch <= "\x9f" for ch in value):
        ctx.warn("a %s URL contains whitespace or unsafe characters; ignoring", kind)
        return None
    lowered = value.lower()
    if lowered.startswith("/") and not lowered.startswith("//"):
        return value
    if lowered.startswith(schemes):
        return value
    ctx.warn(
        "%s URL scheme in %r is not allowed (permitted: %s); ignoring",
        kind,
        value[:40],
        ", ".join(schemes),
    )
    return None


def validate_link_url(raw: Any, ctx: _Ctx) -> Optional[str]:
    return _validate_url(raw, ctx, LINK_SCHEMES, "link")


def validate_image_url(raw: Any, ctx: _Ctx) -> Optional[str]:
    """An image source: http(s), a root-relative path, or a raster ``data:`` URI."""
    if isinstance(raw, str) and raw.strip().lower().startswith("data:"):
        value = raw.strip()
        if len(value) > BANNER_MAX_URL_CHARS:
            ctx.warn(
                "an inline image exceeds %d characters; ignoring", BANNER_MAX_URL_CHARS
            )
            return None
        if value.lower().startswith(IMAGE_DATA_PREFIXES) and _is_base64_tail(value):
            return value
        ctx.warn(
            "inline image data must be base64 %s; ignoring",
            "/".join(p.split("/")[1].split(";")[0] for p in IMAGE_DATA_PREFIXES),
        )
        return None
    return _validate_url(raw, ctx, ("https://", "http://"), "image")


def _is_base64_tail(data_uri: str) -> bool:
    tail = data_uri.split(",", 1)[1] if "," in data_uri else ""
    return bool(tail) and not set(tail) - set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
    )


def _validate_choice(
    raw: Any, ctx: _Ctx, key: str, choices: Sequence[str]
) -> Optional[str]:
    if not isinstance(raw, str):
        ctx.warn("%s=%r is not a string; ignoring", key, raw)
        return None
    value = raw.strip().lower()
    if value in choices:
        return value
    ctx.warn("%s=%r is not one of %s; ignoring", key, raw, ", ".join(choices))
    return None


def _validate_int(raw: Any, ctx: _Ctx, key: str, low: int, high: int) -> Optional[int]:
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        ctx.warn("%s=%r is not a number; ignoring", key, raw)
        return None
    if not low <= value <= high:
        ctx.warn("%s=%d is outside %d-%d; clamping", key, value, low, high)
        value = max(low, min(high, value))
    return value


# --------------------------------------------------------------------------- #
# Attributes: the `{...}` suffix shared by spans and blocks
# --------------------------------------------------------------------------- #


def parse_attributes(raw: str, ctx: _Ctx) -> Tuple[Dict[str, Any], Optional[str]]:
    """Parse ``{color=#fff size=xl bold}`` into ``(span_style, align)``.

    ``align`` is separated out because it is meaningful on a block and not on
    an inline span. Unknown keys and unusable values are dropped with a
    warning; the text they decorated is always kept.
    """
    style: Dict[str, Any] = {}
    align: Optional[str] = None
    for match in _ATTR_RE.finditer(raw):
        key = match.group(1)
        value = next((g for g in match.group(2, 3, 4) if g is not None), None)
        if key in SPAN_FLAGS:
            # `bold` and `bold=false` are both spellings an operator will try.
            style[key] = value is None or value.strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            if not style[key]:
                del style[key]
        elif key in _COLOR_ATTRS:
            color = validate_color(value, ctx, key)
            if color:
                style[key] = color
        elif key == "size":
            size = _validate_choice(value, ctx, "size", SIZES)
            if size:
                style["size"] = size
        elif key == "animate":
            anim = _validate_choice(value, ctx, "animate", SPAN_ANIMATIONS)
            if anim:
                style["animate"] = anim
        elif key == "height":
            height = _validate_int(value, ctx, "height", 8, 256)
            if height:
                style["height"] = height
        elif key == "align":
            align = _validate_choice(value, ctx, "align", ALIGNMENTS) or align
        else:
            ctx.warn("unknown attribute %r; ignoring it", key)
    return style, align


# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


@dataclass
class Span:
    """One leaf of inline content: styled text, a link, or an image.

    Spans are deliberately *flat*. Nested markup (``**bold [red]{color=red}**``)
    is resolved during parsing by merging each level's style downwards, so the
    renderer walks a list and never a tree — which is also what keeps the JSON
    payload and the React renderer small.
    """

    text: str = ""
    href: Optional[str] = None
    src: Optional[str] = None
    alt: Optional[str] = None
    style: Dict[str, Any] = field(default_factory=dict)

    @property
    def kind(self) -> str:
        if self.src:
            return "image"
        return "link" if self.href else "text"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.kind == "image":
            out["src"] = self.src
            if self.alt:
                out["alt"] = self.alt
        else:
            out["text"] = self.text
        # An image inside link text (`[![logo](…)](https://…)`) is a clickable
        # logo, so `href` is not exclusive to text spans.
        if self.href:
            out["href"] = self.href
        # Style keys sit alongside, not nested: they cannot collide with the
        # keys above and a flatter payload is a smaller <meta> attribute.
        for key in sorted(self.style):
            out[key] = self.style[key]
        return out


@dataclass
class Block:
    """One line of the message: a paragraph, heading, bullet, quote or rule."""

    kind: str = "p"  # p | h1 | h2 | h3 | quote | li | rule | spacer
    spans: List[Span] = field(default_factory=list)
    align: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"kind": self.kind}
        if self.align:
            out["align"] = self.align
        if self.spans:
            out["spans"] = [s.to_dict() for s in self.spans]
        return out


@dataclass
class BannerStyle:
    """The banner's own chrome, from the ``style:`` mapping.

    Every field is optional and already validated; ``None``/empty means "the
    renderer's default", so the UI needs no knowledge of the YAML.
    """

    background: Optional[str] = None
    gradient: List[str] = field(default_factory=list)
    gradient_angle: Optional[int] = None
    text_color: Optional[str] = None
    border_color: Optional[str] = None
    border_width: Optional[int] = None
    border_style: Optional[str] = None
    radius: Optional[int] = None
    padding: Optional[str] = None
    align: Optional[str] = None
    font_size: Optional[str] = None
    shadow: Optional[str] = None
    glow: Optional[str] = None
    icon: Optional[str] = None
    # The mark is often not the same colour as the words beside it — the store's
    # own masthead puts a blue `</>` next to a white wordmark — so the icon gets
    # its own colour rather than inheriting the banner's.
    icon_color: Optional[str] = None
    image: Optional[str] = None
    image_height: Optional[int] = None
    animate: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for key, value in sorted(self.__dict__.items()):
            if value not in (None, [], ""):
                out[key] = value
        return out


@dataclass
class LoginBanner:
    """A parsed, fully validated rich login banner."""

    blocks: List[Block] = field(default_factory=list)
    style: BannerStyle = field(default_factory=BannerStyle)

    def __bool__(self) -> bool:
        return bool(self.blocks)

    def to_dict(self) -> Dict[str, Any]:
        """The JSON payload the SPA reads out of the ``<meta>`` tag."""
        out: Dict[str, Any] = {"version": PAYLOAD_VERSION}
        style = self.style.to_dict()
        if style:
            out["style"] = style
        out["blocks"] = [b.to_dict() for b in self.blocks]
        return out

    def to_plain_text(self) -> str:
        """The same message with the presentation removed.

        This is what ``standalone.login_info`` resolves to in rich mode, and so
        what ``sbs login`` prints and the ``GET /auth/whoami`` 401 carries. The
        aim is a line-for-line match with what the operator typed, minus the
        markup: a link whose text is its own URL collapses back to the URL,
        code spans keep their backticks because they read correctly in a
        terminal, and an icon keeps the glyph it renders as in the UI.
        """
        lines: List[str] = []
        for block in self.blocks:
            if block.kind == "rule":
                lines.append("---")
                continue
            if block.kind == "spacer":
                lines.append("")
                continue
            text = "".join(_span_plain_text(s) for s in block.spans).strip()
            if block.kind == "li":
                text = f"- {text}"
            elif block.kind == "quote" and text:
                text = f"> {text}"
            lines.append(text)
        return "\n".join(lines).strip()


def _span_plain_text(span: Span) -> str:
    if span.kind == "image":
        return span.alt or ""
    text = span.text
    if span.style.get("caps"):
        text = text.upper()
    if span.style.get("mono"):
        text = f"`{text}`"
    if span.kind == "link" and span.href:
        # `[https://x](https://x)` and `[x.com/y](https://x.com/y)` both came
        # from an operator writing one URL; echoing it twice would be noise.
        if _same_target(text, span.href):
            return span.href
        return f"{text} ({span.href})"
    return text


def _same_target(text: str, href: str) -> bool:
    def bare(value: str) -> str:
        for scheme in ("https://", "http://", "mailto:"):
            if value.lower().startswith(scheme):
                value = value[len(scheme) :]
                break
        return value.rstrip("/").lower()

    return bool(text) and bare(text) == bare(href)


# --------------------------------------------------------------------------- #
# Inline parsing
# --------------------------------------------------------------------------- #

# Paired inline markers, longest first so `**` wins over `*` and `~~` over `~`.
_MARKERS: Tuple[Tuple[str, Dict[str, Any]], ...] = (
    ("**", {"bold": True}),
    ("__", {"bold": True}),
    ("~~", {"strike": True}),
    ("==", dict(_MARK_DEFAULTS)),
    ("*", {"italic": True}),
    ("_", {"italic": True}),
)


def _find_marker_close(text: str, start: int, marker: str) -> int:
    """Index of the next unescaped ``marker`` at or after ``start``, or -1."""
    i = start
    while i < len(text):
        if text[i] == "\\":
            i += 2
            continue
        if text.startswith(marker, i):
            return i
        i += 1
    return -1


def _at_word_boundary(text: str, start: int, end: int) -> bool:
    """True when neither edge of ``text[start:end]`` sits inside a word."""
    before = text[start - 1] if start > 0 else " "
    after = text[end] if end < len(text) else " "
    return not before.isalnum() and not after.isalnum()


def _match_delim(text: str, start: int, open_ch: str, close_ch: str) -> int:
    """Index of the delimiter matching ``text[start] == open_ch``, or -1.

    Nesting-aware and escape-aware, so ``[a [b] c]`` and ``(http://x/(y))``
    both close where a reader expects.
    """
    if start >= len(text) or text[start] != open_ch:
        return -1
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Style inheritance: the inner (more specific) value wins."""
    merged = dict(base)
    merged.update(extra)
    return merged


def parse_inline(text: str, base: Dict[str, Any], ctx: _Ctx) -> List[Span]:
    """Parse one line of inline markup into a flat list of styled spans.

    Never raises and never rejects input: a construct that does not complete —
    an unclosed ``**``, a ``[label]`` with no target — is emitted as the literal
    characters the operator typed.
    """
    spans: List[Span] = []
    buf: List[str] = []

    def flush() -> None:
        if buf:
            content = "".join(buf)
            buf.clear()
            if ctx.take_node():
                spans.append(Span(text=content, style=dict(base)))

    def add(new: List[Span]) -> None:
        flush()
        spans.extend(new)

    i, n = 0, len(text)
    while i < n:
        ch = text[i]

        # Escapes: `\*` is a literal asterisk. A backslash before anything
        # else is itself literal, which is what a Windows path in a message
        # needs.
        if ch == "\\" and i + 1 < n and text[i + 1] in _ESCAPABLE:
            buf.append(text[i + 1])
            i += 2
            continue

        # Code span: opaque, so markup inside it stays visible text.
        if ch == "`":
            close = text.find("`", i + 1)
            if close > i + 1:
                flush()
                if ctx.take_node():
                    spans.append(
                        Span(
                            text=text[i + 1 : close],
                            style=_merge(base, {"mono": True}),
                        )
                    )
                i = close + 1
                continue

        # Image, link, or attribute span — all start with `[`, images with `![`.
        if ch == "!" and text.startswith("![", i):
            consumed = _parse_image(text, i + 1, base, ctx, add)
            if consumed:
                i += 1 + consumed
                continue
        if ch == "[":
            consumed = _parse_bracketed(text, i, base, ctx, add)
            if consumed:
                i += consumed
                continue

        # `:rocket:`
        if ch == ":":
            icon = _ICON_RE.match(text, i)
            if icon and icon.group(1) in ICONS:
                buf.append(ICONS[icon.group(1)])
                i = icon.end()
                continue

        # Paired emphasis markers.
        marker_hit = False
        for marker, marker_style in _MARKERS:
            if not text.startswith(marker, i):
                continue
            close = _find_marker_close(text, i + len(marker), marker)
            if close == -1:
                continue
            # `_` only emphasizes at a word boundary, the way GFM has it —
            # otherwise `login_info_message` in a message would come out
            # half-italic.
            if marker in ("_", "__") and not _at_word_boundary(
                text, i, close + len(marker)
            ):
                continue
            inner = text[i + len(marker) : close]
            if not inner.strip():
                # An empty pair is not emphasis. Without this, the second `*`
                # of a stray `**unclosed` would close the first, silently
                # swallowing both instead of leaving them as typed.
                continue
            add(parse_inline(inner, _merge(base, marker_style), ctx))
            i = close + len(marker)
            marker_hit = True
            break
        if marker_hit:
            continue

        # Bare URL.
        auto = _AUTOLINK_RE.match(text, i)
        if auto:
            url = auto.group(0).rstrip(_AUTOLINK_TRIM)
            href = validate_link_url(url, ctx)
            if href:
                flush()
                if ctx.take_node():
                    spans.append(Span(text=url, href=href, style=dict(base)))
                i += len(url)
                continue

        buf.append(ch)
        i += 1

    flush()
    return spans


def _read_group(
    text: str, i: int, open_ch: str, close_ch: str
) -> Tuple[Optional[str], int]:
    """``(contents, next_index)`` for an optional ``(...)`` / ``{...}`` group."""
    close = _match_delim(text, i, open_ch, close_ch)
    if close == -1:
        return None, i
    return text[i + 1 : close], close + 1


def _parse_image(text: str, i: int, base: Dict[str, Any], ctx: _Ctx, add) -> int:
    """``![alt](src){height=…}`` at ``text[i] == '['``; 0 if it does not close."""
    bracket_close = _match_delim(text, i, "[", "]")
    if bracket_close == -1:
        return 0
    src_raw, after = _read_group(text, bracket_close + 1, "(", ")")
    if src_raw is None:
        return 0
    attrs_raw, after = _read_group(text, after, "{", "}")
    src = validate_image_url(src_raw, ctx)
    if not src or not ctx.take_image():
        return 0
    style = dict(base)
    if attrs_raw is not None:
        extra, _ = parse_attributes(attrs_raw, ctx)
        style = _merge(style, extra)
    alt = text[i + 1 : bracket_close]
    if ctx.take_node():
        add([Span(src=src, alt=alt, style=style)])
    return after - i


def _parse_bracketed(text: str, i: int, base: Dict[str, Any], ctx: _Ctx, add) -> int:
    """``[text](href)``, ``[text]{attrs}`` or both; 0 if neither follows.

    A bare ``[label]`` is not a construct — it stays literal text, which is
    what an operator writing ``[LIVE DEMO]`` for effect expects.
    """
    bracket_close = _match_delim(text, i, "[", "]")
    if bracket_close == -1:
        return 0
    href_raw, after = _read_group(text, bracket_close + 1, "(", ")")
    attrs_raw, after = _read_group(text, after, "{", "}")
    if href_raw is None and attrs_raw is None:
        return 0
    style = dict(base)
    if attrs_raw is not None:
        extra, _ = parse_attributes(attrs_raw, ctx)
        style = _merge(style, extra)
    inner = parse_inline(text[i + 1 : bracket_close], style, ctx)
    if href_raw is not None:
        href = validate_link_url(href_raw, ctx)
        if href:
            for span in inner:
                # A nested link keeps its own target; the outer one only fills
                # in spans that have none.
                if span.href is None:
                    span.href = href
    add(inner)
    return after - i


# --------------------------------------------------------------------------- #
# Block parsing
# --------------------------------------------------------------------------- #

_BLOCK_PREFIXES: Tuple[Tuple[str, str], ...] = (
    ("### ", "h3"),
    ("## ", "h2"),
    ("# ", "h1"),
    ("> ", "quote"),
    ("- ", "li"),
    ("* ", "li"),
)


def _split_block_attributes(
    line: str, ctx: _Ctx
) -> Tuple[str, Dict[str, Any], Optional[str]]:
    """Peel a trailing ``{...}`` off a line, if it is a *block* attribute group.

    The distinguishing rule is the character before the brace: in
    ``[LIVE]{color=gold}`` it is ``]``, which makes the group belong to that
    inline span, while in ``Sign in now {align=center}`` it is whitespace,
    which makes it the block's. Anything else is left for the inline parser.
    """
    stripped = line.rstrip()
    if not stripped.endswith("}"):
        return line, {}, None
    open_idx = stripped.rfind("{")
    if open_idx <= 0 or stripped[open_idx - 1] not in " \t":
        return line, {}, None
    if _match_delim(stripped, open_idx, "{", "}") != len(stripped) - 1:
        return line, {}, None
    style, align = parse_attributes(stripped[open_idx + 1 : -1], ctx)
    if not style and align is None:
        return line, {}, None
    return stripped[:open_idx].rstrip(), style, align


def parse_message(message: str, where: str = "the login banner") -> List[Block]:
    """Parse a rich message into blocks. Never raises; drops what it cannot use."""
    ctx = _Ctx(where)
    blocks: List[Block] = []
    for raw_line in message.split("\n"):
        line = raw_line.strip()
        if not line:
            # Collapse runs of blank lines: one spacer is a paragraph break,
            # three is an accident.
            if blocks and blocks[-1].kind != "spacer":
                if ctx.take_node():
                    blocks.append(Block(kind="spacer"))
            continue
        if not ctx.take_node():
            break
        if set(line) <= {"-", "*", "_"} and len(line) >= 3:
            blocks.append(Block(kind="rule"))
            continue
        body, block_style, align = _split_block_attributes(line, ctx)
        kind = "p"
        for prefix, block_kind in _BLOCK_PREFIXES:
            if body.startswith(prefix):
                kind, body = block_kind, body[len(prefix) :]
                break
        spans = parse_inline(body, block_style, ctx)
        if not spans:
            continue
        blocks.append(Block(kind=kind, spans=spans, align=align))
    # A trailing spacer renders as dead space under the last line.
    while blocks and blocks[-1].kind == "spacer":
        blocks.pop()
    return blocks


# --------------------------------------------------------------------------- #
# The `style:` mapping
# --------------------------------------------------------------------------- #


def parse_style(raw: Any, where: str = "the login banner") -> BannerStyle:
    """Validate the ``style:`` mapping into a :class:`BannerStyle`.

    A non-mapping, an unknown key and an unusable value are each warned about
    and dropped; the banner then renders with the defaults for whatever was
    dropped rather than not rendering at all.
    """
    ctx = _Ctx(where)
    style = BannerStyle()
    if raw is None:
        return style
    if not isinstance(raw, dict):
        ctx.warn("style must be a mapping, got %r; ignoring it", raw)
        return style

    for key, value in raw.items():
        key = str(key)
        if key in ("background", "text_color", "border_color", "glow", "icon_color"):
            setattr(style, key, validate_color(value, ctx, key))
        elif key == "gradient":
            style.gradient = _parse_gradient(value, ctx)
        elif key == "gradient_angle":
            style.gradient_angle = _validate_int(value, ctx, key, 0, 360)
        elif key == "border_width":
            style.border_width = _validate_int(value, ctx, key, 0, 8)
        elif key == "radius":
            style.radius = _validate_int(value, ctx, key, 0, 32)
        elif key == "image_height":
            style.image_height = _validate_int(value, ctx, key, 8, 256)
        elif key == "border_style":
            style.border_style = _validate_choice(value, ctx, key, BORDER_STYLES)
        elif key == "padding":
            style.padding = _validate_choice(value, ctx, key, PADDINGS)
        elif key == "align":
            style.align = _validate_choice(value, ctx, key, ALIGNMENTS)
        elif key == "font_size":
            style.font_size = _validate_choice(value, ctx, key, SIZES)
        elif key == "shadow":
            style.shadow = _validate_choice(value, ctx, key, SHADOWS)
        elif key == "icon":
            style.icon = _parse_icon(value, ctx)
        elif key == "image":
            style.image = validate_image_url(value, ctx)
        elif key == "animate":
            style.animate = _parse_animations(value, ctx)
        else:
            ctx.warn("unknown style key %r; ignoring it", key)
    return style


def _parse_gradient(raw: Any, ctx: _Ctx) -> List[str]:
    """Two or three color stops. One stop is a ``background``, not a gradient."""
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,\s]+", raw.strip()) if part]
    if not isinstance(raw, (list, tuple)):
        ctx.warn("gradient must be a list of colors, got %r; ignoring", raw)
        return []
    stops = [c for c in (validate_color(v, ctx, "gradient") for v in raw) if c]
    if len(stops) < 2:
        if stops:
            ctx.warn("gradient needs at least 2 usable colors; ignoring it")
        return []
    if len(stops) > 3:
        ctx.warn("gradient takes at most 3 colors; using the first 3")
        stops = stops[:3]
    return stops


def _parse_icon(raw: Any, ctx: _Ctx) -> Optional[str]:
    """An allow-listed ``:name:`` shortcode or a literal glyph.

    A glyph is any short run of printable characters — an emoji, or a wordmark
    like ``</>``. The length cap is what makes this a glyph rather than a second
    message: chrome is a mark, and prose belongs in ``message``.

    ASCII is deliberately allowed. It is tempting to bar ``<`` and ``>`` here
    on the theory that they are dangerous, but that would be defending the
    wrong thing — the value is escaped into an attribute and rendered as a React
    text child, exactly like every other character in a message, and no
    character is special in either position. Barring them would only mean the
    store could not show its own wordmark.
    """
    if not isinstance(raw, str):
        ctx.warn("icon=%r is not a string; ignoring", raw)
        return None
    value = raw.strip().strip(":")
    if value in ICONS:
        return ICONS[value]
    literal = raw.strip()
    if literal and len(literal) <= 8 and not any(_is_control(ch) for ch in literal):
        return literal
    ctx.warn("icon=%r is not a known name and is too long for a glyph; ignoring", raw)
    return None


def _is_control(ch: str) -> bool:
    return "\x00" <= ch <= "\x1f" or "\x7f" <= ch <= "\x9f"


def _parse_animations(raw: Any, ctx: _Ctx) -> List[str]:
    if isinstance(raw, str):
        raw = [part for part in re.split(r"[,\s]+", raw.strip()) if part]
    if isinstance(raw, bool) or not isinstance(raw, (list, tuple)):
        ctx.warn("animate must be a name or a list of names, got %r; ignoring", raw)
        return []
    out: List[str] = []
    for value in raw:
        name = _validate_choice(value, ctx, "animate", BANNER_ANIMATIONS)
        if name and name not in out:
            out.append(name)
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_banner(
    message: str, raw_style: Any, where: str = "the login banner"
) -> Optional[LoginBanner]:
    """Parse ``message`` and ``raw_style`` into a banner, or ``None``.

    ``None`` means "there is nothing to render" — an empty message, or one
    whose every construct was dropped — and the caller then falls back to the
    plain message exactly as it would with ``format: plain``.
    """
    blocks = parse_message(message, where)
    if not blocks:
        return None
    return LoginBanner(blocks=blocks, style=parse_style(raw_style, where))
