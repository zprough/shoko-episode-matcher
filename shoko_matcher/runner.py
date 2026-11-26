"""Reusable matching run loop shared by the CLI and the web UI."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Optional

from .client import ShokoAuthError, ShokoClient, ShokoError
from .config import Config
from .linker import Linker, LinkOutcome
from .models import Decision
from .resolver import Resolver, similarity

LOG = logging.getLogger("shoko_matcher.runner")

DecisionCallback = Callable[[Decision, LinkOutcome], None]


@dataclass
class RunSummary:
    decisions: list[Decision] = field(default_factory=list)
    outcomes: dict[int, LinkOutcome] = field(default_factory=dict)
    baseline: Optional[int] = None
    after: Optional[int] = None
    mylist_warning: bool = False
    notes: list[str] = field(default_factory=list)


def _try_create_series(
    client: ShokoClient, config: Config, decision: Decision
) -> None:
    parsed = decision.parsed
    if not parsed or not parsed.series_hint:
        return
    try:
        ban = client.anidb_ban_status()
    except ShokoError:
        ban = {}
    if any(
        isinstance(value, dict) and value.get("Banned")
        for value in ban.values()
    ):
        decision.reason = "series not found; AniDB is banned, skipped creation"
        return
    try:
        hits = client.anidb_search(parsed.series_hint, local=False)
    except ShokoError as exc:
        decision.reason = f"series not found; AniDB search failed: {exc}"
        return
    best: Optional[dict] = None
    best_score = 0.0
    for hit in hits:
        title = str(hit.get("Title") or "")
        score = similarity(title, parsed.series_hint)
        if score > best_score:
            best, best_score = hit, score
    if best and best_score >= 0.6 and best.get("ID"):
        anidb_id = int(best["ID"])
        try:
            client.refresh_anidb(anidb_id, create=True)
            decision.reason = (
                f"queued AniDB series creation for {anidb_id} "
                f"('{best.get('Title')}'); re-run after it is imported"
            )
        except ShokoError as exc:
            decision.reason = f"failed to queue AniDB refresh: {exc}"
    else:
        decision.reason = "series not found in library or AniDB title dump"


def run_matching(
    config: Config,
    client: ShokoClient,
    on_decision: Optional[DecisionCallback] = None,
) -> RunSummary:
    summary = RunSummary()
    try:
        summary.baseline = int(client.stats().get("UnrecognizedFiles"))
    except (ShokoError, TypeError, ValueError):
        summary.baseline = None

    if not config.dry_run:
        try:
            if client.mylist_add_files():
                summary.mylist_warning = True
                message = (
                    "AniDB MyList_AddFiles is enabled. Manual links may add "
                    "files to your AniDB MyList."
                )
                summary.notes.append(message)
                LOG.warning(message)
        except ShokoError:
            pass

    resolver = Resolver(client, config)
    linker = Linker(client, config)

    processed = 0
    for unrecognized in client.unrecognized_files(
        import_folder_id=config.import_folder
    ):
        if config.limit is not None and processed >= config.limit:
            break
        path = (
            unrecognized.locations[0].display_path
            if unrecognized.locations
            else ""
        )
        try:
            decision = resolver.resolve(unrecognized)
            if decision.filtered:
                # Excluded by the series (or another user) filter: don't count
                # it toward --limit, link it, or include it in any output.
                continue
            if (
                config.create_missing_series
                and decision.series_id is None
                and decision.parsed is not None
                and decision.parsed.episode is not None
            ):
                _try_create_series(client, config, decision)
        except ShokoAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep one bad file isolated
            decision = Decision(
                file_id=unrecognized.id,
                path=path,
                parsed=None,
                action="skip",
                reason=f"error: {exc}",
            )
        processed += 1
        outcome = linker.apply(decision)
        summary.decisions.append(decision)
        summary.outcomes[decision.file_id] = outcome
        if on_decision is not None:
            on_decision(decision, outcome)
        LOG.debug(
            "file=%s action=%s confidence=%s eps=%s reason=%s",
            decision.file_id, decision.action, decision.confidence,
            decision.episode_ids, outcome.message,
        )

    try:
        summary.after = int(client.stats().get("UnrecognizedFiles"))
    except (ShokoError, TypeError, ValueError):
        summary.after = None
    return summary
