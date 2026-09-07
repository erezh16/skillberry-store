// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0
//
// The rich login banner: payload validation and rendering.
// See §7 and §12 of docs/design/login-banner.md.
//
// Two things are under test here, and they are different things:
//
//  * `parseBannerPayload` — the client-side re-validation. The server already
//    allow-listed everything, so this exists to make an unusable payload
//    *degrade* rather than break, and to be a second barrier under a hostile
//    value. Every rejection test below asserts null or a dropped field, never a
//    thrown error.
//  * `LoginBanner` — that already-safe values reach the DOM as attributes and
//    inline styles, and that markup in the operator's text never becomes an
//    element.

import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LoginBanner } from './LoginBanner';
import {
  BANNER_PAYLOAD_VERSION,
  parseBannerPayload,
  safeColor,
  safeHref,
  safeImageSrc,
  type LoginBannerPayload,
} from '@/types/loginBanner';

const payload = (over: Partial<LoginBannerPayload> = {}): LoginBannerPayload => ({
  version: BANNER_PAYLOAD_VERSION,
  style: {},
  blocks: [{ kind: 'p', spans: [{ kind: 'text', text: 'hello' }] }],
  ...over,
});

const banner = () => screen.getByTestId('login-banner');

// --------------------------------------------------------------------------- //
// Value allow-lists
// --------------------------------------------------------------------------- //

describe('colour validation', () => {
  it.each(['#fff', '#ffff', '#ffd166', '#ffd166cc', 'gold', 'GOLD'])(
    'accepts %s',
    (value) => {
      expect(safeColor(value)).toBe(value.toLowerCase());
    }
  );

  it.each([
    'rgb(255,0,0)',
    'url(https://e.com/x.png)',
    'notacolour',
    '#12345',
    'red;position:fixed',
    'expression(alert(1))',
    42,
    null,
    undefined,
    {},
  ])('rejects %s', (value) => {
    expect(safeColor(value)).toBeUndefined();
  });
});

describe('link validation', () => {
  it.each(['https://e.com/a', 'http://e.com/a', 'mailto:ops@e.com', '/docs'])(
    'accepts %s',
    (value) => {
      expect(safeHref(value)).toBe(value);
    }
  );

  it.each([
    'javascript:alert(1)',
    'JavaScript:alert(1)',
    'data:text/html,<script>alert(1)</script>',
    'vbscript:x',
    '//evil.example.com',
    'https://e.com/ a',
    42,
  ])('rejects %s', (value) => {
    expect(safeHref(value)).toBeUndefined();
  });
});

describe('image source validation', () => {
  it('accepts a raster data URI', () => {
    const src = 'data:image/png;base64,iVBORw0KGgo=';
    expect(safeImageSrc(src)).toBe(src);
  });

  it.each([
    'data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=',
    'data:text/html;base64,PHA+eDwvcD4=',
    'javascript:alert(1)',
    'mailto:ops@e.com',
  ])('rejects %s', (value) => {
    expect(safeImageSrc(value)).toBeUndefined();
  });
});

// --------------------------------------------------------------------------- //
// Payload parsing — every failure is a graceful null
// --------------------------------------------------------------------------- //

describe('parseBannerPayload', () => {
  it('parses a well-formed payload', () => {
    const parsed = parseBannerPayload(JSON.stringify(payload()));
    expect(parsed?.blocks[0].spans?.[0].text).toBe('hello');
  });

  it.each([
    ['no tag', null],
    ['an empty attribute', ''],
    ['malformed JSON', '{not json'],
    ['a JSON array', '[]'],
    ['a JSON string', '"hello"'],
    ['no blocks key', '{"version":1}'],
    ['zero usable blocks', '{"version":1,"blocks":[{"kind":"nope"}]}'],
    ['an unknown version', '{"version":99,"blocks":[{"kind":"rule"}]}'],
  ])('returns null for %s', (_label, raw) => {
    expect(parseBannerPayload(raw as string | null)).toBeNull();
  });

  it('drops an unknown block kind but keeps the usable ones', () => {
    const parsed = parseBannerPayload(
      JSON.stringify({
        version: BANNER_PAYLOAD_VERSION,
        blocks: [
          { kind: 'script', spans: [{ kind: 'text', text: 'x' }] },
          { kind: 'p', spans: [{ kind: 'text', text: 'kept' }] },
        ],
      })
    );
    expect(parsed?.blocks).toHaveLength(1);
    expect(parsed?.blocks[0].spans?.[0].text).toBe('kept');
  });

  it('strips hostile span values while keeping the text', () => {
    const parsed = parseBannerPayload(
      JSON.stringify({
        version: BANNER_PAYLOAD_VERSION,
        blocks: [
          {
            kind: 'p',
            spans: [
              {
                kind: 'link',
                text: 'click',
                href: 'javascript:alert(1)',
                color: 'red;position:fixed',
                size: 'enormous',
              },
            ],
          },
        ],
      })
    );
    const span = parsed?.blocks[0].spans?.[0];
    expect(span?.text).toBe('click');
    expect(span?.href).toBeUndefined();
    expect(span?.color).toBeUndefined();
    expect(span?.size).toBeUndefined();
    expect(span?.kind).toBe('text');
  });

  it('strips hostile and unknown style values', () => {
    const parsed = parseBannerPayload(
      JSON.stringify({
        version: BANNER_PAYLOAD_VERSION,
        style: {
          background: 'red;position:fixed',
          border_color: 'gold',
          image: 'javascript:alert(1)',
          animate: ['shimmer', 'explode'],
          radius: 9999,
          onclick: 'alert(1)',
        },
        blocks: [{ kind: 'p', spans: [{ kind: 'text', text: 'x' }] }],
      })
    );
    expect(parsed?.style).toMatchObject({
      background: undefined,
      border_color: 'gold',
      image: undefined,
      animate: ['shimmer'],
      radius: 32,
    });
    expect(parsed?.style).not.toHaveProperty('onclick');
  });

  it('only honours a boolean true for a flag', () => {
    const parsed = parseBannerPayload(
      JSON.stringify({
        version: BANNER_PAYLOAD_VERSION,
        blocks: [
          { kind: 'p', spans: [{ kind: 'text', text: 'x', bold: 'yes', pill: true }] },
        ],
      })
    );
    expect(parsed?.blocks[0].spans?.[0].bold).toBeUndefined();
    expect(parsed?.blocks[0].spans?.[0].pill).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
// Rendering
// --------------------------------------------------------------------------- //

describe('LoginBanner rendering', () => {
  it('applies the banner chrome as inline styles', () => {
    render(
      <LoginBanner
        banner={payload({
          style: {
            gradient: ['#1b1141', '#4c1d72'],
            gradient_angle: 130,
            text_color: '#f3edff',
            border_color: 'gold',
            border_width: 2,
            radius: 14,
            align: 'center',
            glow: '#ffd166',
          },
        })}
      />
    );
    const el = banner();
    expect(el.style.backgroundImage).toContain('linear-gradient(130deg');
    expect(el.style.color).toBeTruthy();
    expect(el.style.borderWidth).toBe('2px');
    expect(el.style.borderStyle).toBe('solid');
    expect(el.style.borderRadius).toBe('14px');
    expect(el.style.textAlign).toBe('center');
    expect(el.style.boxShadow).toContain('rgba(255, 209, 102');
  });

  it('applies per-span colour, size and weight', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [
            {
              kind: 'p',
              spans: [
                { kind: 'text', text: 'gold big', color: '#ffd166', size: '2xl', bold: true },
              ],
            },
          ],
        })}
      />
    );
    const span = screen.getByText('gold big');
    expect(span.style.color).toBe('rgb(255, 209, 102)');
    expect(span.style.fontSize).toBe('1.85rem');
    expect(span.style.fontWeight).toBe('700');
  });

  it('renders a pill span as a badge', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [
            { kind: 'p', spans: [{ kind: 'text', text: 'LIVE', pill: true, bg: 'gold' }] },
          ],
        })}
      />
    );
    expect(screen.getByText('LIVE').className).toContain('sbs-login-banner-pill');
  });

  it('renders a mono span as a code element', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [{ kind: 'p', spans: [{ kind: 'text', text: 'skillberry', mono: true }] }],
        })}
      />
    );
    expect(screen.getByText('skillberry').tagName).toBe('CODE');
  });

  it('opens links in a new tab with no handle on this window', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [
            {
              kind: 'p',
              spans: [{ kind: 'link', text: 'repo', href: 'https://github.com/x/y' }],
            },
          ],
        })}
      />
    );
    const link = banner().querySelector('a')!;
    expect(link.getAttribute('href')).toBe('https://github.com/x/y');
    expect(link.getAttribute('target')).toBe('_blank');
    expect(link.getAttribute('rel')).toBe('noopener noreferrer');
  });

  it('renders an image with its alt text and height', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [
            {
              kind: 'p',
              spans: [
                { kind: 'image', src: 'https://e.com/l.png', alt: 'logo', height: 40 },
              ],
            },
          ],
        })}
      />
    );
    const img = screen.getByAltText('logo') as HTMLImageElement;
    expect(img.getAttribute('src')).toBe('https://e.com/l.png');
    expect(img.style.height).toBe('40px');
  });

  it('renders an ASCII wordmark icon as text, in its own colour', () => {
    // `</>` is the store's mark. It must survive validation — a ban on `<`/`>`
    // would be defending the wrong thing, since React escapes a text child —
    // and it must be able to differ in colour from the words beside it, the way
    // the masthead paints a blue mark next to a white wordmark.
    render(
      <LoginBanner
        banner={payload({ style: { icon: '</>', icon_color: '#0066cc', text_color: 'white' } })}
      />
    );
    const icon = banner().querySelector('[aria-hidden="true"]') as HTMLElement;
    expect(icon.textContent).toBe('</>');
    expect(icon.style.color).toBe('rgb(0, 102, 204)');
    // Text, not markup: no element was created from the angle brackets.
    expect(icon.children).toHaveLength(0);
    expect(banner().querySelector('slash, br')).toBeNull();
  });

  it('drops an icon that is too long or carries a control character', () => {
    const parsed = parseBannerPayload(
      JSON.stringify({
        version: BANNER_PAYLOAD_VERSION,
        style: { icon: 'a whole sentence' },
        blocks: [{ kind: 'p', spans: [{ kind: 'text', text: 'x' }] }],
      })
    );
    expect(parsed?.style.icon).toBeUndefined();

    const withControl = parseBannerPayload(
      JSON.stringify({
        version: BANNER_PAYLOAD_VERSION,
        style: { icon: 'a\u001bb' },
        blocks: [{ kind: 'p', spans: [{ kind: 'text', text: 'x' }] }],
      })
    );
    expect(withControl?.style.icon).toBeUndefined();
  });

  it('hides the decorative icon and chrome image from assistive tech', () => {
    render(
      <LoginBanner
        banner={payload({ style: { icon: '🚀', image: 'https://e.com/l.png' } })}
      />
    );
    expect(banner().querySelector('[aria-hidden="true"]')?.textContent).toBe('🚀');
    // A decorative image is announced by an empty alt, not by a filename.
    expect(banner().querySelector('img')?.getAttribute('alt')).toBe('');
  });

  it('renders headings as scaled divs, not as h1-h3', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [{ kind: 'h1', spans: [{ kind: 'text', text: 'Big' }] }],
        })}
      />
    );
    // "Sign in to Skillberry Store" is the page's heading; a decorative banner
    // must not outrank it in the document outline.
    expect(banner().querySelector('h1, h2, h3')).toBeNull();
    expect(screen.getByText('Big')).toBeTruthy();
  });

  it('renders rules and spacers with no spans', () => {
    render(
      <LoginBanner banner={payload({ blocks: [{ kind: 'rule' }, { kind: 'spacer' }] })} />
    );
    expect(banner().querySelector('hr')).toBeTruthy();
    expect(banner().querySelector('.sbs-login-banner-spacer')).toBeTruthy();
  });

  it('adds a motion class only for a configured animation', () => {
    const { unmount } = render(<LoginBanner banner={payload()} />);
    expect(banner().className).not.toContain('sbs-banner-anim-');
    unmount();

    render(<LoginBanner banner={payload({ style: { animate: ['shimmer'] } })} />);
    expect(banner().className).toContain('sbs-banner-anim-shimmer');
  });

  it('composes multiple element animations into one shorthand', () => {
    // Four CSS classes could not do this: `animation` is a shorthand, so the
    // second class would replace the first instead of adding to it.
    render(
      <LoginBanner
        banner={payload({ style: { animate: ['pulse-border', 'gradient-shift'] } })}
      />
    );
    const animation = banner().style.animation;
    expect(animation).toContain('sbs-banner-pulse-border');
    expect(animation).toContain('sbs-banner-gradient-shift');
  });

  it('creates no element from markup-looking text', () => {
    render(
      <LoginBanner
        banner={payload({
          blocks: [
            {
              kind: 'p',
              spans: [{ kind: 'text', text: '<script>alert(1)</script> **not bold**' }],
            },
          ],
        })}
      />
    );
    expect(banner().querySelector('script')).toBeNull();
    expect(screen.getByText('<script>alert(1)</script> **not bold**')).toBeTruthy();
  });
});
