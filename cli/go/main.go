// Command sbs is the Skillberry Store CLI.
//
// It embeds restish (https://rest.sh) as a Go *library* rather than shelling
// out to it, which is what lets every usage line, error and hint say "sbs".
// See docs/design/new_cli.md — §3 for why the subprocess shim was replaced,
// §4 for the branding layer this file is.
//
// The whole of the branding is the ~30 lines in run(): a command name, a
// description, a default config, a command surface and an auth handler. There
// is deliberately no output rewriting anywhere in this program; the superseded
// design's ~150-line scrubber is the thing this approach exists to not write
// (§10).
package main

import (
	"errors"
	"fmt"
	"os"
	"strings"

	restish "github.com/rest-sh/restish/v2"
)

// apiName is the key our baked API config is registered under, and the value
// of CommandSurface.PromotedAPI. Promoting it lifts generated operations to the
// root, so the user types `sbs list-skills` and not `sbs store list-skills`
// (§4.2) — the two-level shape was the shim's core ergonomic problem.
const apiName = "store"

// supportNamespace collects restish's own support commands under one branded
// parent: `sbs cli doctor`, `sbs cli config path`, `sbs cli auth inspect`.
//
// They stay reachable (support tickets, CI, recovery) but off the primary help
// screen, which also keeps the two residual "Restish" flag descriptions of §3.3
// off the first thing a user reads. Hiding them outright (HideSupportCommands)
// was the alternative and is strictly worse: `doctor` is what a support request
// starts with.
const supportNamespace = "cli"

func main() {
	err := run(os.Args, os.Stdout, os.Stderr)
	var ec exitCode
	if errors.As(err, &ec) {
		os.Exit(int(ec))
	}
	if err != nil {
		// A single-line error. restish already prints operation-level failures
		// itself; this catches the wiring-level ones.
		fmt.Fprintf(os.Stderr, "%s: %v\n", cliName, err)
		os.Exit(1)
	}
}

// exitCode carries a specific process exit status out of run() as an error.
//
// The verbs need to distinguish "failed" from "failed with status 2" (bad
// usage) and from "succeeded" — and run() has to stay a plain
// `func(...) error` to be testable without the test process exiting. Calling
// os.Exit inside run() would make every one of its tests unrunnable.
type exitCode int

func (c exitCode) Error() string { return fmt.Sprintf("exit status %d", int(c)) }

// exitCode(0) is success, so it must not surface as a non-nil error.
func newExitCode(code int) error {
	if code == 0 {
		return nil
	}
	return exitCode(code)
}

// run is main() with its I/O and argv injected, so the whole startup path —
// including the local verbs and the spec-discovery error wrapper — is testable
// without building and exec'ing a binary.
func run(argv []string, stdout, stderr *os.File) error {
	env := osEnviron{}

	home, err := os.UserHomeDir()
	if err != nil {
		home = ""
	}

	// Branding the paths must happen before anything reads config, because
	// restish resolves RSH_CONFIG_DIR / RSH_CONFIG / RSH_CACHE_DIR eagerly.
	paths, pathErr := brandPaths(env, home)
	if pathErr == nil {
		// One-time migration from ~/.config/restish (§4.4, G9). A failure here
		// is reported but never fatal: the user can always `sbs connect`, and
		// refusing to start because an *old* config could not be copied would
		// be a self-inflicted outage.
		if notice, err := migrateLegacyConfig(paths, legacyConfigPath(env, home)); err != nil {
			writeLine(stderr, fmt.Sprintf("Warning: could not migrate the previous configuration: %v", err))
		} else if notice != "" {
			writeLine(stderr, notice)
		}

		// Must come after the migration (which writes the file itself when it
		// has something to copy) and before any engine dispatch. See
		// ensureConfigFile: without a file at RSH_CONFIG the config load fails
		// and the whole branded command surface silently reverts to stock
		// restish.
		if err := ensureConfigFile(paths); err != nil {
			writeLine(stderr, fmt.Sprintf("Warning: could not create %s: %v", paths.ConfigFile, err))
		}
	}

	// Verbs we own that must work with no engine at all, handled before restish
	// sees argv. They need no upstream command registration (§G4), which keeps
	// this file's dependency on the embedding API small enough to pin
	// confidently.
	//
	// "With no engine at all" is the load-bearing part, not just an
	// optimisation. A promoted API fetches its spec before it can build any
	// command, and fails hard when that fetch fails (§3.4 #2). So the two verbs
	// whose whole job is to recover from an unreachable or wrong store —
	// `connect` and `download-cli` — must not be routed through Run(), or they
	// would be unusable in exactly the situation they exist for.
	if handled, code := localVerb(argv, stdout, stderr, env, paths); handled {
		return newExitCode(code)
	}

	// The thin auth verbs (§4.3) are dispatched *through* the engine instead,
	// because their work is "clear the cached token" and "authenticate now" —
	// and restish already owns the token cache and derives its keys. Calling
	// `cli auth logout` rather than reaching into the cache ourselves means the
	// cache-key derivation lives in exactly one place, upstream's.
	if len(argv) >= 2 {
		switch argv[1] {
		case "login":
			return newExitCode(doLogin(argv[2:], stdout, stderr, env))
		case "logout":
			return newExitCode(doLogout(argv[2:], stdout, stderr, env))
		}
	}

	return wrapRunError(newCLI(env).Run(argv), baseURL(env))
}

// newCLI builds a configured embedded CLI.
//
// A factory rather than a single long-lived instance because the thin verbs run
// two engine dispatches in sequence, and a cobra command tree is not documented
// as re-entrant — reusing one instance across two Run() calls would be relying
// on an implementation detail upstream never promised.
func newCLI(env environ) *restish.CLI {
	c := restish.New()
	c.SetCommandName(cliName)
	c.SetCommandDescription(shortHelp, longHelp())
	c.SetVersion(versionLine())
	c.SetDefaultConfig(defaultConfig(env))
	c.SetCommandSurface(restish.CommandSurface{
		PromotedAPI:             apiName,
		SupportCommandNamespace: supportNamespace,
	})
	c.AddAuthHandler(authSchemeName, &standaloneAuth{})
	return c
}

// defaultConfig is the compiled-in API registration: base URL, spec URL and the
// two profiles.
//
// SetDefaultConfig merges *underneath* the user's config file, so this supplies
// defaults without overriding anything the user set with `sbs connect`. That
// precedence is a library property here rather than something this repo
// implements and tests — which is most of why the shim's `_restish_connect`,
// `_registered_base` and `_ensure_env_profile` could simply be deleted (§4.5).
func defaultConfig(env environ) *restish.Config {
	base := baseURL(env)
	return &restish.Config{
		APIs: map[string]*restish.APIConfig{
			apiName: {
				BaseURL: base,
				SpecURL: base + "/openapi.json",
				Profiles: map[string]*restish.ProfileConfig{
					// The interactive default: prompt, POST /auth/login, cache
					// the bearer in restish's own token store.
					"default": {
						Auth: &restish.AuthConfig{Type: authSchemeName},
					},
					// CI / scripting. `env:SBS_TOKEN` is how the shim's
					// `env-token` profile worked, now a literal in the baked
					// config instead of a subprocess call that wrote one.
					"env-token": {
						Headers: []string{"Authorization: Bearer ${" + tokenEnvVar + "}"},
					},
				},
			},
		},
	}
}

const shortHelp = "Skillberry Store CLI"

// longHelp is the root description. Written as configuration rather than as
// text injected into someone else's help output (D2): restish prints whatever
// we hand it here, so there is nothing to intercept or rewrite.
func longHelp() string {
	return strings.TrimSpace(fmt.Sprintf(`
%[1]s is the command-line interface to a Skillberry Store.

Operations are generated from the store's own OpenAPI spec, so the commands
available always match the store you are pointed at:

  %[1]s list-skills                 List the skills in the store
  %[1]s get-skill <uuid>            Fetch one skill
  %[1]s list-tools                  List the tools in the store

Pointing at a store:

  %[1]s connect <url>               Remember a store URL (writes your config)
  %[2]s=<url> %[1]s list-skills        Override the URL for one command

Authentication happens on demand: any command prompts when the store requires
it, and the token is cached, so there is no separate setup step. %[3]s
pre-authenticates and %[4]s drops the cached token.

  %[5]s=<token> %[1]s list-skills      Use a token instead of prompting (CI)

Getting the CLI onto another machine:

  %[1]s download-cli                Download the artifact for any platform
  %[1]s self-update                 Replace this binary with the store's build

Configuration, diagnostics and cache management live under "%[1]s %[6]s":

  %[1]s %[6]s doctor                     Show resolved URLs, paths and spec freshness
  %[1]s %[6]s config path                Print the config file path
  %[1]s %[6]s cache clear                Drop the cached OpenAPI spec
`, cliName, urlEnvVar, cliName+" login", cliName+" logout", tokenEnvVar, supportNamespace))
}

// versionLine reports our version and the embedded engine, so a bug report
// carries both numbers without anyone having to ask for the second one.
//
// Deliberately NOT prefixed with cliName: restish renders this as
// "<command> version <what SetVersion was given>", so including the name here
// produces "sbs version sbs 0.1.0".
func versionLine() string {
	return fmt.Sprintf("%s (engine: restish %s)", version, engineVersion)
}

// wrapRunError turns restish's spec-discovery failure into one actionable line.
//
// A promoted API fetches its spec when help or dispatch needs command metadata,
// and with an unreachable SpecURL the root help fails *hard* rather than
// degrading (§3.4 #2, G3). That is upstream's documented behaviour for promoted
// roots, and it is the single most likely first-run failure for a downloaded
// artifact: the binary is fine, the store is simply not reachable from here.
// Without this wrapper the user sees an internal-sounding
// `generated commands for promoted API "store" are unavailable: …` and has no
// idea which URL was tried or how to change it.
func wrapRunError(err error, base string) error {
	if err == nil {
		return nil
	}
	// Matched on the message rather than on a sentinel: upstream exports no
	// error value for this, and the alternative (wrapping every error the same
	// way) would attach store-URL advice to genuine 404s and validation errors.
	msg := err.Error()
	if !strings.Contains(msg, "spec discovery failed") && !strings.Contains(msg, "are unavailable") {
		return err
	}
	return errors.New(strings.TrimSpace(fmt.Sprintf(`could not load the API description from %[2]s

%[3]s

This build is configured to talk to %[2]s. If that is not the store you meant,
or it is not reachable from here:

  %[1]s connect <url>        point this CLI at a different store, permanently
  %[4]s=<url> %[1]s ...      override the URL for a single command
  %[1]s download-cli         re-download a build configured for another store`,
		cliName, base, msg, urlEnvVar)))
}
