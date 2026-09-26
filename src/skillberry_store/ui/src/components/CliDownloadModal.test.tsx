// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0

/**
 * CliDownloadModal — docs/design/new_cli.md §8.3 #21.
 *
 * The assertions that matter here are the ones a user notices when they break:
 * the detected platform is preselected, unavailable platforms are disabled *and
 * labelled*, the sha256 is on screen, `preparing` reads as "come back" rather
 * than "broken", a fetch failure is visible rather than a blank modal, and the
 * download control is a real `<a href download>` carrying the right query string
 * (not a Button whose onClick would buffer 32 MB into memory).
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CliDownloadModal } from './CliDownloadModal';
import type { CliManifest } from '@/types/cli';

const READY_SHA = 'a'.repeat(64);

function manifest(overrides: Partial<CliManifest> = {}): CliManifest {
  return {
    cli_name: 'sbs',
    cli_version: '1.2.3',
    public_url: 'http://store.test:8000',
    generated_at: '2026-09-25T12:00:00Z',
    engine: {
      name: 'restish',
      version: '2.3.0',
      license: 'MIT',
      license_url: '/cli/license',
    },
    platforms: {
      'linux-amd64': {
        state: 'ready',
        filename: 'sbs',
        size: 33_000_000,
        sha256: READY_SHA,
        url_injection: 'patch',
        download_url: '/cli/download?platform=linux-amd64&format=raw',
        archive_url: '/cli/download?platform=linux-amd64&format=archive',
        archive_sha256: 'b'.repeat(64),
        archive_size: 11_000_000,
        archive_filename: 'sbs-linux-amd64.tar.gz',
      },
      'darwin-arm64': {
        state: 'ready',
        filename: 'sbs',
        size: 31_000_000,
        sha256: 'c'.repeat(64),
        url_injection: 'sidecar',
        download_url: '/cli/download?platform=darwin-arm64&format=raw',
        archive_url: '/cli/download?platform=darwin-arm64&format=archive',
        archive_sha256: 'd'.repeat(64),
        archive_size: 10_000_000,
      },
      'windows-amd64': { state: 'preparing', retry_after: 10 },
      'linux-arm64': { state: 'unavailable', reason: 'not_bundled' },
      // darwin-amd64 absent entirely — the manifest is a Partial record, and a
      // missing key must behave like `unavailable` rather than crashing.
    },
    ...overrides,
  };
}

function mockFetch(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    statusText: ok ? 'OK' : 'Internal Server Error',
    json: async () => body,
  });
}

/** Force the User-Agent so platform preselection is deterministic. */
function setUserAgent(ua: string) {
  Object.defineProperty(window.navigator, 'userAgent', {
    value: ua,
    configurable: true,
  });
}

const UA_LINUX = 'Mozilla/5.0 (X11; Linux x86_64) Chrome/123.0.0.0';
const UA_MAC = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Safari/605.1.15';
const UA_WINDOWS = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/123.0.0.0';

describe('CliDownloadModal', () => {
  beforeEach(() => {
    setUserAgent(UA_LINUX);
    // No userAgentData by default: that is Safari and Firefox, and it is the
    // path where the server's guess has to stand on its own.
    delete (window.navigator as any).userAgentData;
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches the manifest and shows the store URL', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/http:\/\/store\.test:8000/)).toBeTruthy();
    });
    expect(global.fetch).toHaveBeenCalledWith('/cli/manifest');
  });

  it('does not fetch while closed', () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen={false} onClose={() => {}} />);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it('preselects the platform detected from the User-Agent', async () => {
    global.fetch = mockFetch(manifest()) as any;
    setUserAgent(UA_WINDOWS);
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = (await screen.findByLabelText(
      'Select your platform'
    )) as HTMLSelectElement;
    expect(select.value).toBe('windows-amd64');
  });

  it('prefers Apple Silicon for a Mac User-Agent', async () => {
    // Every Mac reports "Intel Mac OS X 10_15_7", so the UA cannot reveal the
    // CPU. Apple Silicon is the more likely machine, and the chooser stays
    // visible precisely because this is a guess.
    global.fetch = mockFetch(manifest()) as any;
    setUserAgent(UA_MAC);
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = (await screen.findByLabelText(
      'Select your platform'
    )) as HTMLSelectElement;
    expect(select.value).toBe('darwin-arm64');
  });

  it('refines a Mac guess with userAgentData when available', async () => {
    // The only client-side way to learn a Mac's real architecture. An Intel Mac
    // must end up on darwin-amd64 despite the UA guess above.
    global.fetch = mockFetch(manifest()) as any;
    setUserAgent(UA_MAC);
    (window.navigator as any).userAgentData = {
      getHighEntropyValues: vi.fn().mockResolvedValue({
        platform: 'macOS',
        architecture: 'x86',
        bitness: '64',
      }),
    };

    render(<CliDownloadModal isOpen onClose={() => {}} />);

    await waitFor(async () => {
      const select = (await screen.findByLabelText(
        'Select your platform'
      )) as HTMLSelectElement;
      expect(select.value).toBe('darwin-amd64');
    });
  });

  it('keeps the guess when userAgentData is rejected', async () => {
    // Some privacy configurations refuse the request; the guess must survive.
    global.fetch = mockFetch(manifest()) as any;
    setUserAgent(UA_MAC);
    (window.navigator as any).userAgentData = {
      getHighEntropyValues: vi.fn().mockRejectedValue(new Error('denied')),
    };

    render(<CliDownloadModal isOpen onClose={() => {}} />);
    const select = (await screen.findByLabelText(
      'Select your platform'
    )) as HTMLSelectElement;
    expect(select.value).toBe('darwin-arm64');
  });

  it('always shows every platform, and labels the unavailable ones', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = await screen.findByLabelText('Select your platform');
    const options = within(select).getAllByRole(
      'option'
    ) as HTMLOptionElement[];

    // Every platform is offered — the chooser is never reduced to the guess.
    expect(options.map((o) => o.value).sort()).toEqual(
      [
        'darwin-amd64',
        'darwin-arm64',
        'linux-amd64',
        'linux-arm64',
        'windows-amd64',
      ].sort()
    );

    const unavailable = options.find((o) => o.value === 'linux-arm64')!;
    expect(unavailable.disabled).toBe(true);
    // Disabled AND labelled: a greyed-out option with no explanation reads as
    // a bug in the page.
    expect(unavailable.textContent).toMatch(/unavailable/i);

    // A platform missing from the manifest entirely behaves like unavailable.
    const absent = options.find((o) => o.value === 'darwin-amd64')!;
    expect(absent.disabled).toBe(true);

    // A ready one is selectable.
    expect(options.find((o) => o.value === 'linux-amd64')!.disabled).toBe(false);
  });

  it('shows the sha256 for the selected platform', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);
    // ClipboardCopy renders its content in a read-only input, not as text.
    expect(await screen.findByDisplayValue(READY_SHA)).toBeTruthy();
  });

  it('offers the download as an <a href download> with the right query string', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const link = (await screen.findByRole('link', {
      name: /Download sbs/i,
    })) as HTMLAnchorElement;

    // An anchor, not a button: the browser must own a 32 MB transfer.
    expect(link.tagName).toBe('A');
    expect(link.hasAttribute('download')).toBe(true);
    expect(link.getAttribute('href')).toContain(
      '/cli/download?platform=linux-amd64&format=raw'
    );
  });

  it('offers the archive download separately', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const link = (await screen.findByRole('link', {
      name: /Download archive/i,
    })) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toContain('format=archive');
  });

  it('updates the download link when the platform changes', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = await screen.findByLabelText('Select your platform');
    await userEvent.selectOptions(select, 'darwin-arm64');

    await waitFor(() => {
      const link = screen.getByRole('link', {
        name: /Download sbs/i,
      }) as HTMLAnchorElement;
      expect(link.getAttribute('href')).toContain('platform=darwin-arm64');
    });
  });

  it('explains the sidecar mechanism for platforms that need it', async () => {
    // darwin-arm64 carries its URL beside the binary, so a *raw* download alone
    // would come up pointing at the compile-time default.
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = await screen.findByLabelText('Select your platform');
    await userEvent.selectOptions(select, 'darwin-arm64');

    await waitFor(() => {
      expect(screen.getByText(/separate file/i)).toBeTruthy();
    });
  });

  it('shows a "being prepared" message rather than an error', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = await screen.findByLabelText('Select your platform');
    await userEvent.selectOptions(select, 'windows-amd64');

    await waitFor(() => {
      expect(screen.getByText(/being prepared/i)).toBeTruthy();
    });

    // And the download is not offered as if it would work. A `preparing` entry
    // carries no download_url, so the control renders as an anchor with no href
    // — which has no link role at all, and is marked aria-disabled. Queried by
    // text rather than by role for exactly that reason.
    const control = screen.getByText(/Download sbs/i).closest('a');
    expect(control).toBeTruthy();
    expect(control!.getAttribute('aria-disabled')).toBe('true');
    expect(control!.hasAttribute('href')).toBe(false);
  });

  it('explains an unavailable platform with the server reason', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const select = await screen.findByLabelText('Select your platform');
    // Disabled in the chooser, but a programmatic change still exercises the
    // rendering path — and the option could become selectable after a refresh.
    await userEvent.selectOptions(select, 'linux-arm64').catch(() => {});
    (select as HTMLSelectElement).value = 'linux-arm64';
    select.dispatchEvent(new Event('change', { bubbles: true }));

    await waitFor(() => {
      expect(screen.getByText(/No build for this platform is bundled/i)).toBeTruthy();
    });
  });

  it('shows an inline alert on a fetch failure, never a blank modal', async () => {
    global.fetch = vi.fn().mockRejectedValue(new Error('network down')) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    await waitFor(() => {
      expect(screen.getByText(/Could not load the available builds/i)).toBeTruthy();
    });
    // And it still offers the route that needs no prepared artifact list.
    expect(
      await screen.findByDisplayValue(/curl -fsSL .*\/cli\/install\.sh \| sh/)
    ).toBeTruthy();
  });

  it('can retry after a failure', async () => {
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('network down'))
      .mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => manifest(),
      });
    global.fetch = fetchMock as any;

    render(<CliDownloadModal isOpen onClose={() => {}} />);
    await screen.findByText(/Could not load the available builds/i);

    await userEvent.click(screen.getByRole('button', { name: /Try again/i }));

    expect(await screen.findByDisplayValue(READY_SHA)).toBeTruthy();
  });

  it('offers the curl one-liner, and the PowerShell one for Windows', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    expect(
      await screen.findByDisplayValue(
        'curl -fsSL http://store.test:8000/cli/install.sh | sh'
      )
    ).toBeTruthy();

    const select = await screen.findByLabelText('Select your platform');
    await userEvent.selectOptions(select, 'windows-amd64');

    // Windows users get the PowerShell twin, not a curl command they cannot run.
    expect(
      await screen.findByDisplayValue(
        'irm http://store.test:8000/cli/install.ps1 | iex'
      )
    ).toBeTruthy();
  });

  it('links the engine licence, because we redistribute it', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    const link = (await screen.findByRole('link', {
      name: /restish 2\.3\.0 \(MIT\)/i,
    })) as HTMLAnchorElement;
    expect(link.getAttribute('href')).toContain('/cli/license');
  });

  it('warns that the binaries are unsigned', async () => {
    global.fetch = mockFetch(manifest()) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);
    await waitFor(() => {
      expect(screen.getByText(/not code-signed/i)).toBeTruthy();
    });
  });

  it('falls back to the page origin when the store has no public URL', async () => {
    // An operator who never set SBS_PUBLIC_URL still gets a usable one-liner.
    global.fetch = mockFetch(manifest({ public_url: null })) as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    expect(
      await screen.findByDisplayValue(
        `curl -fsSL ${window.location.origin}/cli/install.sh | sh`
      )
    ).toBeTruthy();
  });

  it('sends no credentials with the manifest request', async () => {
    // §8.3 #21b: the modal is offered on the sign-in screen, so the fetch must
    // work with no session. Passing credentials would also make it fail CORS in
    // some deployments.
    const fetchMock = mockFetch(manifest());
    global.fetch = fetchMock as any;
    render(<CliDownloadModal isOpen onClose={() => {}} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    const [, init] = fetchMock.mock.calls[0];
    // Either no init at all, or one that does not ask for credentials or add
    // an Authorization header.
    expect(init?.credentials).toBeUndefined();
    expect(init?.headers?.Authorization).toBeUndefined();
  });

  it('calls onClose from the Close button', async () => {
    global.fetch = mockFetch(manifest()) as any;
    const onClose = vi.fn();
    render(<CliDownloadModal isOpen onClose={onClose} />);

    // PatternFly's Modal renders its own dismiss control also labelled "Close",
    // so match the footer action specifically rather than by name alone.
    const closeButtons = await screen.findAllByRole('button', { name: /^Close$/i });
    await userEvent.click(closeButtons[closeButtons.length - 1]);
    expect(onClose).toHaveBeenCalled();
  });
});
