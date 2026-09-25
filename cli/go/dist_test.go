package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

// manifestServer serves a /cli/manifest + /cli/download pair for one platform.
func manifestServer(t *testing.T, payload []byte, state string) (*httptest.Server, string) {
	t.Helper()
	sum := sha256.Sum256(payload)
	hexSum := hex.EncodeToString(sum[:])

	mux := http.NewServeMux()
	mux.HandleFunc("/cli/manifest", func(w http.ResponseWriter, r *http.Request) {
		doc := map[string]any{
			"cli_name":    cliName,
			"cli_version": "9.9.9",
			"public_url":  "http://store.test",
			"platforms": map[string]any{
				currentPlatform(): map[string]any{
					"state":          state,
					"filename":       cliName,
					"size":           len(payload),
					"sha256":         hexSum,
					"url_injection":  "patch",
					"download_url":   "/cli/download?platform=" + currentPlatform() + "&format=raw",
					"archive_url":    "/cli/download?platform=" + currentPlatform() + "&format=archive",
					"archive_sha256": hexSum,
					"retry_after":    10,
				},
			},
		}
		w.Header().Set("Content-Type", "application/json")
		_ = json.NewEncoder(w).Encode(doc)
	})
	mux.HandleFunc("/cli/download", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write(payload)
	})

	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)
	return srv, hexSum
}

func TestDownloadCLIWritesVerifiedExecutable(t *testing.T) {
	payload := []byte("#!/bin/sh\necho fake-sbs\n")
	srv, wantSum := manifestServer(t, payload, "ready")

	target := filepath.Join(t.TempDir(), "sbs")
	out, _ := captureFile(t)
	env := mapEnviron{urlEnvVar: srv.URL}

	code := doDownloadCLI([]string{"--output", target}, out, devNull(t), env)
	if code != 0 {
		t.Fatalf("doDownloadCLI = %d, want 0", code)
	}

	got, err := os.ReadFile(target)
	if err != nil {
		t.Fatalf("artifact not written: %v", err)
	}
	if string(got) != string(payload) {
		t.Errorf("artifact contents = %q, want the served payload", got)
	}
	sum := sha256.Sum256(got)
	if hex.EncodeToString(sum[:]) != wantSum {
		t.Error("written artifact does not match the manifest sha256")
	}

	if runtime.GOOS != "windows" {
		// A lost executable bit is the exact problem a browser download has and
		// this path exists to avoid (§5.5.2).
		info, err := os.Stat(target)
		if err != nil {
			t.Fatal(err)
		}
		if perm := info.Mode().Perm(); perm != 0o755 {
			t.Errorf("artifact mode = %o, want 755", perm)
		}
	}
}

// The supply-chain assertion (§5.8 #3, B18): a payload whose hash does not match
// the manifest must never reach the target path, let alone with +x set.
func TestDownloadCLIRefusesChecksumMismatch(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/cli/manifest", func(w http.ResponseWriter, r *http.Request) {
		doc := map[string]any{
			"cli_name": cliName, "cli_version": "9.9.9",
			"platforms": map[string]any{
				currentPlatform(): map[string]any{
					"state": "ready", "filename": cliName,
					"sha256":       strings.Repeat("0", 64), // deliberately wrong
					"download_url": "/cli/download",
				},
			},
		}
		_ = json.NewEncoder(w).Encode(doc)
	})
	mux.HandleFunc("/cli/download", func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("tampered payload"))
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	target := filepath.Join(t.TempDir(), "sbs")
	code := doDownloadCLI([]string{"--output", target}, devNull(t), devNull(t), mapEnviron{urlEnvVar: srv.URL})
	if code == 0 {
		t.Fatal("a checksum mismatch must fail")
	}
	if _, err := os.Stat(target); !os.IsNotExist(err) {
		t.Error("a tampered artifact was written to the target path")
	}
}

func TestDownloadCLIReportsPreparingWithRetryHint(t *testing.T) {
	srv, _ := manifestServer(t, []byte("x"), "preparing")
	errOut, readErr := captureFile(t)

	code := doDownloadCLI([]string{"--output", filepath.Join(t.TempDir(), "sbs")}, devNull(t), errOut, mapEnviron{urlEnvVar: srv.URL})
	if code == 0 {
		t.Fatal("a preparing platform must not report success")
	}
	msg := readErr()
	if !strings.Contains(msg, "prepared") {
		t.Errorf("stderr = %q, want it to say the build is being prepared", msg)
	}
	// The Retry-After hint is what turns "try again" into actionable advice.
	if !strings.Contains(msg, "10") {
		t.Errorf("stderr = %q, want the retry-after seconds", msg)
	}
}

func TestDownloadCLIReportsUnavailableReason(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/cli/manifest", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"platforms": map[string]any{
				currentPlatform(): map[string]any{"state": "unavailable", "reason": "not_bundled"},
			},
		})
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	errOut, readErr := captureFile(t)
	code := doDownloadCLI(nil, devNull(t), errOut, mapEnviron{urlEnvVar: srv.URL})
	if code == 0 {
		t.Fatal("an unavailable platform must not report success")
	}
	if !strings.Contains(readErr(), "not_bundled") {
		t.Errorf("stderr = %q, want the server's reason verbatim", readErr())
	}
}

func TestDownloadCLIUnknownPlatformListsWhatIsAvailable(t *testing.T) {
	srv, _ := manifestServer(t, []byte("x"), "ready")
	errOut, readErr := captureFile(t)

	code := doDownloadCLI([]string{"--platform", "plan9-mips"}, devNull(t), errOut, mapEnviron{urlEnvVar: srv.URL})
	if code == 0 {
		t.Fatal("an unknown platform must fail")
	}
	// Naming what *is* available turns a dead end into a next step.
	if !strings.Contains(readErr(), currentPlatform()) {
		t.Errorf("stderr = %q, want it to list the available platforms", readErr())
	}
}

// A manifest must not be able to redirect the download to another host: that
// would turn one compromised or misconfigured store into arbitrary code
// delivery on the user's machine.
func TestDownloadRefusesOffHostDownloadURL(t *testing.T) {
	evil := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte("malicious"))
	}))
	t.Cleanup(evil.Close)

	entry := manifestPlatform{DownloadURL: evil.URL + "/payload", SHA256: ""}
	target := filepath.Join(t.TempDir(), "sbs")
	_, _, err := downloadVerified("http://store.test", entry, "raw", target)
	if err == nil {
		t.Fatal("an off-host download URL must be refused")
	}
	if !strings.Contains(err.Error(), "refusing") {
		t.Errorf("error = %v, want an explicit refusal", err)
	}
	if _, statErr := os.Stat(target); !os.IsNotExist(statErr) {
		t.Error("an off-host payload was written")
	}
}

func TestDownloadVerifiedRejectsSizeMismatch(t *testing.T) {
	payload := []byte("twelve bytes")
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write(payload)
	}))
	t.Cleanup(srv.Close)

	sum := sha256.Sum256(payload)
	entry := manifestPlatform{
		DownloadURL: "/x",
		SHA256:      hex.EncodeToString(sum[:]),
		Size:        999, // disagrees with what arrives
	}
	target := filepath.Join(t.TempDir(), "sbs")
	if _, _, err := downloadVerified(srv.URL, entry, "raw", target); err == nil {
		t.Fatal("a size mismatch must fail even when the hash matches")
	}
}

func TestDownloadCLIArchiveUsesArchiveURLAndMode(t *testing.T) {
	payload := []byte("fake tarball")
	srv, _ := manifestServer(t, payload, "ready")

	target := filepath.Join(t.TempDir(), "sbs.tar.gz")
	code := doDownloadCLI([]string{"--format", "archive", "--output", target}, devNull(t), devNull(t), mapEnviron{urlEnvVar: srv.URL})
	if code != 0 {
		t.Fatalf("doDownloadCLI --format archive = %d", code)
	}
	if runtime.GOOS != "windows" {
		info, err := os.Stat(target)
		if err != nil {
			t.Fatal(err)
		}
		// An archive is data, not a program: it must not be +x.
		if perm := info.Mode().Perm(); perm != 0o644 {
			t.Errorf("archive mode = %o, want 644", perm)
		}
	}
}

func TestDownloadCLIDefaultFilenames(t *testing.T) {
	payload := []byte("x")
	srv, _ := manifestServer(t, payload, "ready")

	// Run in a temp cwd so the default output path lands somewhere disposable.
	dir := t.TempDir()
	orig, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	if err := os.Chdir(dir); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(orig) })

	if code := doDownloadCLI(nil, devNull(t), devNull(t), mapEnviron{urlEnvVar: srv.URL}); code != 0 {
		t.Fatalf("doDownloadCLI = %d", code)
	}
	if _, err := os.Stat(filepath.Join(dir, cliName)); err != nil {
		t.Errorf("default raw download should be named %q: %v", cliName, err)
	}
}

func TestFetchPlatformSurfacesDownloadsDisabled(t *testing.T) {
	// SBS_CLI_DOWNLOAD=off unregisters the endpoints entirely, so the CLI sees a
	// 404. Saying so beats "unexpected status 404".
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
	}))
	t.Cleanup(srv.Close)

	_, _, err := fetchPlatform(srv.URL, currentPlatform())
	if err == nil {
		t.Fatal("a 404 manifest must be an error")
	}
	if !strings.Contains(err.Error(), "SBS_CLI_DOWNLOAD") {
		t.Errorf("error = %v, want it to name the operator switch", err)
	}
}

func TestFetchPlatformUnreachableStore(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	url := srv.URL
	srv.Close() // now refusing connections

	_, _, err := fetchPlatform(url, currentPlatform())
	if err == nil {
		t.Fatal("an unreachable store must be an error")
	}
	if !strings.Contains(err.Error(), "could not reach") {
		t.Errorf("error = %v, want a connection-level message naming the host", err)
	}
}

func TestSelfUpdateRejectsArchiveFormat(t *testing.T) {
	// Replacing the running binary with a tarball would produce a file that is
	// not executable; refusing beats "installed" followed by "command not found".
	code := doSelfUpdate([]string{"--format", "archive"}, devNull(t), devNull(t), mapEnviron{})
	if code != 2 {
		t.Errorf("doSelfUpdate --format archive = %d, want 2", code)
	}
}

func TestSelfUpdateSkipsWhenVersionsMatch(t *testing.T) {
	mux := http.NewServeMux()
	mux.HandleFunc("/cli/manifest", func(w http.ResponseWriter, r *http.Request) {
		_ = json.NewEncoder(w).Encode(map[string]any{
			"cli_name":    cliName,
			"cli_version": version, // identical to this build
			"platforms": map[string]any{
				currentPlatform(): map[string]any{
					"state": "ready", "filename": cliName,
					"download_url": "/cli/download",
				},
			},
		})
	})
	mux.HandleFunc("/cli/download", func(w http.ResponseWriter, r *http.Request) {
		t.Error("self-update must not download when the versions already match")
	})
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	out, read := captureFile(t)
	if code := doSelfUpdate(nil, out, devNull(t), mapEnviron{urlEnvVar: srv.URL}); code != 0 {
		t.Fatalf("doSelfUpdate = %d, want 0", code)
	}
	if !strings.Contains(read(), "Already running") {
		t.Errorf("stdout = %q, want it to say no update was needed", read())
	}
}

func TestReadyPlatformsReportsNoneWhenEmpty(t *testing.T) {
	got := readyPlatforms(manifestDoc{Platforms: map[string]manifestPlatform{
		"linux-amd64": {State: "unavailable"},
	}})
	if len(got) != 1 || got[0] != "none" {
		t.Errorf("readyPlatforms = %v, want [none]", got)
	}
}

func TestManifestDocIgnoresUnknownFields(t *testing.T) {
	// Forward compatibility: the server must be able to add fields to the
	// manifest without breaking binaries users already downloaded.
	body := `{"cli_name":"sbs","brand_new_field":{"nested":true},
	          "platforms":{"linux-amd64":{"state":"ready","future":"ok"}}}`
	var doc manifestDoc
	if err := json.Unmarshal([]byte(body), &doc); err != nil {
		t.Fatalf("unknown manifest fields must be ignored, got %v", err)
	}
	if doc.Platforms["linux-amd64"].State != "ready" {
		t.Error("known fields must still parse alongside unknown ones")
	}
}

func TestDownloadVerifiedResolvesRelativeURL(t *testing.T) {
	payload := []byte("relative-ok")
	var gotPath string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.Path + "?" + r.URL.RawQuery
		_, _ = w.Write(payload)
	}))
	t.Cleanup(srv.Close)

	sum := sha256.Sum256(payload)
	entry := manifestPlatform{
		DownloadURL: "/cli/download?platform=linux-amd64&format=raw",
		SHA256:      hex.EncodeToString(sum[:]),
	}
	target := filepath.Join(t.TempDir(), "sbs")
	if _, _, err := downloadVerified(srv.URL, entry, "raw", target); err != nil {
		t.Fatalf("downloadVerified: %v", err)
	}
	// Relative URLs are what keep the manifest correct behind any path prefix.
	if want := "/cli/download?platform=linux-amd64&format=raw"; gotPath != want {
		t.Errorf("requested %q, want %q", gotPath, want)
	}
}

func TestDownloadVerifiedMissingURLForFormat(t *testing.T) {
	entry := manifestPlatform{DownloadURL: "/cli/download"} // no archive_url
	_, _, err := downloadVerified("http://store.test", entry, "archive", filepath.Join(t.TempDir(), "a.tgz"))
	if err == nil {
		t.Fatal("a missing archive URL must be an error")
	}
	if !strings.Contains(err.Error(), "archive") {
		t.Errorf("error = %v, want it to name the missing format", err)
	}
}

func TestDownloadCLIHelpFlagExplainsUsage(t *testing.T) {
	for _, verb := range []string{"download-cli", "self-update"} {
		_, err := parseDistFlags([]string{"--help"}, verb)
		if err == nil {
			t.Fatalf("%s --help should produce usage text", verb)
		}
		if !strings.Contains(err.Error(), fmt.Sprintf("%s %s", cliName, verb)) {
			t.Errorf("%s --help = %v, want branded usage", verb, err)
		}
	}
}
