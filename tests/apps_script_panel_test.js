// Node test for the Apps Script control panel (no dependencies): node tests/apps_script_panel_test.js
// Loads the pure logic block of Index.html and the whole of Code.gs (with fake Google services).
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const DIR = path.join(__dirname, '..', 'docs', 'apps_script', 'lauren');
const html = fs.readFileSync(path.join(DIR, 'Index.html'), 'utf8');
const gs = fs.readFileSync(path.join(DIR, 'Code.gs'), 'utf8');

let failed = 0;
function test(name, fn) {
  try { fn(); console.log('ok   - ' + name); } catch (e) { failed++; console.log('FAIL - ' + name + '\n' + (e && e.stack || e)); }
}

/* ---------- Index.html logic ---------- */
const block = /\/\/ LOGIC_BEGIN[^\n]*\n([\s\S]*?)\/\/ LOGIC_END/.exec(html);
assert(block, 'LOGIC block markers missing in Index.html');
const L = {};
vm.runInNewContext(block[1], L);

const MIN = 60000;
const panelOf = (state, unlockMs, extra) => Object.assign({head: {n: 1, state: state}, unlockMs: unlockMs, approvePending: false}, extra || {});
const NOW = Date.UTC(2026, 10, 22, 12, 0, 0);

test('countdown format', () => {
  assert.strictEqual(L.fmtCountdown(72 * MIN), '1h 12m');
  assert.strictEqual(L.fmtCountdown(60 * MIN), '1h 00m');
  assert.strictEqual(L.fmtCountdown(65 * MIN + 5000), '1h 05m');
  assert.strictEqual(L.fmtCountdown(5 * MIN + 3000), '5m 03s');
  assert.strictEqual(L.fmtCountdown(45000), '45s');
  assert.strictEqual(L.fmtCountdown(0), '0s');
  assert.strictEqual(L.fmtCountdown(-5000), '0s');
});

test('APPROVE enabled only for AWAITING_APPROVAL with the approve time passed', () => {
  ['QUEUED', 'EDITING', 'READY', 'AWAITING_APPROVAL', 'POSTING', 'POSTED', 'DONE'].forEach((st) => {
    [null, NOW - MIN, NOW].forEach((unlock) => {
      assert.strictEqual(L.approveButton(panelOf(st, unlock), NOW).enabled, st === 'AWAITING_APPROVAL', st + ' unlock=' + unlock);
    });
    assert.strictEqual(L.approveButton(panelOf(st, NOW + MIN), NOW).enabled, false, st + ' before unlock');
  });
});

test('disabled APPROVE shows a live countdown', () => {
  const b = L.approveButton(panelOf('READY', NOW + 72 * MIN), NOW);
  assert.strictEqual(b.enabled, false);
  assert.strictEqual(b.note, 'opens in 1h 12m');
  assert.strictEqual(L.approveButton(panelOf('READY', NOW + 72 * MIN), NOW + 12 * MIN).note, 'opens in 1h 00m');
});

test('APPROVE: pending tick, no head, posting, not ready', () => {
  assert.strictEqual(L.approveButton(panelOf('AWAITING_APPROVAL', null, {approvePending: true}), NOW).enabled, false);
  assert.strictEqual(L.approveButton({head: null, unlockMs: null, approvePending: false}, NOW).enabled, false);
  assert.strictEqual(L.approveButton(null, NOW).enabled, false);
  assert.ok(/Posting/.test(L.approveButton(panelOf('POSTING', null), NOW).note));
  assert.ok(/not ready yet \(EDITING\)/.test(L.approveButton(panelOf('EDITING', NOW - MIN), NOW).note));
  assert.strictEqual(L.approveButton(panelOf('AWAITING_APPROVAL', null), NOW).label, 'APPROVE clip #1');
});

test('submit countdown: posted + 30 min, red under 5 minutes', () => {
  const posted = {submitBeforeMs: NOW + 30 * MIN, submitBeforeText: '17:30'};
  let s = L.submitInfo(posted, NOW);
  assert.strictEqual(s.text, 'submit to Reach before 17:30');
  assert.strictEqual(s.left, '30m 00s');
  assert.strictEqual(s.urgent, false);
  assert.strictEqual(L.submitInfo(posted, NOW + 25 * MIN).urgent, false);          // exactly 5:00 left
  s = L.submitInfo(posted, NOW + 25 * MIN + 1000);                                  // 4:59 left
  assert.strictEqual(s.urgent, true);
  assert.strictEqual(s.left, '4m 59s');
  s = L.submitInfo(posted, NOW + 30 * MIN);
  assert.strictEqual(s.closed, true);
  assert.ok(/closed/.test(s.text));
  assert.strictEqual(L.submitInfo(null, NOW), null);
});

test('link validation (client)', () => {
  checkLinks(L.validateLink);
});

function checkLinks(fn) {
  const good = ['https://twitch.tv/videos/123', 'https://www.twitch.tv/x/clip/Abc-d', 'https://m.twitch.tv/x/clip/Abc',
    'https://youtube.com/watch?v=abc', 'https://www.youtube.com/live/xyz', 'https://m.youtube.com/watch?v=1', '  https://twitch.tv/videos/9  ',
    'https://twitch.tv/' + 'a'.repeat(300 - 'https://twitch.tv/'.length)];
  good.forEach((u) => assert.strictEqual(fn(u).ok, true, u));
  const bad = ['', '   ', 'http://twitch.tv/videos/1', 'twitch.tv/videos/1', 'https://evil.com/twitch.tv', 'https://twitch.tv.evil.com/x',
    'https://twitch.tv@evil.com/x', 'https://twitch.tv:8080/x', 'javascript:alert(1)', 'https://twitch.tv/a b', 'https://twitch.tv/a\nb',
    'https://notyoutube.com/x', 'https://twitch.tv/' + 'a'.repeat(301 - 'https://twitch.tv/'.length), null, undefined];
  bad.forEach((u) => assert.strictEqual(fn(u).ok, false, String(u)));
  assert.strictEqual(fn('  https://twitch.tv/videos/9  ').url, 'https://twitch.tv/videos/9');
}

/* ---------- Code.gs against a fake spreadsheet ---------- */
class FakeSheet {
  constructor(name, rows, cols, log) {
    this.name = name; this.log = log; this.data = rows.map((r) => r.concat(Array(Math.max(0, cols - r.length)).fill('')));
  }
  getLastRow() {
    for (let i = this.data.length - 1; i >= 0; i--) if (this.data[i].some((v) => v !== '')) return i + 1;
    return 0;
  }
  getRange(r, c, nr, nc) {
    const self = this;
    if (nr === undefined) {
      return {setValue(v) { self.log.push({sheet: self.name, row: r, col: c, value: v}); while (self.data.length < r) self.data.push([]); self.data[r - 1][c - 1] = v; }};
    }
    return {getValues() {
      const out = [];
      for (let i = 0; i < nr; i++) { const row = self.data[r - 1 + i] || []; out.push(Array.from({length: nc}, (_, j) => (row[c - 1 + j] === undefined ? '' : row[c - 1 + j]))); }
      return out;
    }};
  }
}

function formatDate(d, tz, pattern) {
  const f = new Intl.DateTimeFormat('en-GB', {timeZone: tz, hour12: false, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit'});
  const p = {}; f.formatToParts(d).forEach((x) => { p[x.type] = x.value; });
  const hh = p.hour === '24' ? '00' : p.hour;
  if (pattern === 'HH:mm') return hh + ':' + p.minute;
  if (pattern === 'MM-dd HH:mm') return p.month + '-' + p.day + ' ' + hh + ':' + p.minute;
  throw new Error('pattern not faked: ' + pattern);
}

const ISO = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const HEAD = ['#', 'Source', 'Kind', 'State', 'Moment', 'Hook', 'Caption', 'Note', 'Posted at (UTC)', 'Media id', 'Reel link', 'Submit before', 'Updated'];

function boot(opts) {
  const log = [];
  const camp = Array.from({length: 25}, () => ['', '']);
  const set = (r, v) => { camp[r - 1][1] = v; };
  set(3, 'Lauren Hamden GTA 6 Clipping Campaign'); set(4, opts.state || 'READY'); set(5, '#1 clip (src)'); set(6, 'cap cell');
  set(7, opts.approve === undefined ? false : opts.approve); set(13, opts.confirm === undefined ? false : opts.confirm);
  set(10, opts.linkCell || ''); set(16, opts.unlock || '');
  set(18, opts.lock || ''); set(19, opts.heartbeat || ''); set(20, opts.igExpires || '');
  const queue = [HEAD].concat(opts.items);
  const sheets = {
    Campaign: new FakeSheet('Campaign', camp, 2, log),
    Queue: new FakeSheet('Queue', queue, 13, log),
    Log: new FakeSheet('Log', [['Time (UTC)', 'Event', 'Detail']].concat(opts.log || []), 3, log)
  };
  const opened = [];
  const ctx = {
    SpreadsheetApp: {openById(id) { opened.push(id); return {getSheetByName: (n) => sheets[n] || null}; }, flush() {}},
    LockService: {getScriptLock: () => ({tryLock: () => !opts.busy, releaseLock() {}})},
    Utilities: {formatDate},
    HtmlService: {}
  };
  vm.runInNewContext(gs, ctx);
  ctx.nowMs_ = () => NOW;
  return {ctx, log, sheets, opened};
}

// A Queue row: #, Source, Kind, State, Moment, Hook, Caption, Note, Posted at, Media id, Reel link, Submit before, Updated.
// `extra` overrides columns by index (holes keep the default).
function fill(row, extra) { (extra || []).forEach((v, i) => { if (v !== undefined && v !== null) row[i] = v; }); return row; }
const it = (n, state, extra) => fill([n, 'https://twitch.tv/x/clip/' + n, 'clip', state, '0-30', 'hook' + n, 'caption ' + n, '', '', '', '', '', ''], extra);

test('uses openById with the sheet id, never getActiveSpreadsheet', () => {
  const b = boot({items: [it(1, 'READY')]});
  b.ctx.getPanel();
  assert.deepStrictEqual(b.opened, ['1VYtK6mm40NXyB6ixLRoMtsc5yDxFXX9u_FUCcLGLvh8']);
});

test('getPanel: head, countdown data, queue, log (newest first), Karachi time', () => {
  const b = boot({unlock: ISO(NOW + 72 * MIN), items: [it(1, 'READY'), it(2, 'QUEUED')],
    log: [[ISO(NOW - 3 * MIN), 'AWAIT', 'clip #1'], [ISO(NOW - 1 * MIN), 'FAKE_EDIT', 'clip #2 READY']]});
  const p = b.ctx.getPanel();
  assert.strictEqual(p.head.n, 1);
  assert.strictEqual(p.head.state, 'READY');
  assert.strictEqual(p.unlockMs, NOW + 72 * MIN);
  assert.strictEqual(p.updated, '17:00');                       // 12:00 UTC = 17:00 Asia/Karachi
  assert.strictEqual(p.log[0].event, 'FAKE_EDIT');
  assert.strictEqual(p.log.length, 2);
  assert.strictEqual(p.queue.length, 2);
  assert.strictEqual(p.canAdd, false);
  assert.strictEqual(p.reachUrl, 'https://reachclipping.com/Sisters');
  assert.strictEqual(JSON.stringify(p).includes('Invalid Date'), false);
});

test('log shows only the last 10 rows', () => {
  const rows = Array.from({length: 25}, (_, i) => [ISO(NOW - (25 - i) * MIN), 'E' + i, 'd']);
  const p = boot({items: [it(1, 'READY')], log: rows}).ctx.getPanel();
  assert.strictEqual(p.log.length, 10);
  assert.strictEqual(p.log[0].event, 'E24');
  assert.strictEqual(p.log[9].event, 'E15');
});

test('posted block: permalink and submit-before = posted + 30 min (Karachi HH:mm)', () => {
  const posted = ISO(NOW - 10 * MIN);
  const b = boot({items: [it(1, 'POSTED', [,,,, ,,,, posted, 'FAKE-1', 'https://www.instagram.com/reel/FAKE1/']), it(2, 'READY')]});
  const p = b.ctx.getPanel();
  assert.strictEqual(p.posted.n, 1);
  assert.strictEqual(p.posted.permalink, 'https://www.instagram.com/reel/FAKE1/');
  assert.strictEqual(p.posted.submitBeforeMs, NOW - 10 * MIN + 30 * MIN);
  assert.strictEqual(p.posted.submitBeforeText, '17:20');
  assert.strictEqual(p.head.n, 2);
});

test('non-https permalink is dropped', () => {
  const b = boot({items: [it(1, 'POSTED', [,,,, ,,,, ISO(NOW - MIN), 'm', 'javascript:alert(1)'])]});
  assert.strictEqual(b.ctx.getPanel().posted.permalink, '');
});

test('sheet text is returned as plain strings (the page renders with textContent only)', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const b = boot({items: [fill(it(1, 'READY'), [,,,, ,evil, evil])]});
  const p = b.ctx.getPanel();
  assert.strictEqual(p.head.caption, evil);
  assert.strictEqual(typeof p.head.caption, 'string');
});

test('APPROVE: writes exactly B7=true, only when AWAITING_APPROVAL and unlocked', () => {
  const b = boot({unlock: ISO(NOW - MIN), items: [it(1, 'AWAITING_APPROVAL')]});
  const r = b.ctx.approveClip(1);
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(b.log, [{sheet: 'Campaign', row: 7, col: 2, value: true}]);
});

test('APPROVE: works with no unlock time (first clip)', () => {
  const b = boot({items: [it(1, 'AWAITING_APPROVAL')]});
  assert.strictEqual(b.ctx.approveClip(1).ok, true);
  assert.strictEqual(b.log.length, 1);
});

test('APPROVE: stale and double taps write nothing', () => {
  let b = boot({items: [it(1, 'READY')]});
  assert.strictEqual(b.ctx.approveClip(1).ok, false);                         // not awaiting
  b = boot({items: [it(1, 'POSTED', [,,,, ,,,, ISO(NOW - MIN), 'm', 'https://x.y/']), it(2, 'AWAITING_APPROVAL')]});
  assert.strictEqual(b.ctx.approveClip(1).ok, false);                         // page showed clip 1, queue moved to 2
  b = boot({unlock: ISO(NOW + MIN), items: [it(1, 'AWAITING_APPROVAL')]});
  assert.strictEqual(b.ctx.approveClip(1).ok, false);                         // time not passed
  b = boot({items: [it(1, 'AWAITING_APPROVAL')], approve: true});
  const r = b.ctx.approveClip(1);
  assert.strictEqual(r.ok, false);                                            // already ticked
  assert.ok(/Already approved/.test(r.message));
  b = boot({items: [it(1, 'AWAITING_APPROVAL')]});
  assert.strictEqual(b.ctx.approveClip(1).ok, true);
  assert.strictEqual(b.ctx.approveClip(1).ok, false);                         // second tap after the first wrote
  assert.strictEqual(b.log.length, 1);
  b = boot({items: [fill(it(1, 'DONE'))]});
  assert.strictEqual(b.ctx.approveClip(1).ok, false);
  b = boot({items: [it(1, 'AWAITING_APPROVAL')], busy: true});
  assert.strictEqual(b.ctx.approveClip(1).ok, false);                         // lock busy
  assert.strictEqual(b.log.length, 0);
});

test('ADD LINK: only when every clip is used; writes only the link and confirm cells', () => {
  const used = [it(1, 'POSTED', [,,,, ,,,, ISO(NOW - 60 * MIN), 'm', 'https://x.y/']), it(2, 'DONE')];
  const b = boot({items: used});
  assert.strictEqual(b.ctx.getPanel().canAdd, true);
  const r = b.ctx.addLink('https://twitch.tv/videos/555');
  assert.strictEqual(r.ok, true);
  assert.deepStrictEqual(b.log, [
    {sheet: 'Campaign', row: 12, col: 2, value: 'https://twitch.tv/videos/555'},
    {sheet: 'Campaign', row: 13, col: 2, value: true}]);
});

test('ADD LINK: rejected while a clip is unused, invalid link, or a confirm is pending', () => {
  let b = boot({items: [it(1, 'READY')]});
  assert.strictEqual(b.ctx.getPanel().canAdd, false);
  assert.strictEqual(b.ctx.addLink('https://twitch.tv/videos/1').ok, false);
  const used = [it(1, 'DONE')];
  b = boot({items: used});
  ['http://twitch.tv/videos/1', 'https://evil.com/x', '', 'https://twitch.tv/' + 'a'.repeat(300)].forEach((u) => assert.strictEqual(b.ctx.addLink(u).ok, false, u));
  b = boot({items: used, confirm: true});
  assert.strictEqual(b.ctx.getPanel().canAdd, false);
  assert.strictEqual(b.ctx.addLink('https://twitch.tv/videos/1').ok, false);
  b = boot({items: []});
  assert.strictEqual(b.ctx.getPanel().canAdd, false);                          // empty sheet: the engine would reject too
  assert.strictEqual(b.log.length, 0);
});

test('link validation (server) matches the client', () => {
  const b = boot({items: [it(1, 'DONE')]});
  checkLinks((u) => b.ctx.validateLink_(u));
});

test('setCell_ only writes the 3 allowed cells', () => {
  const b = boot({items: [it(1, 'READY')]});
  const s = b.ctx.openSheets_();
  ['approve', 'add_link', 'add_confirm'].forEach((k) => b.ctx.setCell_(s, k, true));
  assert.strictEqual(b.log.length, 3);
  ['name', 'state', 'next', 'caption', 'timer', 'opens', 'link', 'submit', 'updated', 'unlock_utc', 'log_rows', 'lock_run', 'heartbeat', 'ig_expires', 'handoff', 'loop_start', 'nope', '__proto__', 'hasOwnProperty'].forEach((k) => {
    assert.throws(() => b.ctx.setCell_(s, k, 'x'), /not allowed/, k);
  });
  assert.strictEqual(b.log.length, 3);
});

test('engine banner: offline since HH:mm (Karachi) when the heartbeat is 5 minutes old or more', () => {
  const live = boot({items: [it(1, 'READY')], lock: 'R1', heartbeat: ISO(NOW - 299000)}).ctx.getPanel();
  assert.strictEqual(live.engineLive, true);
  assert.strictEqual(live.engineText, '');
  const dead = boot({items: [it(1, 'READY')], lock: 'R1', heartbeat: ISO(NOW - 6 * MIN)}).ctx.getPanel();
  assert.strictEqual(dead.engineLive, false);
  assert.strictEqual(dead.engineText, 'engine offline since 16:54');   // 11:54 UTC = 16:54 Karachi
  assert.strictEqual(boot({items: [it(1, 'READY')], heartbeat: ISO(NOW - 5 * MIN)}).ctx.getPanel().engineLive, false);
  assert.ok(/never started/.test(boot({items: [it(1, 'READY')]}).ctx.getPanel().engineText));
});

test('token warning: quiet far away, warns from 7 days before, EXPIRED after', () => {
  const day = (d) => new Date(NOW + d * 86400000).toISOString().slice(0, 10);
  const w = (v) => boot({items: [it(1, 'READY')], igExpires: v}).ctx.getPanel().tokenWarning;
  assert.strictEqual(w(''), '');
  assert.strictEqual(w('soon'), '');
  assert.strictEqual(w(day(8)), '');
  assert.ok(w(day(7)).indexOf('in 7 days') >= 0, w(day(7)));
  assert.ok(w(day(1)).indexOf('in 1 day (') >= 0, w(day(1)));
  assert.ok(w(day(0)).indexOf('today') >= 0, w(day(0)));
  assert.ok(w(day(-1)).indexOf('EXPIRED') >= 0, w(day(-1)));
});

if (failed) { console.log(failed + ' test(s) failed'); process.exit(1); }
console.log('all apps script panel tests passed');
