import json

import httpx

from shoko_matcher.cli import run
from shoko_matcher.client import ShokoClient
from shoko_matcher.config import Config
from shoko_matcher.runner import run_matching

SERIES = {
    "IDs": {"ID": 1, "AniDB": 10, "ParentGroup": 1, "TopLevelGroup": 1},
    "Name": "Solo",
    "AniDB": {
        "AirDate": "2020-01-01",
        "EpisodeCount": 12,
        "Title": "Solo",
        "Titles": [{"Name": "Solo"}],
    },
}


def episodes_payload():
    return {
        "Total": 12,
        "List": [
            {
                "IDs": {"ID": 100 + n, "AniDB": 1000 + n, "ParentSeries": 1},
                "AniDB": {
                    "Type": "Episode",
                    "EpisodeNumber": n,
                    "AirDate": "2020-01-01",
                },
                "Name": f"Episode {n}",
                "Files": [],
            }
            for n in range(1, 13)
        ],
    }


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/Dashboard/Stats"):
        return httpx.Response(200, json={"UnrecognizedFiles": 1})
    if path.endswith("/api/v3/File"):
        return httpx.Response(
            200,
            json={
                "Total": 1,
                "List": [
                    {
                        "ID": 7,
                        "Size": 123,
                        "Locations": [
                            {
                                "ImportFolderID": 1,
                                "RelativePath": "Solo/Solo - 05.mkv",
                                "AbsolutePath": "/lib/Solo/Solo - 05.mkv",
                                "IsAccessible": True,
                            }
                        ],
                    }
                ],
            },
        )
    if path.endswith("/Series/PathEndsWith/Solo"):
        return httpx.Response(200, json=[SERIES])
    if path.endswith("/Series/1/Group"):
        return httpx.Response(200, json={"IDs": {"ID": 1}, "Name": "g"})
    if path.endswith("/Group/1/Series"):
        return httpx.Response(200, json=[SERIES])
    if path.endswith("/Series/1/Episode"):
        return httpx.Response(200, json=episodes_payload())
    if path.endswith("/Series/1"):
        return httpx.Response(200, json=SERIES)
    return httpx.Response(404, json={"error": f"unhandled {path}"})


def test_full_dry_run_pipeline_is_read_only(capsys):
    seen = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(
            (request.method, request.url.path, request.url.query)
        )
        return handler(request)

    transport = httpx.MockTransport(recording)
    config = Config(apikey="secret", dry_run=True, timeout=5, max_retries=0)
    client = ShokoClient(config, transport=transport)
    code = run(config, client)
    out = capsys.readouterr().out

    assert code == 0
    assert "linkable=1" in out
    assert all(method == "GET" for method, _, _ in seen)
    file_queries = [q for m, p, q in seen if p.endswith("/api/v3/File")]
    assert file_queries and b"include_only=Unrecognized" in file_queries[0]


def test_link_episodes_posts_expected_body():
    captured = {}

    def capturing(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured["path"] = request.url.path
            captured["body"] = json.loads(request.content)
            captured["apikey"] = request.headers.get("apikey")
            return httpx.Response(200)
        return handler(request)

    transport = httpx.MockTransport(capturing)
    config = Config(apikey="secret", dry_run=False, timeout=5, max_retries=0)
    client = ShokoClient(config, transport=transport)
    client.link_episodes(7, [105])

    assert captured["path"] == "/api/v3/File/7/Link"
    assert captured["body"] == {"EpisodeIDs": [105]}
    assert captured["apikey"] == "secret"


def test_api_key_header_is_sent():
    headers = {}

    def capturing(request: httpx.Request) -> httpx.Response:
        headers["apikey"] = request.headers.get("apikey")
        return handler(request)

    transport = httpx.MockTransport(capturing)
    config = Config(apikey="topsecret", dry_run=True, timeout=5, max_retries=0)
    client = ShokoClient(config, transport=transport)
    client.stats()
    assert headers["apikey"] == "topsecret"


def test_series_and_episode_requests_ask_for_anidb_data():
    queries = []

    def recording(request: httpx.Request) -> httpx.Response:
        queries.append((request.url.path, request.url.query))
        return handler(request)

    transport = httpx.MockTransport(recording)
    config = Config(apikey="secret", dry_run=True, timeout=5, max_retries=0)
    client = ShokoClient(config, transport=transport)
    client.series(1)
    client.series_episodes(1)
    client.close()

    series_query = next(
        q for p, q in queries if p.endswith("/Series/1")
    )
    episode_query = next(
        q for p, q in queries if p.endswith("/Series/1/Episode")
    )
    assert b"includeDataFrom=AniDB" in series_query
    assert b"includeDataFrom=AniDB" in episode_query


def test_episodes_without_anidb_data_go_to_review():
    """When Shoko returns episodes with no populated AniDB sub-object the
    matcher sees ``0:Unknown`` for every entry and must send the file to
    review rather than linking to episode 0."""

    def no_anidb_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/Series/1/Episode"):
            return httpx.Response(
                200,
                json={
                    "Total": 12,
                    "List": [
                        {
                            "IDs": {
                                "ID": 100 + n,
                                "AniDB": 1000 + n,
                                "ParentSeries": 1,
                            },
                            "Name": f"Episode {n}",
                            "Files": [],
                        }
                        for n in range(1, 13)
                    ],
                },
            )
        return handler(request)

    transport = httpx.MockTransport(no_anidb_handler)
    config = Config(apikey="secret", dry_run=True, timeout=5, max_retries=0)
    client = ShokoClient(config, transport=transport)
    summary = run_matching(config, client)
    client.close()

    assert len(summary.decisions) == 1
    decision = summary.decisions[0]
    assert decision.action == "review"
    assert decision.episode_ids == []
    assert "0:Unknown" in decision.reason


def test_series_filter_excludes_files_from_output():
    transport = httpx.MockTransport(handler)
    config = Config(
        apikey="secret",
        dry_run=True,
        timeout=5,
        max_retries=0,
        series_filter="Other",
    )
    client = ShokoClient(config, transport=transport)
    summary = run_matching(config, client)
    client.close()

    # The only file ("Solo") doesn't match the filter, so it is excluded from
    # the decisions/outcomes that feed the CLI report, CSV/JSON, and web UI.
    assert summary.decisions == []
    assert summary.outcomes == {}
