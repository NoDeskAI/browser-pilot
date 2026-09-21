import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.routes import browser


class NoteDetailRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.body = browser.NoteDetailBody(sessionId="session", noteId="a" * 24)
        self.user = object()
        self.pool = SimpleNamespace(fetchrow=AsyncMock(return_value={"browser_runtime": "cloak_chromium"}))
        self.mocks = {}
        for name, value in {
            "verify_session_access": AsyncMock(),
            "get_pool": lambda: self.pool,
            "get_container_status": AsyncMock(return_value="running"),
            "resolve_selenium_base_url": AsyncMock(return_value="http://existing-runtime"),
            "wd_fetch": AsyncMock(side_effect=[{"nodes": [{"slots": [{"session": {"sessionId": "existing"}}]}]}, {"ok": True, "note": {"noteId": self.body.noteId}}]),
        }.items():
            p = patch.object(browser, name, value)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)
        for name, value in {
            "begin_compatible_action": AsyncMock(return_value=(object(), None)),
            "require_active_lease": AsyncMock(),
            "complete_compatible_action": AsyncMock(side_effect=lambda ctx, result, **kw: result),
            "fail_compatible_action": AsyncMock(side_effect=lambda ctx, error, **kw: {"ok": False, "error": error}),
        }.items():
            p = patch.object(browser.agent_devices, name, value)
            self.mocks[name] = p.start()
            self.addCleanup(p.stop)

    async def test_rejected_lease_never_contacts_runtime(self):
        self.mocks["begin_compatible_action"].return_value = (None, {"ok": False, "error": "lease_required"})
        self.assertFalse((await browser.api_note_detail(self.body, self.user))["ok"])
        self.mocks["wd_fetch"].assert_not_called()

    async def test_stopped_runtime_is_not_started(self):
        self.mocks["get_container_status"].return_value = "stopped"
        self.assertEqual((await browser.api_note_detail(self.body, self.user))["error"], "runtime_not_running")
        self.mocks["wd_fetch"].assert_not_called()

    async def test_unsupported_runtime_is_not_contacted(self):
        self.pool.fetchrow.return_value = {"browser_runtime": "standard_chrome"}
        self.assertEqual((await browser.api_note_detail(self.body, self.user))["error"], "unsupported_runtime")
        self.mocks["wd_fetch"].assert_not_called()

    async def test_fixed_script_uses_existing_session_only(self):
        self.assertTrue((await browser.api_note_detail(self.body, self.user))["ok"])
        calls = self.mocks["wd_fetch"].call_args_list
        self.assertEqual([c.args[0] for c in calls], ["/status", "/session/existing/execute/sync"])
        self.assertEqual(calls[1].args[2]["args"], [self.body.noteId])
        self.assertEqual(self.mocks["require_active_lease"].await_count, 2)

    async def test_lost_lease_does_not_return_note(self):
        self.mocks["require_active_lease"].side_effect = [None, RuntimeError("lease_lost")]
        result = await browser.api_note_detail(self.body, self.user)
        self.assertEqual(result, {"ok": False, "error": "lease_lost"})
        self.mocks["complete_compatible_action"].assert_not_called()

    async def test_page_error_not_reported_as_success(self):
        self.mocks["wd_fetch"].side_effect = [{"nodes": [{"slots": [{"session": {"sessionId": "existing"}}]}]}, {"ok": False, "error": "verification_required"}]
        self.assertEqual((await browser.api_note_detail(self.body, self.user))["error"], "verification_required")
