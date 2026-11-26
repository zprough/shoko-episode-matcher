"""Best-effort filename / path parser.

Extracts a series title hint plus season, episode (and range), absolute number,
special/credit markers, version and part number from release-style names such as:

* ``[SubsPlease] Jujutsu Kaisen - 24 (1080p) [ABCD1234].mkv``
* ``Show Name - S02E05 - Some Title [1080p].mkv``
* ``Show 2x05.mkv`` / ``Show - 05v2.mkv`` / ``Show - E01-E02.mkv``
* ``Show - SP01.mkv`` / ``[Group] Show - NCOP1.mkv``

The parser never guesses a series id; it only produces a *hint* that the
resolver validates against the Shoko API.
"""

from __future__ import annotations

import re
from typing import Optional

from .models import EpisodeKind, ParsedFilename

_RESOLUTION = re.compile(
    r"\b(?:2160|1440|1080|720|576|540|480|360|240)[pi]?\b", re.I
)
_CODEC = re.compile(
    r"\b(?:x26[45]|h\.?26[45]|hevc|avc|xvid|av1|10bit|8bit|hi10p?|flac|aac|"
    r"ac3|dts|dual[\s._-]?audio)\b",
    re.I,
)
_SEASON_EP = re.compile(
    r"\bS(?P<season>\d{1,2})\s*[._\- ]?\s*E(?P<ep>\d{1,4})"
    r"(?:\s*[-~]\s*E?(?P<end>\d{1,4}))?",
    re.I,
)
_SEASON_EP_LONG = re.compile(
    r"\bSeason\s*(?P<season>\d{1,2})\s*[._\- ]*\s*Episode\s*(?P<ep>\d{1,4})",
    re.I,
)
_NXNN = re.compile(r"\b(?P<season>\d{1,2})x(?P<ep>\d{1,4})\b", re.I)
_EP_ONLY = re.compile(
    r"(?<![A-Za-z])E(?:P|pisode)?\s*(?P<ep>\d{1,4})"
    r"(?:\s*[-~]\s*E?(?P<end>\d{1,4}))?",
    re.I,
)
_DASH_NUM = re.compile(
    r"[-–—]\s*(?P<ep>\d{1,4})(?:\s*[-~]\s*(?P<end>\d{1,4}))?"
    r"(?=\s*(?:\[|\(|$|v\d))",
    re.I,
)
_CREDITS = re.compile(r"\b(?:NCOP|NCED)(?:[\s._-]*(\d{1,3}))?\b", re.I)
_CREDITS_SHORT = re.compile(r"\b(?:OP|ED)(\d{1,3})\b", re.I)
_SPECIAL = re.compile(
    r"\b(?:OVA|OAD|SPECIALS?|EXTRAS?|SP)(?![A-Za-z])[\s._-]*(\d{1,3})?",
    re.I,
)
_VERSION = re.compile(r"(?<![A-Za-z])v(\d{1,2})\b", re.I)
_PART = re.compile(r"\b(?:part|pt|cd|disc)[\s._-]*(\d{1,2})\b", re.I)
_BRACKETS = re.compile(r"\[[^\]]*\]")
_SEASON_FOLDER = re.compile(
    r"^(?:season\s*\d+|s\d+|specials?|movies?|ova|oad|extras?|complete|tv)$",
    re.I,
)


def _clean_title(text: str) -> str:
    text = _BRACKETS.sub(" ", text)
    if text.count(".") >= 2:
        text = text.replace(".", " ")
    text = text.replace("_", " ")
    text = _RESOLUTION.sub(" ", text)
    text = _CODEC.sub(" ", text)
    text = re.sub(r"\(\s*\)", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" .-_–—[](){}")


def _pick_folder_title(folder: Optional[str]) -> str:
    if not folder:
        return ""
    parts = [p for p in folder.replace("\\", "/").split("/") if p.strip()]
    for part in reversed(parts):
        cleaned = _clean_title(part)
        if cleaned and not _SEASON_FOLDER.match(cleaned):
            return cleaned
    return ""


def _bare_numbers(text: str) -> list[int]:
    text = _BRACKETS.sub(" ", text)
    text = _RESOLUTION.sub(" ", text)
    text = _CODEC.sub(" ", text)
    text = _VERSION.sub(" ", text)
    found: list[int] = []
    for match in re.finditer(r"(?<!\d)(\d{1,4})(?!\d)", text):
        value = int(match.group(1))
        if 1900 <= value <= 2099:  # likely a year
            continue
        found.append(value)
    return found


def parse_filename(
    relative_path: str, folder: Optional[str] = None
) -> ParsedFilename:
    """Parse a file's relative path (or filename) into a :class:`ParsedFilename`."""
    raw = relative_path
    norm = relative_path.replace("\\", "/")
    parts = [p for p in norm.split("/") if p]
    filename = parts[-1] if parts else norm
    if folder is None:
        folder = "/".join(parts[:-1])

    stem = filename
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]

    notes: list[str] = []
    cut = len(stem)

    version: Optional[int] = None
    match = _VERSION.search(stem)
    if match:
        version = int(match.group(1))
        cut = min(cut, match.start())

    part: Optional[int] = None
    match = _PART.search(stem)
    if match:
        part = int(match.group(1))
        cut = min(cut, match.start())

    kind = EpisodeKind.EPISODE
    credits = _CREDITS.search(stem) or _CREDITS_SHORT.search(stem)
    if credits:
        kind = EpisodeKind.CREDITS
        cut = min(cut, credits.start())

    season: Optional[int] = None
    episode: Optional[int] = None
    episode_end: Optional[int] = None
    is_absolute = False
    matched = False

    if kind is not EpisodeKind.CREDITS:
        special = _SPECIAL.search(stem)
        if special:
            kind = EpisodeKind.SPECIAL
            cut = min(cut, special.start())
            if special.group(1):
                episode = int(special.group(1))
                matched = True

    if not matched and kind is not EpisodeKind.CREDITS:
        for pattern in (_SEASON_EP, _SEASON_EP_LONG, _NXNN):
            found = pattern.search(stem)
            if not found:
                continue
            ep_val = int(found.group("ep"))
            if pattern is _NXNN and ep_val in {
                240, 360, 480, 540, 576, 720, 1080, 1440, 2160,
            }:
                continue
            season = int(found.group("season"))
            episode = ep_val
            end_raw = found.groupdict().get("end")
            episode_end = int(end_raw) if end_raw else None
            cut = min(cut, found.start())
            matched = True
            break

    if not matched and kind is EpisodeKind.EPISODE:
        found = _EP_ONLY.search(stem)
        if found:
            episode = int(found.group("ep"))
            end_raw = found.group("end")
            episode_end = int(end_raw) if end_raw else None
            cut = min(cut, found.start())
            matched = True

    if not matched and kind is EpisodeKind.EPISODE:
        found = _DASH_NUM.search(stem)
        if found:
            episode = int(found.group("ep"))
            end_raw = found.group("end")
            episode_end = int(end_raw) if end_raw else None
            is_absolute = True
            cut = min(cut, found.start())
            matched = True

    if not matched and kind is EpisodeKind.EPISODE:
        numbers = _bare_numbers(stem)
        if numbers:
            episode = numbers[-1]
            is_absolute = episode > 99
            if not is_absolute:
                notes.append("bare number treated as single episode")
            matched = True

    if kind is EpisodeKind.CREDITS and episode is None and credits:
        group = credits.group(1) if credits.lastindex else None
        if group:
            episode = int(group)

    if season == 0 and kind is EpisodeKind.EPISODE:
        kind = EpisodeKind.SPECIAL
        notes.append("season 0 -> special")

    filename_title = _clean_title(stem[:cut])
    folder_title = _pick_folder_title(folder)
    series_hint = folder_title if len(folder_title) >= 2 else filename_title
    if not series_hint:
        series_hint = filename_title or _clean_title(stem)

    if episode is None:
        notes.append("no episode number found")

    return ParsedFilename(
        raw=raw,
        series_hint=series_hint,
        folder=folder,
        season=season,
        episode=episode,
        episode_end=episode_end,
        kind=kind,
        is_absolute=is_absolute,
        version=version,
        part=part,
        notes=notes,
    )


__all__ = ["parse_filename"]
