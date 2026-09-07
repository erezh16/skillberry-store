# Rich Login Banner — Design

Status: **Implemented**
Owner: skillberry-store
Scope: `skillberry-store` — an operator-configurable *rich* presentation of the login information message on the UI sign-in screen, opted into with `standalone.login_info.format: rich`.

Related: [login-info.md](login-info.md) (the plain layer this extends — §5 resolution, §6 UI, §7 CLI, §8 REST), [access-control.md](access-control.md) (§5.1 schema, §5.2 validation).

---

## 1. Goals & Non-Goals

### Goals

* Let an operator make the sign-in banner **impossible to miss**: different text colours within one message, larger type, bold/italic/strike/underline, highlights, badges, links (explicit and embedded), embedded images and icons, a background or gradient ground, a coloured frame, a glow, and motion.
* Configure it **where the plain message is already configured** and in the same shape: `standalone.login_info`, with two additive keys — `format:` and `style:`.
* Keep the **CLI plain**. `sbs login` and `GET /auth/whoami` show the same message as text, with no markup and no escape sequences (§8). This is a requirement, not a limitation.
* Keep the **security posture of the plain layer unchanged**: operator text must never reach an HTML parsing context (§9).
* **Cost nothing when off.** `format` defaults to `plain`, so every config written before this feature resolves to exactly the value it did before.

### Non-Goals

* Not arbitrary HTML, Markdown-as-a-library, or CSS. The markup is a closed grammar with an allow-listed vocabulary (§4, §5); anything outside it is text.
* Not a rich CLI. No ANSI colour, no terminal layout — see §8 for why that is deliberate.
* Not per-tenant, per-user or localized, and not live-reloadable. Both inherited from login-info.md §1.
* Not a layout engine. One column, one block per line; the banner sits in the login card's flow.

---

## 2. Why parse on the server

The plain layer's central guarantee is that the operator's text is escaped into an inert `<meta>` attribute and never enters a JavaScript or HTML parsing context (login-info.md §10). Rich content is exactly the pressure that usually breaks such a guarantee: the tempting implementations are "allow a subset of HTML and sanitize it" or "render Markdown with raw HTML enabled", and both put operator text back into a parser whose escaping rules now have to be got right.

So the markup is parsed **on the server, into a validated tree**, and what ships to the browser is JSON:

```
YAML message  ──parse──▶  Block/Span tree  ──json+escape──▶  <meta content="…">  ──▶  React elements
                  │                  │
                  │                  └─ every colour, size, URL scheme, attribute
                  │                     and animation name matched an allow-list
                  └─ to_plain_text() ──▶ login_info ──▶ sbs login, whoami 401
```

Three consequences fall out of that shape, and each is worth more than the parser costs:

1. **There is no HTML string anywhere in the path.** Not on the server, not in the payload, not in the renderer. `dangerouslySetInnerHTML` is not used and could not be introduced without deleting the tree.
2. **The same tree renders both surfaces.** Plain-text degradation is a method on the parsed banner, not a second parse of the same string with different rules — so the two cannot drift.
3. **Validation happens once, at config load.** A malformed value is warned about in the startup log, where an operator will see it, rather than silently doing nothing at request time.

---

## 3. Configuration

### 3.1 Schema

```yaml
standalone:
  login_info:
    enabled: true                    # unchanged: the display gate
    format: rich                     # plain (default) | rich
    style:                           # rich only; every key optional
      background: "#0b1021"          # solid ground …
      gradient: ["#1b1141", "#4c1d72", "#0d1b3e"]   # … or 2-3 stops
      gradient_angle: 130            # 0-360
      text_color: "#f3edff"
      border_color: "#ffd166"
      border_width: 2                # 0-8 px
      border_style: solid            # solid | dashed | dotted | double
      radius: 14                     # 0-32 px
      padding: lg                    # sm | md | lg
      align: center                  # left | center | right
      font_size: lg                  # sm | md | lg | xl — the banner's base scale
      shadow: lg                     # none | sm | md | lg
      glow: "#ffd166"                # coloured outer glow
      icon: code                     # allow-listed name, or a literal glyph
      icon_color: "#0066cc"          # the mark need not match the words
      image: "https://…/logo.png"    # chrome image above the text
      image_height: 48               # 8-256 px
      animate: [pulse-border, shimmer]   # off unless named; §7.3
    message: |                       # the markup; see §4
      # [!!!]{color=#ff7b7b size=lg} Store [LIVE DEMO]{bg=#ffd166 pill bold caps}
      ## [**Visit us** and *drop us a star!*]{color=#ffe9a8}
      [https://github.com/x/y](https://github.com/x/y){bold color=#8ee6ff}
```

`enabled`, `format`, `style` and `message` are four independent controls, extending the "set it before switching it on" property of the plain layer (login-info.md §4.1): a `style` block can be committed and reviewed while `format` is still `plain`, and `format: rich` can be committed while `enabled` is still false.

### 3.2 Validation — warn and drop, never fail closed

Every row here is a warning plus a drop, never an `AccessControlConfigError`. A login banner must not be able to stop a standalone server from booting (access-control.md §5.2).

| Condition | Result |
|---|---|
| `format` not `plain`/`rich` | warn; treated as `plain`, so the text is still shown |
| `style` not a mapping | warn; whole block dropped, banner renders with defaults |
| Unknown `style` key | warn; that key dropped |
| Unusable `style` value (bad colour, unknown enum) | warn; that key dropped |
| Numeric `style` value out of range | warn; **clamped**, not dropped |
| `gradient` with fewer than 2 usable stops | warn; not a gradient (a single colour is a `background`) |
| Unknown inline attribute (`{colour=red}`) | warn; attribute dropped, **text kept** |
| Rejected colour / URL scheme | warn; value dropped, **text kept** |
| Unclosed or unknown construct | **literal text**, no warning — it may well be intentional |
| Over the node / image caps | warn once; the excess dropped |
| `format: rich` but nothing renderable | warn; falls back to treating the message as plain |
| `style` present under `format: plain` | debug only — this is the staging pattern, not a mistake |

Warnings are emitted **once per distinct message** per parse. An operator who typed `{colour=red}` on ten lines needs telling once; ten identical lines would bury the rest of a startup log.

---

## 4. The markup

Line-based: one block per line, blank lines separate paragraphs. Nothing spans lines, which is what makes a truncated or malformed message degrade cleanly.

### 4.1 Blocks

| Syntax | Block |
|---|---|
| `# `, `## `, `### ` | heading, levels 1-3 |
| `> ` | callout with a left accent strip |
| `- `, `* ` | bullet item |
| `---` (3+ of `-`, `*`, `_`) | divider rule |
| *blank line* | one spacer, however many blank lines were typed |
| anything else | paragraph |
| trailing `{…}` after whitespace | block-level attributes (§4.4) |

### 4.2 Inline

| Syntax | Effect |
|---|---|
| `**bold**`, `__bold__` | bold |
| `*italic*`, `_italic_` | italic — `_` only at a word boundary, so `login_info_message` survives intact |
| `` `code` `` | monospace, and **opaque**: markup inside it stays text |
| `~~strike~~` | strikethrough |
| `==mark==` | highlight (sets both background and foreground, so it cannot land dark-on-dark) |
| `[text](url)` | link |
| `https://…`, `mailto:…` | autolinked; trailing sentence punctuation stays text |
| `![alt](src)` | image |
| `[text]{…}` | attribute span — §4.3 |
| `[text](url){…}` | both |
| `:rocket:` | icon (§4.5) |
| `\*`, `\[`, `\{`, … | literal character |

Nested markup **merges downwards** and the innermost value wins: `**[big]{size=2xl color=red}**` is bold *and* red *and* 2xl. Spans are flattened during parsing, so the renderer walks a list rather than a tree.

An unbalanced or incomplete construct is **never an error**: `**unclosed`, `[label` and `[label](https://x` all come out as the characters that were typed. A bare `[LIVE DEMO]` with no `(` or `{` after it is likewise not a construct — writing brackets for effect is a normal thing to do.

### 4.3 Attributes

Inside `{…}`, space-separated, values optionally quoted:

| Key | Values |
|---|---|
| `color=`, `bg=`, `glow=` | a colour (§5) |
| `size=` | `sm` `md` `lg` `xl` `2xl` `3xl` |
| `animate=` | `shimmer` `pulse` `blink` `glow` |
| `height=` | 8-256, images only |
| `align=` | `left` `center` `right`, blocks only |
| bare flags | `bold` `italic` `underline` `strike` `mono` `caps` `pill` |

A flag can be written `bold` or `bold=true`, and switched off with `bold=false`.

### 4.4 Block attributes vs. span attributes

Both end a line with `}`, so the rule is **the character before the `{`**:

```
Sign in now {align=center color=teal}      ← whitespace before `{` → the block's
plain then [styled]{color=red}            ← `]` before `{`        → that span's
```

A block's attributes are inherited by every span on the line, so `**a** and *b* {color=gold}` makes both gold.

### 4.5 Icons

`:name:` resolves to a glyph from a fixed table (`rocket`, `star`, `sparkles`, `fire`, `warning`, `lock`, `party`, `zap`, … plus `code` for the store's own `</>` wordmark — see `ICONS` in [`login_banner.py`](../../src/skillberry_store/access_control/login_banner.py)). An unknown name stays literal text.

Glyphs rather than an icon component, for two reasons: the same glyph then appears in the UI *and* in the plain text a terminal receives, and a pre-authentication page needs no icon font or sprite to render it.

`style.icon` also takes a literal glyph, and it may be **ASCII** — `icon: "</>"` is the same value `icon: code` resolves to. It is tempting to bar `<` and `>` in a style value on the theory that they are dangerous, but that would be defending the wrong thing: the value is escaped into an attribute and then rendered as a React text child, where no character is special, exactly like every other character in a message. Barring them would only mean the store could not show its own wordmark. The real check is a length cap plus "no control characters" — chrome is a mark, and prose belongs in `message`.

`style.icon_color` colours the mark independently of `text_color`, because a logotype and the words beside it are often not the same colour — the store's masthead paints a blue `</>` next to a white wordmark, and a banner that matches it needs both.

---

## 5. Value allow-lists

No value from the config becomes a CSS declaration or a URL verbatim. Sizes, paddings, shadows, border styles and animation names are enum tokens looked up in fixed tables on both sides. That leaves two free-form value types, and both are constrained:

**Colours** — `#rgb`, `#rgba`, `#rrggbb`, `#rrggbbaa`, or one of ~45 allow-listed names. Everything else is dropped: `rgb(255,0,0)`, `url(…)`, `expression(…)`, `red;position:fixed`, unknown names.

**URLs** — no whitespace, no `"`, `<`, `>` or `\`, capped at 4096 characters, and:

| | Links | Images |
|---|---|---|
| `https://`, `http://` | ✅ | ✅ |
| `mailto:` | ✅ | ❌ |
| `/root-relative` | ✅ | ✅ |
| `//protocol-relative` | ❌ — an off-site link that reads as a local path | ❌ |
| `data:image/{png,jpeg,gif,webp};base64,` | ❌ | ✅ |
| `data:image/svg+xml` | ❌ | ❌ — a document format; buys nothing a raster does not |
| `data:text/html`, `javascript:`, anything else | ❌ | ❌ |

### 5.1 Caps

| | Plain | Rich |
|---|---|---|
| Characters | 1024 | 8192 |
| Lines | 10 | 40 |
| Nodes (blocks + spans) | — | 400 |
| Images | — | 4 |

The rich caps are larger because markup, and optionally an inline image, spend characters before any of them reach the reader. The node cap bounds both the injected HTML and the work the renderer does on a pre-authentication page.

**The plain caps still apply to the degraded text**, re-applied after degradation — so what a terminal is shown cannot grow just because the UI banner did.

---

## 6. Serving

Resolution produces two independent values on `AccessControlConfig`:

* `login_info: Optional[str]` — the plain text. Unchanged in `plain` mode; the **degradation** in `rich` mode. Every existing surface reads this and nothing else.
* `login_info_banner: Optional[LoginBanner]` — the parsed tree, set only in `rich` mode.

The control-character strip runs **before** the markup parser, not instead of it, so a configured ANSI escape still cannot survive into the degraded text (login-info.md §5 step 4).

`index.html` gains a **second** `<meta>` tag rather than a replacement:

```html
<meta name="sbs-login-info" content="…plain text…">
<meta name="sbs-login-banner" content="{&quot;version&quot;:1,&quot;style&quot;:{…},&quot;blocks&quot;:[…]}">
```

Two tags because they answer different questions — what the message says, and how to present it — and because that makes the plain tag the SPA's fallback if the payload is unusable (§7.1). They are also genuinely independent: an image-only banner has no plain text, and `format: plain` has no banner, so **either alone** makes the page active.

JSON in an attribute rather than a `<script type="application/json">` block: `html.escape` makes an attribute inert in one step, while escaping for a script context means reasoning about `</script>` and `<!--` inside the data. The payload is serialized `ensure_ascii=True` so an emoji survives a bundle served as anything but UTF-8.

Everything else about the serving path is unchanged from login-info.md §6.1-6.2 — same injection point, same HEAD handling, same cache directives, same untouched `FileResponse` when nothing is configured.

---

## 7. UI

### 7.1 Parse, then re-validate

[`types/loginBanner.ts`](../../src/skillberry_store/ui/src/types/loginBanner.ts) parses the payload and re-checks every value against the same allow-lists the server used. This is not distrust of our own server; it is what makes the failure mode correct. `parseBannerPayload` returns `null` for a missing tag, malformed JSON, an unknown `version`, or a payload whose every block was unusable — and `LoginPage` then renders the plain `Alert` it renders today. **A login screen must always appear**, so every failure has to be a fallback rather than an exception.

It is also a second barrier at the point of use: `safeHref` is what keeps `javascript:` out of an `href` attribute, independently of the server having already rejected it.

### 7.2 Rendering

[`LoginBanner.tsx`](../../src/skillberry_store/ui/src/components/LoginBanner.tsx) assembles React elements from already-safe values. Size, padding and shadow tokens are looked up in fixed tables; colours are the only free-form values, and they matched a hex/name pattern twice.

Two details are deliberate:

* **Headings are scaled `div`s, not `h1`-`h3`.** "Sign in to Skillberry Store" is the page's heading; a decorative banner should not outrank it in the document outline.
* **No `aria-label` on the region.** Every styled span is real text in document order, so a screen reader already reads the message correctly; labelling the region with the same words would announce them twice. The decorative parts — the chrome image and the icon glyph — are hidden from it instead (`alt=""`, `aria-hidden`).

The card widens from 420px to 560px when a banner is present: headings and badges need the room, and a banner squeezed into the form's width stops looking deliberate.

### 7.3 Motion

[`login-banner.css`](../../src/skillberry_store/ui/src/styles/login-banner.css) holds the keyframes — the codebase's first — plus the rules inline styles cannot express: the shimmer pseudo-element, the quote strip, and overrides for two `global.css` rules that would otherwise repaint banner text and code chips.

Banner-level: `pulse-border`, `shimmer`, `float`, `glow-breathe`, `gradient-shift`. Span-level: `shimmer`, `pulse`, `blink`, `glow`. All off unless named in the config.

**The element-level animations are composed into one inline `animation` shorthand**, not applied as four CSS classes. `animation` is a shorthand, so a second class setting it on the same element *replaces* the first rather than adding to it — `animate: [pulse-border, gradient-shift]` as two classes silently runs only whichever rule came last in the stylesheet. This was found by driving the real page, and it is why the reduced-motion block needs `!important`: a media query cannot otherwise outrank an inline style.

Everything stops under `prefers-reduced-motion: reduce`. Motion here is decoration and never the only carrier of meaning, so a viewer who has asked for less of it loses nothing but the movement. The shimmer band is removed (`content: none`) rather than paused, because a frozen bright stripe across the banner reads as a rendering fault.

### 7.4 `make ui-dev` still does not show it

Same limitation as the plain banner (login-info.md §6.4): Vite serves its own `index.html`, so neither tag is injected. The bundle must be served by the FastAPI app.

---

## 8. The CLI stays plain — by design

`sbs login` prints the message as text, exactly as it did before this feature existed. Not a limitation to be lifted later; three things follow from it:

* **No change to [`sdk_cli.py`](../../skillberry-common/scripts/sdk_cli.py) or its generated copy**, so no SDK regeneration and no risk of drift between template and copy.
* **No change to the `GET /auth/whoami` 401 body.** It carries `{detail, login_info}` and nothing else — `login_info` being the plain degradation. No banner key, no JSON payload, and `test_whoami_401_carries_only_plain_text_under_format_rich` asserts the whole body under a rich config.
* **The terminal-safety property is kept for free.** A terminal never receives markup, and never receives an escape sequence: the CLI is handed a string that went through the same control-character strip as before, and the server does not generate escapes of its own.

An older CLI against a newer server, and vice versa, both behave exactly as today.

---

## 9. Security considerations

Everything in login-info.md §10 still applies — the message is served **pre-authentication** to anyone who can reach `/ui/` or `GET /auth/whoami`, and must contain no secrets. Rich content adds four concerns:

1. **Markup could become markup.** Addressed structurally: the operator's text is parsed into a tree of validated nodes and the renderer emits React elements. There is no HTML string in the path and no sanitizer to get wrong. `<script>alert(1)</script>` in a message is visible text, asserted in both the Python and vitest suites.
2. **A URL could become an execution vector.** Scheme allow-lists at both ends (§5), with `javascript:` and `data:text/html` explicitly tested in several spellings, and every link rendered `rel="noopener noreferrer"` so a page opened from a pre-auth screen gets no handle on this window.
3. **A style value could become a CSS injection.** No operator string becomes a CSS declaration: enums are table lookups, colours match a hex/name pattern, lengths are integers clamped to a range. `red;position:fixed` is rejected as a colour.
4. **An `image:` URL leaks a request.** A remote image means the pre-auth login page fetches from a third party, exposing viewer IPs to it. `data:` URIs are allowed precisely so a logo can be self-contained; the caps in §5.1 bound how large one gets.

---

## 10. Backward compatibility

* **Off by default.** `format` defaults to `plain`; with no `format` key, resolution is byte-identical to before, `login_info_banner` is `None`, and only the one `<meta>` tag is injected. The existing suites pass unmodified.
* **No REST or CLI change** (§8), so **no SDK regeneration**, no OpenAPI change, no new route and no `unauthenticated_paths` entry.
* **No new environment variable.**
* **Additive dataclass field.** `login_info` keeps its type and meaning, so every existing reader — `auth_api`, `server`, the tests — is unaffected.
* **A UI rebuild is needed once** to ship the renderer, but a *config* edit still does not force one: the message is not a build input.
* **An old bundle against a new server** ignores the banner tag and renders the plain message from the tag it does know — which is the same path a `version` bump would take (§7.1).

---

## 11. Test plan

| Layer | Suite | What it pins |
|---|---|---|
| Parser | [`test_login_banner.py`](../../src/skillberry_store/tests/access_control/test_login_banner.py) | every construct and its plain degradation (a parametrized table — the §8 contract as data); attribute and value allow-lists including `javascript:` in five spellings, `data:text/html`, `image/svg+xml`, `rgb(…)`, `expression(…)`, `red;position:fixed`; unbalanced markers; all four caps; `style` coercion and clamping; and that dressing a realistic four-line message up with a heading, a badge, colours and an embedded link leaves its plain text byte-identical |
| REST | [`test_access_control.py`](../../src/skillberry_store/tests/fast_api/test_access_control.py) | with a rich banner configured, the 401 body is still exactly `{detail, login_info}` with plain text, no markup, no escape sequence, no banner key |
| Injection | [`test_login_info_page.py`](../../src/skillberry_store/tests/fast_api/test_login_info_page.py) | both tags before `</head>`; JSON escaped out of attribute syntax; ASCII-only payload; either tag alone sufficient; inert with neither |
| Route | [`test_ui_serving.py`](../../src/skillberry_store/tests/fast_api/test_ui_serving.py) | both tags on `/ui/`, `/ui/index.html`, `/ui/login`; HEAD length; no banner tag under `format: plain`; the built bundle references the banner tag name |
| Renderer | [`LoginBanner.test.tsx`](../../src/skillberry_store/ui/src/components/LoginBanner.test.tsx) | hostile and unknown payload values degrade rather than throw; safe values reach the DOM; no element from markup; `noopener noreferrer`; no `h1`-`h3`; animations compose |
| Page | [`LoginPage.login-info.test.tsx`](../../src/skillberry_store/ui/src/pages/LoginPage.login-info.test.tsx) | banner wins when both tags are present; plain alert when the payload is unusable |

Note that vitest is not part of `make test` (see the comment in `skillberry-common/.mk/dev.mk`); run the two UI suites with `make ui-test UI_TEST_ARGS=…`.
