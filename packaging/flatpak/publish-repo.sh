#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# Release tags only: sign the Flatpak repo flatpak-builder made (./repo) and lay out the GitHub Pages
# site in ./site (repo/, MousePods.flatpakref, mousepods.flatpakrepo, index.html).
#
#   FLATPAK_GPG_PRIVATE_KEY=<armored secret key> publish-repo.sh <base-url>
#
# The secret key must belong to the public key committed in mousepods-repo.asc: that one goes into
# the .flatpakref, so a mismatch would give users a repo they cannot verify.
set -euo pipefail

BASE_URL=${1:?usage: publish-repo.sh <base-url>}
HERE=$(cd "$(dirname "$0")" && pwd)
PUB="$HERE/mousepods-repo.asc"
APP_ID=io.github.henningnorden.MousePods

fail() { echo "::error title=flatpak repo::$*"; exit 1; }
[ -s "$PUB" ] || fail "public key $PUB is missing"
[ -n "${FLATPAK_GPG_PRIVATE_KEY:-}" ] || fail "secret FLATPAK_GPG_PRIVATE_KEY is not set"
command -v gpg >/dev/null || fail "gpg is not installed in the build container"

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

# sign every app ref the build exported (the app, and its .Debug/.Locale parts if there are any)
for ref in $(ostree refs --repo=repo | grep "^app/$APP_ID"); do
    IFS=/ read -r _ id arch branch <<< "$ref"
    flatpak build-sign repo "$id" "$branch" --arch="$arch" --gpg-sign="$KEY" --gpg-homedir="$GNUPGHOME"
done
flatpak build-update-repo repo --title=MousePods --default-branch=master --prune \
    --gpg-sign="$KEY" --gpg-homedir="$GNUPGHOME"

rm -rf site
mkdir -p site
cp -a repo site/repo
python3 "$HERE/make_refs.py" "$BASE_URL" "$GNUPGHOME/public.gpg" site
cp "$HERE/site-index.html" site/index.html
echo "repo signed with $KEY, site ready for $BASE_URL"
