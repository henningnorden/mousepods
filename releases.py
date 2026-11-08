# SPDX-License-Identifier: Apache-2.0
"""Version and release notes: read from the AppStream metainfo (the same text Discover shows as
"what's new"), channel and commit from /.flatpak-info inside the Flatpak."""
import configparser
import os
import xml.etree.ElementTree as ET   # our own metainfo file only, never input from elsewhere

HERE = os.path.dirname(os.path.abspath(__file__))
APP_ID = "io.github.henningnorden.MousePods"
METAINFO_PATHS = (f"/app/share/metainfo/{APP_ID}.metainfo.xml",                      # inside the Flatpak
                  os.path.join(HERE, "packaging", "flatpak", f"{APP_ID}.metainfo.xml"))  # from the source tree


def metainfo_path():
    forced = os.environ.get("MOUSEPODS_METAINFO")
    if forced:
        return forced
    return next((p for p in METAINFO_PATHS if os.path.isfile(p)), METAINFO_PATHS[-1])


def _text(el):
    return " ".join("".join(el.itertext()).split())


def load(path):
    """All <release> entries, newest first: [{"version", "date", "paragraphs", "items"}]."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return []
    out = []
    for rel in root.findall("./releases/release"):
        desc = rel.find("description")
        out.append({"version": rel.get("version"), "date": rel.get("date"),
                    "paragraphs": [_text(p) for p in desc.findall("p")] if desc is not None else [],
                    "items": [_text(li) for li in desc.findall("./ul/li")] if desc is not None else []})
    return out


def flatpak_instance(path=None):
    """Channel (Flatpak branch: stable/test) and short commit of the running Flatpak, None outside."""
    path = path or os.environ.get("MOUSEPODS_FLATPAK_INFO", "/.flatpak-info")
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except (OSError, configparser.Error):
        return {"channel": None, "commit": None}
    inst = cp["Instance"] if cp.has_section("Instance") else {}
    commit = inst.get("app-commit")
    return {"channel": inst.get("branch"), "commit": commit[:7] if commit else None}


def info(meta_path=None, flatpak_info=None, seen=None):
    """What the app shows: version, channel, all releases, and the release to announce once
    (the newest, if the user has seen an older one; nothing on the very first start)."""
    rs = load(meta_path or metainfo_path())
    version = rs[0]["version"] if rs else None
    whats_new = rs[0] if rs and seen and seen != version else None
    return {"version": version, **flatpak_instance(flatpak_info), "releases": rs, "whats_new": whats_new}
