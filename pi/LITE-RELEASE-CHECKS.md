# Public-site release gate

Netlify only runs the standard-library Python lite generator. It needs no npm
packages, browser installation or test runner. Generated build-info.json and
visible page footers identify the release and exact Git commit.

Browser checks run from the release workstation, not inside Netlify. Install
Node, Playwright from tests/package.json, and its Chromium browser there.
The Pi package builder runs the local browser check before writing packages.
These tools are not required to run the installed Pi service.

The check opens a phone-sized Chromium browser and refuses the build if either
Video Hub or Read Only is empty. It tests a clean browser, an old saved catalogue
with theme/splash flags and empty offline cache, blocked browser storage and an
unreachable optional Pi backup endpoint. Locally it also installs an old
service worker and verifies its replacement unregisters it and clears its cache.
It checks that unavailable intro media reaches the pill choice and that both
Scripture PDF links return actual PDF documents.

After Netlify finishes a branch deployment, run:

    node tests/lite-smoke.cjs --url https://THE-ACTUAL-NETLIFY-PREVIEW-URL

Never promote the lite branch to main before this remote check passes and the
owner approves the phone preview. The remote check installs a simulated old
worker through request interception in its isolated browser profile; it does
not modify files on the hosted site.

The empty-list bug was that limited mode skipped cached rendering, then skipped
network rendering if the saved and received catalogues were equal. A successful
load now always renders. The JSON response is authoritative; embedded lite data
is only a fallback when fetching fails. Optional backup enrichment cannot replace
the catalogue with an older embedded version.
