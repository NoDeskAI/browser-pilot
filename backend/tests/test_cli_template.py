from pathlib import Path


def test_cli_exposes_current_page_image_export_command():
    template = (Path(__file__).parents[1] / "app" / "cli_template.sh").read_text()

    assert "cmd_export_image()" in template
    assert '_api_post "/api/browser/image/export"' in template
    assert "export-image <url> [--name <n>]" in template
    assert "export-image --selector <css> [--name <n>]" in template
    assert '\\"selector\\":\\"$(_esc "$selector")\\"' in template
    assert '_print_or_fail_ok "$resp"' in template
