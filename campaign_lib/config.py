"""Load a campaign.json. One campaign = one folder under campaigns/. Never shared state."""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def campaign_path(slug="lauren-hamden"):
    return os.path.join(ROOT, "campaigns", slug, "campaign.json")


def load(slug="lauren-hamden", path=None):
    with open(path or campaign_path(slug), encoding="utf-8") as f:
        cfg = json.load(f)
    validate(cfg)
    return cfg


def validate(cfg):
    for k in ("name", "handle", "caption", "timings", "sources", "secrets"):
        if k not in cfg:
            raise ValueError("campaign.json missing %r" % k)
    h = cfg["handle"]
    if not h or not h.isascii() or not h.replace("_", "").isalnum():
        raise ValueError("handle must be plain ASCII letters/digits/underscore, no # or @: %r" % h)
    t = cfg["timings"]
    if t["approve_gap_min"] <= 0 or t["submit_window_min"] <= 0:
        raise ValueError("timings must be positive")
    u = cfg.get("reach_url")
    if u is not None and (not str(u).startswith("https://") or any(ch in str(u) for ch in ' "\'\n')):
        raise ValueError("reach_url must be a plain https:// link (no spaces or quotes): %r" % u)


def required_tag(cfg):
    return "#" + cfg["handle"]


def required_mention(cfg):
    return "@" + cfg["handle"]
