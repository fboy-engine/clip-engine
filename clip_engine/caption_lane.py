"""Where text may go: on the seam between the cam block and the gameplay panel, clear of the platform UI."""
OUT_H = 1920
PLATFORM_BOTTOM = 420      # px kept free for the platform's own buttons/description
HOOK_TOP = 300             # hook lane without a cam block (upper lane)
SEAM_Y = 540               # cam block height: the caption lane is centred on this row
HOOK_BELOW_SEAM = 700      # with a cam block the hook sits in the gameplay panel, under the seam captions


def lanes(cam_visible=False, cam_rect=None, cam_top=False):
    """caption_margin_v: px from the bottom for the bottom lane; seam_y: row the captions are centred on
    (None = bottom lane); hook_margin_v: px from the top for the hook."""
    if cam_top:
        return {"caption_margin_v": PLATFORM_BOTTOM, "seam_y": SEAM_Y, "hook_margin_v": HOOK_BELOW_SEAM}
    cap = PLATFORM_BOTTOM
    if cam_visible and cam_rect and cam_rect[1] > 0.5:       # facecam in the lower half
        cap = max(cap, int((1 - cam_rect[1]) * OUT_H * 0.6) + PLATFORM_BOTTOM // 2)
    return {"caption_margin_v": cap, "seam_y": None, "hook_margin_v": HOOK_TOP}
