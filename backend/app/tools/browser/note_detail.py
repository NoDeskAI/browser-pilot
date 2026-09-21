"""Fixed, read-only current-note extraction; never return the whole page store."""

NOTE_DETAIL_SCRIPT = r"""
const expected = arguments[0];
const fail = error => ({ok: false, error});
const page = new URL(location.href);
if (!['www.xiaohongshu.com', 'xiaohongshu.com'].includes(page.hostname))
  return fail('unsupported_page');
const visible = el => !!el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden';
if ([...document.querySelectorAll('[class*="captcha"], iframe[src*="captcha"], #captcha')].some(visible))
  return fail('verification_required');
if (/\/login(?:\/|$)/.test(page.pathname) || [...document.querySelectorAll('.login-container, .login-modal')].some(visible))
  return fail('login_required');
const match = page.pathname.match(/^\/(?:explore|discovery\/item)\/([a-fA-F0-9]{24})(?:\/|$)/);
const current = match ? match[1] : page.searchParams.get('note_id');
if (current !== expected) return fail('note_id_mismatch');
// Read exactly the requested key. Never fall back to the first cached note.
const store = window.__INITIAL_STATE__?.note?.noteDetailMap;
const entry = store && Object.prototype.hasOwnProperty.call(store, expected) ? store[expected] : null;
const note = entry?.note;
if (!note || note.noteId !== expected) return fail('note_detail_unavailable');
const text = (v, max=4096) => typeof v === 'string' ? v.slice(0, max) : typeof v === 'number' && Number.isFinite(v) ? String(v) : '';
const url = v => {
  if (typeof v !== 'string' || !v.trim() || v.length > 8192 || !/^(https?:\/\/|\/\/)/i.test(v)) return '';
  try {
    const u = new URL(v, location.href);
    return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';
  } catch { return ''; }
};
const size = v => Number.isFinite(Number(v)) && Number(v) >= 0 ? Number(v) : null;
const imageList = (Array.isArray(note.imageList) ? note.imageList : []).slice(0, 100).map(i => ({
  urlDefault: url(i.urlDefault), urlPre: url(i.urlPre), url: url(i.url), width: size(i.width), height: size(i.height)
}));
const stream = Object.create(null);
const warnings = ['media_urls_may_require_browser_context_and_can_expire'];
let mediaV2 = note.video?.mediaV2;
if (typeof mediaV2 === 'string') {
  if (mediaV2.length > 1048576) {
    warnings.push('video_media_v2_too_large');
    mediaV2 = null;
  } else {
    try { mediaV2 = JSON.parse(mediaV2); }
    catch { warnings.push('video_media_v2_invalid'); mediaV2 = null; }
  }
}
// Provider group names need not be codec names (the live page uses EF4/EF5).
// Preserve them without inferring an encoding, and export only known fields.
const sources = [
  [note.video?.media?.stream, false, 'note.video.media.stream'],
  [mediaV2?.stream, true, 'note.video.mediaV2.stream'],
  [mediaV2?.video?.stream, true, 'note.video.mediaV2.video.stream']
];
for (const [groups, legacy, source] of sources) {
  if (!groups || typeof groups !== 'object' || Array.isArray(groups)) continue;
  for (const [group, list] of Object.entries(groups).slice(0, 16)) {
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,23}$/.test(group) || ['constructor', 'prototype'].includes(group) || !Array.isArray(list)) continue;
    for (const v of list.slice(0, 16)) {
      if (!v || typeof v !== 'object') continue;
      const masterUrl = url(legacy ? v.master_url : v.masterUrl);
      if (!masterUrl) continue;
      if (!stream[group]) {
        if (Object.keys(stream).length >= 16) continue;
        stream[group] = [];
      }
      if (stream[group].length >= 16 || stream[group].some(item => item.masterUrl === masterUrl)) continue;
      const backups = legacy ? v.backup_urls : v.backupUrls;
      stream[group].push({
        masterUrl, backupUrls: (Array.isArray(backups) ? backups : []).slice(0, 4).map(url).filter(Boolean),
        width: size(v.width), height: size(v.height), duration: size(v.duration),
        weight: size(v.weight), source
      });
    }
  }
}
if (note.type === 'video' && !Object.keys(stream).length) warnings.push('video_source_unavailable');
const interactInfo = {};
for (const k of ['likedCount', 'collectedCount', 'commentCount', 'shareCount']) interactInfo[k] = text(note.interactInfo?.[k], 64);
return {ok: true, schemaVersion: 1, source: 'current_page_note_store',
  pageUrl: page.origin + page.pathname, note: {
    noteId: expected, title: text(note.title), desc: text(note.desc, 100000), type: text(note.type, 32),
    tagList: (Array.isArray(note.tagList) ? note.tagList : []).slice(0, 100).map(t => ({name: text(t.name, 256)})),
    user: {userId: text(note.user?.userId, 128), nickname: text(note.user?.nickname, 512), avatar: url(note.user?.avatar)},
    interactInfo, time: text(note.time, 64), ipLocation: text(note.ipLocation, 256), imageList,
    video: {media: {stream}}
  }, meta: {}, warnings};
"""
