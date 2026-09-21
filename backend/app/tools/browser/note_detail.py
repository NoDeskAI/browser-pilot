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
  if (typeof v !== 'string' || v.length > 8192) return '';
  try {
    const u = new URL(v, location.href);
    return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : '';
  } catch { return ''; }
};
const size = v => Number.isFinite(Number(v)) && Number(v) >= 0 ? Number(v) : null;
const imageList = (Array.isArray(note.imageList) ? note.imageList : []).slice(0, 100).map(i => ({
  urlDefault: url(i.urlDefault), urlPre: url(i.urlPre), url: url(i.url), width: size(i.width), height: size(i.height)
}));
const stream = {};
for (const codec of ['h264', 'h265', 'av1']) {
  const list = note.video?.media?.stream?.[codec];
  if (Array.isArray(list)) stream[codec] = list.slice(0, 16).map(v => ({
    masterUrl: url(v.masterUrl), backupUrls: (Array.isArray(v.backupUrls) ? v.backupUrls : []).slice(0, 4).map(url).filter(Boolean),
    width: size(v.width), height: size(v.height), duration: size(v.duration)
  }));
}
const interactInfo = {};
for (const k of ['likedCount', 'collectedCount', 'commentCount', 'shareCount']) interactInfo[k] = text(note.interactInfo?.[k], 64);
return {ok: true, schemaVersion: 1, source: 'current_page_note_store',
  pageUrl: page.origin + page.pathname, note: {
    noteId: expected, title: text(note.title), desc: text(note.desc, 100000), type: text(note.type, 32),
    tagList: (Array.isArray(note.tagList) ? note.tagList : []).slice(0, 100).map(t => ({name: text(t.name, 256)})),
    user: {userId: text(note.user?.userId, 128), nickname: text(note.user?.nickname, 512), avatar: url(note.user?.avatar)},
    interactInfo, time: text(note.time, 64), ipLocation: text(note.ipLocation, 256), imageList,
    video: {media: {stream}}
  }, meta: {}, warnings: ['media_urls_may_require_browser_context_and_can_expire']};
"""
