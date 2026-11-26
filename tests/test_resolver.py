from shoko_matcher.config import Config
from shoko_matcher.models import (
    Episode,
    EpisodeKind,
    FileLocation,
    Group,
    Series,
    SeriesSearchHit,
    UnrecognizedFile,
)
from shoko_matcher.resolver import Resolver


def make_series(sid, name, air, count, group=1):
    return Series(
        id=sid,
        anidb_id=sid * 10,
        name=name,
        titles=[name],
        air_date=air,
        episode_count=count,
        parent_group=group,
        top_level_group=group,
    )


def make_episodes(sid, count, kind=EpisodeKind.EPISODE, linked=()):
    return [
        Episode(
            id=sid * 100 + n,
            anidb_id=sid * 1000 + n,
            number=n,
            kind=kind,
            file_ids=[999] if n in linked else [],
        )
        for n in range(1, count + 1)
    ]


class FakeApi:
    def __init__(
        self,
        series_list,
        episodes_map,
        path_series=None,
        hits=None,
        path_map=None,
    ):
        self._by_id = {s.id: s for s in series_list}
        self.series_list = series_list
        self.episodes_map = episodes_map
        self.path_series = path_series if path_series is not None else []
        self.hits = hits if hits is not None else []
        self.path_map = path_map

    def series_by_path(self, path):
        if self.path_map is not None:
            return self.path_map.get(path, [])
        return self.path_series

    def search_series(self, query, fuzzy=True, limit=10):
        return self.hits

    def series(self, series_id):
        return self._by_id.get(series_id)

    def series_group(self, series_id):
        return Group(1, "g")

    def group_series(self, group_id, recursive=True):
        return self.series_list

    def series_episodes(self, series_id):
        return self.episodes_map.get(series_id, [])


def file_for(path, fid=7):
    return UnrecognizedFile(id=fid, locations=[FileLocation(1, path)])


def cfg(**kwargs):
    base = {"apikey": "x", "dry_run": True}
    base.update(kwargs)
    return Config(**base)


def test_season_maps_to_sibling_series():
    s1 = make_series(1, "Franchise", "2020-01-01", 12)
    s2 = make_series(2, "Franchise Season 2", "2021-01-01", 12)
    api = FakeApi([s1, s2], {1: make_episodes(1, 12), 2: make_episodes(2, 12)},
                  path_series=[s1])
    decision = Resolver(api, cfg()).resolve(
        file_for("Franchise/Franchise S02E05.mkv")
    )
    assert decision.action == "link"
    assert decision.episode_ids == [205]
    assert "season-map" in decision.method


def test_absolute_number_maps_across_group():
    s1 = make_series(1, "Franchise", "2020-01-01", 12)
    s2 = make_series(2, "Franchise Season 2", "2021-01-01", 12)
    api = FakeApi([s1, s2], {1: make_episodes(1, 12), 2: make_episodes(2, 12)},
                  path_series=[s1])
    decision = Resolver(api, cfg()).resolve(
        file_for("Franchise/Franchise - 15.mkv")
    )
    assert decision.action == "link"
    assert decision.episode_ids == [203]
    assert "absolute-map" in decision.method


def test_missing_episode_goes_to_review():
    s1 = make_series(1, "Solo", "2020-01-01", 3)
    api = FakeApi([s1], {1: make_episodes(1, 3)}, path_series=[s1])
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - 09.mkv"))
    assert decision.action == "review"
    assert "not found" in decision.reason


def test_already_linked_episode_goes_to_review():
    s1 = make_series(1, "Solo", "2020-01-01", 3)
    api = FakeApi(
        [s1], {1: make_episodes(1, 3, linked={2})}, path_series=[s1]
    )
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - 02.mkv"))
    assert decision.action == "review"
    assert "already linked" in decision.reason


def test_unique_path_match_links_with_high_confidence():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    api = FakeApi([s1], {1: make_episodes(1, 12)}, path_series=[s1])
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - 05.mkv"))
    assert decision.action == "link"
    assert decision.confidence >= 80
    assert decision.episode_ids == [105]


def test_ambiguous_candidates_go_to_review():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    hits = [
        SeriesSearchHit(1, 10, "Solo", exact_match=True),
        SeriesSearchHit(3, 30, "Solo", exact_match=True),
    ]
    api = FakeApi([s1], {1: make_episodes(1, 12)}, path_series=[], hits=hits)
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - 05.mkv"))
    assert decision.action == "review"
    assert "multiple" in decision.reason


def test_anidb_id_type_uses_anidb_episode_ids():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    api = FakeApi([s1], {1: make_episodes(1, 12)}, path_series=[s1])
    decision = Resolver(api, cfg(episode_id_type="anidb")).resolve(
        file_for("Solo/Solo - 05.mkv")
    )
    assert decision.episode_ids == [1005]


def test_special_episode_links():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    special = Episode(
        id=9999, anidb_id=8888, number=1, kind=EpisodeKind.SPECIAL
    )
    api = FakeApi(
        [s1], {1: make_episodes(1, 12) + [special]}, path_series=[s1]
    )
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - SP01.mkv"))
    assert decision.action == "link"
    assert decision.episode_ids == [9999]


def test_fuzzy_match_links_when_threshold_lowered():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    hits = [SeriesSearchHit(1, 10, "Solo", exact_match=True)]
    api = FakeApi([s1], {1: make_episodes(1, 12)}, path_series=[], hits=hits)
    decision = Resolver(api, cfg(min_confidence=60)).resolve(
        file_for("Solo/Solo - 05.mkv")
    )
    assert decision.action == "link"
    assert "fuzzy" in decision.method


def test_series_filter_skips_non_matching():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    api = FakeApi([s1], {1: make_episodes(1, 12)}, path_series=[s1])
    decision = Resolver(api, cfg(series_filter="Other")).resolve(
        file_for("Solo/Solo - 05.mkv")
    )
    assert decision.action == "skip"
    assert decision.filtered is True


def test_unmappable_season_goes_to_review():
    s1 = make_series(1, "Solo", "2020-01-01", 12)
    api = FakeApi([s1], {1: make_episodes(1, 12)}, path_series=[s1])
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo S03E05.mkv"))
    assert decision.action == "review"
    assert "map season" in decision.reason


def test_path_falls_back_to_parent_folder_for_season_dirs():
    s1 = make_series(1, "Bleach", "2004-10-05", 366)
    api = FakeApi(
        [s1],
        {1: make_episodes(1, 366)},
        path_map={"Bleach/Season 01": [], "Bleach": [s1]},
    )
    decision = Resolver(api, cfg()).resolve(
        file_for(
            "Bleach/Season 01/Bleach (2004) - S01E01 - 001 - "
            "The Day I Became a Shinigami [DSNP][WEBDL-1080p]-NORViNE.mkv"
        )
    )
    assert decision.action == "link"
    assert decision.series_id == 1
    assert decision.episode_ids == [101]


def test_missing_episode_reports_available_numbers():
    s1 = make_series(1, "Solo", "2020-01-01", 3)
    api = FakeApi([s1], {1: make_episodes(1, 3)}, path_series=[s1])
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - 09.mkv"))
    assert decision.action == "review"
    assert "available:" in decision.reason
    assert "1:Episode" in decision.reason


def test_empty_episode_list_suggests_refresh():
    s1 = make_series(1, "Solo", "2020-01-01", 0)
    api = FakeApi([s1], {1: []}, path_series=[s1])
    decision = Resolver(api, cfg()).resolve(file_for("Solo/Solo - 01.mkv"))
    assert decision.action == "review"
    assert "no episodes loaded" in decision.reason


def test_path_lookup_uses_absolute_directory():
    s1 = make_series(1, "Bleach", "2004-10-05", 366)
    api = FakeApi(
        [s1],
        {1: make_episodes(1, 366)},
        path_map={
            "/mnt/anime/Bleach/Season 01": [s1],
            "Bleach/Season 01": [],
            "Bleach": [],
        },
    )
    name = (
        "Bleach (2004) - S01E01 - 001 - The Day I Became a Shinigami "
        "[DSNP][WEBDL-1080p]-NORViNE.mkv"
    )
    file = UnrecognizedFile(
        id=7,
        locations=[
            FileLocation(
                1,
                f"Bleach/Season 01/{name}",
                absolute_path=f"/mnt/anime/Bleach/Season 01/{name}",
            )
        ],
    )
    decision = Resolver(api, cfg()).resolve(file)
    assert decision.action == "link"
    assert decision.series_id == 1
    assert decision.method == "path"
    assert decision.episode_ids == [101]


def test_episode_title_similarity_lifts_fuzzy_match_over_threshold():
    s1 = make_series(59, "Bleach", "2004-10-05", 366)
    episodes = make_episodes(59, 366)
    episodes[0].title = "The Day I Became a Shinigami"
    hits = [SeriesSearchHit(59, 602, "Bleach", exact_match=True)]
    api = FakeApi([s1], {59: episodes}, path_series=[], hits=hits)
    decision = Resolver(api, cfg()).resolve(
        file_for(
            "Bleach/Season 01/Bleach (2004) - S01E01 - 001 - The Day I "
            "Became a Shinigami [DSNP][WEBDL-1080p]-NORViNE.mkv"
        )
    )
    assert decision.action == "link"
    assert decision.confidence >= 80
    assert decision.episode_ids == [5901]


def test_exact_folder_title_breaks_franchise_ambiguity():
    bleacher = make_series(59, "Bleach", "2004-10-05", 366)
    movie = make_series(60, "Bleach: Memories of Nobody", "2006-01-01", 1)
    hits = [
        SeriesSearchHit(59, 602, "Bleach", exact_match=True),
        SeriesSearchHit(
            60, 603, "Bleach: Memories of Nobody", exact_match=False,
            distance=0.6,
        ),
    ]
    api = FakeApi(
        [bleacher, movie],
        {59: make_episodes(59, 366), 60: make_episodes(60, 1)},
        path_series=[],
        hits=hits,
    )
    decision = Resolver(api, cfg()).resolve(
        file_for(
            "Bleach/Season 01/Bleach (2004) - S01E01 - 001 - The Day I "
            "Became a Shinigami [DSNP][WEBDL-1080p]-NORViNE.mkv"
        )
    )
    assert decision.action == "link"
    assert decision.series_id == 59
    assert decision.reason == "matched"
    assert decision.confidence >= 80


def test_already_matched_sibling_files_select_series():
    bleacher = make_series(59, "Bleach", "2004-10-05", 366)
    movie = make_series(60, "Bleach: Memories of Nobody", "2006-01-01", 1)
    episodes = make_episodes(59, 366)
    episodes[0].file_dirs = {"downloads/bleach s1"}
    episodes[1].file_dirs = {"downloads/bleach s1"}
    hits = [
        SeriesSearchHit(59, 602, "Bleach", exact_match=True),
        SeriesSearchHit(
            60, 603, "Bleach: Memories of Nobody", exact_match=False,
            distance=0.6,
        ),
    ]
    api = FakeApi(
        [bleacher, movie],
        {59: episodes, 60: make_episodes(60, 1)},
        path_series=[],
        hits=hits,
    )
    decision = Resolver(api, cfg()).resolve(
        file_for(
            "Downloads/Bleach S1/Bleach (2004) - S01E01 - 001 - The Day I "
            "Became a Shinigami [DSNP][WEBDL-1080p]-NORViNE.mkv"
        )
    )
    assert decision.action == "link"
    assert decision.series_id == 59
    assert decision.confidence >= 80


def test_second_season_uses_absolute_episode_on_base_series():
    s1 = make_series(59, "Bleach", "2004-10-05", 366)
    api = FakeApi([s1], {59: make_episodes(59, 366)}, path_series=[s1])
    decision = Resolver(api, cfg()).resolve(
        file_for(
            "Bleach/Season 02/Bleach (2004) - S02E01 - 021 - Enter! The "
            "World of the Shinigami [DSNP][WEBDL-1080p]-NORViNE.mkv"
        )
    )
    assert decision.action == "link"
    assert decision.series_id == 59
    assert decision.episode_ids == [5921]
    assert "absolute" in decision.method
