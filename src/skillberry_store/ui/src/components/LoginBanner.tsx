// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0
//
// Renders the operator's rich login banner (`standalone.login_info` with
// `format: rich`) on the sign-in screen. See docs/design/login-banner.md §7.
//
// Everything drawn here comes from a payload that was validated twice: against
// allow-lists on the server when the config was parsed, and again in
// `types/loginBanner.ts` when the injected `<meta>` tag was read. This file
// therefore assembles React elements from already-safe values — there is no
// `dangerouslySetInnerHTML` and no HTML string anywhere in the path, so no
// amount of markup in the operator's message can become an element.

import { CSSProperties, Fragment, ReactNode } from 'react';
import type {
  BannerAlign,
  BannerBlock,
  BannerSize,
  BannerSpan,
  BannerStyle,
  LoginBannerPayload,
} from '@/types/loginBanner';
import '@/styles/login-banner.css';

// Size tokens → rem. The parser only ever emits these six names, so a lookup
// is all the renderer needs and no operator value reaches CSS as a length.
const SIZE_REM: Record<BannerSize, number> = {
  sm: 0.8,
  md: 0.95,
  lg: 1.15,
  xl: 1.45,
  '2xl': 1.85,
  '3xl': 2.35,
};

// Headings scale off the banner's base size rather than off a fixed rem value,
// so `font_size: lg` enlarges the whole banner proportionally.
const HEADING_SCALE: Record<string, number> = { h1: 1.8, h2: 1.4, h3: 1.15 };

const PADDING_REM = { sm: 0.6, md: 0.95, lg: 1.35 } as const;

const SHADOW: Record<string, string> = {
  none: 'none',
  sm: '0 1px 3px rgba(0, 0, 0, 0.2)',
  md: '0 4px 12px rgba(0, 0, 0, 0.28)',
  lg: '0 10px 30px rgba(0, 0, 0, 0.35)',
};

/**
 * Element-level animations, composed into one `animation` shorthand.
 *
 * They cannot be four CSS classes: `animation` is a shorthand, so a second
 * class setting it on the same element replaces the first rather than adding to
 * it, and `animate: [pulse-border, gradient-shift]` would silently run only
 * whichever rule came last in the stylesheet. Built here as a comma-separated
 * list instead — which is the one form CSS does compose — and overridden with
 * `!important` in the reduced-motion block, since a media query cannot
 * otherwise outrank an inline style.
 *
 * `shimmer` is absent on purpose: it animates a `::after` pseudo-element, which
 * is a different element and so has no conflict to resolve.
 */
const ELEMENT_ANIMATIONS: Record<string, string> = {
  'pulse-border': 'sbs-banner-pulse-border 2.1s ease-in-out infinite',
  float: 'sbs-banner-float 3.4s ease-in-out infinite',
  'glow-breathe': 'sbs-banner-glow-breathe 2.8s ease-in-out infinite',
  'gradient-shift': 'sbs-banner-gradient-shift 7s ease-in-out infinite',
};

/** `#rrggbb` → `rgba(r, g, b, alpha)`; named colors fall back unchanged. */
function withAlpha(color: string, alpha: number): string {
  const hex = color.replace('#', '');
  const full =
    hex.length === 3
      ? hex
          .split('')
          .map((c) => c + c)
          .join('')
      : hex.slice(0, 6);
  if (full.length !== 6 || !/^[0-9a-f]{6}$/i.test(full)) return color;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(full.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function bannerStyle(style: BannerStyle): CSSProperties {
  const base = SIZE_REM[style.font_size ?? 'md'];
  const padding = PADDING_REM[style.padding ?? 'md'];
  const css: CSSProperties = {
    fontSize: `${base}rem`,
    padding: `${padding}rem ${padding * 1.15}rem`,
    borderRadius: `${style.radius ?? 8}px`,
    textAlign: style.align ?? 'left',
  };

  if (style.gradient?.length) {
    css.backgroundImage = `linear-gradient(${style.gradient_angle ?? 135}deg, ${style.gradient.join(
      ', '
    )})`;
    // A solid `background` alongside the gradient is what shows through if a
    // browser cannot paint the image, and what a `background-position`
    // animation slides over.
    css.backgroundColor = style.background ?? style.gradient[0];
  } else if (style.background) {
    css.backgroundColor = style.background;
  }

  if (style.text_color) css.color = style.text_color;

  const borderWidth = style.border_width ?? (style.border_color ? 2 : 0);
  if (borderWidth > 0) {
    css.borderWidth = `${borderWidth}px`;
    css.borderStyle = style.border_style ?? 'solid';
    css.borderColor = style.border_color ?? 'currentColor';
  }

  // Drop shadow and colored glow compose: the glow is what makes a banner look
  // lit rather than merely raised.
  const layers: string[] = [];
  const shadow = SHADOW[style.shadow ?? 'none'];
  if (shadow && shadow !== 'none') layers.push(shadow);
  if (style.glow) {
    layers.push(`0 0 18px ${withAlpha(style.glow, 0.55)}`);
    layers.push(`0 0 42px ${withAlpha(style.glow, 0.28)}`);
  }
  if (layers.length) css.boxShadow = layers.join(', ');

  const animations = (style.animate ?? [])
    .map((name) => ELEMENT_ANIMATIONS[name])
    .filter(Boolean);
  if (animations.length) css.animation = animations.join(', ');

  return css;
}

/**
 * CSS custom properties the stylesheet's keyframes read.
 *
 * Keyframes cannot interpolate towards a value that only exists in an inline
 * style, so the two animations that need the operator's colors get them through
 * variables set here.
 */
function motionVariables(style: BannerStyle): CSSProperties {
  const vars: Record<string, string> = {};
  if (style.border_color) {
    vars['--sbs-banner-border'] = style.border_color;
    vars['--sbs-banner-pulse-to'] = withAlpha(style.glow ?? style.border_color, 0.25);
  }
  if (style.glow) {
    vars['--sbs-banner-glow-min'] = `0 0 10px ${withAlpha(style.glow, 0.35)}`;
    vars['--sbs-banner-glow-max'] = `0 0 30px ${withAlpha(style.glow, 0.75)}, 0 0 60px ${withAlpha(
      style.glow,
      0.4
    )}`;
  }
  return vars as CSSProperties;
}

function spanStyle(span: BannerSpan): CSSProperties {
  const css: CSSProperties = {};
  if (span.color) css.color = span.color;
  if (span.bg) css.backgroundColor = span.bg;
  if (span.size) css.fontSize = `${SIZE_REM[span.size]}rem`;
  if (span.bold) css.fontWeight = 700;
  if (span.italic) css.fontStyle = 'italic';
  if (span.caps) css.textTransform = 'uppercase';
  if (span.glow) css.textShadow = `0 0 10px ${withAlpha(span.glow, 0.85)}`;
  const lines = [span.underline && 'underline', span.strike && 'line-through'].filter(Boolean);
  if (lines.length) css.textDecoration = lines.join(' ');
  // `bg` without padding paints a stripe flush against the glyphs.
  if (span.bg && !span.pill) css.padding = '0.05em 0.25em';
  return css;
}

function SpanContent({ span }: { span: BannerSpan }) {
  const classes = [
    span.pill ? 'sbs-login-banner-pill' : null,
    span.animate ? `sbs-span-anim-${span.animate}` : null,
  ]
    .filter(Boolean)
    .join(' ');

  if (span.kind === 'image' && span.src) {
    return (
      <img
        className="sbs-login-banner-image"
        src={span.src}
        alt={span.alt ?? ''}
        style={span.height ? { height: `${span.height}px` } : undefined}
      />
    );
  }

  // `mono` renders as <code> so it inherits the stylesheet's chip treatment
  // instead of needing five inline declarations.
  const Tag = span.mono ? 'code' : 'span';
  return (
    <Tag className={classes || undefined} style={spanStyle(span)}>
      {span.text}
    </Tag>
  );
}

function Span({ span }: { span: BannerSpan }) {
  const content = <SpanContent span={span} />;
  if (!span.href) return content;
  return (
    // `noopener noreferrer` on a pre-authentication page in particular: the
    // banner's links are operator-controlled but the page they open should get
    // no handle on this window.
    <a href={span.href} target="_blank" rel="noopener noreferrer">
      {content}
    </a>
  );
}

function Block({ block, align }: { block: BannerBlock; align?: BannerAlign }) {
  if (block.kind === 'rule') return <hr className="sbs-login-banner-rule" />;
  if (block.kind === 'spacer') return <div className="sbs-login-banner-spacer" />;

  const spans = (
    <Fragment>
      {block.spans?.map((span, i) => (
        <Span key={i} span={span} />
      ))}
    </Fragment>
  );

  const style: CSSProperties = {};
  if (block.align) style.textAlign = block.align;

  if (block.kind === 'li') {
    return (
      <ul className="sbs-login-banner-list" style={{ textAlign: block.align ?? align }}>
        <li>{spans}</li>
      </ul>
    );
  }
  if (block.kind === 'quote') {
    return (
      <div className="sbs-login-banner-quote" style={style}>
        {spans}
      </div>
    );
  }
  if (block.kind === 'p') {
    return <p style={style}>{spans}</p>;
  }

  // h1 / h2 / h3: scaled off the banner's base size, and deliberately not real
  // heading elements — "Sign in to Skillberry Store" is the page's heading, and
  // a decorative banner should not outrank it in the document outline.
  return (
    <div
      style={{
        ...style,
        fontSize: `${HEADING_SCALE[block.kind]}em`,
        fontWeight: 700,
        lineHeight: 1.2,
        margin: '0.1rem 0 0.25rem',
      }}
    >
      {spans}
    </div>
  );
}

export interface LoginBannerProps {
  banner: LoginBannerPayload;
}

/**
 * No `aria-label` on the region: every styled span is real text in document
 * order, so a screen reader already reads the message correctly, and labelling
 * the region with the same words would announce them twice. The decorative
 * parts — the chrome image and the icon glyph — are hidden from it instead.
 */
export function LoginBanner({ banner }: LoginBannerProps): ReactNode {
  const { style, blocks } = banner;
  const classes = [
    'sbs-login-banner',
    ...(style.animate ?? []).map((a) => `sbs-banner-anim-${a}`),
  ].join(' ');

  return (
    <section
      className={classes}
      data-testid="login-banner"
      style={{ ...bannerStyle(style), ...motionVariables(style) }}
    >
      {style.image && (
        <img
          className="sbs-login-banner-image"
          src={style.image}
          alt=""
          style={{ height: `${style.image_height ?? 48}px` }}
        />
      )}
      {style.icon && (
        <span
          className="sbs-login-banner-icon"
          aria-hidden="true"
          style={{
            fontSize: '1.7em',
            // The mark carries its own colour when given one, so a banner can
            // reproduce a masthead's blue logo above white words.
            color: style.icon_color,
            // A wordmark like `</>` is glyphs, not a pictograph, so it wants the
            // weight and tracking of a logotype rather than of body text.
            fontWeight: 700,
            letterSpacing: '-0.04em',
          }}
        >
          {style.icon}
        </span>
      )}
      {blocks.map((block, i) => (
        <Block key={i} block={block} align={style.align} />
      ))}
    </section>
  );
}
