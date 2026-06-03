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
