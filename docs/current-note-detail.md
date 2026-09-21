# Current Note Detail API (not yet deployed or live-validated)

`POST /api/browser/note/detail`

```json
{"sessionId":"prxanhiceb63","noteId":"0123456789abcdef01234567"}
```

CLI: `bpilot note-detail <24-character-note-id>` using the selected session.

Requires existing session access and a valid active DeviceLease owned by the
caller. Only an already-running `cloak_chromium` with an existing driver session
is supported. The endpoint never acquires a lease, calls runtime start/recreate,
initializes download capture, or enables CDP Network. It executes one fixed
script; extra request fields such as `script` are rejected.

Successful response uses the existing governed action envelope plus `ok: true`,
`schemaVersion: 1`, `source: current_page_note_store`, `pageUrl` (no query),
`note`, `meta: {}`, and `warnings`.

`note` contains only noteId/title/desc/type, tagList[].name,
user.userId/nickname/avatar, interactInfo likedCount/collectedCount/commentCount/
shareCount, time, ipLocation, imageList URL/dimensions, and
video.media.stream.{h264,h265,av1}[] masterUrl/backupUrls/width/height/duration.
Missing fields stay empty; no URL construction from opaque video IDs, no HTML
dump, cookies, storage, arbitrary initial-state export, screenshot or recording.

The current URL must identify the requested note; the matching note-store entry
must contain the same noteId. Never fall back to the first cached SPA entry.
This schema is fixture-tested, not yet validated on a real current note.

Errors use the existing action failure/rejection envelope (`ok: false`):
lease_required/operator_mismatch (existing governance), unsupported_runtime,
runtime_not_running, browser_not_ready, unsupported_page, login_required,
verification_required, note_id_mismatch, note_detail_unavailable. Invalid request
bodies receive 422. Permission errors retain existing session-access behavior.
Clients must check `ok`, not just HTTP status. On login or verification errors,
stop and request user intervention; do not retry through another access path.

Media URLs are download candidates, not exported bytes or an archival guarantee.
They may expire, require browser context, or fail independent HTTP requests.
Only successfully downloaded, validated media stored in private TOS may count as
archived. No live video download/TOS verification has been performed.

Known driver boundary: deployed execute/sync invokes active_page and can recover
a crashed/closed page before running the fixed script. The backend does not
request recovery, but strict fail-closed page behavior requires a driver-side
no-recovery capability. Do not advertise this as a guarantee of zero page changes
on driver failure, and do not roll/restart Cloak to add it without authorization.

Release scope, if approved: backend image and generated CLI only; no frontend,
database migration, or Cloak Runtime rollout. Production release requires user
confirmation and a real-note validation under the caller's owned lease.
