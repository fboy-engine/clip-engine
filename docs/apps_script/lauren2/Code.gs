/**
 * Clip review page (Apps Script web app). Drive folder "ClipPool" = the clips waiting for your decision.
 * Per clip: Approve / Keep / Delete. Approve also ticks the existing APPROVE cell (Campaign!B7) and adds a row
 * to a "Review" tab (file id, name, state, link) so the engine can pick that exact file.
 * Sheet writes: only B7 (approve) and the Review tab. Drive: reads/trashes files inside ClipPool only.
 */
var SHEET_ID = '1VYtK6mm40NXyB6ixLRoMtsc5yDxFXX9u_FUCcLGLvh8';
var POOL_NAME = 'ClipPool';
var CAMP = 'Campaign', REVIEW = 'Review';
var ROW_APPROVE = 7, ROW_LINK = 10, ROW_UNLOCK = 16;
var LINK_RE = /^https:\/\/(www\.)?instagram\.com\/\S+$/i;

function doGet() {
  return HtmlService.createHtmlOutputFromFile('Index').setTitle('Clips')
    .addMetaTag('viewport', 'width=device-width, initial-scale=1, viewport-fit=cover');
}

function pool_() {
  var it = DriveApp.getFoldersByName(POOL_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(POOL_NAME);
}
function props_() { return PropertiesService.getScriptProperties(); }
function camp_() { return SpreadsheetApp.openById(SHEET_ID).getSheetByName(CAMP); }
function cell_(row) { return camp_().getRange(row, 2).getValue(); }

function inPool_(id) {
  var f = DriveApp.getFileById(id), ps = f.getParents();
  while (ps.hasNext()) if (ps.next().getId() === pool_().getId()) return f;
  throw new Error('Not a pool clip');
}
function clips_() {
  var out = [], it = pool_().getFiles();
  while (it.hasNext()) {
    var f = it.next();
    if (String(f.getMimeType()).indexOf('video/') !== 0) continue;
    var d = f.getDescription() || '';
    if (d === 'approved') continue;
    out.push({id: f.getId(), name: f.getName().replace(/\.[^.]+$/, ''), held: d === 'held', ts: f.getDateCreated().getTime()});
  }
  out.sort(function (a, b) { return (b.held - a.held) || (b.ts - a.ts); });
  return out.map(function (c) { return {id: c.id, name: c.name, held: c.held}; });
}

function getState() {
  var p = props_(), appr = p.getProperty('APPROVED_ID');
  if (appr) {
    var link = String(cell_(ROW_LINK) || '').trim();
    if (LINK_RE.test(link) && link !== p.getProperty('BASE_LINK')) {      // posted: new link has arrived
      try { DriveApp.getFileById(appr).setTrashed(true); } catch (e) {}
      p.setProperty('LAST_LINK', link); p.deleteProperty('APPROVED_ID');
      markReview_(appr, 'POSTED', link);
      appr = null;
    }
  }
  var last = p.getProperty('LAST_LINK');
  if (last) return {phase: 'link', link: last, clips: []};
  if (appr) return {phase: 'waiting', clips: []};
  return {phase: 'list', clips: clips_()};
}

function markReview_(id, state, link) {
  var ss = SpreadsheetApp.openById(SHEET_ID), sh = ss.getSheetByName(REVIEW);
  if (!sh) { sh = ss.insertSheet(REVIEW); sh.appendRow(['file_id', 'name', 'state', 'link']); }
  var v = sh.getDataRange().getValues();
  for (var i = 1; i < v.length; i++) if (v[i][0] === id) { sh.getRange(i + 1, 3, 1, 2).setValues([[state, link || '']]); return; }
  sh.appendRow([id, '', state, link || '']);
}

function withLock_(fn) {
  var l = LockService.getScriptLock();
  if (!l.tryLock(10000)) return {ok: false, message: 'Busy, try again.'};
  try { return fn(); } finally { l.releaseLock(); }
}

function decide(id, action) {
  return withLock_(function () {
    var p = props_(), st = getState();
    if (st.phase !== 'list') return {ok: false, message: 'Finish the current clip first.', state: st};
    var f = inPool_(id);
    if (action === 'delete') f.setTrashed(true);
    else if (action === 'keep') f.setDescription('held');
    else if (action === 'approve') {
      var unlock = Date.parse(String(cell_(ROW_UNLOCK)));
      if (!isNaN(unlock) && Date.now() < unlock) return {ok: false, message: 'Approve is not open yet.', state: st};
      p.setProperty('BASE_LINK', String(cell_(ROW_LINK) || '').trim());
      p.setProperty('APPROVED_ID', id);
      f.setDescription('approved');
      var ss = SpreadsheetApp.openById(SHEET_ID), sh = ss.getSheetByName(REVIEW);
      if (!sh) { sh = ss.insertSheet(REVIEW); sh.appendRow(['file_id', 'name', 'state', 'link']); }
      sh.appendRow([id, f.getName(), 'APPROVED', '']);
      camp_().getRange(ROW_APPROVE, 2).setValue(true);
      SpreadsheetApp.flush();
    } else return {ok: false, message: 'Unknown action'};
    return {ok: true, state: getState()};
  });
}

var CH = 3 * 1024 * 1024;
function clipInfo(id) { return {size: inPool_(id).getSize(), ch: CH}; }
/** One byte range of a pool clip, base64 (the page rebuilds the video from these; no Drive iframe, no cookies). */
function clipChunk(id, start, end) {
  inPool_(id);
  if (end - start >= CH) throw new Error('range too big');
  var r = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/files/' + encodeURIComponent(id) + '?alt=media', {
    headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken(), Range: 'bytes=' + start + '-' + end}, muteHttpExceptions: true});
  if (r.getResponseCode() >= 300) throw new Error('read failed ' + r.getResponseCode());
  return Utilities.base64Encode(r.getContent());
}

function dismissLink() {
  props_().deleteProperty('LAST_LINK');
  return getState();
}
