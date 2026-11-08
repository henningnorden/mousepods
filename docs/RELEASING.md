# Releasing

## What's new

Release notes live in the AppStream metainfo,
`packaging/flatpak/io.github.starman_tech.MousePods.metainfo.xml`, newest entry first.
Discover and GNOME Software show that entry as "what's new" when the update arrives, and the
GitHub release page starts with the same text.

```xml
<releases>
  <release version="1.1.0" date="2026-11-20">
    <description>
      <p>One sentence about the release.</p>
      <ul>
        <li>A change people notice</li>
        <li>Another one</li>
      </ul>
    </description>
  </release>
  ...
</releases>
```

Keep it short and about what users notice; the full list of pull requests is added below it on
the release page automatically.

## Tagging

1. Add the `<release>` entry and merge it into main.
2. Tag main: `git tag v1.1.0 && git push origin v1.1.0`.
3. The CI checks that the metainfo has an entry with a description for `1.1.0` and stops the
   release if not. It builds the Flatpak, publishes it in the **stable** channel and creates the
   release page.

Pre-release tags (`v1.1.0-rc1`) need no entry and become pre-releases on GitHub.

## Channels

| Channel | Built from | Install |
| --- | --- | --- |
| stable | release tags `v*` | `MousePods.flatpakref` |
| test | every push to main, or "Run workflow" | `MousePods-test.flatpakref` |

Both are signed with the key in `packaging/flatpak/mousepods-repo.asc` (secret
`FLATPAK_GPG_PRIVATE_KEY`). Without the secret nothing is published and the build stays green.

## One-time setup of the repo

1. A signing key, without a passphrase (the CI cannot type one):
   `gpg --batch --passphrase '' --quick-gen-key "MousePods Flatpak repo" rsa4096 sign never`
2. Commit the public part as `packaging/flatpak/mousepods-repo.asc`:
   `gpg --armor --export "MousePods Flatpak repo" > packaging/flatpak/mousepods-repo.asc`
3. Store the secret part as the Actions secret `FLATPAK_GPG_PRIVATE_KEY`
   (`gpg --armor --export-secret-keys "MousePods Flatpak repo"`), and keep a backup of it: users
   installed with the public key, a new key means they have to install again.
4. Settings → Pages → Source: **GitHub Actions**. Settings → Environments → github-pages → deployment
   branches and tags: allow `main` and tags `v*`.

From then on every push to main updates the test channel and every `v*` tag the stable channel. The
website from `docs/` is deployed with them unchanged, the install page is `install.html`.
