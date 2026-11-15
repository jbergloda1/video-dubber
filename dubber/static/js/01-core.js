const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
let SETTINGS = {}, CUR = null, ES = null, LINES = null, STATE = null;
const STEP_NAMES = {};

function toast(msg, ms = 3500) { const t = $('#toast'); t.textContent = msg; t.style.display = 'block'; clearTimeout(t._h); t._h = setTimeout(() => t.style.display = 'none', ms); }
async function api(path, opt = {}) {
  const r = await fetch(path, { headers: { 'Content-Type': 'application/json' }, ...opt, body: opt.body && typeof opt.body !== 'string' ? JSON.stringify(opt.body) : opt.body });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m } catch { } throw new Error(m); }
  return r.headers.get('content-type')?.includes('json') ? r.json() : r;
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtT = t => { t = Math.max(0, t || 0); const m = Math.floor(t / 60), s = (t % 60).toFixed(1).padStart(4, '0'); return `${m}:${s}` };

/* ---------------- form <-> settings ---------------- */
const FORM_KEYS = ['tts_provider', 'voice_gender_mode', 'tts_fallback_local', 'elevenlabs_model', 'elevenlabs_stability', 'elevenlabs_similarity', 'elevenlabs_style', 'elevenlabs_speed',
  'translate_provider', 'translate_style', 'translate_style_preset', 'hook_intro', 'fit_to_time', 'style_auto_lean', 'source_lang', 'target_lang', 'hide_original_subs', 'hide_style', 'preview_seconds', 'output_short_side',
  'bg_volume', 'voice_volume', 'original_voice_volume', 'max_speedup', 'sub_font_scale', 'sub_style', 'separate_music', 'burn_subtitles', 'wait_for_preview', 'screen_text', 'screen_text_clean', 'screen_text_font', 'screen_text_min_height',
  'google_speed', 'separate_model', 'bg_duck', 'vo_original_volume', 'vo_music_volume', 'narrator_remove', 'wm_remove', 'wm_clean', 'brand_enabled', 'brand_tiktok', 'brand_youtube', 'brand_facebook',
  'brand_motion', 'brand_show', 'brand_cycle_s', 'brand_opacity', 'brand_size', 'brand_credit', 'tiktok_check', 'tiktok_autofix', 'content_enabled', 'content_review', 'content_persona', 'content_tone', 'transform_zoom', 'outro_seconds', 'transform_grade', 'outro_text'];
let MODE = 'dub';

function setVal(el, v) { if (!el) return; if (el.type === 'checkbox') el.checked = !!v; else el.value = v ?? ''; el.dispatchEvent(new Event('input')); }
function getVal(el) { if (el.type === 'checkbox') return el.checked; if (el.type === 'range' || el.type === 'number') return parseFloat(el.value); return el.value; }

/* voices: one male + one female per provider */
const VOICE_KEYS = ['edge_voice_male', 'edge_voice_female', 'elevenlabs_voice_male', 'elevenlabs_voice_female', 'piper_voice_male', 'piper_voice_female', 'vieneu_voice_male', 'vieneu_voice_female'];
const VOICES = {};          // current values of the six keys
const VOICE_LISTS = {};     // provider -> [{id,name,gender}]
const prov = () => $('#tts_provider').value;

async function loadVoices(force) {
  const p = prov();
  $('#voiceWarn').textContent = '';
  if (force || !VOICE_LISTS[p]) {
    try {
      const r = await api(`/api/voices?provider=${p}&lang=` + ($('#target_lang').value || 'vi'));
      VOICE_LISTS[p] = r.voices; if (r.warning) $('#voiceWarn').textContent = '⚠ ' + r.warning;
    } catch (e) { $('#voiceWarn').textContent = '⚠ ' + e.message; VOICE_LISTS[p] = VOICE_LISTS[p] || []; }
  }
  for (const g of ['male', 'female']) {
    const sel = g === 'male' ? $('#voiceMale') : $('#voiceFemale'), key = `${p}_voice_${g}`;
    sel.innerHTML = '';
    const list = [...VOICE_LISTS[p]].sort((a, b) => (b.gender === g) - (a.gender === g));
    list.forEach(v => sel.add(new Option((v.gender && v.gender !== g ? '· ' : '') + v.name + (v.info ? ` — ${v.info}` : ''), v.id)));
    if (VOICES[key] && ![...sel.options].some(o => o.value === VOICES[key])) sel.add(new Option(VOICES[key] + ' (đã lưu)', VOICES[key]), 0);
    sel.value = VOICES[key] || sel.options[0]?.value || '';
    VOICES[key] = sel.value;
    sel.onchange = () => VOICES[key] = sel.value;
  }
  $('#pasteVoice').hidden = p !== 'elevenlabs';
}
$('#reloadVoices').onclick = () => loadVoices(true);
$('#pasteVoice').onclick = () => {
  const id = prompt('Dán Voice ID của ElevenLabs (trang Voices → ⋯ → Copy voice ID):'); if (!id) return;
  const g = confirm('Dùng giọng này cho NAM? (OK = nam, Cancel = nữ)') ? 'male' : 'female';
  VOICES[`elevenlabs_voice_${g}`] = id.trim(); loadVoices();
};

/* ---- translation style ---- */
let STYLE_MENU = {}, JSTYLE = null;
api('/api/styles').then(r => { r.presets.forEach(x => STYLE_MENU[x.id] = x); syncStyle(); }).catch(() => {});
function syncStyle() {
  const v = $('#translate_style_preset').value;
  $('#translate_style').style.display = v === 'custom' ? '' : 'none';
  $('#styleLeanRow').style.display = v === 'auto' ? '' : 'none';
  $('#styleHint').textContent = v === 'auto' ? 'AI đọc tiêu đề + vài câu đầu rồi chọn phong cách hợp với từng video. Video buồn/nghiêm túc (tai nạn, thiên tai, tin buồn…) không bao giờ bị dịch kiểu bựa.'
    : v === 'custom' ? '' : (STYLE_MENU[v]?.text || '');
}
$('#translate_style_preset').addEventListener('input', syncStyle);
$('#translate_style_preset').addEventListener('change', syncStyle);
async function loadStyle() {
  try { JSTYLE = await api(`/api/jobs/${CUR}/style`); } catch { JSTYLE = null; }
  $$('[data-sb]').forEach(b => {
    b.style.display = JSTYLE ? '' : 'none'; if (!JSTYLE) return;
    b.querySelector('.sb-cur').innerHTML = `Phong cách: <b>${esc(JSTYLE.name || JSTYLE.preset)}</b>` +
      (JSTYLE.auto ? ` <span class="hint">(tự chọn${JSTYLE.why ? ': ' + esc(JSTYLE.why) : ''})</span>` : '') +
      (JSTYLE.note ? `<div class="hint">${esc(JSTYLE.note)}</div>` : '');
    b.querySelector('.sb-sel').value = JSTYLE.preset === 'custom' ? 'custom' : JSTYLE.preset;
  });
}
$$('[data-sb] .sb-go').forEach(btn => btn.onclick = async () => {
  const sel = btn.parentElement.querySelector('.sb-sel').value;
  const opts = { translate_style_preset: sel };
  if (sel === 'custom') { const txt = prompt('Mô tả giọng văn bạn muốn:', JSTYLE?.text || ''); if (!txt) return; opts.translate_style = txt; }
  const textOnly = (STATE?.options?.output_mode || MODE) === 'text';
  if (!confirm(`Dịch lại ${textOnly ? 'chữ trên màn hình' : 'lời nói'} theo phong cách mới rồi tạo lại giọng đọc và render? Các chỗ bạn đã sửa tay trong bảng sẽ bị thay.`)) return;
  try { await api(`/api/jobs/${CUR}/rerun`, { method: 'POST', body: { from_step: textOnly ? 'screen_text' : 'translate', options: opts } }); toast('Đang dịch lại…'); }
  catch (e) { toast(e.message, 8000) }
});

function fillForm(s) {
  if (s.translate_provider === 'google') s = { ...s, translate_provider: 'google_free' };   // old settings
  FORM_KEYS.forEach(k => setVal($('#' + k), s[k]));
  VOICE_KEYS.forEach(k => VOICES[k] = s[k]);
  setVal($('#edge_rate_n'), parseInt(s.edge_rate) || 0);
  setVal($('#edge_pitch_n'), parseInt(s.edge_pitch) || 0);
  setMode(s.output_mode || 'dub');
  syncProvider(); syncStyle();
}
function formOptions() {
  const o = { output_mode: MODE, ...VOICES };
  FORM_KEYS.forEach(k => { const el = $('#' + k); if (el) o[k] = getVal(el); });
  const r = +$('#edge_rate_n').value, p = +$('#edge_pitch_n').value;
  o.edge_rate = (r >= 0 ? '+' : '') + r + '%';
  o.edge_pitch = (p >= 0 ? '+' : '') + p + 'Hz';
  o.output_short_side = parseInt(o.output_short_side);
  return o;
}
function setMode(m) {
  MODE = m; $$('.mode').forEach(x => x.classList.toggle('on', x.dataset.mode === m));
  const voiced = m === 'dub' || m === 'voiceover' || m === 'auto';
  $('#voiceBox').style.display = voiced ? '' : 'none';
  $('#mixBox').style.display = voiced ? '' : 'none';
  $('#voMixBox').style.display = m === 'voiceover' ? '' : 'none';
  $('#screenTextRow').style.display = $('#burnRow').style.display = m === 'text' ? 'none' : '';   // forced in text mode
  $('#voiceNone').hidden = $('#audioNone').hidden = voiced;
}
/* form tabs */
function showFTab(k) {
  if (!$(`.fpanel[data-ft="${k}"]`)) k = 'voice';
  $$('#ftabs button').forEach(b => b.classList.toggle('on', b.dataset.ft === k));
  $$('.fpanel').forEach(x => x.hidden = x.dataset.ft !== k);
  try { localStorage.setItem('ftab', k) } catch { }
}
$$('#ftabs button').forEach(b => b.onclick = () => showFTab(b.dataset.ft));
{ let k = 'voice'; try { k = localStorage.getItem('ftab') || k } catch { } showFTab(k); }
const MODE_LABEL = { dub: 'Lồng tiếng', sub: 'Sub', text: 'Chữ màn hình' };
function syncProvider() {
  const p = prov();
  $('#edgeBox').hidden = p !== 'edge'; $('#elevenBox').hidden = p !== 'elevenlabs'; $('#googleBox').hidden = p !== 'google';
  loadVoices();
}
$$('.mode').forEach(x => x.onclick = () => setMode(x.dataset.mode));
$('#tts_provider').onchange = syncProvider;
$$('input[type=range]').forEach(r => r.addEventListener('input', () => {
  const o = r.parentElement.querySelector('output'); if (!o) return;
  if (r.id === 'edge_rate_n') o.textContent = (r.value >= 0 ? '+' : '') + r.value + '%';
  else if (r.id === 'edge_pitch_n') o.textContent = (r.value >= 0 ? '+' : '') + r.value + 'Hz';
  else if (r.id === 'sub_font_scale') o.textContent = Math.round(r.value * 1080) + 'px';
  else if (r.id === 'max_speedup') o.textContent = (+r.value).toFixed(2) + 'x';
  else if (r.id === 'screen_text_min_height') o.textContent = (r.value * 100).toFixed(1) + '%';
  else o.textContent = (+r.value).toFixed(2);
}));

$('#target_lang').onchange = () => { delete VOICE_LISTS.edge; loadVoices(); };

$$('.testBtn').forEach(b => b.onclick = async () => {
  const label = b.textContent; b.disabled = true; b.textContent = '…';
  try {
    const opts = { ...formOptions(), voice_gender_mode: b.dataset.g };
    const r = await fetch('/api/tts-test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text: $('#testText').value, options: opts, gender: b.dataset.g }) });
    if (!r.ok) throw new Error((await r.json()).detail);
    const a = $('#testAudio'); a.src = URL.createObjectURL(await r.blob()); a.play();
  } catch (e) { toast('Lỗi nghe thử: ' + e.message, 8000) } finally { b.disabled = false; b.textContent = label }
});

async function startJob(url, opts) {
  const r = await api('/api/jobs', { method: 'POST', body: { url, options: opts } }); $('#url').value = ''; await loadJobs(); openJob(r.id);
}
const fmtLong = s => { s = Math.round(s); const h = Math.floor(s / 3600), m = Math.floor(s % 3600 / 60), x = s % 60; return h ? `${h} giờ ${m} phút` : m ? `${m} phút ${x ? x + ' giây' : ''}` : `${x} giây`; };
const fmtVnd = v => Math.round(v).toLocaleString('vi-VN') + ' đ';
function renderEstimate(e) {
  const money = e.money.map(m => `<tr><td>${esc(m.item)}<div class="hint">${esc(m.note || '')}</div></td>
    <td class="n">${m.usd === null || m.usd === undefined ? (m.vnd !== undefined ? fmtVnd(m.vnd) : '?') : m.usd === 0 ? 'miễn phí' : '$' + m.usd.toFixed(m.usd < 0.01 ? 4 : 3)}</td></tr>`).join('');
  const steps = e.steps.map(st => `<tr><td>${esc(st.name)}${st.samples ? '' : ' <span class="hint">(mặc định)</span>'}</td><td class="n">${fmtLong(st.secs)}</td></tr>`).join('');
  return `${e.title ? `<div style="margin-top:8px"><b>${esc(e.title.slice(0, 90))}</b><div class="hint">${esc(e.uploader || '')}</div></div>` : ''}
    <div class="est-big">
      <div><small>Độ dài video${e.count ? ` (${e.count} video)` : ''}</small><b>${fmtLong(e.duration)}</b></div>
      <div><small>Thời gian xử lý ≈</small><b>${fmtLong(e.total_secs)}</b></div>
      <div><small>Chi phí ≈</small><b>${e.total_usd > 0 ? '$' + e.total_usd.toFixed(3) : '0 $'}</b><small>≈ ${fmtVnd(e.total_vnd)} (gồm điện)${e.price_unknown ? ' · có mục chưa rõ giá' : ''}</small></div>
    </div>
    ${(e.warnings || []).map(w => `<div class="hint" style="color:var(--warn)">⚠ ${esc(w)}</div>`).join('')}
    <label>Chi phí</label><table class="est-t">${money}</table>
    <details style="margin-top:10px"><summary>Thời gian từng bước · dung lượng ổ cứng ~${(e.disk_mb / 1024).toFixed(1)} GB</summary>
    <table class="est-t">${steps}</table><div class="hint">Thời gian học từ các job trước trên máy bạn; bước ghi “mặc định” chưa có số đo.</div></details>`;
}
let EST_GO = null;
async function showEstimate(body, title, go) {
  EST_GO = go; $('#estTitle').textContent = title; $('#estAsk').checked = SETTINGS.confirm_estimate !== false;
  $('#estBody').innerHTML = '<div class="hint" style="padding:18px 0">⏳ Đang lấy thông tin video (độ dài) để ước tính… Douyin có thể mất 10–30 giây.</div>';
  $('#estGo').disabled = false; $('#dlgEst').showModal();
  try { $('#estBody').innerHTML = renderEstimate(await api('/api/estimate', { method: 'POST', body })); }
  catch (e) { $('#estBody').innerHTML = `<div class="err-box" style="margin-top:10px">${esc(e.message)}</div><div class="hint">Vẫn có thể bấm Bắt đầu để chạy mà không có ước tính.</div>`; }
}
$('#estGo').onclick = async () => { $('#estGo').disabled = true; try { await EST_GO(); $('#dlgEst').close(); } catch (e) { toast(e.message) } finally { $('#estGo').disabled = false } };
$('#estCancel').onclick = $('#estClose').onclick = () => $('#dlgEst').close();
$('#estAsk').onchange = async () => { SETTINGS.confirm_estimate = $('#estAsk').checked; await api('/api/settings', { method: 'PUT', body: { confirm_estimate: SETTINGS.confirm_estimate } }); };
/* ---- import a video from this computer ---- */
let UPFILE = null, UPDUR = 0;
function pickFile(f) {
  if (!f) return;
  if (!/^video\//.test(f.type) && !/\.(mp4|mov|mkv|webm|avi|m4v|flv|ts)$/i.test(f.name)) return toast('Chỉ nhận file video');
  UPFILE = f; UPDUR = 0;
  $('#dropTxt').innerHTML = `Đã chọn: <b>${esc(f.name)}</b> (${(f.size / 1048576).toFixed(0)} MB)`;
  $('#dropClear').hidden = false; $('#url').value = '';
  const v = document.createElement('video'); v.preload = 'metadata';      // read the duration locally for the estimate
  v.onloadedmetadata = () => { UPDUR = v.duration || 0; URL.revokeObjectURL(v.src); }; v.src = URL.createObjectURL(f);
}
function clearFile() { UPFILE = null; UPDUR = 0; $('#fileIn').value = ''; $('#dropClear').hidden = true;
  $('#dropTxt').innerHTML = '…hoặc <b>kéo thả video từ máy vào đây</b> / bấm để chọn file (mp4, mov, mkv, webm…)'; }
$('#drop').onclick = e => { if (e.target.id !== 'dropClear') $('#fileIn').click(); };
$('#dropClear').onclick = e => { e.stopPropagation(); clearFile(); };
$('#fileIn').onchange = () => pickFile($('#fileIn').files[0]);
['dragenter', 'dragover'].forEach(t => $('#drop').addEventListener(t, e => { e.preventDefault(); $('#drop').classList.add('over'); }));
['dragleave', 'drop'].forEach(t => $('#drop').addEventListener(t, e => { e.preventDefault(); $('#drop').classList.remove('over'); }));
$('#drop').addEventListener('drop', e => pickFile(e.dataTransfer.files[0]));
$('#url').addEventListener('input', () => { if ($('#url').value.trim() && UPFILE) clearFile(); });
function uploadJob(f, opts) {
  return new Promise((resolve, reject) => {
    const fd = new FormData(); fd.append('file', f); fd.append('options', JSON.stringify(opts));
    const x = new XMLHttpRequest(); x.open('POST', '/api/jobs/upload');
    $('#upbar').hidden = false;
    x.upload.onprogress = e => { if (e.lengthComputable) $('#upbarI').style.width = (100 * e.loaded / e.total) + '%'; };
    x.onload = async () => {
      $('#upbar').hidden = true; $('#upbarI').style.width = 0;
      let r = {}; try { r = JSON.parse(x.responseText) } catch { }
      if (x.status >= 300) return reject(new Error(r.detail || x.statusText));
      clearFile(); await loadJobs(); openJob(r.id); resolve(r);
    };
    x.onerror = () => { $('#upbar').hidden = true; reject(new Error('Tải file lên thất bại')); };
    x.send(fd);
  });
}
function startCurrent(opts) { return UPFILE ? uploadJob(UPFILE, opts) : startJob($('#url').value.trim(), opts); }
function estimateBody(opts) { return UPFILE ? { durations: [UPDUR || 60], options: opts } : { url: $('#url').value.trim(), options: opts }; }
$('#btnStart').onclick = async () => {
  if (!UPFILE && !$('#url').value.trim()) return toast('Dán link hoặc chọn một video trên máy trước đã');
  const opts = formOptions();
  if (SETTINGS.confirm_estimate === false) { try { await startCurrent(opts) } catch (e) { toast(e.message) } return; }
  showEstimate(estimateBody(opts), 'Ước tính trước khi chạy', () => startCurrent(opts));
};
$('#btnEstimate').onclick = () => {
  if (!UPFILE && !$('#url').value.trim()) return toast('Dán link hoặc chọn một video trên máy trước đã');
  const opts = formOptions(); showEstimate(estimateBody(opts), 'Ước tính chi phí & thời gian', () => startCurrent(opts));
};
$('#openExports').onclick = $('#openFinal').onclick = () => api('/api/exports/open', { method: 'POST' }).catch(e => toast(e.message));
$('#exportAll').onclick = async () => {
  const b = $('#exportAll'); b.disabled = true; b.textContent = '⏳ Đang gom…';
  try { const r = await api('/api/exports/all', { method: 'POST' }); toast(`Đã gom ${r.videos} video của ${r.jobs} job vào ${r.folder}`, 6000); }
  catch (e) { toast(e.message, 8000) } finally { b.disabled = false; b.textContent = '⤓ Gom tất cả' }
};
$('#btnSaveDefault').onclick = async () => { await api('/api/settings', { method: 'PUT', body: formOptions() }); toast('Đã lưu làm mặc định'); };

/* ---------------- presets: one click sets the options for a purpose ---------------- */
const PRESETS = {
  fast: { label: 'Reup nhanh: tự nhận diện kiểu video, không chờ duyệt, không Biên kịch', set: { output_mode: 'auto', content_enabled: false, wait_for_preview: false, hook_intro: true } },
  film: { label: 'Phim / truyện: thuyết minh giữ giọng gốc + Biên kịch (hook, lời kể, cắt lặng) + dịch chữ màn hình', set: { output_mode: 'voiceover', content_enabled: true, screen_text: true } },
  sub: { label: 'Chỉ phụ đề Việt, giữ nguyên âm thanh gốc', set: { output_mode: 'sub', content_enabled: false, burn_subtitles: true, wait_for_preview: false } },
};
$$('#presets button').forEach(b => {
  if (PRESETS[b.dataset.ps]) b.title = PRESETS[b.dataset.ps].label;
  b.onclick = () => {
    const k = b.dataset.ps;
    fillForm(k === 'default' ? SETTINGS : { ...formOptions(), ...PRESETS[k].set });
    $$('#presets button').forEach(x => x.classList.toggle('on', x === b && k !== 'default'));
    toast(k === 'default' ? 'Đã về lựa chọn mặc định đã lưu' : PRESETS[k].label, 5000);
  };
});
