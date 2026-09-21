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
const f = JSON.parse(process.argv[1]);
global.location = {href:'https://www.xiaohongshu.com'+f.path};
global.window = {__INITIAL_STATE__: {secret:'NEVER_EXPORT', note:{noteDetailMap:{[process.argv[2]]:{note:f.note}, other:{note:{title:'OTHER_NOTE'}}}}}};
global.document = {querySelectorAll: selector => f.selectors.some(s=>selector.includes(s)) ? [{getClientRects:()=>[{}]}] : []};
global.getComputedStyle = () => ({visibility:'visible'});
const result = new Function(process.argv[3])(process.argv[2]);
console.log(JSON.stringify(result));
"""
    return json.loads(subprocess.check_output(["node", "-e", harness, json.dumps(fixture), NOTE_ID, NOTE_DETAIL_SCRIPT], text=True))


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
