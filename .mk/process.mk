##@ Setup & teardown as a process

# Add ui-build as a prerequisite of the common `run` target so the UI static
# bundle exists (and is current with respect to sources and ACL config) before
# UIManager spawns `vite preview`. The recipe lives in skillberry-common.
# In DEPLOY_ONLY mode the bundle is already baked into the image; skip the
# prereq (the `ui-build` target itself remains invokable directly).
ifneq ($(DEPLOY_ONLY),TRUE)
run: ui-build

# Same reasoning for the test targets, but best-effort — see ui-build-optional
# in .mk/dev.mk. Without this the whole /ui surface is untested in CI.
test: ui-build-optional
test-e2e: ui-build-optional
endif

# The plugin enable/disable config. skillberry_store.plugins.config resolves
# SKILLBERRY_PLUGIN_CONFIG and otherwise falls back to ~/.skillberry/plugins.json,
# so the repo's own plugin_config.json was read by nothing and a machine-local
# home file silently decided which plugins ran. Point the service at the in-repo
# file instead, so the enabled set is version-controlled and travels with the
# checkout.
#
# Guarded on the file existing, because a MISSING config means "every plugin
# enabled": exporting a path that isn't there (a container image that never
# copied the file) would be worse than not exporting at all. An explicit
# environment value still wins.
SKILLBERRY_PLUGIN_CONFIG ?= $(wildcard $(CURDIR)/plugin_config.json)
ifneq ($(SKILLBERRY_PLUGIN_CONFIG),)
export SKILLBERRY_PLUGIN_CONFIG
endif

clean-service-data: stop
	@echo "Clean $(SERVICE_NAME) /tmp directory"
	+rm -rf /tmp/manifest
	+rm -rf /tmp/descriptions
	+rm -rf /tmp/files

