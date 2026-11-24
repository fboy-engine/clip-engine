/**
 * Clip Engine - Lauren Hamden control panel (Google Apps Script web app).
 *
 * Standalone project "clip-engine". The Google Sheet is the database: the engine (GitHub Actions)
 * and this panel read/write the same cells. The sheet is always opened by id (a standalone project has no bound sheet).
 *
 * SAFETY: the only cells this panel may write are WRITABLE below (APPROVE, add link, confirm add
 * link). Every write goes through setCell_(), which throws for any other cell. No secrets, no
 * external requests (the manifest has no external_request scope).
 *
 * The cell map between CELL_MAP_BEGIN / CELL_MAP_END is the ONE copy on the Apps Script side. It
 * mirrors campaign_lib/sheet.py (CELLS, QUEUE_LAST_ROW, QUEUE_HEAD, tab names); the python test
 * tests/test_apps_script_panel.py fails if the two ever differ. Change the python first, then here.
 */
var SHEET_ID = '1VYtK6mm40NXyB6ixLRoMtsc5yDxFXX9u_FUCcLGLvh8'; // campaigns/lauren-hamden/campaign.json sheet.id
var TZ = 'Asia/Karachi';                                          // campaign.json display_tz
var REACH_URL = 'https://reachclipping.com/Sisters';              // campaign.json reach_url (open only, never submitted)
var SUBMIT_WINDOW_MIN = 30;                                       // campaign.json timings.submit_window_min
var LOG_ROWS = 10;
var ENGINE_STALE_MS = 5 * 60000;   // engine.py STALE_S: heartbeat older than this = engine offline
var TOKEN_WARN_DAYS = 7;           // engine.py TOKEN_WARN_DAYS
var MAX_LINK_CHARS = 300;

// CELL_MAP_BEGIN
var TABS = {campaign: 'Campaign', queue: 'Queue', log: 'Log'};
var CELLS = {name: 3, state: 4, next: 5, caption: 6, approve: 7, timer: 8, opens: 9, link: 10, submit: 11, add_link: 12, add_confirm: 13, updated: 14, unlock_utc: 16, log_rows: 17, lock_run: 18, heartbeat: 19, ig_expires: 20, handoff: 21, loop_start: 22};
var QUEUE_LAST_ROW = 100;
var QUEUE_COLS = ['n', 'source', 'kind', 'state', 'moment', 'hook', 'caption', 'note', 'posted_at', 'media_id', 'link', 'submit_before', 'updated'];
// CELL_MAP_END

var WRITABLE = ['approve', 'add_link', 'add_confirm'];
var LINK_RE = /^https:\/\/(www\.|m\.)?(twitch\.tv|youtube\.com)(\/\S*)?$/i;

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index')
    .setTitle('Clip panel')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}

/* ---------- sheet access ---------- */

function openSheets_() {
  var ss = SpreadsheetApp.openById(SHEET_ID);
  var s = {camp: ss.getSheetByName(TABS.campaign), queue: ss.getSheetByName(TABS.queue), log: ss.getSheetByName(TABS.log)};
  if (!s.camp || !s.queue || !s.log) throw new Error('Sheet tabs Campaign/Queue/Log not found');
  return s;
}

function readSnapshot_(s) {
  var lastLog = s.log.getLastRow();
  var logRows = [];
  if (lastLog >= 2) {
    var first = Math.max(2, lastLog - LOG_ROWS + 1);
    logRows = s.log.getRange(first, 1, lastLog - first + 1, 3).getValues();
  }
  return {
    camp: s.camp.getRange(1, 1, 25, 2).getValues(),
    queue: s.queue.getRange(1, 1, QUEUE_LAST_ROW, QUEUE_COLS.length).getValues(),
    log: logRows
  };
}

/** The ONLY write path. Throws for any cell that is not in WRITABLE. */
function setCell_(s, key, value) {
  if (WRITABLE.indexOf(key) < 0 || !CELLS.hasOwnProperty(key)) throw new Error('write not allowed: ' + key);
  s.camp.getRange(CELLS[key], 2).setValue(value);
}

/* ---------- pure helpers ---------- */

function clean_(v, max) {
  return String(v === null || v === undefined ? '' : v).slice(0, max || 500);
}

function isTicked_(v) {
  return v === true || String(v).trim().toUpperCase() === 'TRUE';
}

function toMs_(v) {
  if (Object.prototype.toString.call(v) === '[object Date]') return isNaN(v.getTime()) ? null : v.getTime();
  if (typeof v !== 'string') return null;
  var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})Z$/.exec(v);
  return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
}

/** Same rule as engine.engine_status in campaign_lib/engine.py. */
function engineStatus_(cells, nowMs) {
  var hb = toMs_(cells.heartbeat);
  if (hb === null) return {live: false, text: 'engine offline (never started)'};
  if (nowMs - hb < ENGINE_STALE_MS) return {live: true, text: ''};
  return {live: false, text: 'engine offline since ' + fmt_(hb, 'HH:mm')};
}

/** Same rule as engine.token_warning in campaign_lib/engine.py: calendar days in UTC. */
function tokenWarning_(raw, nowMs) {
  var m = /^(\d{4})-(\d{2})-(\d{2})(?:T\d{2}:\d{2}:\d{2}Z)?$/.exec(String(raw === null || raw === undefined ? '' : raw).trim());
  if (!m) return '';
  var dayMs = Date.UTC(+m[1], +m[2] - 1, +m[3]);
  var endMs = dayMs + 86400000 - 1000;
  var withTime = /T/.test(String(raw));
  if (withTime) endMs = toMs_(String(raw).trim());
  var label = m[1] + '-' + m[2] + '-' + m[3];
  if (nowMs > endMs) return 'Instagram token EXPIRED - renew it before the next post';
  var n = new Date(nowMs);
  var days = Math.round((dayMs - Date.UTC(n.getUTCFullYear(), n.getUTCMonth(), n.getUTCDate())) / 86400000);
  if (days > TOKEN_WARN_DAYS) return '';
  return 'Instagram token expires ' + (days === 0 ? 'today' : 'in ' + days + ' day' + (days === 1 ? '' : 's')) + ' (' + label + ')';
}

function nowMs_() {
  return Date.now();
}

function fmt_(ms, pattern) {
  return Utilities.formatDate(new Date(ms), TZ, pattern);
}

function cellsOf_(camp) {
  var o = {};
  for (var k in CELLS) {
    var r = camp[CELLS[k] - 1];
    o[k] = r && r.length > 1 ? r[1] : '';
  }
  return o;
}

function itemsOf_(queue) {
  var items = [];
  for (var i = 1; i < queue.length; i++) {          // row 0 is the header
    var row = queue[i];
    if (row[0] === '' || row[0] === null) continue;
    var n = Number(row[0]);
    if (isNaN(n)) continue;
    var it = {};
    for (var c = 0; c < QUEUE_COLS.length; c++) it[QUEUE_COLS[c]] = row[c];
    it.n = n;
    items.push(it);
  }
  items.sort(function (a, b) { return a.n - b.n; });
  return items;
}

/** Same rule as campaign_lib/states.py head(): first item that is not POSTED/DONE/SKIPPED. */
function headOf_(items) {
  for (var i = 0; i < items.length; i++) {
    var st = String(items[i].state);
    if (st !== 'POSTED' && st !== 'DONE' && st !== 'SKIPPED') return items[i];
  }
  return null;
}

function validateLink_(raw) {
  var url = String(raw === null || raw === undefined ? '' : raw).trim();
  if (!url) return {ok: false, message: 'Paste a link first'};
  if (url.length > MAX_LINK_CHARS) return {ok: false, message: 'Link is longer than ' + MAX_LINK_CHARS + ' characters'};
  if (!LINK_RE.test(url)) return {ok: false, message: 'Only https twitch.tv or youtube.com links'};
  return {ok: true, url: url};
}

/** Everything the page shows, from a snapshot. Plain data only (no Date objects). */
function buildPanel_(snap, nowMs) {
  var cells = cellsOf_(snap.camp);
  var items = itemsOf_(snap.queue);
  var head = headOf_(items);
  var unlockMs = toMs_(cells.unlock_utc);
  var posted = null;
  for (var i = 0; i < items.length; i++) {
    var pm = toMs_(items[i].posted_at);
    if (pm !== null && (posted === null || pm > posted.postedMs)) posted = {item: items[i], postedMs: pm};
  }
  var postedOut = null;
  if (posted) {
    var link = clean_(posted.item.link || cells.link, 300);
    var sb = posted.postedMs + SUBMIT_WINDOW_MIN * 60000;
    postedOut = {
      n: posted.item.n,
      permalink: /^https:\/\//.test(link) ? link : '',
      submitBeforeMs: sb,
      submitBeforeText: fmt_(sb, 'HH:mm')
    };
  }
  var log = [];
  for (var j = snap.log.length - 1; j >= 0; j--) {
    var lr = snap.log[j];
    var lm = toMs_(lr[0]);
    log.push({t: lm === null ? clean_(lr[0], 30) : fmt_(lm, 'MM-dd HH:mm'), event: clean_(lr[1], 40), detail: clean_(lr[2], 200)});
  }
  return {
    serverNow: nowMs,
    updated: fmt_(nowMs, 'HH:mm'),
    name: clean_(cells.name, 120),
    stateText: clean_(cells.state, 200),
    engineText: engineStatus_(cells, nowMs).text,
    engineLive: engineStatus_(cells, nowMs).live,
    tokenWarning: tokenWarning_(cells.ig_expires, nowMs),
    nextText: clean_(cells.next, 200),
    head: head ? {n: head.n, state: clean_(head.state, 30), kind: clean_(head.kind, 20),
                  hook: clean_(head.hook, 200), caption: clean_(head.caption || cells.caption, 2200)} : null,
    unlockMs: unlockMs,
    approvePending: isTicked_(cells.approve),
    posted: postedOut,
    reachUrl: REACH_URL,
    canAdd: items.length > 0 && head === null && !isTicked_(cells.add_confirm),
    queue: items.map(function (it) {
      return {n: it.n, state: clean_(it.state, 30), kind: clean_(it.kind, 20), label: clean_(it.source, 300).slice(-48)};
    }),
    log: log
  };
}

/** Decide whether APPROVE may be written now. Pure. `n` is the clip number the person saw. */
function checkApprove_(p, n) {
  if (!p.head) return {ok: false, message: 'Nothing is waiting for approval.'};
  if (Number(n) !== p.head.n) return {ok: false, message: 'The queue moved on (clip #' + p.head.n + ' now). Check and tap again.'};
  if (p.approvePending) return {ok: false, message: 'Already approved. The engine posts on its next run.'};
  if (p.head.state !== 'AWAITING_APPROVAL') return {ok: false, message: 'Clip #' + p.head.n + ' is ' + p.head.state + ', not awaiting approval.'};
  if (p.unlockMs !== null && p.serverNow < p.unlockMs) return {ok: false, message: 'APPROVE is not open yet.'};
  return {ok: true};
}

function withLock_(fn) {
  var lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return {ok: false, message: 'Busy, try again in a moment.'};
  try { return fn(); } finally { lock.releaseLock(); }
}

/* ---------- called from the page (google.script.run) ---------- */

function getPanel() {
  return buildPanel_(readSnapshot_(openSheets_()), nowMs_());
}

function approveClip(n) {
  return withLock_(function () {
    var s = openSheets_();
    var p = buildPanel_(readSnapshot_(s), nowMs_());   // re-read under the lock: stale and double taps stop here
    var chk = checkApprove_(p, n);
    if (!chk.ok) return {ok: false, message: chk.message, panel: p};
    setCell_(s, 'approve', true);
    SpreadsheetApp.flush();
    return {ok: true, message: 'Approved. The engine posts on its next run.', panel: buildPanel_(readSnapshot_(s), nowMs_())};
  });
}

function addLink(raw) {
  var v = validateLink_(raw);
  if (!v.ok) return {ok: false, message: v.message};
  return withLock_(function () {
    var s = openSheets_();
    var p = buildPanel_(readSnapshot_(s), nowMs_());
    if (!p.canAdd) return {ok: false, message: 'A link can only be added when every clip is used.', panel: p};
    setCell_(s, 'add_link', v.url);          // same two cells the sheet uses: link, then confirm tick
    setCell_(s, 'add_confirm', true);
    SpreadsheetApp.flush();
    return {ok: true, message: 'Link added. The engine queues it on its next run.', panel: buildPanel_(readSnapshot_(s), nowMs_())};
  });
}
