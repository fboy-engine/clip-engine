import numpy as np
from clip_engine import subject, trim


def _dets(n):
    car = (0.10, 0.3, 0.70, 0.9, 0.9, "car")           # 0.60 of the frame wide: no panel can contain it
    person = (0.45, 0.4, 0.55, 0.9, 0.6, "person")
    return [[car, person] for _ in range(n)]


def test_unframeable_box_is_not_the_subject():
    n = 8
    gray = np.random.RandomState(0).randint(0, 255, (n, 27, 48)).astype(np.float32)
    masks = []
    plain = subject.build_tracked(_dets(n), gray, masks)
    assert set(plain["source"]) == {"vehicle"}
    framed = subject.build_tracked(_dets(n), gray, masks, reach=(0.02, 0.66, 0.40))
    assert set(framed["source"]) == {"person"}
    assert all(len(u) == 1 and u[0][5] == "car" for u in framed["unframeable"])


def test_box_beyond_reach_is_dropped_and_none_counts_as_fail():
    n = 6
    gray = np.random.RandomState(1).randint(0, 255, (n, 27, 48)).astype(np.float32)
    far = [[(0.55, 0.3, 0.80, 0.9, 0.9, "car")] for _ in range(n)]
    sub = subject.build_tracked(far, gray, [], reach=(0.02, 0.66, 0.40))
    assert all(b is None or b[4] == 0.0 for b in sub["box"])            # no detection survives (a motion box has score 0.0)
    assert "car" not in [s for s in sub["label"] if s]


def test_last_word_pad_past_the_envelope_cuts_at_the_envelope_end():
    words = [{"w": "so", "s": 24.0, "e": 24.3}, {"w": "me", "s": 31.9, "e": 32.26}]
    end, why, _ = trim.payoff_end(words, 0.0, 25.0, 32.6)
    assert end == 32.6 and "after 'me'" in why


def test_no_tail_room_still_refuses():
    words = [{"w": "me", "s": 32.3, "e": 32.55}]
    try:
        trim.payoff_end(words, 0.0, 25.0, 32.6)
    except ValueError as e:
        assert "no word-end cut point" in str(e)
    else:
        raise AssertionError("a word ending <0.1 s before the envelope end must not be accepted")
