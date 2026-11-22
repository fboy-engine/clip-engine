"""Caption builder. Reach rule: the caption must contain the exact ASCII strings
#<handle> and @<handle>, ONE short natural English line, and at most 5 more decent hashtags.
The handle lives in ONE place: campaign.json "handle"."""
import re

from . import config

_TAG = re.compile(r"^#[A-Za-z0-9_]{2,30}$")


class CaptionError(ValueError):
    pass


def _check_line(line, cfg):
    line = " ".join((line or "").split())
    if not line:
        raise CaptionError("caption line is empty")
    if len(line) > cfg["caption"]["max_line_chars"]:
        raise CaptionError("caption line longer than %d chars" % cfg["caption"]["max_line_chars"])
    if not line.isascii() or not line.isprintable():
        raise CaptionError("caption line must be plain printable ASCII English (no emoji, no non-English)")
    if "#" in line or "@" in line or "http" in line.lower():
        raise CaptionError("caption line must not contain #, @ or links (tags are added by the builder)")
    return line


def _check_tags(tags, cfg):
    c = cfg["caption"]
    banned = {b.lower() for b in c["banned_hashtags"]}
    required = config.required_tag(cfg).lower()
    out, seen = [], set()
    for t in tags:
        t = t.strip()
        if not _TAG.match(t):
            raise CaptionError("bad hashtag %r" % t)
        k = t.lower()
        if k in banned:
            raise CaptionError("spam hashtag %r" % t)
        if k == required or k in seen:
            continue  # required tag is added once by the builder; duplicates dropped
        seen.add(k)
        out.append(t)
    if len(out) > c["max_extra_hashtags"]:
        raise CaptionError("at most %d extra hashtags, got %d" % (c["max_extra_hashtags"], len(out)))
    return out


def build_caption(cfg, line, extra_tags=None):
    """Return the final caption text. Raises CaptionError instead of ever shipping a bad one."""
    line = _check_line(line, cfg)
    tags = _check_tags(extra_tags if extra_tags is not None else cfg["caption"]["default_extra_hashtags"], cfg)
    tag, mention = config.required_tag(cfg), config.required_mention(cfg)
    caption = "%s\n\n%s %s%s" % (line, tag, mention, "".join(" " + t for t in tags))
    verify_caption(cfg, caption)
    return caption


def verify_caption(cfg, caption):
    """Final gate, also used right before an upload: both exact ASCII strings present."""
    tag, mention = config.required_tag(cfg), config.required_mention(cfg)
    if not caption.isascii():
        raise CaptionError("caption is not ASCII")
    for needle in (tag, mention):
        if needle not in caption:
            raise CaptionError("caption is missing %s" % needle)
    hashtags = re.findall(r"#\w+", caption)
    if len({h.lower() for h in hashtags}) > 1 + cfg["caption"]["max_extra_hashtags"]:
        raise CaptionError("too many hashtags")
    return True
