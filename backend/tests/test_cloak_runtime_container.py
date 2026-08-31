from pathlib import Path


def test_cloak_runtime_hides_container_chrome_artifacts():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "services/cloak-chromium-runtime/Dockerfile").read_text()
    start = (root / "services/cloak-chromium-runtime/start-cloak-runtime.sh").read_text()
    driver = (root / "services/cloak-chromium-runtime/bp_cloak_driver.py").read_text()

    assert "CommandLineFlagSecurityWarningsEnabled" in dockerfile
    assert "/etc/chromium/policies/managed" in dockerfile
    assert "/etc/opt/chrome/policies/managed" in dockerfile
    assert "--test-type" in driver
    assert "session.screen0.toolbar.visible: false" in dockerfile
    assert "session.screen0.toolbar.visible: false" in start
    assert "(class=Chromium-browser)" in dockerfile
    assert "(class=Chromium-browser)" in start
    assert "(class=Google-chrome)" in dockerfile
    assert "(class=Google-chrome)" in start


def test_cloak_driver_recovers_crashed_targets_before_reusing_page():
    root = Path(__file__).resolve().parents[2]
    driver = (root / "services/cloak-chromium-runtime/bp_cloak_driver.py").read_text()

    assert 'page.on("crash"' in driver
    assert "def is_target_crash_error" in driver
    assert "async def recover_page" in driver
    assert "page = await state.recover_page()" in driver


def test_cloak_driver_exports_only_current_page_images_without_network_cdp():
    root = Path(__file__).resolve().parents[2]
    driver = (root / "services/cloak-chromium-runtime/bp_cloak_driver.py").read_text()

    assert 'app.router.add_post("/session/{sid}/image/export", export_page_image)' in driver
    assert "Array.from(document.images)" in driver
    assert "candidate.currentSrc" in driver
    assert "state.context.request.get" in driver
    assert "MAX_IMAGE_EXPORT_BYTES = 8 * 1024 * 1024" in driver
    assert "BP_IMAGE_EXPORT_HOSTS" in driver
    assert '"Network.enable"' not in driver
