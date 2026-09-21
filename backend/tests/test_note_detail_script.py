import json
import shutil
import subprocess

import pytest

from app.tools.browser.note_detail import NOTE_DETAIL_SCRIPT


NOTE_ID = "a" * 24


def extract(*, path=None, note=None, selectors=None):
    if not shutil.which("node"):
        pytest.skip("node required for fixed-script execution")
    fixture = {"path": path or "/explore/" + NOTE_ID, "note": note or {"noteId": NOTE_ID}, "selectors": selectors or []}
    harness = """
const f = JSON.parse(require('fs').readFileSync(0, 'utf8'));
global.location = {href:'https://www.xiaohongshu.com'+f.path};
global.window = {__INITIAL_STATE__: {secret:'NEVER_EXPORT', note:{noteDetailMap:{[process.argv[1]]:{note:f.note}, other:{note:{title:'OTHER_NOTE'}}}}}};
global.document = {querySelectorAll: selector => f.selectors.some(s=>selector.includes(s)) ? [{getClientRects:()=>[{}]}] : []};
global.getComputedStyle = () => ({visibility:'visible'});
const result = new Function(process.argv[2])(process.argv[1]);
console.log(JSON.stringify(result));
"""
    return json.loads(subprocess.check_output(["node", "-e", harness, NOTE_ID, NOTE_DETAIL_SCRIPT], input=json.dumps(fixture), text=True))


def test_extract_whitelist_and_video_without_private_state():
    result = extract(note={"noteId": NOTE_ID, "title": "title", "desc": "body", "cookie": "NEVER_EXPORT", "video": {"media": {"stream": {"h264": [{"masterUrl": "https://cdn.example/video.mp4", "token": "NEVER_EXPORT"}]}}}})
    assert result["note"]["video"]["media"]["stream"]["h264"][0]["masterUrl"].endswith("video.mp4")
    assert "NEVER_EXPORT" not in json.dumps(result)
    assert "OTHER_NOTE" not in json.dumps(result)


def test_spa_wrong_note_never_falls_back():
    assert extract(path="/explore/" + "b" * 24)["error"] == "note_id_mismatch"
    assert extract(note={"noteId": "b" * 24})["error"] == "note_detail_unavailable"


def test_login_and_verification_stop_reading():
    assert extract(path="/login")["error"] == "login_required"
    assert extract(selectors=["captcha"])["error"] == "verification_required"


def test_non_http_media_and_private_query_are_not_returned():
    result = extract(path="/explore/" + NOTE_ID + "?xsec_token=NEVER_EXPORT", note={"noteId": NOTE_ID, "imageList": [{"url": "data:image/png;base64,private"}, {"url": "https://user:password@example.com/a"}]})
    assert all(not i["url"] for i in result["note"]["imageList"])
    assert "NEVER_EXPORT" not in result["pageUrl"]


@pytest.mark.parametrize("encoded", [True, False])
def test_media_v2_is_normalized_without_exporting_raw_state(encoded):
    media = {"private": "NEVER_EXPORT", "video": {"stream": {"h264": [None, {
        "master_url": "https://cdn.example/video.mp4", "backup_urls": ["//cdn.example/backup.mp4"],
        "width": 1080, "height": 1920, "duration": 42, "secret": "NEVER_EXPORT",
    }]}}}
    result = extract(note={"noteId": NOTE_ID, "type": "video", "video": {"mediaV2": json.dumps(media) if encoded else media}})
    video = result["note"]["video"]["media"]["stream"]["h264"][0]
    assert video["masterUrl"] == "https://cdn.example/video.mp4"
    assert video["backupUrls"] == ["https://cdn.example/backup.mp4"]
    assert video["width"] == 1080
    assert "NEVER_EXPORT" not in json.dumps(result)
    assert "video_source_unavailable" not in result["warnings"]


@pytest.mark.parametrize("source", [None, "", "relative-path", "blob:https://example.com/1", "https://u:p@example.com/v"])
def test_invalid_video_urls_never_become_page_url(source):
    result = extract(note={"noteId": NOTE_ID, "type": "video", "video": {"media": {"stream": {"h264": [{"masterUrl": source}]}}}})
    assert result["note"]["video"]["media"]["stream"] == {}
    assert "video_source_unavailable" in result["warnings"]


@pytest.mark.parametrize("media", ["invalid json", "x" * 1048577], ids=["invalid", "oversized"])
def test_malformed_media_v2_is_bounded_and_warned(media):
    result = extract(note={"noteId": NOTE_ID, "type": "video", "video": {"mediaV2": media}})
    assert ("video_media_v2_too_large" if len(media) > 1048576 else "video_media_v2_invalid") in result["warnings"]
    assert "video_source_unavailable" in result["warnings"]
