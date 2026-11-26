"""Human-readable summaries plus CSV / JSON report output."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable, Optional, TextIO

from .linker import LinkOutcome
from .models import Decision

CSV_FIELDS = [
    "file_id",
    "action",
    "confidence",
    "series_id",
    "series_name",
    "episode_ids",
    "range_start",
    "range_end",
    "method",
    "season",
    "episode",
    "episode_end",
    "kind",
    "absolute",
    "version",
    "part",
    "applied",
    "success",
    "reason",
    "path",
]


def decision_to_row(
    decision: Decision, outcome: Optional[LinkOutcome]
) -> dict[str, object]:
    parsed = decision.parsed
    return {
        "file_id": decision.file_id,
        "action": decision.action,
        "confidence": decision.confidence,
        "series_id": decision.series_id or "",
        "series_name": decision.series_name or "",
        "episode_ids": ";".join(str(e) for e in decision.episode_ids),
        "range_start": decision.range_start or "",
        "range_end": decision.range_end or "",
        "method": decision.method,
        "season": parsed.season if parsed and parsed.season is not None else "",
        "episode": parsed.episode if parsed and parsed.episode is not None else "",
        "episode_end": (
            parsed.episode_end if parsed and parsed.episode_end is not None else ""
        ),
        "kind": parsed.kind.value if parsed else "",
        "absolute": bool(parsed.is_absolute) if parsed else False,
        "version": parsed.version if parsed and parsed.version is not None else "",
        "part": parsed.part if parsed and parsed.part is not None else "",
        "applied": outcome.applied if outcome else False,
        "success": outcome.success if outcome else "",
        "reason": outcome.message if outcome else decision.reason,
        "path": decision.path,
    }


def write_report(
    path: str,
    decisions: Iterable[Decision],
    outcomes: dict[int, LinkOutcome],
) -> None:
    rows = [decision_to_row(d, outcomes.get(d.file_id)) for d in decisions]
    target = Path(path)
    if target.suffix.lower() == ".json":
        target.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        return
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def print_report(
    stream: TextIO,
    decisions: list[Decision],
    outcomes: dict[int, LinkOutcome],
    baseline: Optional[int],
    after: Optional[int],
) -> None:
    linkable = sum(1 for d in decisions if d.action == "link")
    review = sum(1 for d in decisions if d.action == "review")
    skipped = sum(1 for d in decisions if d.action == "skip")
    applied = sum(1 for o in outcomes.values() if o.applied and o.success)
    failed = sum(1 for o in outcomes.values() if o.applied and not o.success)

    stream.write("\nShoko filename matcher summary\n")
    stream.write("-" * 60 + "\n")
    for decision in decisions:
        outcome = outcomes.get(decision.file_id)
        eps = ",".join(str(e) for e in decision.episode_ids) or "-"
        status = decision.action
        if outcome and outcome.applied:
            status = "LINKED" if outcome.success else "FAILED"
        stream.write(
            f"[{status:>6}] {decision.confidence:5.1f}  file={decision.file_id:<7} "
            f"series={decision.series_name or '-':<30} eps={eps:<8} "
            f"{decision.method or '-':<18} {decision.reason}\n"
        )
        if decision.path:
            stream.write(f"         {decision.path}\n")
    stream.write("-" * 60 + "\n")
    stream.write(
        f"files={len(decisions)} linkable={linkable} review={review} "
        f"skipped={skipped} applied={applied} failed={failed}\n"
    )
    if baseline is not None:
        stream.write(f"unrecognized before={baseline}")
        if after is not None:
            stream.write(f" after={after} (delta={baseline - after})")
        stream.write("\n")
