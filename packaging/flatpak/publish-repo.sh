#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Pushes to main and release tags: add the freshly built channel to the signed Flatpak repo on
# GitHub Pages and lay out the site in ./site (repo/, one .flatpakref per channel, index.html).
#
#   FLATPAK_GPG_PRIVATE_KEY=<armored secret key> publish-repo.sh <base-url> <channel>
#
# GitHub Pages replaces the whole site on every deploy, so the repo that is live now is mirrored
# first: publishing "test" keeps "stable" and the other way round.
# The secret key must belong to the public key committed in mousepods-repo.asc: that one goes into
# the .flatpakref, so a mismatch would give users a repo they cannot verify.
# Not set up yet (no secret or no public key): says so and writes published=false, the build stays green.
set -euo pipefail

BASE_URL=${1:?usage: publish-repo.sh <base-url> <channel>}
CHANNEL=${2:?usage: publish-repo.sh <base-url> <channel>}
HERE=$(cd "$(dirname "$0")" && pwd)
PUB="$HERE/mousepods-repo.asc"
APP_ID=io.github.henningnorden.MousePods
OUT=${GITHUB_OUTPUT:-/dev/null}

fail() { echo "::error title=flatpak repo::$*"; exit 1; }
skip() { echo "::notice title=flatpak repo::$* - not publishing"; echo "published=false" >> "$OUT"; exit 0; }
case "$CHANNEL" in stable|test) ;; *) fail "unknown channel $CHANNEL" ;; esac
[ -s "$PUB" ] || skip "no public key in packaging/flatpak/mousepods-repo.asc yet"
[ -n "${FLATPAK_GPG_PRIVATE_KEY:-}" ] || skip "secret FLATPAK_GPG_PRIVATE_KEY is not set"
for tool in gpg ostree curl; do command -v "$tool" >/dev/null || fail "$tool is not installed in the build container"; done

GNUPGHOME=$(mktemp -d)
export GNUPGHOME
chmod 700 "$GNUPGHOME"
cleanup() { gpgconf --kill gpg-agent 2>/dev/null || true; rm -rf "$GNUPGHOME"; }
trap cleanup EXIT

printf '%s\n' "$FLATPAK_GPG_PRIVATE_KEY" | gpg --batch --quiet --import
KEY=$(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr/ {print $10; exit}')
WANT=$(gpg --batch --with-colons --show-keys "$PUB" | awk -F: '/^fpr/ {print $10; exit}')
[ -n "$KEY" ] || fail "the secret key could not be imported"
[ "$KEY" = "$WANT" ] || fail "secret key $KEY does not match the public key $WANT in mousepods-repo.asc"
gpg --batch --yes --dearmor -o "$GNUPGHOME/public.gpg" "$PUB"

# the repo that is live now, so the other channel survives this deploy
rm -rf site
mkdir -p site
ostree --repo=site/repo init --mode=archive-z2
if curl -fsS -o /dev/null "${BASE_URL%/}/repo/config"; then
    ostree --repo=site/repo remote add --no-gpg-verify live "${BASE_URL%/}/repo/"
    ostree --repo=site/repo pull --mirror live
    ostree --repo=site/repo remote delete live
else
    echo "no repo on Pages yet, starting a new one"
fi

# the new build of this channel (and its .Debug/.Locale parts if there are any), signed
for ref in $(ostree refs --repo=repo | grep "^app/$APP_ID"); do
    flatpak build-commit-from --src-repo=repo --gpg-sign="$KEY" --gpg-homedir="$GNUPGHOME" site/repo "$ref"
done
flatpak build-update-repo site/repo --title=MousePods --default-branch=stable --prune \
    --gpg-sign="$KEY" --gpg-homedir="$GNUPGHOME"

python3 "$HERE/make_refs.py" "$BASE_URL" "$GNUPGHOME/public.gpg" site
cp "$HERE/site-index.html" site/index.html
echo "published=true" >> "$OUT"
echo "channel $CHANNEL signed with $KEY, site ready for $BASE_URL"
