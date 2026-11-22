"""Single source of truth for edit rules. Change here, not inline."""
OUT_W, OUT_H = 1080, 1920
MIN_LEN, MAX_LEN = 25.0, 45.0
CRF = 17
PRESET = "slow"
TARGET_LUFS = -14.0
TRUE_PEAK = -1.5         # hard limit on the FINISHED file (ebur128 true peak, after the AAC encode)
LIMITER_CEIL_DB = -3.0   # limiter ceiling before the AAC encode; AAC overshoots, so it sits well under TRUE_PEAK
TP_PASSES = 3            # audio-only re-encodes (ceiling -1 dB each) if the finished file is still over TRUE_PEAK
LRA = 11.0
PUNCH_TARGET = 0.08      # normal cap on punch-in
PUNCH_HARD_CAP = 0.12    # never exceeded
HOOK_MAX_SEC = 1.0       # hook caption must be visible by this time
SFX_DEFAULT = False
MUSIC_DEFAULT = False
DRY_RUN_DEFAULT = True
