"""FastAPI web UI for running the matcher from a browser.

The web layer is a thin shell around :func:`shoko_matcher.runner.run_matching`.
Only one job runs at a time to avoid two writers racing against Shoko. Jobs are
kept in memory (the most recent 20) and expose their rows as CSV/JSON.
"""

from __future__ import annotations

import csv
import io
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from types import SimpleNamespace
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import __version__
from .client import ShokoClient
from .config import Config, ConfigError, load_config
from .linker import LinkOutcome
from .models import Decision
from .reporter import CSV_FIELDS, decision_to_row
from .runner import run_matching
from .webui import INDEX_HTML


class JobBusy(Exception):
    """Raised when a job is already running."""


@dataclass
class Job:
    id: str
    created: float
    request: dict[str, Any] = field(default_factory=dict)
    status: str = "queued"  # queued | running | done | error
    started: Optional[float] = None
    finished: Optional[float] = None
    baseline: Optional[int] = None
    after: Optional[int] = None
    mylist_warning: bool = False
    error: Optional[str] = None
    processed: int = 0
    notes: list[str] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        counts = {"link": 0, "review": 0, "skip": 0, "applied": 0, "failed": 0}
        for row in list(self.rows):
            action = row.get("action")
            if action in ("link", "review", "skip"):
                counts[action] += 1
            if row.get("applied"):
                counts["applied"] += 1
                if row.get("success") is False:
                    counts["failed"] += 1
        return counts

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "created": self.created,
            "started": self.started,
            "finished": self.finished,
            "request": self.request,
            "baseline": self.baseline,
            "after": self.after,
            "mylist_warning": self.mylist_warning,
            "error": self.error,
            "processed": self.processed,
            "notes": list(self.notes),
            "counts": self.counts(),
            "rows": list(self.rows),
        }


def _build_config(request: dict[str, Any]) -> Config:
    base = load_config(SimpleNamespace())
    overrides: dict[str, Any] = {"dry_run": not bool(request.get("apply"))}
    min_confidence = request.get("min_confidence")
    if min_confidence is not None:
        overrides["min_confidence"] = max(0.0, min(100.0, float(min_confidence)))
    limit = request.get("limit")
    if limit is not None:
        overrides["limit"] = max(0, int(limit))
    series = request.get("series")
    if series:
        overrides["series_filter"] = str(series)
    import_folder = request.get("import_folder")
    if import_folder is not None:
        overrides["import_folder"] = int(import_folder)
    episode_id_type = request.get("episode_id_type")
    if episode_id_type:
        if episode_id_type not in {"shoko", "anidb"}:
            raise ConfigError("episode_id_type must be shoko or anidb")
        overrides["episode_id_type"] = episode_id_type
    link_method = request.get("link_method")
    if link_method:
        if link_method not in {"episode", "range"}:
            raise ConfigError("link_method must be episode or range")
        overrides["link_method"] = link_method
    if request.get("create_missing_series"):
        overrides["create_missing_series"] = True
    if request.get("absolute_numbering"):
        overrides["absolute_numbering"] = True
    return replace(base, **overrides)


class JobManager:
    def __init__(self, max_jobs: int = 20) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._active: Optional[str] = None
        self.max_jobs = max_jobs

    def create(self, request: dict[str, Any]) -> Job:
        with self._lock:
            if self._active:
                active = self._jobs.get(self._active)
                if active and active.status in {"queued", "running"}:
                    raise JobBusy("a job is already running")
            job = Job(id=uuid.uuid4().hex[:12], created=time.time(), request=request)
            self._jobs[job.id] = job
            self._order.append(job.id)
            self._active = job.id
        threading.Thread(target=self._execute, args=(job,), daemon=True).start()
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def recent(self) -> list[dict[str, Any]]:
        with self._lock:
            ids = self._order[-self.max_jobs:]
        return [self._jobs[i].to_dict() for i in reversed(ids) if i in self._jobs]

    def _execute(self, job: Job) -> None:
        job.status = "running"
        job.started = time.time()
        try:
            config = _build_config(job.request)
            with ShokoClient(config) as client:
                def on_decision(decision: Decision, outcome: LinkOutcome) -> None:
                    job.rows.append(decision_to_row(decision, outcome))
                    job.processed = len(job.rows)

                summary = run_matching(config, client, on_decision=on_decision)
            job.baseline = summary.baseline
            job.after = summary.after
            job.mylist_warning = summary.mylist_warning
            job.notes = list(summary.notes)
            job.status = "done"
        except ConfigError as exc:
            job.status = "error"
            job.error = f"configuration: {exc}"
        except Exception as exc:  # noqa: BLE001 - surface to the UI
            job.status = "error"
            job.error = str(exc)
        finally:
            job.finished = time.time()
            with self._lock:
                if self._active == job.id:
                    self._active = None
            self._trim()

    def _trim(self) -> None:
        with self._lock:
            while len(self._order) > self.max_jobs:
                oldest = self._order[0]
                if oldest == self._active:
                    break
                self._order.pop(0)
                self._jobs.pop(oldest, None)


app = FastAPI(title="Shoko Filename Matcher", version=__version__)
manager = JobManager()


class RunRequest(BaseModel):
    apply: bool = False
    min_confidence: Optional[float] = None
    limit: Optional[int] = None
    series: Optional[str] = None
    import_folder: Optional[int] = None
    episode_id_type: Optional[str] = None
    link_method: Optional[str] = None
    create_missing_series: bool = False
    absolute_numbering: bool = False


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return INDEX_HTML


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@app.get("/api/config")
def config_info() -> dict[str, Any]:
    base_url = os.environ.get("SHOKO_URL", "")
    try:
        config = load_config(SimpleNamespace())
    except ConfigError as exc:
        return {
            "configured": False,
            "base_url": base_url,
            "min_confidence": 80.0,
            "error": str(exc),
        }
    return {
        "configured": bool(config.apikey),
        "base_url": config.base_url,
        "min_confidence": config.min_confidence,
        "dry_run": config.dry_run,
    }


@app.post("/api/jobs", status_code=202)
def create_job(request: RunRequest) -> dict[str, Any]:
    data = (
        request.model_dump()
        if hasattr(request, "model_dump")
        else request.dict()
    )
    try:
        job = manager.create(data)
    except JobBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"id": job.id, "status": job.status}


@app.get("/api/jobs")
def list_jobs() -> list[dict[str, Any]]:
    return manager.recent()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, Any]:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return job.to_dict()


@app.get("/api/jobs/{job_id}/report.csv")
def job_report_csv(job_id: str) -> Response:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(job.rows)
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="shoko-matcher-{job_id}.csv"'
        },
    )


@app.get("/api/jobs/{job_id}/report.json")
def job_report_json(job_id: str) -> JSONResponse:
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="unknown job")
    return JSONResponse({"job": job.to_dict(), "rows": job.rows})


def run() -> None:
    import uvicorn

    host = os.environ.get("MATCHER_HOST", "0.0.0.0")
    port = int(os.environ.get("MATCHER_PORT", "8080"))
    level = os.environ.get("MATCHER_LOG_LEVEL", "info").lower()
    uvicorn.run(
        "shoko_matcher.webapp:app", host=host, port=port, log_level=level
    )
