// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0
//
// The operator-configured login message on the sign-in screen.
// See §6.3 and §12.4 of docs/design/login-info.md.
//
// The server injects `<meta name="sbs-login-info" content="...">` into
// index.html at serve time, so these tests set that tag on the jsdom document
// before rendering — exactly what the component reads.

import { describe, it, expect, afterEach, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { LoginPage } from './LoginPage';

vi.mock('@/contexts/AuthContext', () => ({
  useAuth: () => ({
    mode: 'standalone' as const,
    token: null,
    tenantId: null,
    signIn: vi.fn(),
    signOut: vi.fn(),
    whoami: vi.fn(),
  }),
}));

const META_NAME = 'sbs-login-info';

function setLoginInfoMeta(content: string) {
  const meta = document.createElement('meta');
  meta.setAttribute('name', META_NAME);
  meta.setAttribute('content', content);
  document.head.appendChild(meta);
}

function renderLoginPage() {
  return render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>
  );
}

afterEach(() => {
  document.head
    .querySelectorAll(`meta[name="${META_NAME}"]`)
    .forEach((el) => el.remove());
});

describe('LoginPage login-information message', () => {
  it('renders the message from the meta tag', () => {
    setLoginInfoMeta('Shared eval box — do not store secrets.');
    renderLoginPage();

    // getByText throws when absent, so this both finds and asserts.
    expect(screen.getByText('Shared eval box — do not store secrets.')).not.toBeNull();
  });

  it('renders the message verbatim, adding no heading or label', () => {
    // A change that reintroduces a "Notice:" prefix or a separate title must
    // fail here: a message that wants to open with a word says so itself.
    const message = 'Access requests: ops@example.com';
    setLoginInfoMeta(message);
    const { container } = renderLoginPage();

    const alert = container.querySelector('[data-testid="login-info"]');
    expect(alert).not.toBeNull();
    // PatternFly's Alert adds a screen-reader-only "Info alert:" prefix; the
    // visible text must be the message and nothing else.
    const visible = Array.from(alert!.querySelectorAll('span'))
      .map((el) => el.textContent)
      .filter((text): text is string => !!text);
    expect(visible).toContain(message);
    expect(visible.some((text) => text !== message && text.includes(message))).toBe(
      false
    );
  });

  it('renders no alert when the meta tag is absent', () => {
    const { container } = renderLoginPage();

    expect(container.querySelector('[data-testid="login-info"]')).toBeNull();
  });

  it('renders no alert when the meta tag content is empty', () => {
    setLoginInfoMeta('');
    const { container } = renderLoginPage();

    expect(container.querySelector('[data-testid="login-info"]')).toBeNull();
  });

  it('renders markup in the message as visible text, creating no element', () => {
    const hostile = '<script>alert(1)</script>';
    setLoginInfoMeta(hostile);
    const { container } = renderLoginPage();

    const alert = container.querySelector('[data-testid="login-info"]');
    expect(alert).not.toBeNull();
    expect(alert!.querySelector('script')).toBeNull();
    expect(screen.getByText(hostile)).not.toBeNull();
  });

  it('preserves configured line breaks with white-space: pre-line', () => {
    const message = 'First line.\nSecond line.';
    setLoginInfoMeta(message);
    const { container } = renderLoginPage();

    const rendered = Array.from(
      container.querySelectorAll<HTMLElement>('[data-testid="login-info"] span')
    ).find((el) => el.textContent === message);
    expect(rendered).toBeDefined();
    expect(rendered!.style.whiteSpace).toBe('pre-line');
    expect(rendered!.style.fontWeight).toBe('normal');
  });

  it('still renders the sign-in form alongside the message', () => {
    setLoginInfoMeta('Heads up.');
    renderLoginPage();

    expect(screen.getByLabelText(/username/i)).not.toBeNull();
    expect(screen.getByLabelText(/password/i)).not.toBeNull();
  });
});

// The rich banner (`format: rich`) arrives in its own tag alongside the plain
// one. See docs/design/login-banner.md §7.4 — the point of these tests is which
// of the two the page chooses, not how the banner itself renders (that is
// components/LoginBanner.test.tsx).

const BANNER_META_NAME = 'sbs-login-banner';

function setLoginBannerMeta(content: string) {
  const meta = document.createElement('meta');
  meta.setAttribute('name', BANNER_META_NAME);
  meta.setAttribute('content', content);
  document.head.appendChild(meta);
}

const VALID_BANNER = JSON.stringify({
  version: 1,
  style: { border_color: 'gold' },
  blocks: [{ kind: 'h1', spans: [{ kind: 'text', text: 'Rich banner', bold: true }] }],
});

afterEach(() => {
  document.head
    .querySelectorAll(`meta[name="${BANNER_META_NAME}"]`)
    .forEach((el) => el.remove());
});

describe('LoginPage rich banner', () => {
  it('renders the banner instead of the plain alert when both tags are present', () => {
    setLoginInfoMeta('Plain fallback text.');
    setLoginBannerMeta(VALID_BANNER);
    renderLoginPage();

    expect(screen.getByTestId('login-banner')).not.toBeNull();
    expect(screen.queryByTestId('login-info')).toBeNull();
    expect(screen.getByText('Rich banner')).not.toBeNull();
  });

  it('falls back to the plain alert when the banner payload is unusable', () => {
    // The whole reason the server injects both tags: a login screen must appear.
    setLoginInfoMeta('Plain fallback text.');
    setLoginBannerMeta('{not json');
    renderLoginPage();

    expect(screen.queryByTestId('login-banner')).toBeNull();
    expect(screen.getByTestId('login-info')).not.toBeNull();
    expect(screen.getByText('Plain fallback text.')).not.toBeNull();
  });

  it('renders the banner even with no plain tag at all', () => {
    setLoginBannerMeta(VALID_BANNER);
    renderLoginPage();

    expect(screen.getByTestId('login-banner')).not.toBeNull();
    expect(screen.queryByTestId('login-info')).toBeNull();
  });

  it('still renders the sign-in form alongside the banner', () => {
    setLoginBannerMeta(VALID_BANNER);
    renderLoginPage();

    expect(screen.getByLabelText(/username/i)).not.toBeNull();
    expect(screen.getByLabelText(/password/i)).not.toBeNull();
  });
});
