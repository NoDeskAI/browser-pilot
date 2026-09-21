# Network Egress Editing

CE and EE share the settings editor. Organization admins and superadmins can
open the pencil action on managed Clash/OpenVPN profiles; Direct is immutable.

`GET /api/network-egress/{id}` loads one tenant-scoped profile and its persisted
`configText`. It requires an admin/superadmin user-level identity and returns
`Cache-Control: no-store`. Listing profiles still omits configuration. Separate
OpenVPN credentials and internal configuration paths are never returned.

The dialog preloads name, type and configuration. Type cannot be changed.
`PATCH /api/network-egress/{id}` receives only the name for a rename. This leaves
the configuration, credentials, runtime and health error unchanged. Config edits
submit the replacement text or URL; file and URL imports remain available.
Blank OpenVPN credentials preserve the existing auth file. Replacement requires
both username and password in the editor. Configuration replacement follows the
existing egress lifecycle; it may interrupt connections, and Kubernetes sessions
may need a user-initiated restart to consume updated configuration. The editor
does not restart browser sessions.

Saving disables duplicate submissions. Failure keeps the dialog and inputs;
success refreshes the list and closes the dialog. Closing clears sensitive input.

Regression checks:
- Backend: `python -m pytest backend/tests/test_network_egress_editor.py backend/tests/test_network_egress.py`
- Frontend: CE and EE builds.
- Isolated browser fixture: run Vite, open `/tests/network-egress-editor.html`.
  It mocks all HTTP requests, never forwarding them to a real server. Check
  prefill, rename-only payload, config edit, simulated save failure/retry via
  `egressFixture.failSave`, credential validation, member role, and create reset.

Release requires both backend and frontend artifacts; no database migration or
Runtime image changes are needed.
