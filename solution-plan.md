# Shoko Filename Matcher — Build Plan (implemented)

## Goal
Find files Shoko Server lists as **Unrecognized** and link them to the correct
AniDB-based episode(s) through the Shoko v3 REST API. Most such files are already
in the right series folder with clean names; they are unrecognized only because
their ED2K hash is unknown to AniDB.

## Resolved open questions
1. **`EpisodeIDs` in `POST /File/{fileID}/Link`** — treated as **Shoko episode
   IDs** (the `Episode.IDs.ID` value), because every other `/File/{fileID}/*`
   endpoint speaks Shoko IDs. Configurable with `--episode-id-type {shoko,anidb}`.
2. **`RangeStart` type prefix** — the swagger only says "append a type prefix".
   To avoid guessing, the default link path resolves concrete episode IDs and uses
   `POST /File/{id}/Link`. `--link-method range` is opt-in and uses `S`/`C`/`T`/`P`/`O`
   prefixes for non-regular types.
3. **`POST /api/auth`** — not needed; auth is the `apikey` header. The tool reads
   the key from `--apikey`, `--apikey-file`, `SHOKO_APIKEY`, a JSON config file, or
   a local `file-matcher-api.txt`.

## Endpoints used
- `GET /Dashboard/Stats` → `UnrecognizedFiles` (before/after).
- `GET /File?include_only=Unrecognized&include=AbsolutePaths` (page size 1000) →
  `ID`, `Locations[].RelativePath`, `ImportFolderID`, `AbsolutePath`.
- `GET /Series/PathEndsWith/{folder}` → series already living in the folder.
- `GET /Series/Search?query=&fuzzy=true&limit=10` → `ExactMatch`, `Distance`,
  `LengthDifference`.
- `GET /Series/{id}?includeDataFrom=AniDB` and `GET /Series/{id}/AniDB` →
  `AniDB.AirDate`, `AniDB.EpisodeCount`, titles. `includeDataFrom=AniDB` must be
  sent or the server may return a stub with an empty `AniDB` sub-object, which
  drops `AirDate`/`EpisodeCount` and breaks season/absolute mapping.
- `GET /Series/{id}/Group`, `GET /Group/{id}/Series?recursive=true` → sibling
  series (AniDB seasons), sorted by air date.
- `GET /Series/{id}/Episode?includeMissing=True&includeFiles=true&includeDataFrom=AniDB`
  (paginated) → all episode types; `Files` reveals whether an episode is already
  linked. `includeDataFrom=AniDB` is **required**: without it Shoko can return
  episodes whose `AniDB` sub-object is empty, so `Episode.from_api` sees
  `EpisodeNumber=0` / `Type=Unknown` for every entry and number lookups fail
  (`available: 0:Unknown, ...`). Debug builds log the episode count and a sample
  of `number:kind` pairs at `-v` to confirm whether AniDB data arrived.
- Writes: `POST /File/{id}/Link` (default) or `POST /File/{id}/LinkFromSeries`
  (`--link-method range`). Undo remains `DELETE /File/{id}/Link`.
- Optional: `GET /AniDB/BanStatus`, `GET /Series/AniDB/Search?local=false`,
  `POST /Series/AniDB/{id}/Refresh?createSeriesEntry=true` behind
  `--create-missing-series` (off by default).
- `GET /Settings` → `AniDb.MyList_AddFiles` warning before `--apply`.

## Matching pipeline
1. Paginate unrecognized files.
2. Parse path: `S02E05`, `2x05`, `E01-E02`, ` - 146`, `SP01`, `NCOP1`, `v2`,
   `Part 2`, `S00`, dotted release names, resolution/codec exclusion. The
   episode title that trails the marker (e.g. `- 001 - The Day I Became a
   Shinigami`) is captured into `ParsedFilename.episode_title`, and a trailing
   **absolute** episode number (`S02E01 - 021 - Title`) is captured into
   `ParsedFilename.absolute_episode` instead of being mistaken for a range end.
   `absolute_numbering` / `--absolute-numbering` (Sonarr `{absolute:000}`)
   forces this for any `SxxEyy - NNN` filename.
3. Resolve series: path lookup first (strongest), then fuzzy title search. Path
   lookups try, most-specific first, the **absolute directory**, the
   import-relative folder, the folder with trailing season dirs stripped (the
   series root, e.g. `Bleach/Season 01` → `Bleach`), then the title hint; each
   attempt is logged at debug. A single hit gets `unique_path=True`, which
   suppresses the ambiguity penalty and scores 55–70. Before fuzzy ranking is
   trusted, candidates are also scored by whether the series **already has
   recognised files in the same folder** (read from
   `Episode.Files[].Locations[].RelativePath`/`AbsolutePath`); a clear sibling
   majority picks that series and bypasses ambiguity. When path/sibling lookups
   miss (e.g. a folder with only *unrecognized* files, which Shoko has no
   series-path for), a **unique exact title match** against the folder/hint is
   treated like a path hit: its score is raised to ≥60 and the ambiguity guard
   is bypassed. Only if two series share that exact title does ambiguity stand.
4. Resolve target:
   - `S02E05` with an explicit absolute number (`S02E05 - 017`) → if the base
     series already contains that absolute episode (a single long series such
     as Bleach), link to the **base** series at that number (e.g.
     `S02E01 - 021` → series 59 episode 21); otherwise fall through to season
     mapping.
   - `S02E05` → Nth air-date sibling series in the group, episode 5.
   - bare/absolute numbers → walk group in air-date order, summing Episode counts.
   - specials/credits → base series, matching `AniDB.Type`.
   - unmappable season → review (never silently link the base episode).
5. Confidence: path/title score + episode exists + unlinked + mapping, plus a
   fuzzy **episode-title** bonus (0–10 when the filename title matches
   `Episode.Name`), minus ambiguity. Default threshold 80. Only `link` at/above
   threshold.
6. Dry-run default; `--apply` enables writes.

## Package layout
```
shoko_matcher/
  cli.py        argparse, run loop, MyList/AniDB-create handling
  config.py     env/config-file/CLI resolution, base URL normalisation
  client.py     httpx client: retries, pagination, endpoint methods
  models.py     tolerant dataclasses for all API payloads
  parser.py     filename/path parser
  resolver.py   series + season/absolute resolution, confidence, Decision
  linker.py     the only write path (Link / LinkFromSeries)
  reporter.py   human summary + CSV/JSON report
tests/
  test_parser.py        filename parsing
  test_resolver.py      season/absolute/ambiguity/linking logic
  test_integration.py   httpx.MockTransport full dry-run + link body
```

## Safety
- Dry-run by default; only GETs unless `--apply`.
- Only link creation; never move/rename/ignore/delete or change settings.
- Idempotent (unrecognized == unlinked), flags already-linked targets.
- `--series` (and `--import-folder`) exclusions are removed from all output
  (CLI summary, CSV/JSON, web UI) and don't count toward `--limit`.
- Ambiguous, low-confidence, missing-episode, and unmappable-season cases → review.
- MyList warning and optional AniDB ban check.

## CLI
`--dry-run/--apply`, `--min-confidence`, `--series`, `--import-folder`, `--limit`,
`--report`, `--create-missing-series`, `--absolute-numbering`, `--episode-id-type`,
`--link-method`, `--timeout`, `--max-retries`, `--base-url`, `--apikey`,
`--apikey-file`, `--config`, `-v`.

## Tests
`python -m pytest -q` → 29 passing (parser + resolver + mocked HTTP pipeline + web API).

## Known limitations / follow-ups
- TMDB is not required; group air-date ordering handles seasons. TMDB episode
  cross-references could later refine tricky mappings.
- Season mapping assumes siblings are ordered by AniDB air date; unusual release
  groupings may need `--min-confidence` tuning or manual review.
- AniDB series creation is asynchronous; re-run after the import completes.

## Addendum — Web UI & Docker (implemented)
- `runner.py` holds the shared run loop; the CLI and web UI both use it.
- `webapp.py` (FastAPI) exposes `GET /` (single-page UI), `/api/health`,
  `/api/config`, `POST /api/jobs` (one concurrent job; 409 when busy),
  `GET /api/jobs`, `GET /api/jobs/{id}`, and CSV/JSON report downloads.
- The results table shows a human-readable **Target** (`S01E05`, `S01E01-E02`,
  `SP1 (Special)`) built from the parsed season/episode/kind, instead of opaque
  episode IDs. Episode IDs remain available in the CSV/JSON downloads.
- Per-file errors are isolated (recorded as `skip`); auth errors abort the job.
- `Dockerfile` installs `.[web]`, runs as non-root, exposes port 8080, and has a
  healthcheck. `docker-compose.example.yml` shows the Shoko stack wiring.
- `SHOKO_URL` / `SHOKO_APIKEY` configure the container; the UI is unauthenticated
  and should stay on a trusted network.
- `tests/test_webapp.py` covers the UI/API with a fake runner.
