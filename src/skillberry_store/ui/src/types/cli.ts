// Copyright 2025 IBM Corp.
// Licensed under the Apache License, Version 2.0

/**
 * Types for the CLI download manifest (`GET /cli/manifest`).
 *
 * See docs/design/new_cli.md §5.5.1. The document is deliberately tolerant:
 * fields are optional wherever a platform can be in a non-ready state, because
 * the manifest always answers 200 and uses `state` to say what is available —
 * a client must be able to tell "not yet" from "never" from "no such store".
 */

/** Artifact platform ids. A closed enum server-side (§5.1). */
export type CliPlatform =
  | 'linux-amd64'
  | 'linux-arm64'
  | 'darwin-amd64'
  | 'darwin-arm64'
  | 'windows-amd64';

export type CliPlatformState = 'ready' | 'preparing' | 'unavailable';

/**
 * How the store injected its URL into this artifact.
 *
 * `sidecar` matters to the UI: that binary carries no baked URL, so the archive
 * is the only complete download for it and a raw download needs `sbs connect`.
 */
export type CliUrlInjection = 'patch' | 'rebuild' | 'sidecar' | 'pristine';

export interface CliPlatformEntry {
  state: CliPlatformState;
  /** Present only when `state === 'ready'`. */
  filename?: string;
  size?: number;
  sha256?: string;
  url_injection?: CliUrlInjection;
  /** Relative, so the document stays correct behind any path prefix. */
  download_url?: string;
  archive_url?: string;
  archive_sha256?: string;
  archive_size?: number;
  archive_filename?: string;
  /** Present when not ready: `not_bundled`, `prepare_failed`, ... */
  reason?: string;
  /** Seconds to wait before retrying, when `state === 'preparing'`. */
  retry_after?: number;
}

export interface CliEngine {
  name: string;
  version: string;
  license: string;
  license_url: string;
}

export interface CliManifest {
  cli_name: string;
  cli_version: string;
  /** Null when the operator has not set SBS_PUBLIC_URL. */
  public_url: string | null;
  generated_at: string | null;
  engine: CliEngine;
  platforms: Partial<Record<CliPlatform, CliPlatformEntry>>;
}

/** Display order and labels for the platform chooser. */
export const CLI_PLATFORM_LABELS: Array<{ id: CliPlatform; label: string }> = [
  { id: 'darwin-arm64', label: 'macOS — Apple Silicon (M1–M4)' },
  { id: 'darwin-amd64', label: 'macOS — Intel' },
  { id: 'linux-amd64', label: 'Linux — x86-64' },
  { id: 'linux-arm64', label: 'Linux — ARM64' },
  { id: 'windows-amd64', label: 'Windows — x86-64' },
];

/** Human text for a `reason` code, which is otherwise machine-shaped. */
export function cliReasonText(reason?: string): string {
  switch (reason) {
    case 'not_bundled':
      return 'No build for this platform is bundled with this store.';
    case 'prepare_failed':
      return 'Preparing this build failed. Check the server logs.';
    case 'url_too_long':
      return "This store's public URL is too long to embed in the binary.";
    case 'no_slot_found':
      return 'The bundled build is incompatible with this store version.';
    case 'no_toolchain':
      return 'This store is configured to compile the CLI but has no Go toolchain.';
    default:
      return 'This build is not available from this store.';
  }
}
