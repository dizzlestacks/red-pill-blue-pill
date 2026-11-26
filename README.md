# Current release: 2026.11.15.14

Latest changes (Twitter link removed from the Video Hub links bar, remaining links evenly spaced): see [release notes](pi/RELEASE-2026.11.15.14.md). Previous: [2026.11.15.13](pi/RELEASE-2026.11.15.13.md) (/admin uses the Video Hub's own stylesheet).

The real watched-profile adapter is connected. Existing posts are opt-in batches; automatic imports only include posts newer than the persisted watching start time. See [release notes](pi/RELEASE-2026.11.15.6.md).

# WokeAreWe lite preview — release 2026.11.15.4

This standalone branch contains the clean release source and intentional public
assets. It deliberately has no parent commit from the workspace history, to
avoid uploading old archives, catalogue backups or private historical files.
Do not merge it into the live branch as part of previewing.

## Netlify preview

Use **lite-preview** as a branch deploy, not as the production branch.
Keep the existing site's production branch and wokearewe.com domain unchanged.
The committed netlify.toml uses:

    python3 lite_site.py --catalog videos.json --output generated-artifacts/netlify-lite

Only **generated-artifacts/netlify-lite** is published. The repository root,
admin code, Python backend and Pi installer must never be the publish directory.
If branch deploys are not enabled, allow lite-preview in Netlify's branch deploy
settings. Alternatively, use a separate Netlify preview site without adding the
live custom domain.

The output contains public pages, the seed catalogue, intro, themes and Scripture
PDFs. It is always in limited mode. YouTube requires internet. Pi admin and
backup downloads are hidden. Configured Pi backups play inline while reachable.

The bundled catalogue is the initial seed. Optional Pi GitHub sync commits only
the sanitized public catalogue to the configured branch, triggering Netlify's
normal build. See pi/README.md for token setup and conflict handling.
Optional PI_PUBLIC_URL enables inline published-backup playback from the public
Video Hub without exposing admin access or navigating visitors to the Pi site.

## Pi source

The server and installer source are included for release completeness.
See pi/README.md for Pi instructions. No settings, credentials, runtime data,
database, queue, handover code, catalogue backup or release archive is included.
