# Release 2026.11.15.7

## Phone-first admin update

Admin now has Home, Videos, Add, Automatic and Settings navigation. Home shows
items needing review and setup before progress and health. Videos has search,
source, missing-title, missing-copy and pinned filters. Add keeps manual link
adding and import review. Settings holds publishing, limits, NAS, sync, logout
and the advanced JSON editor.

Success messages disappear after 4.5 seconds or a tap. Errors have a close
button and remain until dismissed. Messages occupy normal page space rather
than floating over form controls. A dismissible first-use guide explains the layout.

TikTok imports now explicitly disable end cards, including Import everything.
Unsent requests and stored watcher defaults are migrated; already-submitted
requests retain their immutable original settings and are labelled in admin.
In Automatic, choose Import everything. Confirm the
listed count and estimated space. Estimates use existing average file size or
100 MiB per post; actual sizes vary. The reserve is enforced during downloads.

The run persists in the data folder's SQLite database and recovery snapshots.
Pause stops new work after the current operation finishes. Disk pauses recover
automatically; failures retry with exponential delay up to one hour. Permanent
service failures may still require fixing the source or service permissions.
The hourly limit remains shared with new-post imports.

Save Publish automatically in watcher settings to apply it to both new posts
and back-catalogue imports. Review-first remains the default. Missing original
dates use the import/publication date.

Unique titles at least 16 characters long with 96% similarity may attach to
existing YouTube entries. Ambiguous matches remain separate. Undo match keeps
the original entry, returns the imported copy to review and disables its automatic
publication. Original YouTube metadata is preserved.

NAS copies run on an independent daemon and retry every minute. The count is
the number of video files verified on the last completed check, not a live mount
guarantee. Missing or stalled NAS access never blocks primary imports.

Install using the existing pi/update.sh process. No new settings or packages
are required. Physical Pi/NAS and real upstream downloads require live acceptance.
