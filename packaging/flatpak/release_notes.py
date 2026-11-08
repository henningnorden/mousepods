# SPDX-License-Identifier: Apache-2.0
"""Release notes from the AppStream metainfo: Discover / GNOME Software show the <releases> entries
as "what's new", and the GitHub release page gets the same text.

    python3 release_notes.py check <tag>       exit 1 (with a CI annotation) if a release tag has no entry
    python3 release_notes.py markdown <tag>    the entry as Markdown, for the release page

Pre-release tags (v1.2.0-rc1) need no entry.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from releases import load  # noqa: E402  (one parser for the app and this check)

HERE = os.path.dirname(os.path.abspath(__file__))
METAINFO = os.path.join(HERE, "io.github.henningnorden.MousePods.metainfo.xml")


def version_of(tag):
    return tag[1:] if tag.startswith("v") else tag


def is_prerelease(tag):
    return "-" in version_of(tag)


def entry(path, tag, require_text=True):
    """{"version", "date", "paragraphs", "items"} for the tag, or None if there is no usable entry."""
    e = next((r for r in load(path) if r["version"] == version_of(tag)), None)
    if e is None or (require_text and not (e["paragraphs"] or e["items"])):
        return None
    return e


def markdown(e):
    out = "".join(p + "\n\n" for p in e["paragraphs"])
    out += "".join(f"- {i}\n" for i in e["items"])
    return out


def check(path, tag):
    if is_prerelease(tag) or entry(path, tag) is not None:
        return 0
    print(f"::error title=release notes::{os.path.basename(path)} has no <release version=\"{version_of(tag)}\"> "
          "with a description. Add one (Discover shows it as what's new), then tag again.")
    return 1


def main(argv):
    cmd, tag = argv[0], argv[1]
    if cmd == "check":
        return check(METAINFO, tag)
    if cmd == "markdown":
        e = entry(METAINFO, tag)
        sys.stdout.write(markdown(e) if e else "")
        return 0
    raise SystemExit(f"unknown command {cmd}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
