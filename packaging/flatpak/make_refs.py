# SPDX-License-Identifier: Apache-2.0
"""Files for the own Flatpak repo on GitHub Pages: MousePods.flatpakref (install once, then updates
come through `flatpak update` / Discover) and mousepods.flatpakrepo (add the remote only).

    python3 make_refs.py <base-url> <public-key.gpg> <out-dir>
"""
import base64
import os
import sys

APP_ID = "io.github.henningnorden.MousePods"
BRANCH = "master"
TITLE = "MousePods"
HOMEPAGE = "https://github.com/henningnorden/mousepods"
FLATHUB = "https://dl.flathub.org/repo/flathub.flatpakrepo"


def _common(base_url, gpg_key):
    if not gpg_key:
        raise ValueError("the repo is signed: a public GPG key is required")
    if not base_url.startswith("https://"):
        raise ValueError("the repo must be served over https")
    return base_url.rstrip("/") + "/repo/", base64.b64encode(gpg_key).decode()


def flatpakref(base_url, gpg_key):
    url, key = _common(base_url, gpg_key)
    return (f"[Flatpak Ref]\nTitle={TITLE}\nName={APP_ID}\nBranch={BRANCH}\nUrl={url}\n"
            f"SuggestRemoteName=mousepods\nHomepage={HOMEPAGE}\nIsRuntime=false\n"
            f"RuntimeRepo={FLATHUB}\nGPGKey={key}\n")


def flatpakrepo(base_url, gpg_key):
    url, key = _common(base_url, gpg_key)
    return (f"[Flatpak Repo]\nTitle={TITLE}\nUrl={url}\nHomepage={HOMEPAGE}\n"
            f"Comment=AirPods as a head tracker for Proton games\nGPGKey={key}\n")


def main(argv):
    base_url, key_path, out = argv
    with open(key_path, "rb") as f:
        key = f.read()
    os.makedirs(out, exist_ok=True)
    for name, text in (("MousePods.flatpakref", flatpakref(base_url, key)),
                       ("mousepods.flatpakrepo", flatpakrepo(base_url, key))):
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(text)


if __name__ == "__main__":
    main(sys.argv[1:])
