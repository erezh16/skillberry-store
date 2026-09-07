// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0
//
// The rich login banner payload, and the parser that turns the injected
// `<meta name="sbs-login-banner">` attribute back into it.
//
// The server has already validated everything in this payload against
// allow-lists in `access_control/login_banner.py` — no value here became a CSS
// declaration or a URL without passing one. This module re-checks the same
// allow-lists anyway, for two reasons: it is cheap, and it means an
// unrecognized or truncated payload degrades to the plain message instead of
// producing a broken login page. A login screen must render.
//
// See docs/design/login-banner.md §7.

/** Payload schema version this bundle understands. */
export const BANNER_PAYLOAD_VERSION = 1;

/** Injected by the server; see fast_api/login_info.py. */
export const BANNER_META_NAME = 'sbs-login-banner';

export type BannerSize = 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl';
export type BannerAlign = 'left' | 'center' | 'right';
export type BannerSpanAnimation = 'shimmer' | 'pulse' | 'blink' | 'glow';
export type BannerAnimation =
  | 'pulse-border'
  | 'shimmer'
  | 'float'
  | 'glow-breathe'
  | 'gradient-shift';
export type BlockKind = 'p' | 'h1' | 'h2' | 'h3' | 'quote' | 'li' | 'rule' | 'spacer';

export interface BannerSpan {
  kind: 'text' | 'link' | 'image';
  text?: string;
  href?: string;
  src?: string;
  alt?: string;
  color?: string;
  bg?: string;
  glow?: string;
  size?: BannerSize;
  height?: number;
  animate?: BannerSpanAnimation;
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  mono?: boolean;
  caps?: boolean;
  pill?: boolean;
}

export interface BannerBlock {
  kind: BlockKind;
  align?: BannerAlign;
  spans?: BannerSpan[];
}

export interface BannerStyle {
  background?: string;
  gradient?: string[];
  gradient_angle?: number;
  text_color?: string;
  border_color?: string;
  border_width?: number;
  border_style?: 'solid' | 'dashed' | 'dotted' | 'double';
  radius?: number;
  padding?: 'sm' | 'md' | 'lg';
  align?: BannerAlign;
  font_size?: BannerSize;
  shadow?: 'none' | 'sm' | 'md' | 'lg';
  glow?: string;
  icon?: string;
  icon_color?: string;
  image?: string;
  image_height?: number;
  animate?: BannerAnimation[];
}

export interface LoginBannerPayload {
  version: number;
  style: BannerStyle;
  blocks: BannerBlock[];
}

// --------------------------------------------------------------------------- #
// Allow-lists — the mirror of access_control/login_banner.py
// --------------------------------------------------------------------------- #

const HEX_COLOR = /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;

const NAMED_COLORS = new Set(
  `black white red green blue yellow orange purple pink cyan magenta gray grey
   gold silver teal navy lime maroon olive indigo violet crimson coral salmon
   khaki turquoise tomato brown beige ivory tan plum orchid seagreen skyblue
   steelblue slateblue firebrick darkred darkgreen darkblue midnightblue
   transparent`.split(/\s+/)
);

const SIZES: BannerSize[] = ['sm', 'md', 'lg', 'xl', '2xl', '3xl'];
const ALIGNMENTS: BannerAlign[] = ['left', 'center', 'right'];
const BLOCK_KINDS: BlockKind[] = ['p', 'h1', 'h2', 'h3', 'quote', 'li', 'rule', 'spacer'];
const SPAN_ANIMATIONS: BannerSpanAnimation[] = ['shimmer', 'pulse', 'blink', 'glow'];
const BANNER_ANIMATIONS: BannerAnimation[] = [
  'pulse-border',
  'shimmer',
  'float',
  'glow-breathe',
  'gradient-shift',
];
const BORDER_STYLES = ['solid', 'dashed', 'dotted', 'double'] as const;
const PADDINGS = ['sm', 'md', 'lg'] as const;
const SHADOWS = ['none', 'sm', 'md', 'lg'] as const;

const LINK_SCHEMES = ['https://', 'http://', 'mailto:'];
const IMAGE_DATA_PREFIXES = [
  'data:image/png;base64,',
  'data:image/jpeg;base64,',
  'data:image/gif;base64,',
  'data:image/webp;base64,',
];

/** A `#hex` or allow-listed named color, else undefined. */
export function safeColor(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const v = value.trim().toLowerCase();
  return HEX_COLOR.test(v) || NAMED_COLORS.has(v) ? v : undefined;
}

/**
 * A link target the page may navigate to, else undefined.
 *
 * The scheme check is the point: it is what keeps `javascript:` out of an
 * `href`, independently of the server having already rejected it.
 * Protocol-relative `//host` is refused because it is an off-site link that
 * reads as a local path.
 */
export function safeHref(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const v = value.trim();
  if (!v || /[\s"'<>\\]/.test(v)) return undefined;
  const lower = v.toLowerCase();
  if (lower.startsWith('//')) return undefined;
  if (lower.startsWith('/')) return v;
  return LINK_SCHEMES.some((s) => lower.startsWith(s)) ? v : undefined;
}

/** An image source: http(s), a root-relative path, or a raster `data:` URI. */
export function safeImageSrc(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined;
  const v = value.trim();
  const lower = v.toLowerCase();
  if (lower.startsWith('data:')) {
    return IMAGE_DATA_PREFIXES.some((p) => lower.startsWith(p)) ? v : undefined;
  }
  if (lower.startsWith('mailto:')) return undefined;
  return safeHref(v);
}

function oneOf<T extends string>(value: unknown, choices: readonly T[]): T | undefined {
  return typeof value === 'string' && (choices as readonly string[]).includes(value)
    ? (value as T)
    : undefined;
}

function safeInt(value: unknown, low: number, high: number): number | undefined {
  const n = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(n)) return undefined;
  return Math.min(high, Math.max(low, Math.round(n)));
}

const flag = (value: unknown): boolean | undefined => (value === true ? true : undefined);

function safeSpan(raw: unknown): BannerSpan | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const src = safeImageSrc(r.src);
  const kind = src ? 'image' : safeHref(r.href) ? 'link' : 'text';
  const text = typeof r.text === 'string' ? r.text : '';
  // A text span with no text and no image is nothing to draw.
  if (kind !== 'image' && !text) return null;
  return {
    kind,
    text,
    href: safeHref(r.href),
    src,
    alt: typeof r.alt === 'string' ? r.alt : undefined,
    color: safeColor(r.color),
    bg: safeColor(r.bg),
    glow: safeColor(r.glow),
    size: oneOf(r.size, SIZES),
    height: r.height === undefined ? undefined : safeInt(r.height, 8, 256),
    animate: oneOf(r.animate, SPAN_ANIMATIONS),
    bold: flag(r.bold),
    italic: flag(r.italic),
    underline: flag(r.underline),
    strike: flag(r.strike),
    mono: flag(r.mono),
    caps: flag(r.caps),
    pill: flag(r.pill),
  };
}

function safeBlock(raw: unknown): BannerBlock | null {
  if (!raw || typeof raw !== 'object') return null;
  const r = raw as Record<string, unknown>;
  const kind = oneOf(r.kind, BLOCK_KINDS);
  if (!kind) return null;
  if (kind === 'rule' || kind === 'spacer') return { kind };
  const spans = Array.isArray(r.spans)
    ? (r.spans.map(safeSpan).filter(Boolean) as BannerSpan[])
    : [];
  if (!spans.length) return null;
  return { kind, align: oneOf(r.align, ALIGNMENTS), spans };
}

function safeStyle(raw: unknown): BannerStyle {
  if (!raw || typeof raw !== 'object') return {};
  const r = raw as Record<string, unknown>;
  const gradient = Array.isArray(r.gradient)
    ? (r.gradient.map(safeColor).filter(Boolean) as string[])
    : [];
  return {
    background: safeColor(r.background),
    gradient: gradient.length >= 2 ? gradient.slice(0, 3) : undefined,
    gradient_angle: r.gradient_angle === undefined ? undefined : safeInt(r.gradient_angle, 0, 360),
    text_color: safeColor(r.text_color),
    border_color: safeColor(r.border_color),
    border_width: r.border_width === undefined ? undefined : safeInt(r.border_width, 0, 8),
    border_style: oneOf(r.border_style, BORDER_STYLES),
    radius: r.radius === undefined ? undefined : safeInt(r.radius, 0, 32),
    padding: oneOf(r.padding, PADDINGS),
    align: oneOf(r.align, ALIGNMENTS),
    font_size: oneOf(r.font_size, SIZES),
    shadow: oneOf(r.shadow, SHADOWS),
    glow: safeColor(r.glow),
    // An icon is a glyph, not a second message, so the check is a length cap
    // plus "no control characters". Notably NOT a ban on `<` and `>`: the value
    // is rendered as a React text child, where no character is special, and
    // barring them would stop the store showing its own `</>` wordmark.
    icon:
      typeof r.icon === 'string' &&
      r.icon.length > 0 &&
      r.icon.length <= 8 &&
      // eslint-disable-next-line no-control-regex
      !/[\u0000-\u001f\u007f-\u009f]/.test(r.icon)
        ? r.icon
        : undefined,
    icon_color: safeColor(r.icon_color),
    image: safeImageSrc(r.image),
    image_height: r.image_height === undefined ? undefined : safeInt(r.image_height, 8, 256),
    animate: Array.isArray(r.animate)
      ? (r.animate.map((a) => oneOf(a, BANNER_ANIMATIONS)).filter(Boolean) as BannerAnimation[])
      : undefined,
  };
}

/**
 * Parse and validate the injected payload, or return null.
 *
 * Null means "render the plain message instead" and covers every failure: no
 * tag, malformed JSON, a version this bundle does not know, and a payload whose
 * every block was unusable.
 */
export function parseBannerPayload(raw: string | null | undefined): LoginBannerPayload | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object') return null;
  const p = parsed as Record<string, unknown>;
  if (p.version !== BANNER_PAYLOAD_VERSION) return null;
  if (!Array.isArray(p.blocks)) return null;
  const blocks = p.blocks.map(safeBlock).filter(Boolean) as BannerBlock[];
  if (!blocks.length) return null;
  return { version: BANNER_PAYLOAD_VERSION, style: safeStyle(p.style), blocks };
}

/** Read the payload out of the document, if the server injected one. */
export function readBannerFromDocument(): LoginBannerPayload | null {
  return parseBannerPayload(
    document.querySelector(`meta[name="${BANNER_META_NAME}"]`)?.getAttribute('content')
  );
}
