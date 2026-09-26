package cli

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

// The CLI downloading the CLI (§5.8).
//
// Deliberately implemented with net/http against /cli/manifest rather than as a
// generated operation, for the reason documented at the LocalVerb call site: a
// promoted API cannot build any command until it has fetched the store's spec,
// and these two verbs are what a user reaches for when that fetch is exactly
// what is failing. Routing them through the engine would make them unavailable
// in the situation they exist to fix.

// ManifestDoc mirrors the /cli/manifest document of §5.5.1. Only the fields
// this side acts on are declared; unknown fields are ignored, so the server can
// add to the document without breaking already-downloaded binaries.
type ManifestDoc struct {
	CLIName    string                      `json:"cli_name"`
	CLIVersion string                      `json:"cli_version"`
	PublicURL  string                      `json:"public_url"`
	Platforms  map[string]ManifestPlatform `json:"platforms"`
}

type ManifestPlatform struct {
	State         string `json:"state"` // ready | preparing | unavailable
	Filename      string `json:"filename"`
	Size          int64  `json:"size"`
	SHA256        string `json:"sha256"`
	URLInjection  string `json:"url_injection"`
	DownloadURL   string `json:"download_url"`
	ArchiveURL    string `json:"archive_url"`
	ArchiveSHA256 string `json:"archive_sha256"`
	Reason        string `json:"reason"`
	RetryAfter    int    `json:"retry_after"`
}

// CurrentPlatform is this binary's own platform id.
//
// runtime.GOOS/GOARCH is exact — unlike the server-side User-Agent sniffing of
// §5.6, which cannot tell Apple Silicon from Intel. So the CLI always sends an
// explicit ?platform=, and never relies on detection.
func CurrentPlatform() string {
	return runtime.GOOS + "-" + runtime.GOARCH
}

// DoDownloadCLI implements `sbs download-cli`.
func DoDownloadCLI(args []string, stdout, stderr *os.File, env Environ) int {
	opts, err := ParseDistFlags(args, "download-cli")
	if err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: %v", CLIName, err))
		return 2
	}

	base := BaseURL(env)
	plat := opts.Platform
	if plat == "" {
		plat = CurrentPlatform()
	}

	entry, doc, err := FetchPlatform(base, plat)
	if err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: %v", CLIName, err))
		return 1
	}

	target := opts.Output
	if target == "" {
		target = entry.Filename
		if target == "" {
			target = CLIName
		}
		if opts.Format == "archive" {
			target = fmt.Sprintf("%s-%s%s", CLIName, plat, ArchiveSuffix(plat))
		}
	}

	written, sum, err := DownloadVerified(base, entry, opts.Format, target)
	if err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: %v", CLIName, err))
		return 1
	}

	fmt.Fprintf(stdout, "Downloaded %s %s for %s to %s (%d bytes, sha256 %s)\n",
		doc.CLIName, doc.CLIVersion, plat, target, written, sum)
	if opts.Format != "archive" && entry.URLInjection == "sidecar" {
		// The one platform/mechanism combination that needs a second file
		// (§5.2): the binary itself carries no baked URL, so a raw download
		// would come up pointing at its compile-time default.
		fmt.Fprintf(stdout,
			"\nNote: builds for %s carry their store URL in a sidecar file rather than in the\n"+
				"binary. Download --format archive instead, or run:\n  %s connect %s\n",
			plat, target, doc.PublicURL)
	}
	return 0
}

// DoSelfUpdate implements `sbs self-update`: replace the running binary.
func DoSelfUpdate(args []string, stdout, stderr *os.File, env Environ) int {
	opts, err := ParseDistFlags(args, "self-update")
	if err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: %v", CLIName, err))
		return 2
	}
	if opts.Format == "archive" {
		WriteLine(stderr, fmt.Sprintf("%s: self-update cannot use --format archive", CLIName))
		return 2
	}

	// Always this platform: self-update replaces *this* binary, so honouring a
	// --platform here would install an artifact that cannot execute.
	plat := CurrentPlatform()
	base := BaseURL(env)

	self, err := os.Executable()
	if err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: cannot locate the running binary: %v", CLIName, err))
		return 1
	}
	// Resolve symlinks so an update through ~/.local/bin/sbs -> /opt/sbs/sbs
	// replaces the real file rather than turning the symlink into a regular one.
	if resolved, err := filepath.EvalSymlinks(self); err == nil {
		self = resolved
	}

	entry, doc, err := FetchPlatform(base, plat)
	if err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: %v", CLIName, err))
		return 1
	}

	if doc.CLIVersion != "" && doc.CLIVersion == Version {
		fmt.Fprintf(stdout, "Already running %s %s; nothing to do.\n", CLIName, Version)
		return 0
	}

	// Staged in the target's own directory: os.Rename cannot cross filesystems,
	// and /tmp is very often a different one (tmpfs, or a separate volume).
	staged := self + ".new"
	if _, _, err := DownloadVerified(base, entry, "raw", staged); err != nil {
		WriteLine(stderr, fmt.Sprintf("%s: %v", CLIName, err))
		return 1
	}

	if runtime.GOOS == "windows" {
		// Windows refuses to replace a file that is currently mapped as a
		// running image, so the rename has to happen after this process exits.
		// Printing the command is honest; silently scheduling a background
		// mover would be worse than telling the user one line to run.
		fmt.Fprintf(stdout,
			"Downloaded %s %s to %s.\nWindows cannot replace a running executable, so finish with:\n  move /Y \"%s\" \"%s\"\n",
			CLIName, doc.CLIVersion, staged, staged, self)
		return 0
	}

	if err := os.Rename(staged, self); err != nil {
		os.Remove(staged)
		WriteLine(stderr, fmt.Sprintf("%s: could not replace %s: %v", CLIName, self, err))
		return 1
	}
	fmt.Fprintf(stdout, "Updated %s to %s at %s\n", CLIName, doc.CLIVersion, self)
	return 0
}

// DistOptions is the parsed flag set shared by both verbs.
type DistOptions struct {
	Platform string
	Output   string
	Format   string
}

// ParseDistFlags parses the flags by hand rather than with the flag package.
//
// Two reasons: these verbs run before cobra exists, and `flag` would print its
// own unbranded usage to stderr and call os.Exit on a bad value, which would
// bypass the error handling the rest of this program uses.
func ParseDistFlags(args []string, verb string) (DistOptions, error) {
	opts := DistOptions{Format: "raw"}
	for i := 0; i < len(args); i++ {
		arg := args[i]
		// Accept both `--flag value` and `--flag=value`: the second is what
		// people type, and rejecting it would be a gratuitous difference from
		// every generated operation on this same CLI.
		name, inline, hasInline := strings.Cut(arg, "=")
		value := func() (string, error) {
			if hasInline {
				return inline, nil
			}
			if i+1 >= len(args) {
				return "", fmt.Errorf("%s: %s needs a value", verb, name)
			}
			i++
			return args[i], nil
		}

		var err error
		switch name {
		case "--platform":
			opts.Platform, err = value()
		case "--output", "-o":
			opts.Output, err = value()
		case "--format":
			opts.Format, err = value()
		case "--help", "-h":
			return opts, fmt.Errorf("usage: %s %s [--platform <id>] [--output <path>] [--format raw|archive]", CLIName, verb)
		default:
			return opts, fmt.Errorf("%s: unknown option %q", verb, arg)
		}
		if err != nil {
			return opts, err
		}
	}
	if opts.Format != "raw" && opts.Format != "archive" {
		return opts, fmt.Errorf("%s: --format must be raw or archive (got %q)", verb, opts.Format)
	}
	return opts, nil
}

// FetchPlatform gets the manifest and resolves one platform's entry, turning the
// non-ready states into the actionable messages §5.8 #2 asks for.
func FetchPlatform(base, platform string) (ManifestPlatform, ManifestDoc, error) {
	var doc ManifestDoc

	client := &http.Client{Timeout: 30 * time.Second}
	resp, err := client.Get(base + "/cli/manifest")
	if err != nil {
		return ManifestPlatform{}, doc, fmt.Errorf("could not reach %s: %w", base, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusNotFound {
		return ManifestPlatform{}, doc, fmt.Errorf(
			"%s does not offer CLI downloads (the operator may have set SBS_CLI_DOWNLOAD=off)", base)
	}
	if resp.StatusCode != http.StatusOK {
		return ManifestPlatform{}, doc, fmt.Errorf("%s/cli/manifest returned HTTP %d", base, resp.StatusCode)
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&doc); err != nil {
		return ManifestPlatform{}, doc, fmt.Errorf("could not parse %s/cli/manifest: %w", base, err)
	}

	entry, ok := doc.Platforms[platform]
	if !ok {
		return ManifestPlatform{}, doc, fmt.Errorf("%s does not offer a build for %s (available: %s)",
			base, platform, strings.Join(ReadyPlatforms(doc), ", "))
	}

	switch entry.State {
	case "ready":
		return entry, doc, nil
	case "preparing":
		wait := entry.RetryAfter
		if wait <= 0 {
			wait = 10
		}
		return entry, doc, fmt.Errorf("the %s build is still being prepared; try again in %d seconds", platform, wait)
	default:
		reason := entry.Reason
		if reason == "" {
			reason = "unavailable"
		}
		return entry, doc, fmt.Errorf("the %s build is not available from %s (%s)", platform, base, reason)
	}
}

// ReadyPlatforms lists the platform ids in the ready state, for error messages.
func ReadyPlatforms(doc ManifestDoc) []string {
	var out []string
	for id, p := range doc.Platforms {
		if p.State == "ready" {
			out = append(out, id)
		}
	}
	if len(out) == 0 {
		return []string{"none"}
	}
	return out
}

// DownloadVerified streams the artifact to a temp file beside the target,
// verifies its sha256 against the manifest, then renames it into place.
//
// Verifying *before* the rename is the whole point (§5.8 #3, B18): the store is
// handing the user an executable, so a truncated or tampered download must never
// end up at the target path, let alone with the executable bit set. Returns the
// byte count and the hex digest actually computed.
func DownloadVerified(base string, entry ManifestPlatform, format, target string) (int64, string, error) {
	url, want := entry.DownloadURL, entry.SHA256
	if format == "archive" {
		url, want = entry.ArchiveURL, entry.ArchiveSHA256
	}
	if url == "" {
		return 0, "", fmt.Errorf("the manifest has no %s download URL for this platform", format)
	}
	// The manifest carries relative URLs so the document stays correct behind
	// any path prefix (§5.5.1); resolve against the base we already trust
	// rather than accepting an absolute URL from the document, which would let
	// a manifest redirect the download to another host.
	if strings.HasPrefix(url, "/") {
		url = base + url
	} else if !strings.HasPrefix(url, base) {
		return 0, "", fmt.Errorf("refusing a download URL outside %s: %q", base, url)
	}

	dir := filepath.Dir(target)
	if dir == "" {
		dir = "."
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return 0, "", err
	}

	client := &http.Client{Timeout: 10 * time.Minute}
	resp, err := client.Get(url)
	if err != nil {
		return 0, "", fmt.Errorf("download failed: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode == http.StatusServiceUnavailable {
		return 0, "", fmt.Errorf("the build is still being prepared; try again shortly")
	}
	if resp.StatusCode != http.StatusOK {
		return 0, "", fmt.Errorf("download failed: %s returned HTTP %d", url, resp.StatusCode)
	}

	tmp, err := os.CreateTemp(dir, ".sbs-download-*")
	if err != nil {
		return 0, "", err
	}
	tmpName := tmp.Name()
	// Runs on every path; a no-op once the rename below has moved the file.
	defer os.Remove(tmpName)

	hasher := sha256.New()
	written, err := io.Copy(io.MultiWriter(tmp, hasher), resp.Body)
	if err != nil {
		tmp.Close()
		return 0, "", fmt.Errorf("download failed: %w", err)
	}
	if err := tmp.Close(); err != nil {
		return 0, "", err
	}

	sum := hex.EncodeToString(hasher.Sum(nil))
	if want != "" && !strings.EqualFold(sum, want) {
		return 0, "", fmt.Errorf("checksum mismatch: the manifest says %s but the download hashed to %s; not installing it", want, sum)
	}
	if entry.Size > 0 && format == "raw" && written != entry.Size {
		return 0, "", fmt.Errorf("size mismatch: the manifest says %d bytes but %d arrived", entry.Size, written)
	}

	// 0755 for a raw binary — the browser-download problem this exists to avoid
	// (§5.5.2) is precisely a lost executable bit. An archive is data, so 0644.
	mode := os.FileMode(0o755)
	if format == "archive" {
		mode = 0o644
	}
	if err := os.Chmod(tmpName, mode); err != nil {
		return 0, "", err
	}
	if err := os.Rename(tmpName, target); err != nil {
		return 0, "", fmt.Errorf("could not write %s: %w", target, err)
	}
	return written, sum, nil
}

// ArchiveSuffix is the archive extension per platform, matching what the server
// generates in §5.5.2.
func ArchiveSuffix(platform string) string {
	if strings.HasPrefix(platform, "windows-") {
		return ".zip"
	}
	return ".tar.gz"
}
