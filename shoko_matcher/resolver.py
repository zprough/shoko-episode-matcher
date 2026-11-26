"""Series/episode resolution and confidence scoring.

The resolver is deliberately dependency-light and accepts any object exposing the
subset of :class:`~shoko_matcher.client.ShokoClient` methods it uses. That makes
the season/absolute-number logic unit-testable with a fake client.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Optional, Protocol

from .config import Config
from .models import (
    Decision,
    Episode,
    EpisodeKind,
    Group,
    ParsedFilename,
    Series,
    SeriesSearchHit,
    UnrecognizedFile,
)
from .parser import parse_filename


class SeriesApi(Protocol):  # pragma: no cover - structural typing only
    def series_by_path(self, path: str) -> list[Series]: ...
    def search_series(
        self, query: str, fuzzy: bool = True, limit: int = 10
    ) -> list[SeriesSearchHit]: ...
    def series(self, series_id: int) -> Optional[Series]: ...
    def series_group(self, series_id: int) -> Optional[Group]: ...
    def group_series(self, group_id: int, recursive: bool = True) -> list[Series]: ...
    def series_episodes(self, series_id: int) -> list[Episode]: ...


def _normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(a: str, b: str) -> float:
    """Return a 0..1 similarity ratio between two titles."""
    na, nb = _normalize(a), _normalize(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def best_title_similarity(series: Series, hint: str) -> float:
    candidates = series.titles or [series.name]
    return max((similarity(title, hint) for title in candidates if title), default=0.0)


@dataclass
class _Candidate:
    series: Series
    score: float
    source: str
    unique_path: bool = False


def _air_date_key(series: Series) -> tuple[str, int]:
    return (series.air_date or "9999-99-99", series.id)


class Resolver:
    def __init__(self, api: SeriesApi, config: Config) -> None:
        self.api = api
        self.config = config
        self._episodes: dict[int, list[Episode]] = {}
        self._siblings: dict[int, list[Series]] = {}
        self._series: dict[int, Optional[Series]] = {}

    # -- caches ------------------------------------------------------------
    def get_series(self, series_id: int, fallback: Optional[Series] = None) -> Series:
        if series_id not in self._series:
            self._series[series_id] = self.api.series(series_id)
        return self._series[series_id] or fallback or Series(series_id, 0, "")

    def get_episodes(self, series_id: int) -> list[Episode]:
        if series_id not in self._episodes:
            self._episodes[series_id] = self.api.series_episodes(series_id)
        return self._episodes[series_id]

    def get_siblings(self, series_id: int, base: Series) -> list[Series]:
        group = self.api.series_group(series_id)
        group_id = group.id if group and group.id else (
            base.parent_group or base.top_level_group or 0
        )
        if not group_id:
            return [base]
        if group_id not in self._siblings:
            siblings = self.api.group_series(group_id, recursive=True)
            siblings = [s for s in siblings if s.id]
            self._siblings[group_id] = sorted(siblings, key=_air_date_key)
        siblings = self._siblings[group_id] or [base]
        if all(s.id != series_id for s in siblings):
            siblings = sorted(siblings + [base], key=_air_date_key)
        return siblings

    # -- episode helpers ---------------------------------------------------
    @staticmethod
    def find_episode(
        episodes: list[Episode], number: int, kind: EpisodeKind
    ) -> Optional[Episode]:
        for episode in episodes:
            if episode.number == number and episode.kind == kind:
                return episode
        return None

    @staticmethod
    def episode_type_count(series: Series, episodes: list[Episode]) -> Optional[int]:
        if series.episode_count:
            return int(series.episode_count)
        count = sum(1 for e in episodes if e.kind == EpisodeKind.EPISODE)
        return count or None

    # -- series resolution -------------------------------------------------
    def _path_candidates(self, hint: str, folder: Optional[str]) -> list[_Candidate]:
        if not folder:
            return []
        try:
            series_list = self.api.series_by_path(folder)
        except Exception:  # noqa: BLE001 - path lookups are best-effort
            return []
        if not series_list:
            return []
        unique = len(series_list) == 1
        candidates: list[_Candidate] = []
        for series in series_list:
            sim = best_title_similarity(series, hint)
            candidates.append(
                _Candidate(series, min(55.0 + 15.0 * sim, 70.0), "path", unique)
            )
        return candidates

    def _fuzzy_candidates(self, hint: str) -> list[_Candidate]:
        if not hint:
            return []
        try:
            hits = self.api.search_series(hint, fuzzy=True, limit=10)
        except Exception:  # noqa: BLE001
            return []
        candidates: list[_Candidate] = []
        for hit in hits:
            if not hit.series_id:
                continue
            sim = similarity(hit.name, hint)
            if hit.exact_match:
                score = 42.0
            else:
                distance = hit.distance if hit.distance is not None else 0.5
                score = 24.0 + 22.0 * (1.0 - min(max(distance, 0.0), 1.0))
                if hit.length_difference is not None:
                    score -= min(hit.length_difference, 20) * 0.5
                score += 4.0 * sim
            series = Series(
                hit.series_id, hit.anidb_id, hit.name,
                [hit.name] if hit.name else [],
            )
            candidates.append(_Candidate(series, min(score, 55.0), "fuzzy"))
        return candidates

    def resolve_series(
        self, hint: str, folder: Optional[str]
    ) -> tuple[Optional[_Candidate], bool]:
        path_candidates = self._path_candidates(hint, folder)
        candidates = list(path_candidates)
        trust_path = (
            len(path_candidates) == 1
            and best_title_similarity(path_candidates[0].series, hint) >= 0.55
        )
        if not trust_path:
            candidates.extend(self._fuzzy_candidates(hint))

        best_by_id: dict[int, _Candidate] = {}
        for candidate in candidates:
            if not candidate.series.id:
                continue
            previous = best_by_id.get(candidate.series.id)
            if previous is None or candidate.score > previous.score:
                best_by_id[candidate.series.id] = candidate
        ranked = sorted(best_by_id.values(), key=lambda c: c.score, reverse=True)
        if not ranked:
            return None, False
        best = ranked[0]
        ambiguous = False
        if len(ranked) > 1 and not best.unique_path:
            ambiguous = (best.score - ranked[1].score) < 12.0
        return best, ambiguous

    def _map_season(
        self, season: int, base: Series, siblings: list[Series]
    ) -> Optional[Series]:
        usable = [
            s for s in siblings
            if s.episode_count is None or (s.episode_count or 0) > 0
        ] or siblings
        ordered = sorted(usable, key=_air_date_key)
        if 1 <= season <= len(ordered):
            return ordered[season - 1]
        return None

    def _map_absolute(
        self, number: int, base: Series, siblings: list[Series]
    ) -> tuple[Optional[Series], int, bool]:
        ordered = sorted(siblings, key=_air_date_key)
        offset = 0
        for series in ordered:
            episodes = self.get_episodes(series.id)
            count = self.episode_type_count(series, episodes) or 0
            if count <= 0:
                continue
            if number <= offset + count:
                return series, number - offset, series.id != base.id
            offset += count
        return None, 0, False

    # -- target episode selection -----------------------------------------
    def _target_episodes(
        self, parsed: ParsedFilename, base: Series
    ) -> tuple[Series, list[int], EpisodeKind, str]:
        kind = parsed.kind
        number = parsed.episode or 0
        if kind is not EpisodeKind.EPISODE:
            return base, parsed.episode_span(), kind, "base"
        if parsed.season is not None and parsed.season >= 2:
            siblings = self.get_siblings(base.id, base)
            chosen = self._map_season(parsed.season, base, siblings)
            if chosen is None:
                return base, [], EpisodeKind.EPISODE, "season-unmapped"
            mapped = chosen.id != base.id
            return chosen, parsed.episode_span(), EpisodeKind.EPISODE, (
                "season-map" if mapped else "base"
            )
        siblings = self.get_siblings(base.id, base)
        base_count = self.episode_type_count(base, self.get_episodes(base.id)) or 0
        exceeds_base = bool(base_count) and number > base_count
        if parsed.is_absolute or (
            parsed.season in (None, 1) and exceeds_base
        ):
            chosen, local, mapped = self._map_absolute(number, base, siblings)
            if chosen is not None:
                span = [local]
                if parsed.is_range and parsed.episode_end:
                    span = list(
                        range(local, local + (parsed.episode_end - number) + 1)
                    )
                return chosen, span, EpisodeKind.EPISODE, (
                    "absolute-map" if mapped else "base"
                )
        return base, parsed.episode_span(), EpisodeKind.EPISODE, "base"

    def _id_for(self, episode: Episode) -> int:
        if self.config.episode_id_type == "anidb":
            return episode.anidb_id
        return episode.id

    _RANGE_PREFIX = {
        EpisodeKind.EPISODE: "",
        EpisodeKind.SPECIAL: "S",
        EpisodeKind.CREDITS: "C",
        EpisodeKind.TRAILER: "T",
        EpisodeKind.PARODY: "P",
        EpisodeKind.OTHER: "O",
    }

    def range_string(self, kind: EpisodeKind, number: int) -> str:
        return f"{self._RANGE_PREFIX.get(kind, '')}{number}"

    # -- public entry point ------------------------------------------------
    def resolve(self, file: UnrecognizedFile) -> Decision:
        location = next(
            (loc for loc in file.locations if loc.relative_path), None
        )
        relative = location.relative_path if location else ""
        path = location.display_path if location else relative
        parsed = parse_filename(relative)
        decision = Decision(file_id=file.id, path=path, parsed=parsed)

        if parsed.episode is None:
            decision.reason = "could not parse an episode number"
            return decision

        best, ambiguous = self.resolve_series(parsed.series_hint, parsed.folder)
        if best is None:
            decision.action = "review"
            decision.reason = "no series match in local library"
            return decision

        base = self.get_series(best.series.id, best.series)
        decision.series_id = base.id
        decision.series_name = base.name

        if self.config.series_filter and (
            self.config.series_filter.lower() not in base.name.lower()
        ):
            decision.action = "skip"
            decision.reason = "does not match --series filter"
            return decision

        target, numbers, kind, mapping = self._target_episodes(parsed, base)
        method = best.source if mapping == "base" else f"{best.source}/{mapping}"
        decision.method = method

        if not numbers:
            decision.action = "review"
            decision.reason = "could not map season to a series"
            return decision

        resolved: list[Episode] = []
        target_episodes = self.get_episodes(target.id)
        for number in numbers:
            episode = self.find_episode(target_episodes, number, kind)
            if episode is None:
                decision.action = "review"
                decision.reason = (
                    f"episode {number} ({kind.value}) not found in series "
                    f"{target.id}"
                )
                return decision
            resolved.append(episode)

        decision.episode_ids = [self._id_for(ep) for ep in resolved]
        decision.range_start = self.range_string(kind, numbers[0])
        decision.range_end = self.range_string(kind, numbers[-1])

        if any(ep.linked for ep in resolved):
            decision.action = "review"
            decision.reason = "target episode already linked to another file"
            return decision

        confidence = best.score + 20.0 + 10.0
        if mapping != "base":
            confidence += 5.0
        if ambiguous:
            confidence -= 25.0
        decision.confidence = round(max(0.0, min(100.0, confidence)), 1)

        if ambiguous:
            decision.action = "review"
            decision.reason = "multiple plausible series candidates"
        elif decision.confidence < self.config.min_confidence:
            decision.action = "review"
            decision.reason = (
                f"confidence {decision.confidence} below threshold "
                f"{self.config.min_confidence}"
            )
        elif len(resolved) > 1 and not parsed.is_range:
            decision.action = "review"
            decision.reason = "file maps to multiple episodes"
        else:
            decision.action = "link"
            decision.reason = "matched range" if len(resolved) > 1 else "matched"
        return decision
