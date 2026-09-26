// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0

import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  ClipboardCopy,
  ClipboardCopyVariant,
  Form,
  FormGroup,
  FormSelect,
  FormSelectOption,
  Modal,
  ModalVariant,
  Spinner,
  Text,
} from '@patternfly/react-core';
import { DownloadIcon } from '@patternfly/react-icons';
import { cliApi } from '@/services/api';
import {
  CLI_PLATFORM_LABELS,
  cliReasonText,
  type CliManifest,
  type CliPlatform,
} from '@/types/cli';

/**
 * Download the native `sbs` CLI — docs/design/new_cli.md §5.9.
 *
 * One component, four entry points (masthead, home card, sign-in screen, and
 * anywhere else that wants it). Three properties are deliberate:
 *
 * 1. **It works unauthenticated.** `/cli/*` is unauthenticated in every ACL mode
 *    by construction, so this renders and functions on the sign-in screen with no
 *    special case — which matters because a user who cannot sign in yet is
 *    exactly the user who wants the CLI.
 *
 * 2. **The chooser is always visible, never hidden behind the detection.**
 *    Apple Silicon is undecidable from a User-Agent — every Mac reports "Intel
 *    Mac OS X 10_15_7" — so the server's answer can be a guess. The UI refines it
 *    with `navigator.userAgentData` where available and still shows every option,
 *    because silently handing an Intel Mac an arm64 binary produces a failure
 *    ("killed") that looks nothing like the cause.
 *
 * 3. **Download is a plain `<a href download>`, not a fetch.** The browser owns a
 *    32 MB transfer better than JavaScript: progress, resume and streaming to
 *    disk all come free, whereas a blob would buffer the whole artifact in
 *    memory.
 */

interface CliDownloadModalProps {
  isOpen: boolean;
  onClose: () => void;
}

/** Bytes → a short human string for the download button. */
function formatSize(bytes?: number): string {
  if (!bytes) return '';
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(1)} MB`;
}

/**
 * Refine the platform guess using UA Client Hints.
 *
 * `navigator.userAgentData.getHighEntropyValues` is the only client-side way to
 * learn a Mac's real architecture. Chromium-only and async; everything else
 * keeps the server's answer.
 */
async function detectPlatformClientSide(): Promise<CliPlatform | null> {
  const uaData = (navigator as any).userAgentData;
  if (!uaData?.getHighEntropyValues) return null;
  try {
    const { platform, architecture, bitness } = await uaData.getHighEntropyValues([
      'platform',
      'architecture',
      'bitness',
    ]);
    if (bitness && bitness !== '64') return null;
    const arch = architecture === 'arm' ? 'arm64' : architecture === 'x86' ? 'amd64' : null;
    if (!arch) return null;
    if (platform === 'macOS') return `darwin-${arch}` as CliPlatform;
    if (platform === 'Windows') return arch === 'amd64' ? 'windows-amd64' : null;
    if (platform === 'Linux' || platform === 'Chrome OS') return `linux-${arch}` as CliPlatform;
    return null;
  } catch {
    // Some privacy configurations reject the request outright. The server's
    // detection is a perfectly good fallback.
    return null;
  }
}

/**
 * Best guess from the User-Agent, used only to preselect an option.
 *
 * Mirrors the server's logic rather than trusting it, because the manifest
 * carries no per-request detection — it is one cacheable document for everyone.
 */
function guessFromUserAgent(): CliPlatform {
  const ua = navigator.userAgent || '';
  if (/windows/i.test(ua)) return 'windows-amd64';
  if (/mac os x|macintosh/i.test(ua)) return 'darwin-arm64';
  if (/aarch64|arm64/i.test(ua)) return 'linux-arm64';
  return 'linux-amd64';
}

export function CliDownloadModal({ isOpen, onClose }: CliDownloadModalProps) {
  const [manifest, setManifest] = useState<CliManifest | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [selected, setSelected] = useState<CliPlatform>(guessFromUserAgent());

  const load = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const doc = await cliApi.getManifest();
      setManifest(doc);
    } catch (e) {
      // An inline Alert, never a blank modal: "nothing happened" is the worst
      // possible answer to a click on Download.
      setError(e instanceof Error ? e.message : 'Could not load the CLI manifest');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    void load();
    void detectPlatformClientSide().then((detected) => {
      if (detected) setSelected(detected);
    });
  }, [isOpen, load]);

  const entry = manifest?.platforms?.[selected];
  const isReady = entry?.state === 'ready';
  const isPreparing = entry?.state === 'preparing';
  const anyReady = Object.values(manifest?.platforms ?? {}).some((p) => p?.state === 'ready');

  const rawHref = entry?.download_url ? cliApi.downloadUrl(entry.download_url) : undefined;
  const archiveHref = entry?.archive_url ? cliApi.downloadUrl(entry.archive_url) : undefined;

  const publicUrl = manifest?.public_url || window.location.origin;
  const installCommand = `curl -fsSL ${publicUrl}/cli/install.sh | sh`;
  const isWindows = selected.startsWith('windows-');
  const windowsCommand = `irm ${publicUrl}/cli/install.ps1 | iex`;

  return (
    <Modal
      variant={ModalVariant.medium}
      title="Download the sbs CLI"
      isOpen={isOpen}
      onClose={onClose}
      actions={[
        // Real anchors, not Buttons with onClick: the browser owns the transfer,
        // so progress, resume and streaming to disk all come free. A fetch into
        // a blob would buffer the whole ~32 MB artifact in memory.
        // `component="a"` keeps PatternFly's styling while emitting <a href>.
        <Button
          key="download"
          variant="primary"
          component="a"
          href={rawHref}
          download
          isDisabled={!isReady || !rawHref}
          icon={<DownloadIcon />}
        >
          Download {entry?.filename ?? 'sbs'}
          {entry?.size ? ` (${formatSize(entry.size)})` : ''}
        </Button>,
        <Button
          key="archive"
          variant="secondary"
          component="a"
          href={archiveHref}
          download
          isDisabled={!isReady || !archiveHref}
        >
          Download archive
          {entry?.archive_size ? ` (${formatSize(entry.archive_size)})` : ''}
        </Button>,
        <Button key="close" variant="link" onClick={onClose}>
          Close
        </Button>,
      ]}
    >
      <>
        {isLoading && !manifest && (
          <div style={{ textAlign: 'center', padding: '2rem' }}>
            <Spinner size="lg" aria-label="Loading available CLI builds" />
          </div>
        )}

        {error && (
          <Alert
            variant="danger"
            isInline
            title="Could not load the available builds"
            actionLinks={
              <Button variant="link" isInline onClick={() => void load()}>
                Try again
              </Button>
            }
          >
            {error}
            {/* The install script is generated per request and does not depend on
                a prepared artifact list, so it is still worth offering here. */}
            <Text component="p" style={{ marginTop: '0.5rem' }}>
              You can also install directly with:
            </Text>
            <ClipboardCopy isReadOnly hoverTip="Copy" clickTip="Copied">
              {installCommand}
            </ClipboardCopy>
          </Alert>
        )}

        {manifest && (
          <>
            <Text component="p">
              A single native executable — no Python, no <code>pip</code>, nothing else
              to install. This build talks to <strong>{publicUrl}</strong> with no
              configuration.
            </Text>

            <Form>
              <FormGroup label="Platform" fieldId="cli-platform">
                <FormSelect
                  id="cli-platform"
                  value={selected}
                  onChange={(_e, value) => setSelected(value as CliPlatform)}
                  aria-label="Select your platform"
                >
                  {CLI_PLATFORM_LABELS.map(({ id, label }) => {
                    const p = manifest.platforms?.[id];
                    const unavailable = !p || p.state === 'unavailable';
                    return (
                      <FormSelectOption
                        key={id}
                        value={id}
                        // Labelled as well as disabled: a greyed-out option with
                        // no explanation reads as a bug.
                        label={unavailable ? `${label} — unavailable` : label}
                        isDisabled={unavailable}
                      />
                    );
                  })}
                </FormSelect>
              </FormGroup>
            </Form>

            {isPreparing && (
              <Alert
                variant="info"
                isInline
                title="This build is being prepared"
                style={{ marginTop: '1rem' }}
              >
                The store is embedding its URL into this platform's binary. Try again
                shortly.
                <Button variant="link" isInline onClick={() => void load()}>
                  Refresh
                </Button>
              </Alert>
            )}

            {entry?.state === 'unavailable' && (
              <Alert
                variant="warning"
                isInline
                title="Not available from this store"
                style={{ marginTop: '1rem' }}
              >
                {cliReasonText(entry.reason)}
                {!anyReady && (
                  <>
                    <Text component="p" style={{ marginTop: '0.5rem' }}>
                      Routes that need no prepared artifact:
                    </Text>
                    <ClipboardCopy isReadOnly hoverTip="Copy" clickTip="Copied">
                      pip install skillberry-store-cli
                    </ClipboardCopy>
                  </>
                )}
              </Alert>
            )}

            {isReady && entry?.url_injection === 'sidecar' && (
              <Alert
                variant="info"
                isInline
                title="Download the archive for this platform"
                style={{ marginTop: '1rem' }}
              >
                Builds for this platform carry the store URL in a separate file rather
                than inside the binary, so the archive is the complete download. A bare
                binary works too, after <code>sbs connect {publicUrl}</code>.
              </Alert>
            )}

            {isReady && (
              <div style={{ marginTop: '1.5rem' }}>
                <Text component="h4">Verify your download</Text>
                <Text component="p">
                  sha256 of the {entry?.filename} binary:
                </Text>
                <ClipboardCopy
                  isReadOnly
                  variant={ClipboardCopyVariant.expansion}
                  hoverTip="Copy"
                  clickTip="Copied"
                >
                  {entry?.sha256 ?? ''}
                </ClipboardCopy>
              </div>
            )}

            <div style={{ marginTop: '1.5rem' }}>
              <Text component="h4">Or install from your terminal</Text>
              <Text component="p">
                Verifies the checksum for you, and on macOS avoids the quarantine flag
                a browser download carries.
              </Text>
              <ClipboardCopy isReadOnly hoverTip="Copy" clickTip="Copied">
                {isWindows ? windowsCommand : installCommand}
              </ClipboardCopy>
            </div>

            <Text component="p" style={{ marginTop: '1.5rem', fontSize: '0.875rem' }}>
              {manifest.cli_name} {manifest.cli_version} · embeds{' '}
              <a href={cliApi.downloadUrl(manifest.engine.license_url)} target="_blank" rel="noreferrer">
                {manifest.engine.name} {manifest.engine.version} ({manifest.engine.license})
              </a>
              . The binaries are not code-signed: macOS may quarantine a browser
              download, and Windows SmartScreen may warn.
            </Text>
          </>
        )}
      </>
    </Modal>
  );
}
