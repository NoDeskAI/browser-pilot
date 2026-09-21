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


def test_live_provider_groups_are_preserved_not_mislabelled_as_codecs():
    video = {"media": {"stream": {
        "EF4": [{"masterUrl": "https://cdn.example/avc.mp4", "videoCodec": "EF4", "weight": 1}],
        "EF5": [{"masterUrl": "https://cdn.example/hevc.mp4", "videoCodec": "EF5", "weight": 2},
                {"masterUrl": "https://cdn.example/hevc-hd.mp4", "weight": 3}],
        "EF6": [], "EF7": [],
    }}, "mediaV2": json.dumps({"stream": {"EF4": [{"master_url": "https://cdn.example/avc.mp4"}]}})}
    result = extract(note={"noteId": NOTE_ID, "type": "video", "video": video})
    groups = result["note"]["video"]["media"]["stream"]
    assert {key: len(items) for key, items in groups.items()} == {"EF4": 1, "EF5": 2}
    assert groups["EF5"][1]["weight"] == 3
    assert groups["EF4"][0]["source"] == "note.video.media.stream"
    assert "video_source_unavailable" not in result["warnings"]


def test_root_media_v2_stream_is_supported():
    result = extract(note={"noteId": NOTE_ID, "type": "video", "video": {
        "mediaV2": json.dumps({"stream": {"EF4": [{"master_url": "https://cdn.example/video.mp4", "secret": "NEVER_EXPORT"}]}})
    }})
    candidate = result["note"]["video"]["media"]["stream"]["EF4"][0]
    assert candidate["masterUrl"] == "https://cdn.example/video.mp4"
    assert candidate["source"] == "note.video.mediaV2.stream"
    assert "NEVER_EXPORT" not in json.dumps(result)


def test_provider_groups_and_entries_are_bounded_and_fields_whitelisted():
    groups = {f"EF{i}": [{"masterUrl": f"https://cdn.example/{i}-{j}.mp4", "private": "NEVER_EXPORT"} for j in range(20)] for i in range(20)}
    result = extract(note={"noteId": NOTE_ID, "video": {"media": {"stream": groups}}})
    returned = result["note"]["video"]["media"]["stream"]
    assert len(returned) == 16
    assert all(len(items) == 16 for items in returned.values())
    assert "NEVER_EXPORT" not in json.dumps(result)


def test_unsafe_group_names_are_not_exported():
    groups = {key: [{"masterUrl": "https://cdn.example/v.mp4"}] for key in ["__proto__", "constructor", "prototype", "bad/key", "x" * 25]}
    assert extract(note={"noteId": NOTE_ID, "video": {"media": {"stream": groups}}})["note"]["video"]["media"]["stream"] == {}
