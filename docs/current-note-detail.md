# Current Note Detail API

`POST /api/browser/note/detail`

```json
{"sessionId":"prxanhiceb63","noteId":"0123456789abcdef01234567"}
```

CLI: `bpilot --session <session-id> note-detail <24-character-note-id>`.

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
video.media.stream[group][] masterUrl/backupUrls/width/height/duration/weight/source.
Group names are provider identifiers, not necessarily codecs. Current pages use
EF4/EF5 as well as the formerly supported h264/h265/av1 names. Do not infer a codec
from an opaque group name. Consumers must inspect all returned groups, not only
the three codec-named groups. Groups and entries are bounded to 16 each.
The legacy video.mediaV2 JSON/object root stream (and nested video.stream) entries
are normalized from master_url/backup_urls into the same contract; raw mediaV2 is
not exported. Duplicate URLs in a group are removed, preferring media.stream.
source identifies the exact allowed input path, not a reconstructed URL.
Malformed or oversized (>1 MiB) JSON adds a warning. Video notes with no valid
stream URL add video_source_unavailable; ok means detail extraction succeeded,
not that downloadable video exists. No global player or og:video fallback is used.
Missing fields stay empty; no URL construction from opaque video IDs, no HTML
dump, cookies, storage, arbitrary initial-state export, screenshot or recording.

The current URL must identify the requested note; the matching note-store entry
must contain the same noteId. Never fall back to the first cached SPA entry.
Live diagnosis on 2026-09-21 found EF4/EF5 groups and mediaV2 root-level stream;
the previous hard-coded codec filter discarded all candidates. The corrected
fixed script read three URLs from the same current note under an owned lease
(audit c240bc94-67fe-4e0d-a502-11c83adacd6d), without navigation or Network capture.
This proves candidate extraction, not successful media download or archival.

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
archived. On 2026-09-21, ANTA verified one note end to end against deployed ce0972c:
2,284,376 bytes downloaded, ffprobe accepted 720x1280 video of 14,536 ms, private
TOS HEAD size matched, and PostgreSQL readback reported complete with matching
media metadata. SHA256: 068f7a1b9709e2417b0b9bee011ababeae34228af1c970e5d84efd0a3901f347.
This validates that note, not all notes or future expiring media URLs. The ANTA
consumer also needed dynamic group selection; a hard-coded codec-only client
will still omit EF4/EF5. Its verification lease was released afterward.

Known driver boundary: deployed execute/sync invokes active_page and can recover
a crashed/closed page before running the fixed script. The backend does not
request recovery, but strict fail-closed page behavior requires a driver-side
no-recovery capability. Do not advertise this as a guarantee of zero page changes
on driver failure, and do not roll/restart Cloak to add it without authorization.

Release scope, if approved: backend image and generated CLI only; no frontend,
database migration, or Cloak Runtime rollout. Production release requires user
confirmation and a real-note validation under the caller's owned lease.
