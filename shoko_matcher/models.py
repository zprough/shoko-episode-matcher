"""Typed data models and tolerant parsers for Shoko API payloads.

The Shoko v3 API returns anonymous JSON objects. Rather than depend on pydantic
(and to keep the runtime dependency surface to ``httpx`` only) we map the fields
we care about into small dataclasses using defensive ``.get`` lookups.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class EpisodeKind(str, Enum):
    """AniDB episode types as exposed by Shoko (``APIv3.Anidb.EpisodeType``)."""

    EPISODE = "Episode"
    SPECIAL = "Special"
    CREDITS = "Credits"
    TRAILER = "Trailer"
    PARODY = "Parody"
    OTHER = "Other"
    UNKNOWN = "Unknown"

    @classmethod
    def from_value(cls, value: Any) -> "EpisodeKind":
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNKNOWN


@dataclass
class ParsedFilename:
    """Result of parsing a relative path / filename."""

    raw: str
    series_hint: str
    folder: Optional[str] = None
    season: Optional[int] = None
    episode: Optional[int] = None
    episode_end: Optional[int] = None
    kind: EpisodeKind = EpisodeKind.EPISODE
    is_absolute: bool = False
    version: Optional[int] = None
    part: Optional[int] = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_range(self) -> bool:
        return (
            self.episode is not None
            and self.episode_end is not None
            and self.episode_end > self.episode
        )

    def episode_span(self) -> list[int]:
        if self.episode is None:
            return []
        if not self.is_range:
            return [self.episode]
        return list(range(self.episode, (self.episode_end or self.episode) + 1))


@dataclass
class Series:
    id: int
    anidb_id: int
    name: str
    titles: list[str] = field(default_factory=list)
    air_date: Optional[str] = None
    episode_count: Optional[int] = None
    parent_group: Optional[int] = None
    top_level_group: Optional[int] = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Series":
        ids = data.get("IDs") or {}
        anidb = data.get("AniDB") or {}
        name = str(data.get("Name") or anidb.get("Title") or "")
        titles: list[str] = []
        if name:
            titles.append(name)
        for title in anidb.get("Titles") or []:
            if isinstance(title, dict):
                value = title.get("Name") or title.get("Title")
                if value:
                    titles.append(str(value))
        seen: set[str] = set()
        uniq = [t for t in titles if not (t in seen or seen.add(t))]
        return cls(
            id=int(ids.get("ID") or 0),
            anidb_id=int(ids.get("AniDB") or 0),
            name=name,
            titles=uniq,
            air_date=anidb.get("AirDate"),
            episode_count=anidb.get("EpisodeCount"),
            parent_group=ids.get("ParentGroup"),
            top_level_group=ids.get("TopLevelGroup"),
        )


@dataclass
class SeriesSearchHit:
    series_id: int
    anidb_id: int
    name: str
    exact_match: bool = False
    distance: Optional[float] = None
    length_difference: Optional[int] = None
    match: Optional[str] = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "SeriesSearchHit":
        ids = data.get("IDs") or {}
        return cls(
            series_id=int(ids.get("ID") or 0),
            anidb_id=int(ids.get("AniDB") or 0),
            name=str(data.get("Name") or ""),
            exact_match=bool(data.get("ExactMatch")),
            distance=data.get("Distance"),
            length_difference=data.get("LengthDifference"),
            match=data.get("Match"),
        )


@dataclass
class Episode:
    id: int
    anidb_id: int
    number: int
    kind: EpisodeKind
    title: str = ""
    air_date: Optional[str] = None
    file_ids: list[int] = field(default_factory=list)

    @property
    def linked(self) -> bool:
        return bool(self.file_ids)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Episode":
        ids = data.get("IDs") or {}
        anidb = data.get("AniDB") or {}
        file_ids = [
            int(f.get("ID"))
            for f in (data.get("Files") or [])
            if isinstance(f, dict) and f.get("ID") is not None
        ]
        return cls(
            id=int(ids.get("ID") or 0),
            anidb_id=int(ids.get("AniDB") or 0),
            number=int(anidb.get("EpisodeNumber") or 0),
            kind=EpisodeKind.from_value(anidb.get("Type")),
            title=str(data.get("Name") or anidb.get("Title") or ""),
            air_date=anidb.get("AirDate"),
            file_ids=file_ids,
        )


@dataclass
class FileLocation:
    import_folder_id: int
    relative_path: str
    absolute_path: Optional[str] = None
    is_accessible: bool = True

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "FileLocation":
        return cls(
            import_folder_id=int(data.get("ImportFolderID") or 0),
            relative_path=str(data.get("RelativePath") or ""),
            absolute_path=data.get("AbsolutePath"),
            is_accessible=bool(data.get("IsAccessible", True)),
        )

    @property
    def display_path(self) -> str:
        return self.absolute_path or self.relative_path


@dataclass
class UnrecognizedFile:
    id: int
    size: int = 0
    locations: list[FileLocation] = field(default_factory=list)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "UnrecognizedFile":
        return cls(
            id=int(data.get("ID") or 0),
            size=int(data.get("Size") or 0),
            locations=[
                FileLocation.from_api(loc)
                for loc in (data.get("Locations") or [])
                if isinstance(loc, dict)
            ],
        )


@dataclass
class Group:
    id: int
    name: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Group":
        ids = data.get("IDs") or {}
        return cls(id=int(ids.get("ID") or 0), name=str(data.get("Name") or ""))


@dataclass
class Decision:
    """The outcome for a single file: what to link, or why we didn't."""

    file_id: int
    path: str
    parsed: Optional[ParsedFilename]
    series_id: Optional[int] = None
    series_name: Optional[str] = None
    episode_ids: list[int] = field(default_factory=list)
    range_start: Optional[str] = None
    range_end: Optional[str] = None
    method: str = ""
    confidence: float = 0.0
    action: str = "skip"  # link | review | skip
    reason: str = ""

    @property
    def is_link(self) -> bool:
        return self.action == "link"
