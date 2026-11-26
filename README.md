# shoko-matcher

A small Python CLI that finds **unrecognized files** in your Shoko Server library
and links them to the correct AniDB-based episodes through the Shoko v3 REST API.

Most files only end up unrecognized because their ED2K hash is unknown to AniDB.
When they already live in the right series folder with a sensible name, this tool
can resolve the series/episode and create the missing link.

## Safety

* **Dry-run by default.** Only `GET` requests are made unless you pass `--apply`.
* The only write operations are link creation:
  * `POST /File/{id}/Link` (default), or
  * `POST /File/{id}/LinkFromSeries` (`--link-method range`).
* It never moves, renames, ignores, deletes files or touches series/groups/settings.
* Re-running is idempotent: Shoko only lists unrecognized (unlinked) files.
* If `AniDb.MyList_AddFiles` is enabled, a warning is logged before `--apply`.
* Ambiguous or low-confidence matches are reported for review, never guessed.

## Install

```powershell
python -m pip install -e .
```

Runtime dependency: `httpx`. Tests use `pytest` (`pip install -e .[dev]`).

## Configuration

Resolution order (later wins): JSON config file, environment variables, CLI args.

| Setting | Env | CLI |
| --- | --- | --- |
| Base URL | `SHOKO_URL` | `--base-url` |
| API key | `SHOKO_APIKEY` | `--apikey` / `--apikey-file` |
| Config file | `SHOKO_MATCHER_CONFIG` | `--config` |

The API key is sent as the `apikey` header. If none is supplied, the tool looks
for a `file-matcher-api.txt` file in the working directory and reads the first
line. `https://host` / `http://host:port` are normalised to `.../api/v3/`.

Example `shoko-matcher.config.json` (git-ignored):

```json
{
  "base_url": "http://localhost:8111",
  "apikey": "your-api-key",
  "min_confidence": 80
}
```

## Usage

```powershell
# Dry-run the whole library, print a summary
python -m shoko_matcher --base-url http://localhost:8111

# Actually create links, only high-confidence matches, write a CSV report
python -m shoko_matcher --apply --min-confidence 85 --report report.csv

# Just one import folder / a few files
python -m shoko_matcher --import-folder 3 --limit 20

# Only series whose name contains "monogatari"
python -m shoko_matcher --series monogatari
```

The legacy launcher `python shoko-matcher.py ...` works too, as does the console
script `shoko-matcher` after installation.

### Options

```
--base-url URL        Shoko base URL (env SHOKO_URL)
--apikey KEY          Shoko API key (env SHOKO_APIKEY)
--apikey-file PATH    File containing the API key
--config PATH         JSON config file
--dry-run             Only GET (default)
--apply               Actually create links
--min-confidence N    Auto-link threshold, 0-100 (default 80)
--series NAME         Only handle series matching this name
--import-folder ID    Only files in this import folder
--limit N             Process at most N files
--report PATH         Write a CSV or JSON report (by extension)
--create-missing-series  Allow queued AniDB refreshes that create series
--episode-id-type {shoko,anidb}   IDs used by /File/{id}/Link (default shoko)
--link-method {episode,range}     Link by episode IDs or series range (default episode)
--timeout SECONDS     HTTP timeout (default 30)
--max-retries N       HTTP retries with exponential backoff (default 4)
-v / -vv              Increase log verbosity
```

## How matching works

1. Fetch all files from `GET /File?include_only=Unrecognized&include=AbsolutePaths`
   (paginated, page size 1000).
2. Parse the relative path for title, season, episode/range, absolute number,
   specials/credits markers, version and part.
3. Resolve the series:
   * `GET /Series/PathEndsWith/{folder}` — series already living in the folder
     (strongest signal).
   * `GET /Series/Search?query=&fuzzy=true` — fuzzy title matching using Shoko's
     `ExactMatch` / `Distance` / `LengthDifference`.
4. Resolve the episode:
   * Load episodes with `GET /Series/{id}/Episode?includeMissing=True&includeFiles=true`
     (all types, so missing specials/credits are included).
   * **Season mapping** (`S02`): treat each airing-order sibling series in the
     group (`GET /Series/{id}/Group`, `GET /Group/{id}/Series?recursive=true`,
     sorted by `AniDB.AirDate`) as a season.
   * **Absolute numbers** (e.g. `146`): walk the group in air-date order, summing
     each series' episode count, to find the owning series and local number.
5. Score confidence from folder agreement, title distance, whether the target
   episode exists and whether it is still unlinked.
6. Link only matches at/above `--min-confidence`; everything else is reported.

### Why episode IDs by default?

The swagger documents `LinkSeriesBody.RangeStart` only as "append a type prefix
to use another episode type", without spelling out the prefix. The matcher
instead resolves the concrete Shoko episode IDs from `/Series/{id}/Episode` and
uses `POST /File/{id}/Link` with `EpisodeIDs`. `--episode-id-type` lets you switch
to AniDB episode IDs if your server expects those. `--link-method range` is
available for `LinkFromSeries`, using `S`/`C`/`T`/`P`/`O` prefixes for non-regular
types.

## Report

`--report report.csv` or `--report report.json` writes one row per file with:
file id, path, parsed season/episode/kind/version/part, chosen series and episode
IDs, method, confidence, action, applied/success and the skip/link reason.

## Development

```powershell
python -m pip install -e .[dev]
python -m pytest -q
```

Tests cover the filename parser and the season / absolute-number mapping logic
using a fake API client (no network required).

## Notes & limitations

* TMDB is **not** required to link files. The endpoints exist but are not needed
  for AniDB episode targeting; group air-date ordering handles seasons.
* `--create-missing-series` is off by default. It checks `GET /AniDB/BanStatus`,
  searches Shoko's local AniDB title dump, and queues
  `POST /Series/AniDB/{id}/Refresh?createSeriesEntry=true`. Series creation is
  asynchronous, so re-run once the import finishes.
* Files in folders that contain no recognized series yet will fall back to fuzzy
  title matching and may need a lower threshold or manual review.
* `--series` filters on the resolved series name; unmatched files are marked
  `skip`, not `review`.
