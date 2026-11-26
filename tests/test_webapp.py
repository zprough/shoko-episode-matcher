import time

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from shoko_matcher import webapp  # noqa: E402
from shoko_matcher.config import Config, ConfigError  # noqa: E402
from shoko_matcher.linker import LinkOutcome  # noqa: E402
from shoko_matcher.models import (  # noqa: E402
    Decision,
    EpisodeKind,
    ParsedFilename,
)
from shoko_matcher.runner import RunSummary  # noqa: E402


class FakeClient:
    def __init__(self, config):
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def fake_runner(config, client, on_decision=None):
    parsed = ParsedFilename(
        raw="Solo/Solo - 05.mkv",
        series_hint="Solo",
        folder="Solo",
        episode=5,
        kind=EpisodeKind.EPISODE,
    )
    decision = Decision(
        file_id=7,
        path="/lib/Solo/Solo - 05.mkv",
        parsed=parsed,
        series_id=1,
        series_name="Solo",
        episode_ids=[105],
        method="path",
        confidence=100.0,
        action="link",
        reason="matched",
    )
    outcome = LinkOutcome(7, "link", False, True, "dry-run: would link")
    if on_decision is not None:
        on_decision(decision, outcome)
    return RunSummary(
        decisions=[decision],
        outcomes={7: outcome},
        baseline=5,
        after=4,
    )


@pytest.fixture(autouse=True)
def reset_manager(monkeypatch):
    webapp.manager = webapp.JobManager()
    monkeypatch.setattr(
        webapp, "load_config", lambda _ns=None: Config(apikey="x", dry_run=True)
    )
    monkeypatch.setattr(webapp, "ShokoClient", FakeClient)


def test_index_and_health():
    client = TestClient(webapp.app)
    page = client.get("/")
    assert page.status_code == 200
    assert "Shoko Filename Matcher" in page.text
    assert client.get("/api/health").json()["status"] == "ok"


def test_run_job_and_download_report(monkeypatch):
    monkeypatch.setattr(webapp, "run_matching", fake_runner)
    client = TestClient(webapp.app)
    response = client.post(
        "/api/jobs", json={"apply": False, "min_confidence": 80}
    )
    assert response.status_code == 202
    job_id = response.json()["id"]

    job = {}
    deadline = time.time() + 3
    while time.time() < deadline:
        job = client.get(f"/api/jobs/{job_id}").json()
        if job.get("status") in {"done", "error"}:
            break
        time.sleep(0.02)
    assert job.get("status") == "done", job
    assert job["counts"]["link"] == 1
    assert job["baseline"] == 5
    assert job["after"] == 4

    csv_response = client.get(f"/api/jobs/{job_id}/report.csv")
    assert csv_response.status_code == 200
    assert "file_id" in csv_response.text
    assert "action" in csv_response.text

    json_response = client.get(f"/api/jobs/{job_id}/report.json")
    assert json_response.status_code == 200
    assert json_response.json()["rows"][0]["file_id"] == 7


def test_build_config_applies_overrides():
    config = webapp._build_config(
        {
            "apply": True,
            "min_confidence": 150,
            "limit": 10,
            "series": "monogatari",
            "episode_id_type": "anidb",
            "link_method": "range",
        }
    )
    assert config.dry_run is False
    assert config.min_confidence == 100.0
    assert config.limit == 10
    assert config.series_filter == "monogatari"
    assert config.episode_id_type == "anidb"
    assert config.link_method == "range"


def test_build_config_rejects_invalid_choices():
    with pytest.raises(ConfigError):
        webapp._build_config({"episode_id_type": "bogus"})
