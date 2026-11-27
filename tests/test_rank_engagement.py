from clip_engine.rank_local import Window, score


def _w(text, peak, mean=-30.0):
    return Window(0, 35, text, 0.7, peak, mean)


def test_hard_reaction_beats_flat_talk():
    assert score(_w("bruh what the hell did he just do!? no way!", -12)) > score(_w("so then we went to the shop", -22))
