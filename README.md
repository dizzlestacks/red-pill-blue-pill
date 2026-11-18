# WokeAreWe lite preview — release 2026.11.15.3

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
PDFs. It is always in limited mode. YouTube requires internet. Pi admin, local
video backups and their downloads are unavailable in this static preview.

The catalogue is the release seed, not a live connection to the Pi. Future Pi
edits do not automatically update this GitHub branch or Netlify deployment.

## Pi source

The server and installer source are included for release completeness.
See pi/README.md for Pi instructions. No settings, credentials, runtime data,
database, queue, handover code, catalogue backup or release archive is included.
