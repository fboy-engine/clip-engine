"""Where text may go: clear of the platform UI at the bottom and of the facecam if it shows."""
OUT_H = 1920
PLATFORM_BOTTOM = 420      # px kept free for the platform's own buttons/description
HOOK_TOP = 300             # hook sits in the upper lane


def lanes(cam_visible=False, cam_rect=None):
    """margin_v (px from the bottom) for word captions and from the top for the hook."""
    cap = PLATFORM_BOTTOM
    if cam_visible and cam_rect and cam_rect[1] > 0.5:       # facecam in the lower half
        cap = max(cap, int((1 - cam_rect[1]) * OUT_H * 0.6) + PLATFORM_BOTTOM // 2)
    return {"caption_margin_v": cap, "hook_margin_v": HOOK_TOP}
