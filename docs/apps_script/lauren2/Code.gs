/**
 * Clip review page (Apps Script web app). Drive folder "ClipPool" = clips waiting for your decision.
 * Rounds: clips come into the page only when the timer is open (150 min after the last post).
 * Approve (one per round) -> wait for the link -> Copy link -> the clip is trashed. Keep = hidden until the
 * next round. Delete = trash. New passing clips are copied in from the GitHub release "pool" (public, no secrets).
 */
var SHEET_ID = '1VYtK6mm40NXyB6ixLRoMtsc5yDxFXX9u_FUCcLGLvh8';
var POOL_NAME = 'ClipPool';
var CAMP = 'Campaign', REVIEW = 'Review';
var ROW_APPROVE = 7, ROW_LINK = 10, ROW_UNLOCK = 16;
var GAP_MS = 150 * 60000;
var REPO = 'fboy-engine/clip-engine';
var LINK_RE = /^https:\/\/(www\.)?instagram\.com\/\S+$/i;
var CH = 3 * 1024 * 1024;

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index').setTitle('Clips')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}
function pool_() { var it = DriveApp.getFoldersByName(POOL_NAME); return it.hasNext() ? it.next() : DriveApp.createFolder(POOL_NAME); }
function props_() { return PropertiesService.getScriptProperties(); }
function camp_() { return SpreadsheetApp.openById(SHEET_ID).getSheetByName(CAMP); }
function cell_(row) { return camp_().getRange(row, 2).getValue(); }
function num_(k) { return Number(props_().getProperty(k) || 0); }
function inPool_(id) {
  var f = DriveApp.getFileById(id), ps = f.getParents(), pid = pool_().getId();
  while (ps.hasNext()) if (ps.next().getId() === pid) return f;
  throw new Error('Not a pool clip');
}
function videos_() {
  var out = [], it = pool_().getFiles();
  while (it.hasNext()) { var f = it.next(); if (String(f.getMimeType()).indexOf('video/') === 0) out.push(f); }
  return out;
}
function unlockMs_() {
  var s = Date.parse(String(cell_(ROW_UNLOCK)));
  return Math.max(num_('NEXT_OK'), isNaN(s) ? 0 : s);
}
function heldTs_(d) { var m = /^held:(\d+)$/.exec(d); return m ? Number(m[1]) : (d === 'held' ? 1 : -1); }

function markReview_(id, name, state, link) {
  var ss = SpreadsheetApp.openById(SHEET_ID), sh = ss.getSheetByName(REVIEW);
  if (!sh) { sh = ss.insertSheet(REVIEW); sh.appendRow(['file_id', 'name', 'state', 'link']); }
  var v = sh.getDataRange().getValues();
  for (var i = 1; i < v.length; i++) if (v[i][0] === id) { sh.getRange(i + 1, 3, 1, 2).setValues([[state, link || '']]); return; }
  sh.appendRow([id, name || '', state, link || '']);
}
function withLock_(fn) {
  var l = LockService.getScriptLock();
  if (!l.tryLock(20000)) return {ok: false, message: 'Busy, try again.'};
  try { return fn(); } finally { l.releaseLock(); }
}

/** Copy new passing clips from the GitHub release "pool" into ClipPool (called by the page every few minutes). */
function syncPool() {
  return withLock_(function () {
    var p = props_(), n = 0;
    try {
      var r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/releases/tags/pool', {muteHttpExceptions: true, headers: {Accept: 'application/vnd.github+json'}});
      if (r.getResponseCode() !== 200) return {ok: true, added: 0};
      var assets = (JSON.parse(r.getContentText()).assets || []).sort(function (a, b) { return a.created_at < b.created_at ? -1 : 1; });
      var done = JSON.parse(p.getProperty('IMPORTED') || '[]');
      for (var i = 0; i < assets.length && n < 3; i++) {
        var a = assets[i];
        if (done.indexOf(a.id) >= 0 || !/\.mp4$/i.test(a.name)) continue;
        var res = UrlFetchApp.fetch(a.browser_download_url, {muteHttpExceptions: true});
        if (res.getResponseCode() !== 200) continue;
        pool_().createFile(res.getBlob().setName(a.name).setContentType('video/mp4'));
        done.push(a.id); n++;
      }
      p.setProperty('IMPORTED', JSON.stringify(done.slice(-400)));
    } catch (e) {}
    return {ok: true, added: n};
  });
}

function getState() {
  var p = props_(), now = Date.now(), appr = p.getProperty('APPROVED_ID');
  if (appr) {
    var link = String(cell_(ROW_LINK) || '').trim();
    if (LINK_RE.test(link) && link !== p.getProperty('BASE_LINK')) {          // posted: the new link has arrived
      try { DriveApp.getFileById(appr).setTrashed(true); } catch (e) {}
      p.setProperty('LAST_LINK', link); p.deleteProperty('APPROVED_ID');
      p.setProperty('LAST_POST', String(now)); p.setProperty('NEXT_OK', String(now + GAP_MS));
      markReview_(appr, '', 'POSTED', link);
      appr = null;
    }
  }
  var unlock = unlockMs_(), open = now >= unlock;
  var last = p.getProperty('LAST_LINK');
  if (last) return {phase: 'link', link: last, clips: [], now: now, unlock: unlock};
  if (appr) return {phase: 'waiting', clips: [], now: now, unlock: unlock};
  var files = videos_(), shown = [], hidden = 0, lastPost = num_('LAST_POST');
  if (open && !files.some(function (f) { return f.getDescription() === 'shown'; })) {   // new round: bring in new + kept clips
    files.forEach(function (f) {
      var d = f.getDescription() || '', h = heldTs_(d);
      if (d === '' || (h >= 0 && h < lastPost)) f.setDescription('shown');
    });
  }
  files.forEach(function (f) {
    var d = f.getDescription() || '';
    if (d === 'shown') shown.push({id: f.getId(), name: f.getName().replace(/\.[^.]+$/, ''), ts: f.getDateCreated().getTime()});
    else if (d === '' || heldTs_(d) >= 0) hidden++;
  });
  shown.sort(function (a, b) { return b.ts - a.ts; });
  return {phase: shown.length ? 'list' : 'idle', clips: shown.map(function (c) { return {id: c.id, name: c.name}; }),
          open: open, unlock: unlock, now: now, saved: hidden};
}

function decide(id, action) {
  return withLock_(function () {
    var p = props_(), st = getState();
    if (st.phase !== 'list') return {ok: false, message: 'Nothing to decide right now.', state: st};
    var f = inPool_(id);
    if (f.getDescription() !== 'shown') return {ok: false, message: 'That clip already has a decision.', state: getState()};
    if (action === 'delete') f.setTrashed(true);
    else if (action === 'keep') f.setDescription('held:' + Date.now());
    else if (action === 'approve') {
      if (!st.open) return {ok: false, message: 'Approve is not open yet.', state: st};
      p.setProperty('BASE_LINK', String(cell_(ROW_LINK) || '').trim());
      p.setProperty('APPROVED_ID', id);
      f.setDescription('approved');
      markReview_(id, f.getName(), 'APPROVED', '');
      camp_().getRange(ROW_APPROVE, 2).setValue(true);
      SpreadsheetApp.flush();
    } else return {ok: false, message: 'Unknown action', state: st};
    return {ok: true, state: getState()};
  });
}

function dismissLink() { props_().deleteProperty('LAST_LINK'); return getState(); }

function clipInfo(id) { return {size: inPool_(id).getSize(), ch: CH}; }
function clipChunk(id, start, end) {
  inPool_(id);
  if (end - start >= CH) throw new Error('range too big');
  var r = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(id) + '?alt=media', {
    headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken(), Range: 'bytes=' + start + '-' + end}, muteHttpExceptions: true});
  if (r.getResponseCode() >= 300) throw new Error('read failed ' + r.getResponseCode());
  return Utilities.base64Encode(r.getContent());
}
