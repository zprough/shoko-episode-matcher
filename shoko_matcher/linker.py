"""Applies link decisions to Shoko (only ever creating links)."""

from __future__ import annotations

from dataclasses import dataclass

from .client import ShokoClient
from .config import Config
from .models import Decision


@dataclass
class LinkOutcome:
    file_id: int
    action: str
    applied: bool
    success: bool
    message: str


class Linker:
    def __init__(self, client: ShokoClient, config: Config) -> None:
        self.client = client
        self.config = config

    def apply(self, decision: Decision) -> LinkOutcome:
        if decision.action != "link":
            return LinkOutcome(
                decision.file_id, decision.action, False, True, decision.reason
            )
        if not decision.episode_ids:
            return LinkOutcome(
                decision.file_id, decision.action, False, False,
                "no episode ids to link",
            )
        if self.config.dry_run:
            return LinkOutcome(
                decision.file_id, decision.action, False, True,
                "dry-run: would link",
            )
        try:
            if self.config.link_method == "range" and decision.series_id:
                self.client.link_from_series(
                    decision.file_id,
                    decision.series_id,
                    decision.range_start or "1",
                    decision.range_end or "1",
                )
            else:
                self.client.link_episodes(
                    decision.file_id, decision.episode_ids
                )
        except Exception as exc:  # noqa: BLE001 - surface any API failure
            return LinkOutcome(
                decision.file_id, decision.action, True, False, f"link failed: {exc}"
            )
        return LinkOutcome(
            decision.file_id, decision.action, True, True, "linked"
        )
