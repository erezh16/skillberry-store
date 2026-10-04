"""Health and readiness probe contracts.

Two consumers with incompatible needs share these endpoints:

* An external platform health check (Render's health check path, a Kubernetes
  ``livenessProbe``, an ALB target group) asks "should I restart this?". It must
  get 200 throughout a normal boot — a 5xx there is read as a crash, the
  instance is torn down, and the boot it interrupted never finishes. That
  restart loop is what these tests exist to prevent.
* The UI asks "can I turn semantic search on yet?". It needs to know when
  startup has actually finished, which a bare 200 cannot express.

``/health`` serves both by answering 200 always and carrying the boot ``stage``
in its body. ``/health/ready`` keeps the strict traffic-gating contract, and
signals "not yet" with 503 — never 500.
"""

import pytest
from fastapi.testclient import TestClient

from skillberry_store.fast_api.server import SBS
from skillberry_store.services.admin_service import (
    STAGE_INITIALIZING,
    STAGE_OPERATIONAL,
    AdminService,
)
from skillberry_store.tests.utils import clean_test_tmp_dir


class _PendingTask:
    """Stands in for an encoder warmup task that has not finished."""

    def done(self) -> bool:
        return False


@pytest.fixture
def sbs_app():
    """A freshly initialised app with process-lifetime singletons reset.

    Deliberately used *without* ``TestClient``'s context manager in these
    tests: entering it runs the lifespan hook, which schedules the real encoder
    warmup, and whether that task has finished by assertion time is a race. Not
    running it leaves ``app.state.encoder_warmup_task`` unset — which the API
    layer reads as "no warmup pending" — so the warm case is deterministic and
    the pending case is produced explicitly with ``_PendingTask``.
    """
    from skillberry_store.modules import object_handler
    from skillberry_store.services import registry

    clean_test_tmp_dir()
    object_handler.clear_object_handlers()
    registry.clear_services()
    app = SBS()
    yield app
    object_handler.clear_object_handlers()
    registry.clear_services()


# --------------------------------------------------------------------------- #
# /health — liveness, always 200, stage in the body
# --------------------------------------------------------------------------- #


def test_health_is_200_and_operational_once_startup_finished(sbs_app):
    client = TestClient(sbs_app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["stage"] == STAGE_OPERATIONAL
    assert body["checks"]["encoder_warmup"] is True
    assert all(body["checks"].values())


def test_health_stays_200_while_still_initializing(sbs_app):
    """The regression this endpoint shape exists for.

    A platform health check pointed here during a slow boot must see success.
    Before this contract, the only stage-aware endpoint (``/health/ready``)
    answered 500 in exactly this state, and Render read that as a crashed
    service and restarted the instance — repeatedly, because every restart
    re-entered the same window.
    """
    sbs_app.state.encoder_warmup_task = _PendingTask()
    client = TestClient(sbs_app)

    response = client.get("/health")

    assert response.status_code == 200, (
        "/health must never report a non-2xx during a normal boot — a platform "
        "health check reads that as a crash and restarts the instance"
    )
    body = response.json()
    assert body["status"] == "healthy"
    assert body["stage"] == STAGE_INITIALIZING
    assert body["checks"]["encoder_warmup"] is False


def test_health_names_the_gate_that_is_still_open(sbs_app):
    """``checks`` has to identify *which* gate is holding startup open.

    Without it "initializing" is unactionable: an operator watching a boot that
    is taking too long cannot tell a slow encoder download from a store that
    never wired its handlers.
    """
    sbs_app.state.encoder_warmup_task = _PendingTask()
    client = TestClient(sbs_app)

    checks = client.get("/health").json()["checks"]

    open_gates = [name for name, closed in checks.items() if not closed]
    assert open_gates == ["encoder_warmup"]


def test_health_survives_object_handlers_not_being_initialized(sbs_app, monkeypatch):
    """The earliest boot window must not produce a 5xx either.

    ``get_object_handler`` raises ``RuntimeError`` until
    ``initialize_object_handlers()`` has run. Letting that propagate would make
    the liveness probe fail during precisely the window it exists to describe.
    """
    from skillberry_store.modules import object_handler

    def _not_yet(object_type):
        raise RuntimeError("Object handlers not initialized.")

    monkeypatch.setattr(object_handler, "get_object_handler", _not_yet)
    client = TestClient(sbs_app)

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["stage"] == STAGE_INITIALIZING
    # Every object gate is reported open-pending rather than omitted, so the
    # payload still describes what is being waited on.
    assert body["checks"]["skill"] is False


def test_health_reports_uptime(sbs_app):
    client = TestClient(sbs_app)

    first = client.get("/health").json()["uptime_seconds"]
    second = client.get("/health").json()["uptime_seconds"]

    assert isinstance(first, (int, float))
    assert first >= 0
    assert second >= first


def test_health_keeps_the_legacy_status_field(sbs_app):
    """``{"status": "healthy"}`` was the entire old payload.

    Existing consumers — ``scripts/demo/npx_install_e2e.sh``, the CLI-artifacts
    CI workflow, the generated SDK's ``health_check`` — key off that field or
    just the status code. The new fields are additive; this one may not move.
    """
    client = TestClient(sbs_app)

    for warmup in (None, _PendingTask()):
        if warmup is None:
            sbs_app.state.encoder_warmup_task = None
        else:
            sbs_app.state.encoder_warmup_task = warmup
        body = client.get("/health").json()
        assert body["status"] == "healthy"


# --------------------------------------------------------------------------- #
# /health/ready — readiness, 503 while booting
# --------------------------------------------------------------------------- #


def test_ready_is_200_and_reports_the_stage_when_operational(sbs_app):
    client = TestClient(sbs_app)

    response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    assert body["stage"] == STAGE_OPERATIONAL
    assert body["checks"]["encoder_warmup"] is True


def test_ready_is_503_not_500_while_initializing(sbs_app):
    """503 is the honest code: "wait", not "something broke".

    An orchestrator responds to 503 by holding traffic back and polling; many
    respond to 500 by restarting. The status code is the only part of this
    answer some probes look at.
    """
    sbs_app.state.encoder_warmup_task = _PendingTask()
    client = TestClient(sbs_app, raise_server_exceptions=False)

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"


def test_ready_503_payload_carries_stage_and_checks(sbs_app):
    sbs_app.state.encoder_warmup_task = _PendingTask()
    client = TestClient(sbs_app, raise_server_exceptions=False)

    detail = client.get("/health/ready").json()["detail"]

    # ``status: initializing`` and ``checks`` are the pre-existing payload shape
    # and are kept verbatim; ``stage`` and ``uptime_seconds`` are additive.
    assert detail["status"] == STAGE_INITIALIZING
    assert detail["checks"]["encoder_warmup"] is False
    assert detail["stage"] == STAGE_INITIALIZING
    assert "uptime_seconds" in detail


def test_health_and_ready_agree_on_the_stage(sbs_app):
    """One source of truth, two contracts.

    The whole point of splitting them is the *status code*, not the assessment.
    If they could disagree, a caller polling ``/health`` for ``operational``
    could enable semantic search while ``/health/ready`` still refuses traffic.
    """
    client = TestClient(sbs_app, raise_server_exceptions=False)

    for warmup, expected in (
        (None, STAGE_OPERATIONAL),
        (_PendingTask(), STAGE_INITIALIZING),
    ):
        sbs_app.state.encoder_warmup_task = warmup
        health = client.get("/health").json()
        ready = client.get("/health/ready").json()
        ready_body = ready.get("detail", ready)

        assert health["stage"] == expected
        assert ready_body["stage"] == expected
        assert health["checks"] == ready_body["checks"]


# --------------------------------------------------------------------------- #
# Service layer
# --------------------------------------------------------------------------- #


def test_collect_checks_reflects_the_warmup_argument(sbs_app):
    service = AdminService()

    assert service.collect_checks(True)["encoder_warmup"] is True
    assert service.collect_checks(False)["encoder_warmup"] is False


def test_health_report_never_raises_without_object_handlers(monkeypatch):
    """Contract: ``health_report`` is total.

    Called with no object handlers initialised at all — the state a liveness
    probe hits if it arrives early enough — it must still return a payload.
    """
    from skillberry_store.modules import object_handler

    object_handler.clear_object_handlers()
    try:
        report = AdminService().health_report(True)
    finally:
        object_handler.clear_object_handlers()

    assert report["status"] == "healthy"
    assert report["stage"] == STAGE_INITIALIZING
    assert report["checks"]["skill"] is False


def test_health_report_uptime_counts_from_the_injected_start(sbs_app):
    import time

    service = AdminService(started_at=time.monotonic() - 42.0)

    assert service.health_report(True)["uptime_seconds"] >= 42.0
