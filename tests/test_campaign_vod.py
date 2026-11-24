"""Stream (VOD) source finder: only "Grand Theft Auto VI" chapters, find run -> best candidate -> render run,
a window with nothing usable moves on, an exhausted stream is SKIPPED and never blocks the queue. Fake GitHub."""
import io
import json
import unittest
import zipfile
from datetime import timedelta

from campaign_lib import editor, service, states, vod
from campaign_lib.ghclient import GitHubError
from campaign_lib.states import Item
from tests.test_campaign_editor import CFG, GOOD, ISO, NOW, FakeGH, make_zip, probe, run

URL = [s for s in CFG["sources"] if s["kind"] == "vod"][0]["url"]
CH = [{"title": "Just Chatting", "start_time": 0, "end_time": 300},
      {"title": "Grand Theft Auto VI", "start_time": 300, "end_time": 1000},
      {"title": "Just Chatting", "start_time": 1000, "end_time": 1500},
      {"title": "grand theft auto  vi", "start_time": 1500, "end_time": 1700}]


def find_zip(cands):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("out/candidates.json", json.dumps({"offset": 300, "candidates": cands}))
    return zipfile.ZipFile(io.BytesIO(b.getvalue()))


def cand(start, end, ok=True, stream_start=None):
    return {"id": 1, "start": start, "end": end, "gate_ok": ok, "stream_start": stream_start or 300 + start,
            "stream_end": (stream_start or 300 + start) + (end - start)}


def vod_item():
    return Item(n=10, source=URL, kind="vod")


class Windows(unittest.TestCase):
    def test_only_the_game_chapters_and_in_time_order(self):
        w = vod.gta_windows(CH)
        self.assertEqual(w, [(300.0, 900.0), (1500.0, 1700.0)])  # the 100 s tail of the first chapter is dropped
        for s, e in w:  # nothing outside a game chapter
            self.assertTrue(any(c["title"].lower().split() == ["grand", "theft", "auto", "vi"]
                                and c["start_time"] <= s and e <= c["end_time"] for c in CH))

    def test_a_long_chapter_is_cut_in_find_sized_windows_and_a_short_tail_is_dropped(self):
        w = vod.gta_windows([{"title": "Grand Theft Auto VI", "start_time": 0, "end_time": 1500}])
        self.assertEqual(w, [(0.0, 600.0), (600.0, 1200.0), (1200.0, 1500.0)])
        self.assertEqual(vod.gta_windows([{"title": "Grand Theft Auto VI", "start_time": 0, "end_time": 100}]), [])

    def test_other_games_and_no_chapters_give_nothing(self):
        self.assertEqual(vod.gta_windows([{"title": "Grand Theft Auto V", "start_time": 0, "end_time": 900},
                                          {"title": "GTA VI", "start_time": 900, "end_time": 2000}]), [])
        self.assertEqual(vod.gta_windows([]), [])
        self.assertEqual(vod.gta_windows(None), [])

    def test_best_candidate_needs_the_engines_gate_and_is_cut_to_the_span(self):
        data = {"candidates": [cand(10, 80, ok=False), cand(100, 160), cand(200, 230)]}
        self.assertEqual(vod.best_candidate(data, 27), (100.0, 127.0, 400.0))
        self.assertIsNone(vod.best_candidate({"candidates": [cand(10, 80, ok=False)]}, 27))
        self.assertIsNone(vod.best_candidate({"candidates": [cand(10, 14)]}, 27))  # shorter than 8 s
        self.assertIsNone(vod.best_candidate(None, 27))


class Flow(unittest.TestCase):
    def setup(self, chapters=CH):
        gh, it = FakeGH(), vod_item()
        v = vod.Vod(gh, CFG, lambda url: chapters)
        return gh, it, v, editor.Editor(gh, CFG, probe, vod=v)

    def test_start_dispatches_a_find_run_inside_the_first_game_window_with_safe_inputs(self):
        gh, it, v, ed = self.setup()
        ed.advance([it], NOW, [])
        wf, inp = gh.dispatched[0]
        self.assertEqual(wf, "clip.yml")
        self.assertEqual((inp["find"], inp["render"], inp["dry_run"], inp["warp"], inp["cookies"]),
                         ("true", "false", "true", "false", "false"))
        self.assertEqual((inp["start_sec"], inp["end_sec"]), ("300", "900"))
        self.assertEqual(inp["url"], URL)
        self.assertEqual((it.state, it.note.split()[0]), ("EDITING", "vod:find:0"))

    def test_find_done_dispatches_the_render_of_the_best_moment_and_editor_finishes_it(self):
        gh, it, v, ed = self.setup()
        ed.advance([it], NOW, [])
        gh.runs = [run(900)]
        ed.advance([it], NOW + timedelta(seconds=40), [])
        self.assertEqual(it.run, "900")
        gh.run_info["900"] = {"status": "completed", "conclusion": "success"}
        gh.zips["900"] = find_zip([cand(10, 80, ok=False), cand(100, 160)])
        log = []
        ed.advance([it], NOW + timedelta(seconds=80), log)
        wf, inp = gh.dispatched[1]
        self.assertEqual((inp["find"], inp["render"], inp["render_start"], inp["render_end"]), ("false", "true", "100", "127"))
        self.assertEqual((inp["start_sec"], inp["end_sec"]), ("300", "900"))  # same window: the source cache hits
        self.assertEqual(it.moment, "400-427")  # seconds of the stream
        self.assertTrue(it.run.startswith("PENDING@"))
        self.assertEqual(it.state, "EDITING")
        # the render run: the normal clip path decides READY
        gh.runs = [run(901, created=NOW + timedelta(seconds=90))]
        ed.advance([it], NOW + timedelta(seconds=100), [])
        self.assertEqual(it.run, "901")
        gh.run_info["901"] = {"status": "completed", "conclusion": "success"}
        gh.zips["901"] = make_zip(GOOD, {"hook": {"text": "Wild chase"}})
        ed.advance([it], NOW + timedelta(seconds=140), [])
        self.assertEqual(it.state, "READY")
        self.assertIn("#LaurenHamden", it.caption)

    def test_a_failed_render_skips_it_and_the_queue_moves_on(self):
        gh, it, v, ed = self.setup()
        nxt = Item(n=11, source="https://m.twitch.tv/laurenhamdenn/clip/X")
        ed.advance([it, nxt], NOW, [])
        it.run = "901"
        it.note = "vod:render:0 attempt 1"
        gh.run_info["901"] = {"status": "completed", "conclusion": "failure"}
        gh.zips["901"] = make_zip(None, None, mp4=False, cam={"reasons": ["no confident cam"]})
        ed.advance([it, nxt], NOW + timedelta(seconds=50), [])
        self.assertEqual(it.state, "SKIPPED")
        self.assertIn("no confident cam", it.note)
        self.assertIs(states.head([it, nxt]), nxt)

    def test_no_candidate_goes_to_the_next_window_then_skips_when_all_are_spent(self):
        gh, it, v, ed = self.setup()
        t = NOW
        for k in range(2):  # two game windows: (300,900) and (1500,1700)
            ed.advance([it], t, [])
            self.assertEqual(it.note.split()[0], "vod:find:%d" % k)
            rid = str(1000 + k)
            gh.runs = [run(int(rid), created=t)]
            ed.advance([it], t + timedelta(seconds=40), [])
            gh.run_info[rid] = {"status": "completed", "conclusion": "success"}
            gh.zips[rid] = find_zip([cand(10, 80, ok=False)])
            t += timedelta(seconds=100)
            ed.advance([it], t, [])
            t += timedelta(seconds=10)
            if k == 0:  # moved on in the same tick: the next window's find run is already dispatched
                self.assertEqual((it.state, it.note.split()[0]), ("EDITING", "vod:find:1"))
        self.assertEqual(gh.dispatched[1][1]["start_sec"], "1500")
        self.assertEqual(it.state, "SKIPPED")
        self.assertIn("Grand Theft Auto VI", it.note)

    def test_a_failed_find_run_also_moves_on(self):
        gh, it, v, ed = self.setup()
        ed.advance([it], NOW, [])
        it.run = "900"
        gh.run_info["900"] = {"status": "completed", "conclusion": "failure"}
        ed.advance([it], NOW + timedelta(seconds=60), [])
        self.assertEqual((it.state, it.note.split()[0]), ("EDITING", "vod:find:1"))  # next window, same tick
        self.assertEqual(gh.dispatched[1][1]["start_sec"], "1500")

    def test_no_game_chapter_skips_without_downloading_anything(self):
        gh, it, v, ed = self.setup([{"title": "Just Chatting", "start_time": 0, "end_time": 3000}])
        log = []
        ed.advance([it], NOW, log)
        self.assertEqual(it.state, "SKIPPED")
        self.assertEqual(gh.dispatched, [])
        self.assertIn("Grand Theft Auto VI", it.note)

    def test_unreadable_chapters_are_retried_then_skipped(self):
        gh, it = FakeGH(), vod_item()

        def bad(url):
            raise OSError("net")
        ed = editor.Editor(gh, CFG, probe, vod=vod.Vod(gh, CFG, bad))
        for _ in range(3):
            ed.advance([it], NOW, [])
        self.assertEqual(it.state, "SKIPPED")
        self.assertEqual(gh.dispatched, [])

    def test_a_dead_stream_is_skipped_at_once(self):
        gh, it = FakeGH(), vod_item()

        def live(url):
            raise ValueError("stream is live")
        editor.Editor(gh, CFG, probe, vod=vod.Vod(gh, CFG, live)).advance([it], NOW, [])
        self.assertEqual(it.state, "SKIPPED")

    def test_dispatch_error_goes_back_to_queued_and_keeps_the_window(self):
        gh, it, v, ed = self.setup()
        gh.dispatch_error = GitHubError("HTTP 500")
        log = []
        ed.advance([it], NOW, log)
        self.assertEqual(it.state, "QUEUED")
        self.assertEqual(it.note.split()[0], "vod:next:0")
        self.assertEqual(log[-1][0], "EDITOR_ERROR")


class Chapters(unittest.TestCase):
    def test_service_reads_chapters_and_refuses_a_live_stream(self):
        import subprocess
        from unittest import mock
        ok = subprocess.CompletedProcess([], 0, stdout=json.dumps({"chapters": [
            {"title": "Grand Theft Auto VI", "start_time": 0, "end_time": 700}, {"title": "x"}]}), stderr="")
        with mock.patch.object(subprocess, "run", return_value=ok):
            self.assertEqual(service.chapters_source("u"), [{"title": "Grand Theft Auto VI", "start_time": 0, "end_time": 700}])
        live = subprocess.CompletedProcess([], 0, stdout='{"is_live": true}', stderr="")
        with mock.patch.object(subprocess, "run", return_value=live):
            with self.assertRaises(ValueError):
                service.chapters_source("u")
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="")):
            with self.assertRaises(RuntimeError):
                service.chapters_source("u")


if __name__ == "__main__":
    unittest.main()
