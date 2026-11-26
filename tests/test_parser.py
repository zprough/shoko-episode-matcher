from shoko_matcher.models import EpisodeKind
from shoko_matcher.parser import parse_filename


def test_season_episode_from_path():
    parsed = parse_filename(
        r"Show Name\Show Name - S02E05 - Some Title [1080p].mkv"
    )
    assert parsed.season == 2
    assert parsed.episode == 5
    assert parsed.kind is EpisodeKind.EPISODE
    assert parsed.series_hint == "Show Name"


def test_nxnn_pattern():
    parsed = parse_filename("Show Name/Show Name 2x05.mkv")
    assert parsed.season == 2
    assert parsed.episode == 5


def test_dash_absolute_number():
    parsed = parse_filename(
        "[SubsPlease] Jujutsu Kaisen - 24 (1080p) [ABCD1234].mkv",
        folder="Jujutsu Kaisen",
    )
    assert parsed.episode == 24
    assert parsed.is_absolute is True
    assert parsed.series_hint == "Jujutsu Kaisen"


def test_episode_range():
    parsed = parse_filename("Show/Show - E01-E02.mkv")
    assert parsed.episode == 1
    assert parsed.episode_end == 2
    assert parsed.is_range is True
    assert parsed.episode_span() == [1, 2]


def test_version_suffix():
    parsed = parse_filename("Show/Show - 05v2.mkv")
    assert parsed.episode == 5
    assert parsed.version == 2


def test_special_marker():
    parsed = parse_filename("Show/Show - SP01.mkv")
    assert parsed.kind is EpisodeKind.SPECIAL
    assert parsed.episode == 1


def test_credits_marker():
    parsed = parse_filename("Show/Show - NCOP1.mkv")
    assert parsed.kind is EpisodeKind.CREDITS
    assert parsed.episode == 1


def test_season_zero_is_special():
    parsed = parse_filename("Show/Show S00E03.mkv")
    assert parsed.season == 0
    assert parsed.episode == 3
    assert parsed.kind is EpisodeKind.SPECIAL


def test_resolution_is_not_an_episode():
    parsed = parse_filename("Show/Show - 1080p.mkv")
    assert parsed.episode is None


def test_dotted_release_name():
    parsed = parse_filename(
        "[Group] Show.Name.S01E01.1080p.WEB-DL.mkv", folder=""
    )
    assert parsed.season == 1
    assert parsed.episode == 1
    assert "Show Name" in parsed.series_hint


def test_folder_title_preferred():
    parsed = parse_filename(
        "Series A/[Group]_Series_A_-_05.mkv", folder="Series A"
    )
    assert parsed.series_hint == "Series A"
    assert parsed.episode == 5


def test_episode_title_extracted_from_release_name():
    parsed = parse_filename(
        "Bleach/Season 01/Bleach (2004) - S01E01 - 001 - "
        "The Day I Became a Shinigami [DSNP][WEBDL-1080p][AAC 2.0][JA]"
        "[h264 8bit]-NORViNE.mkv"
    )
    assert parsed.episode == 1
    assert parsed.episode_title == "The Day I Became a Shinigami"


def test_episode_title_empty_for_plain_number():
    parsed = parse_filename("Show/Show - 05.mkv")
    assert parsed.episode == 5
    assert parsed.episode_title == ""


def test_absolute_episode_after_marker_is_not_a_range():
    parsed = parse_filename(
        "Bleach/Season 02/Bleach (2004) - S02E01 - 021 - "
        "Enter! The World of the Shinigami [DSNP][WEBDL-1080p].mkv"
    )
    assert parsed.season == 2
    assert parsed.episode == 1
    assert parsed.episode_end is None
    assert parsed.absolute_episode == 21
    assert parsed.is_range is False
    assert parsed.episode_title == "Enter! The World of the Shinigami"


def test_explicit_episode_range_is_not_absolute():
    parsed = parse_filename("Show/Show - S01E01-E02.mkv")
    assert parsed.episode == 1
    assert parsed.episode_end == 2
    assert parsed.absolute_episode is None


def test_absolute_numbering_option_forces_absolute_not_range():
    name = "Show/Show - S02E01 - 2 - Title.mkv"
    # Heuristic (default): a small, non-padded trailing number looks like a range.
    plain = parse_filename(name)
    assert plain.episode_end == 2
    assert plain.absolute_episode is None
    # Explicit Sonarr-style absolute numbering: treat it as the absolute number.
    forced = parse_filename(name, absolute_numbering=True)
    assert forced.episode == 1
    assert forced.episode_end is None
    assert forced.absolute_episode == 2
