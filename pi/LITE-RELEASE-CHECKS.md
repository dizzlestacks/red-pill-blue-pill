# Public-site release gate

The Netlify build and Pi package builder now run `tests/lite-smoke.cjs`.
Install Node and the development dependencies when building a release; these
are not required to run the installed Pi service. Netlify installs Chromium
before the check.

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
owner approves the phone preview. The remote check does not install a simulated
legacy worker on the external origin; local worker migration testing is separate.

The empty-list bug was that limited mode skipped cached rendering, then skipped
network rendering if the saved and received catalogues were equal. A successful
load now always renders. The JSON response is authoritative; embedded lite data
is only a fallback when fetching fails. Optional backup enrichment cannot replace
the catalogue with an older embedded version.
