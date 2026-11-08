# SPDX-License-Identifier: Apache-2.0
"""Files for the own Flatpak repo on GitHub Pages, with two channels (Flatpak branches of the app):
stable from release tags, test from main. One .flatpakref per channel (install once, then updates
come through `flatpak update` / Discover) and mousepods.flatpakrepo (add the remote only).

    python3 make_refs.py <base-url> <public-key.gpg> <out-dir>
    python3 make_refs.py --channel <git-ref>        prints the channel for a ref
"""
import base64
import os
import sys

APP_ID = "io.github.starman_tech.MousePods"
CHANNELS = ("stable", "test")
REF_FILES = {"stable": "MousePods.flatpakref", "test": "MousePods-test.flatpakref"}
TITLE = "MousePods"
HOMEPAGE = "https://github.com/starman-tech/mousepods"
FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def channel_for(git_ref):
    """Release tags go to stable, everything else (main, pull requests) is test."""
    return "stable" if git_ref.startswith("refs/tags/v") else "test"


def _common(base_url, gpg_key):
    if not gpg_key:
        raise ValueError("the repo is signed: a public GPG key is required")
    if not base_url.startswith("https://"):
        raise ValueError("the repo must be served over https")
    return base_url.rstrip("/") + "/repo/", base64.b64encode(gpg_key).decode()


def flatpakref(base_url, gpg_key, channel):
    if channel not in CHANNELS:
        raise ValueError(f"unknown channel {channel!r}")
    url, key = _common(base_url, gpg_key)
    title = TITLE if channel == "stable" else f"{TITLE} ({channel})"
    return (f"[Flatpak Ref]\nTitle={title}\nName={APP_ID}\nBranch={channel}\nUrl={url}\n"
            f"SuggestRemoteName=mousepods\nHomepage={HOMEPAGE}\nIsRuntime=false\n"
            f"RuntimeRepo={FLATHUB}\nGPGKey={key}\n")


def flatpakrepo(base_url, gpg_key):
    url, key = _common(base_url, gpg_key)
    return (f"[Flatpak Repo]\nTitle={TITLE}\nUrl={url}\nHomepage={HOMEPAGE}\n"
            f"Comment=AirPods as a head tracker for Proton games\nGPGKey={key}\n")


def main(argv):
    if argv[:1] == ["--channel"]:
        print(channel_for(argv[1]))
        return
    base_url, key_path, out = argv
    with open(key_path, "rb") as f:
        key = f.read()
    os.makedirs(out, exist_ok=True)
    files = {name: flatpakref(base_url, key, ch) for ch, name in REF_FILES.items()}
    files["mousepods.flatpakrepo"] = flatpakrepo(base_url, key)
    for name, text in files.items():
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main(sys.argv[1:])
