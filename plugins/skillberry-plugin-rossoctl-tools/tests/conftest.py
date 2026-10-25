import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from fake_backend import BACKEND, TOKEN_URL, FakeRossoctl  # noqa: E402


@pytest.fixture
def fake():
    return FakeRossoctl()


@pytest.fixture
def backend(fake):
    from skillberry_plugin_rossoctl_tools.backend import RossoctlBackend

    return RossoctlBackend(
        base_url=BACKEND,
        client_id="skillberry-store-tools",
        client_secret="s3cret",
        token_endpoint=TOKEN_URL,
        allowed_namespaces=[],
        timeout=5,
        transport=fake.transport(),
    )


@pytest.fixture
def store_client(fake, backend, tmp_path, monkeypatch):
    """A full in-process store with the plugin active, wired to the fake backend.

    The plugin is constructed by the real loader (entry point discovery); its
    backend is then pointed at the fake. Plugin config enables only the
    plugins this test needs.
    """
    from fastapi.testclient import TestClient

    from skillberry_store.access_control import config as acl_config
    from skillberry_store.fast_api.server import SBS
    from skillberry_store.modules import object_handler
    from skillberry_store.services import registry

    monkeypatch.setenv("SBS_BASE_DIR", str(tmp_path / "sbs"))
    monkeypatch.setenv("ROSSOCTL_BACKEND_URL", BACKEND)
    monkeypatch.setenv("PROMETHEUS_METRICS_PORT", "0")
    monkeypatch.setenv("SBS_ACCESS_CONTROL_CONFIG", str(tmp_path / "acl.yaml"))
    (tmp_path / "acl.yaml").write_text("mode: disabled\n")
    plugin_cfg = tmp_path / "plugins.json"
    plugin_cfg.write_text(json.dumps({"disabled": [], "owners": {}}))
    monkeypatch.setenv("SKILLBERRY_PLUGIN_CONFIG", str(plugin_cfg))
    acl_config.reset_config_cache()
    object_handler.clear_object_handlers()
    registry.clear_services()
    app = SBS()
    plugin = app.state.plugin_loader.plugins["rossoctl-tools"]
    plugin.backend = backend
    plugin.provider.backend = backend
    client = TestClient(app)
    client.plugin = plugin
    client.loader = app.state.plugin_loader
    yield client
    object_handler.clear_object_handlers()
    registry.clear_services()
    acl_config.reset_config_cache()
