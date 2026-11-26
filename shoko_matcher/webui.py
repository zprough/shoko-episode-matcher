"""Inline single-page UI (no external assets or template engine)."""

_HEAD = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Shoko Matcher</title>
<style>
:root{--bg:#0f1216;--card:#171c22;--muted:#8b98a5;--fg:#e6edf3;--accent:#4da3ff;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--line:#2a323c}
*{box-sizing:border-box}
body{margin:0;font:14px/1.45 system-ui,"Segoe UI",Roboto,sans-serif;background:var(--bg);color:var(--fg)}
header{padding:18px 22px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:18px;margin:0}
h2{font-size:14px;margin:0 0 10px}
.muted{color:var(--muted)}
main{padding:18px 22px;max-width:1400px;margin:0 auto}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin-bottom:16px}
label{display:block;font-size:12px;color:var(--muted);margin-bottom:4px}
input,select{width:100%;padding:8px;background:#0d1117;border:1px solid var(--line);border-radius:6px;color:var(--fg)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px}
.row{display:flex;gap:12px;align-items:center;flex-wrap:wrap}
button{padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:#21262d;color:var(--fg);cursor:pointer;font-weight:600}
button.primary{background:var(--accent);border-color:var(--accent);color:#04101f}
button:disabled{opacity:.5;cursor:not-allowed}
.check{display:flex;gap:8px;align-items:center}
.check input{width:auto}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
.stat{background:#0d1117;border:1px solid var(--line);border-radius:8px;padding:10px}
.stat .v{font-size:20px;font-weight:700}
.stat .k{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:11px;text-transform:uppercase}
td.path{max-width:520px;word-break:break-all;color:var(--muted)}
.badge{padding:2px 8px;border-radius:999px;font-size:11px;font-weight:700;text-transform:uppercase}
.b-link{background:rgba(63,185,80,.15);color:var(--ok)}
.b-review{background:rgba(210,153,34,.15);color:var(--warn)}
.b-skip{background:rgba(248,81,73,.15);color:var(--bad)}
.b-done{background:rgba(63,185,80,.15);color:var(--ok)}
.b-running{background:rgba(77,163,255,.15);color:var(--accent)}
.b-error{background:rgba(248,81,73,.2);color:var(--bad)}
.scroll{max-height:60vh;overflow:auto}
.notice{padding:10px 12px;border-radius:8px;margin-bottom:12px}
.notice.warn{background:rgba(210,153,34,.12);border:1px solid rgba(210,153,34,.4)}
.notice.err{background:rgba(248,81,73,.12);border:1px solid rgba(248,81,73,.4)}
a{color:var(--accent)}
#downloads a{margin-right:14px}
</style>
</head>
<body>
<header>
  <h1>Shoko Filename Matcher</h1>
  <span id="health" class="muted">checking&hellip;</span>
</header>
<main>
  <div id="notice"></div>
  <div class="card">
    <h2>Run</h2>
    <form id="form">
      <div class="grid">
        <div><label for="min_confidence">Min confidence</label><input id="min_confidence" type="number" min="0" max="100" step="1" value="80"/></div>
        <div><label for="limit">Limit (files)</label><input id="limit" type="number" min="1" placeholder="all"/></div>
        <div><label for="series">Series filter</label><input id="series" placeholder="name contains..."/></div>
        <div><label for="import_folder">Import folder ID</label><input id="import_folder" type="number" min="1" placeholder="any"/></div>
        <div><label for="episode_id_type">Episode ID type</label><select id="episode_id_type"><option value="shoko">Shoko</option><option value="anidb">AniDB</option></select></div>
        <div><label for="link_method">Link method</label><select id="link_method"><option value="episode">Episode IDs</option><option value="range">Series range</option></select></div>
      </div>
      <div class="row" style="margin-top:14px">
        <label class="check"><input id="apply" type="checkbox"/><span>Apply &mdash; actually create links</span></label>
        <label class="check"><input id="create_missing_series" type="checkbox"/><span>Create missing series (AniDB)</span></label>
        <button id="run" class="primary" type="submit">Run</button>
        <span id="status" class="muted"></span>
      </div>
    </form>
  </div>
  <div class="card" id="summary" style="display:none">
    <div class="stats" id="stats"></div>
    <div class="row" style="margin-top:12px" id="downloads"></div>
  </div>
  <div class="card" id="results" style="display:none">
    <h2>Results</h2>
    <div class="scroll"><table><thead><tr><th>File</th><th>Action</th><th>Conf</th><th>Series</th><th>Episodes</th><th>Reason</th><th>Path</th></tr></thead><tbody id="rows"></tbody></table></div>
  </div>
</main>
"""

_BODY = """<script>
const $ = (id) => document.getElementById(id);
let current = null;
let timer = null;
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

async function getJSON(url, opts) {
  const r = await fetch(url, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail || r.statusText);
  return data;
}

function showNotice(kind, msg) {
  $('notice').innerHTML = msg ? '<div class="notice ' + kind + '">' + esc(msg) + '</div>' : '';
}

async function loadConfig() {
  try {
    const c = await getJSON('/api/config');
    $('health').textContent = c.configured ? ('Shoko: ' + c.base_url) : 'Shoko not configured';
    if (c.min_confidence != null) $('min_confidence').value = c.min_confidence;
    if (!c.configured) showNotice('err', c.error || 'SHOKO_APIKEY is not set.');
  } catch (e) {
    $('health').textContent = 'web API error';
    showNotice('err', e.message);
  }
}

function payload() {
  const num = (v) => (v === '' ? null : Number(v));
  return {
    apply: $('apply').checked,
    min_confidence: num($('min_confidence').value),
    limit: num($('limit').value),
    series: $('series').value.trim() || null,
    import_folder: num($('import_folder').value),
    episode_id_type: $('episode_id_type').value,
    link_method: $('link_method').value,
    create_missing_series: $('create_missing_series').checked,
  };
}

$('form').addEventListener('submit', async (ev) => {
  ev.preventDefault();
  $('run').disabled = true;
  showNotice('', '');
  try {
    const job = await getJSON('/api/jobs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload()),
    });
    current = job.id;
    $('summary').style.display = 'none';
    $('results').style.display = 'none';
    $('rows').innerHTML = '';
    poll();
  } catch (e) {
    $('run').disabled = false;
    showNotice('err', e.message);
  }
});

function poll() {
  clearTimeout(timer);
  if (!current) return;
  timer = setTimeout(async () => {
    try {
      const j = await getJSON('/api/jobs/' + current);
      render(j);
      if (j.status === 'done' || j.status === 'error') {
        $('run').disabled = false;
      } else {
        poll();
      }
    } catch (e) {
      $('run').disabled = false;
      showNotice('err', e.message);
    }
  }, 900);
}

function stat(k, v, cls) {
  return '<div class="stat"><div class="v ' + (cls || '') + '">' + esc(v) +
    '</div><div class="k">' + esc(k) + '</div></div>';
}

function render(j) {
  $('status').textContent = j.status + (j.processed ? ' — ' + j.processed + ' files' : '');
  if (j.mylist_warning) {
    showNotice('warn', 'AniDB MyList_AddFiles is enabled — links may be added to your AniDB MyList.');
  } else if (j.error) {
    showNotice('err', j.error);
  } else if (j.status === 'done') {
    showNotice('', '');
  }
  if (!j.rows || !j.rows.length) return;
  $('summary').style.display = '';
  $('results').style.display = '';
  const c = j.counts || {};
  const delta = (j.baseline != null && j.after != null) ? (j.baseline - j.after) : '—';
  $('stats').innerHTML = [
    stat('status', j.status, 'b-' + j.status),
    stat('unrecognized before', j.baseline == null ? '—' : j.baseline),
    stat('unrecognized after', j.after == null ? '—' : j.after),
    stat('linked delta', delta),
    stat('link decisions', c.link || 0),
    stat('review', c.review || 0),
    stat('skipped', c.skip || 0),
    stat('applied', c.applied || 0),
    stat('failed', c.failed || 0),
  ].join('');
  $('downloads').innerHTML =
    '<a href="/api/jobs/' + esc(j.id) + '/report.csv">Download CSV</a>' +
    '<a href="/api/jobs/' + esc(j.id) + '/report.json">Download JSON</a>';
  $('rows').innerHTML = j.rows.map((r) => '<tr>' +
    '<td>' + esc(r.file_id) + '</td>' +
    '<td><span class="badge b-' + esc(r.action) + '">' + esc(r.action) + '</span></td>' +
    '<td>' + esc(r.confidence) + '</td>' +
    '<td>' + esc(r.series_name || '—') + '</td>' +
    '<td>' + esc(r.episode_ids || '—') + '</td>' +
    '<td>' + esc(r.reason) + '</td>' +
    '<td class="path">' + esc(r.path) + '</td>' +
    '</tr>').join('');
}

loadConfig();
</script>
</body>
</html>
"""

INDEX_HTML = _HEAD + _BODY
