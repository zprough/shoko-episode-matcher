"""Thin synchronous wrapper around the Shoko v3 REST API.

Only the endpoints needed by the matcher are implemented. Every request goes
through :meth:`ShokoClient._request`, which handles auth errors, 404s (optional),
retries with exponential backoff on transient failures, and JSON decoding.
"""

from __future__ import annotations

import time
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import quote

import httpx

from .config import Config
from .models import (
    Episode,
    Group,
    Series,
    SeriesSearchHit,
    UnrecognizedFile,
)


class ShokoError(Exception):
    """Any non-recoverable API error."""


class ShokoAuthError(ShokoError):
    """The API key was rejected (HTTP 401)."""


class ShokoNotFound(ShokoError):
    """The requested resource returned HTTP 404."""


class ShokoClient:
    def __init__(
        self,
        config: Config,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.normalized_base_url(),
            headers={
                "apikey": config.apikey,
                "Accept": "application/json",
            },
            timeout=config.timeout,
            follow_redirects=True,
            transport=transport,
        )

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ShokoClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- core request handling --------------------------------------------
    def _sleep(self, attempt: int) -> None:
        time.sleep(min(2 ** attempt, 30))

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Any = None,
        json: Any = None,
        allow_404: bool = False,
    ) -> Any:
        last_error: Optional[Exception] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                response = self._client.request(
                    method, path, params=params, json=json
                )
            except httpx.TransportError as exc:  # timeouts, connection resets
                last_error = exc
                if attempt < self.config.max_retries:
                    self._sleep(attempt)
                    continue
                break

            status = response.status_code
            if status == 401:
                raise ShokoAuthError(
                    "Shoko rejected the API key (401). Check --apikey / "
                    "SHOKO_APIKEY."
                )
            if status == 404:
                if allow_404:
                    return None
                raise ShokoNotFound(f"{method} {path} -> 404 Not Found")
            if status in (429, 500, 502, 503, 504):
                last_error = ShokoError(
                    f"{method} {path} -> {status}: {response.text[:200]}"
                )
                if attempt < self.config.max_retries:
                    self._sleep(attempt)
                    continue
                break
            if status >= 400:
                raise ShokoError(
                    f"{method} {path} -> {status}: {response.text[:300]}"
                )
            if status == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return None

        raise ShokoError(
            f"{method} {path} failed after {self.config.max_retries + 1} "
            f"attempts: {last_error}"
        )

    def _paginate(
        self,
        path: str,
        base_params: Optional[Iterable[tuple[str, str]]] = None,
        *,
        page_size: Optional[int] = None,
        list_key: str = "List",
    ) -> Iterator[dict[str, Any]]:
        size = page_size or self.config.page_size
        page = 1
        while True:
            params = list(base_params or [])
            params.append(("pageSize", str(size)))
            params.append(("page", str(page)))
            data = self._request("GET", path, params=params) or {}
            if not isinstance(data, dict):
                break
            items = data.get(list_key) or []
            yield from items
            if len(items) < size:
                break
            total = data.get("Total")
            if total is not None and page * size >= int(total):
                break
            page += 1

    # -- Dashboard / settings ---------------------------------------------
    def stats(self) -> dict[str, Any]:
        return self._request("GET", "Dashboard/Stats") or {}

    def settings(self) -> dict[str, Any]:
        return self._request("GET", "Settings") or {}

    def mylist_add_files(self) -> bool:
        settings = self.settings()
        anidb = settings.get("AniDb") or {}
        return bool(anidb.get("MyList_AddFiles"))

    def anidb_ban_status(self) -> dict[str, Any]:
        return self._request("GET", "AniDB/BanStatus", allow_404=True) or {}

    # -- Files -------------------------------------------------------------
    def unrecognized_files(
        self, import_folder_id: Optional[int] = None
    ) -> Iterator[UnrecognizedFile]:
        params = [
            ("include_only", "Unrecognized"),
            ("include", "AbsolutePaths"),
        ]
        for raw in self._paginate("File", params):
            candidate = UnrecognizedFile.from_api(raw)
            if import_folder_id is not None and not any(
                loc.import_folder_id == import_folder_id
                for loc in candidate.locations
            ):
                continue
            yield candidate

    # -- Series ------------------------------------------------------------
    def series_by_path(self, path: str) -> list[Series]:
        encoded = quote(path, safe="")
        data = self._request(
            "GET", f"Series/PathEndsWith/{encoded}", allow_404=True
        ) or []
        return [Series.from_api(item) for item in data if isinstance(item, dict)]

    def search_series(
        self, query: str, fuzzy: bool = True, limit: int = 10
    ) -> list[SeriesSearchHit]:
        params = {
            "query": query,
            "fuzzy": str(bool(fuzzy)).lower(),
            "limit": str(limit),
        }
        data = self._request(
            "GET", "Series/Search", params=params, allow_404=True
        ) or []
        return [
            SeriesSearchHit.from_api(item)
            for item in data
            if isinstance(item, dict)
        ]

    def series(self, series_id: int) -> Optional[Series]:
        data = self._request("GET", f"Series/{series_id}", allow_404=True)
        return Series.from_api(data) if isinstance(data, dict) else None

    def series_anime(self, series_id: int) -> dict[str, Any]:
        return self._request("GET", f"Series/{series_id}/AniDB", allow_404=True) or {}

    def series_group(self, series_id: int) -> Optional[Group]:
        data = self._request("GET", f"Series/{series_id}/Group", allow_404=True)
        return Group.from_api(data) if isinstance(data, dict) else None

    def group_series(self, group_id: int, recursive: bool = True) -> list[Series]:
        params = [
            ("recursive", str(bool(recursive)).lower()),
            ("includeMissing", "true"),
        ]
        data = self._request(
            "GET", f"Group/{group_id}/Series", params=params, allow_404=True
        ) or []
        return [Series.from_api(item) for item in data if isinstance(item, dict)]

    def series_episodes(self, series_id: int) -> list[Episode]:
        params = [
            ("includeMissing", "True"),
            ("includeFiles", "true"),
        ]
        return [
            Episode.from_api(raw)
            for raw in self._paginate(f"Series/{series_id}/Episode", params)
        ]

    def tmdb_episode_xrefs(self, series_id: int) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            f"Series/{series_id}/TMDB/Show/CrossReferences/Episode",
            allow_404=True,
        ) or {}
        if isinstance(data, dict):
            return list(data.get("List") or [])
        return list(data)

    def anidb_search(
        self, query: str, local: Optional[bool] = None, page_size: int = 50
    ) -> list[dict[str, Any]]:
        params: list[tuple[str, str]] = [
            ("query", query),
            ("fuzzy", "true"),
            ("pageSize", str(page_size)),
        ]
        if local is not None:
            params.append(("local", str(bool(local)).lower()))
        data = self._request(
            "GET", "Series/AniDB/Search", params=params, allow_404=True
        ) or {}
        if isinstance(data, dict):
            return list(data.get("List") or [])
        return list(data)

    def refresh_anidb(self, anidb_id: int, create: bool = True) -> Any:
        params = {"createSeriesEntry": str(bool(create)).lower()}
        return self._request(
            "POST", f"Series/AniDB/{anidb_id}/Refresh", params=params,
            allow_404=True,
        )

    # -- Link operations ---------------------------------------------------
    def link_episodes(self, file_id: int, episode_ids: list[int]) -> Any:
        return self._request(
            "POST", f"File/{file_id}/Link", json={"EpisodeIDs": episode_ids}
        )

    def link_from_series(
        self, file_id: int, series_id: int, range_start: str, range_end: str
    ) -> Any:
        return self._request(
            "POST",
            f"File/{file_id}/LinkFromSeries",
            json={
                "SeriesID": series_id,
                "RangeStart": range_start,
                "RangeEnd": range_end,
            },
        )

    def unlink(self, file_id: int) -> Any:
        return self._request("DELETE", f"File/{file_id}/Link", allow_404=True)
