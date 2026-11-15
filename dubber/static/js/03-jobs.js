/* ---------------- jobs list ---------------- */
const VI_TITLE = {};
const STATUS_VI = { queued: 'Chờ', running: 'Đang chạy', done: 'Xong', error: 'Lỗi', cancelled: 'Đã huỷ', awaiting_approval: 'Chờ duyệt' };
async function loadJobs() {
  const js = await api('/api/jobs');
  js.forEach(j => VI_TITLE[j.id] = j.title_vi || '');
  $('#jobs').innerHTML = js.length ? js.map(j => `
    <div class="jobitem ${CUR === j.id ? 'sel' : ''}" data-id="${j.id}">
      <div class="t"><div title="${esc(j.title_vi ? j.title_vi + '\n— ' + (j.title || '') : (j.title || j.url))}">${esc(j.title_vi || j.title || j.url)}</div>${j.title_vi ? `<div class="orig">${esc(j.title || '')}</div>` : ''}<small><span>${new Date(j.created * 1000).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })} · ${MODE_LABEL[j.options?.output_mode || 'dub'] || ''}</span>
      <span class="badge b-${j.status}">${STATUS_VI[j.status] || j.status}</span></small></div></div>`).join('') : '<div class="hint" style="padding:8px">Chưa có job nào.</div>';
  $$('.jobitem').forEach(x => x.onclick = () => openJob(x.dataset.id));
  filterJobs();
}

/* ---------------- job detail (SSE) ---------------- */
// The form on the left shows the OPEN job's settings (saved defaults + what this job overrode), so "Chạy lại"
// keeps them — it used to show the defaults and a rerun silently replaced the job's settings with them.
function showJobSettings(j) {
  fillForm({ ...SETTINGS, ...(j.options || {}) });
  $('#jobFormName').textContent = (j.title || j.id).slice(0, 60);
  $('#jobFormNote').hidden = false; $('#formTitle').textContent = 'Cài đặt của job đang mở';
  if (!$('#formTitle').parentElement.classList.contains('in-run')) $('#formTitle').parentElement.classList.add('job-open');
}
function showDefaultSettings() {
  fillForm(SETTINGS); $('#jobFormNote').hidden = true; $('#formTitle').textContent = 'Tạo video mới';
  $('#formTitle').parentElement.classList.remove('job-open');
}
$('#btnFormDefault').onclick = () => newJob();
function newJob() {
  if (ES) { ES.close(); ES = null; }
  CUR = null; STATE = null; showDefaultSettings(); $('#mainView').classList.remove('has-job');
  $$('.jobitem').forEach(x => x.classList.remove('sel'));
  $('#jobView').hidden = true; $('#emptyDetail').hidden = false;
  history.replaceState(null, '', location.pathname);
  showView('main'); $('#url').focus();
}
$('#btnNewJob').onclick = newJob;
function filterJobs() {
  const q = $('#jobSearch').value.trim().toLowerCase();
  $$('.jobitem').forEach(x => x.hidden = !!q && !x.textContent.toLowerCase().includes(q));
}
$('#jobSearch').oninput = filterJobs;
function openJob(id) {
  CUR = id; LINES = null; STATE = null; STEV = null; $('#stCard').hidden = true; CT = null; $('#ctCard').hidden = true; WM = null; $('#wmCard').hidden = true; POST = null; $('#postCard').hidden = true; TT = null; $('#ttCard').hidden = true; RV = null; $('#rvCard').hidden = true;
  $$('.jobitem').forEach(x => x.classList.toggle('sel', x.dataset.id === id));
  $('#emptyDetail').hidden = true; $('#jobView').hidden = false; $('#log').innerHTML = ''; $('#videos').innerHTML = ''; $('#vtabs').innerHTML = '';
  if (ES) ES.close();
  ES = new EventSource(`/api/jobs/${id}/events`);
  ES.onmessage = ev => render(JSON.parse(ev.data));
  history.replaceState(null, '', '#' + id);
  $('#mainView').classList.add('has-job');
  if (innerWidth <= 1000) setTimeout(() => $('#detail').scrollIntoView({ block: 'start' }), 300);   // phone: jump to the job
}

function render(j) {
  const prev = STATE; STATE = j;
  if (!prev) showJobSettings(j);
  $('#jTitle').textContent = VI_TITLE[j.id] || j.title || 'Video';
  $('#jTitle').title = VI_TITLE[j.id] ? j.title || '' : '';
  $('#jUrl').textContent = j.url;
  const st = $('#jStatus'); st.className = 'badge b-' + j.status; st.textContent = STATUS_VI[j.status] || j.status;
  $('#jSteps').innerHTML = j.steps.map(s => `
    <div class="step ${s.status}"><div class="ic">${s.status === 'done' ? '✓' : s.status === 'error' ? '!' : ''}</div>
      <div class="name">${esc(s.name)}</div>
      <div><div class="bar"><i style="width:${(s.status === 'done' || s.status === 'skipped' ? 1 : s.progress) * 100}%"></i></div>
      <div class="det">${esc(s.detail || '')}</div></div></div>`).join('');
  { const act = j.steps.filter(s => s.status !== 'skipped'), nd = act.filter(s => s.status === 'done').length;
    const cur = j.steps.find(s => s.status === 'running' || s.status === 'error' || s.status === 'waiting');
    const pct = act.length ? act.reduce((a, s) => a + (s.status === 'done' ? 1 : s.progress || 0), 0) / act.length : 0;
    $('#jStepsSum').textContent = j.status === 'done' && j.finishing ? `✅ Video đã xong — đang ${cur ? cur.name.toLowerCase() : 'hoàn thiện'} ở nền…`
      : j.status === 'done' ? `✅ Xong ${nd}/${act.length} bước — bấm để xem chi tiết`
      : cur ? `${cur.status === 'error' ? '❌' : '⏳'} ${cur.name} (${nd}/${act.length} bước)` : `${nd}/${act.length} bước`;
    $('#jStepsBar').style.width = (pct * 100).toFixed(1) + '%'; $('#jStepsBar').style.background = j.status === 'done' ? 'var(--ok)' : j.status === 'error' ? 'var(--err)' : '';
    if (!prev || prev.status !== j.status) $('#jStepsBox').open = j.status !== 'done'; }
  if (!$('#rerunStep').options.length) j.steps.forEach(s => $('#rerunStep').add(new Option('Từ: ' + s.name, s.id)));
  $('#jError').hidden = !j.error; $('#jError').textContent = j.error;
  const busy = j.status === 'running' || j.status === 'queued';
  $('#btnCancel').hidden = !busy; $('#btnRerun').disabled = busy; $('#btnDelete').disabled = busy; $('#btnSwitchMode').disabled = busy;
  $('#btnApprove').hidden = j.status !== 'awaiting_approval';
  const mode = j.options?.output_mode || 'dub';
  { const sm = $('#switchMode'), want = { dub: '🎙 Bản lồng tiếng', voiceover: '📢 Bản thuyết minh', sub: '💬 Bản Sub Việt (audio gốc)', text: '🔤 Bản chỉ dịch chữ màn hình' };
    if (sm.dataset.mode !== mode) { sm.dataset.mode = mode; sm.innerHTML = Object.entries(want).filter(([k]) => k !== mode).map(([k, v]) => `<option value="${k}">${v}</option>`).join(''); }
    sm.disabled = busy; }

  // logs
  const log = $('#log'), atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  (j.logs || []).forEach(l => {
    const d = document.createElement('div'); d.className = 'l-' + l.level; if (l.level === 'debug') d.dataset.debug = 1;
    d.hidden = l.level === 'debug' && !$('#showDebug').checked;
    d.innerHTML = `<time>${new Date(l.t * 1000).toLocaleTimeString('vi-VN')}</time>${esc(l.msg)}`; log.appendChild(d);
  });
  if (atBottom) log.scrollTop = log.scrollHeight;

  // videos (only rebuild when the file set changes)
  const key = JSON.stringify(j.files) + j.status;
  if (!prev || prev._vkey !== key) { renderVideos(j); }
  j._vkey = key;
  // only the open tab loads its data (opening a job used to fire ~12 requests); the others load when you open them
  const ch = !prev || prev.status !== j.status;
  if (!prev) jtPick(j);
  if (ch) JT_STALE = new Set(['result', 'edit', 'review', 'post']);
  const stSt = (x, id) => x?.steps?.find(s => s.id === id)?.status;
  if (stSt(prev, 'tiktok') !== stSt(j, 'tiktok') || (j.files.tiktok_check && !TT)) JT_STALE.add('result');
  if (stSt(prev, 'summary') !== stSt(j, 'summary') || (j.files.post && !POST)) JT_STALE.add('post');
  if ((j.files.script && !LINES) || (j.files.screentext && !STEV) || (j.files.content && !CT?.plan) || (j.files.source && !WM)) JT_STALE.add('edit');
  jtLoad();
  if (prev && prev.status !== j.status) loadJobs();
}

function renderVideos(j) {
  const f = j.files, v = `?v=${j.version}`;
  const items = [];
  if (f.output) items.push(['output', '🎙 Bản lồng tiếng', `/files/${j.id}/output.mp4${v}`]);
  if (f.output_vo) items.push(['output_vo', '📢 Bản thuyết minh', `/files/${j.id}/output_vo.mp4${v}`]);
  if (f.output_sub) items.push(['output_sub', '💬 Bản Sub Việt', `/files/${j.id}/output_sub.mp4${v}`]);
  if (f.output_text) items.push(['output_text', '🔤 Bản dịch chữ màn hình', `/files/${j.id}/output_text.mp4${v}`]);
  if (f.output_tiktok) items.push(['output_tiktok', '📱 Bản TikTok', `/files/${j.id}/output_tiktok.mp4${v}`]);
  if (f.preview) items.push(['preview', `👀 Preview`, `/files/${j.id}/preview.mp4${v}`]);
  if (f.source) items.push(['source', '📥 Video gốc', `/files/${j.id}/source_view.mp4`]);
  const want = $('#vtabs').dataset.sel;
  const sel = items.find(i => i[0] === want) ? want : items[0]?.[0];
  $('#vtabs').dataset.sel = sel || '';
  $('#vtabs').innerHTML = items.map(i => `<button class="${i[0] === sel ? 'on' : ''}" data-k="${i[0]}">${i[1]}</button>`).join('');
  $$('#vtabs button').forEach(b => b.onclick = () => { $('#vtabs').dataset.sel = b.dataset.k; renderVideos(STATE); });
  const it = items.find(i => i[0] === sel);
  const old = $('#videos video');
  if (!it) $('#videos').innerHTML = '<div class="hint">Video sẽ hiện ở đây khi tải xong.</div>';
  else if (!old || old.dataset.src !== it[2]) $('#videos').innerHTML = `<div class="vbox"><h4>${it[1]}</h4><video controls preload="metadata" data-src="${it[2]}" src="${it[2]}"></video></div>`;
  const dl = [['output', 'output.mp4', '⬇ Video lồng tiếng'], ['output_vo', 'output_vo.mp4', '⬇ Video thuyết minh'], ['output_sub', 'output_sub.mp4', '⬇ Video Sub Việt'], ['output_text', 'output_text.mp4', '⬇ Video dịch chữ màn hình'], ['output_tiktok', 'output_tiktok.mp4', '⬇ Bản TikTok 9:16'], ['srt', 'subs.srt', '⬇ Phụ đề .srt'], ['voice', 'voice.wav', '⬇ Giọng đọc .wav'], ['music', 'no_vocals.wav', '⬇ Nhạc nền .wav']];
  loadExports(j);
  const canPost = j.status === 'done' && ['output', 'output_vo', 'output_sub', 'output_text', 'output_tiktok'].some(k => f[k]);
  $('#dls').innerHTML = (canPost ? '<button class="primary" id="ttQuick">📤 Đăng lên TikTok</button>' : '') +
    dl.filter(d => f[d[0]]).map(d => `<a href="/files/${j.id}/${d[1]}?dl=1">${d[2]}</a>`).join('') +
    (f.voice || f.srt ? '<span class="hint" style="align-self:center">← dùng các file này để dựng lại trong CapCut nếu muốn</span>' : '');
  if ($('#ttQuick')) $('#ttQuick').onclick = quickTTPublish;
}

async function loadExports(j) {
  const has = ['output', 'output_vo', 'output_sub', 'output_text', 'output_tiktok'].some(k => j.files[k]);
  $('#exportBox').hidden = !has || j.status !== 'done'; if ($('#exportBox').hidden) return;
  try {
    const r = await api(`/api/jobs/${j.id}/exports`);
    const names = Object.entries(r.files).filter(([k]) => !k.endsWith('_srt')).map(([, p]) => p.split(/[\\/]/).pop());
    $('#exportName').textContent = names.length ? names.join('  ·  ') : r.folder;
  } catch { $('#exportName').textContent = ''; }
}
$('#btnOpenFolder').onclick = () => api(`/api/jobs/${CUR}/open-folder`, { method: 'POST', body: { key: $('#vtabs').dataset.sel } })
  .then(() => loadExports(STATE)).catch(e => toast(e.message));
async function loadLines() {
  try { LINES = await api(`/api/jobs/${CUR}/lines`); } catch { LINES = null; $('#linesCard').hidden = true; return }
  $('#linesCard').hidden = false;
  const hasG = LINES.some(l => l.gender), hasR = LINES.some(l => l.role);
  $('#lines').innerHTML = LINES.map((l, i) => `<tr><td class="tm" data-t="${l.start}">${fmtT(l.start)}</td>
    ${hasG ? `<td style="width:78px"><select data-g="${i}" style="padding:4px"><option value="male" ${l.gender === 'male' ? 'selected' : ''}>🧔 Nam</option><option value="female" ${l.gender === 'female' ? 'selected' : ''}>👩 Nữ</option></select><div class="hint">${esc(l.speaker || '')}</div></td>` : ''}
    ${hasR ? `<td style="width:112px"><select data-r="${i}" data-spk="${esc(l.speaker || '')}" style="padding:4px"><option value="narrator" ${l.role === 'narrator' ? 'selected' : ''}>🎙 Thuyết minh</option><option value="character" ${l.role !== 'narrator' ? 'selected' : ''}>🎭 Nhân vật</option></select></td>` : ''}
    <td class="src">${esc(l.src)}</td>
    <td><textarea data-i="${i}" rows="1">${esc(l.tgt)}</textarea></td></tr>`).join('');
  // changing one line's role offers to apply it to every line of that speaker
  $$('#lines select[data-r]').forEach(sel => sel.onchange = () => {
    const spk = sel.dataset.spk, same = $$(`#lines select[data-r][data-spk="${spk}"]`);
    if (spk && same.length > 1 && confirm(`Đặt tất cả ${same.length} câu của ${spk} là “${sel.value === 'narrator' ? 'Thuyết minh' : 'Nhân vật'}”?`)) same.forEach(x => x.value = sel.value);
  });
  $$('#lines td.tm').forEach(td => td.onclick = () => { const v = $('#videos video'); if (v) { v.currentTime = +td.dataset.t; v.play(); } });
}
let STEV = null;
async function loadScreenText() {
  try { STEV = await api(`/api/jobs/${CUR}/screentext`); } catch { STEV = null; $('#stCard').hidden = true; return }
  $('#stCard').hidden = !STEV.length;
  $('#stTable').innerHTML = STEV.map(e => `<tr><td class="tm" data-t="${e.start}">${fmtT(e.start)}<div class="hint">→ ${fmtT(e.end)}</div></td>
    <td style="width:52px"><label class="chk" style="margin:0"><input type="checkbox" data-keep="${e.id}" checked> Dịch</label></td>
    <td class="src">${esc(e.src)}</td><td><textarea data-st="${e.id}" rows="2">${esc(e.tgt)}</textarea></td></tr>`).join('');
  $$('#stTable td.tm').forEach(td => td.onclick = () => { const v = $('#videos video'); if (v) { v.currentTime = +td.dataset.t + 0.3; v.play(); } });
}
$('#btnSaveST').onclick = async () => {
  const items = STEV.map(e => ({ id: e.id, tgt: $(`#stTable textarea[data-st="${e.id}"]`).value, remove: !$(`#stTable input[data-keep="${e.id}"]`).checked }));
  try { await api(`/api/jobs/${CUR}/screentext`, { method: 'PUT', body: { items } }); toast('Đã lưu, đang render lại…'); } catch (e) { toast(e.message) }
};
/* ---- post kit: title, description, hashtags ---- */
let POST = null, POST_TAB = 'tiktok';
async function loadPost() {
  if (!STATE?.files?.post) { $('#postCard').hidden = !(STATE && ['done'].includes(STATE.status)); POST = null; renderPost(); return; }
  try { POST = await api(`/api/jobs/${CUR}/post`); } catch { POST = null; }
  $('#postCard').hidden = false; renderPost();
}
function field(label, val, rows = 2) {
  return `<div class="post-f"><label>${label} <span class="hint">(${[...(val || '')].length} ký tự)</span></label><textarea rows="${rows}">${esc(val || '')}</textarea><button class="cp">📋 Chép</button></div>`;
}
function renderPost() {
  $$('#postTabs button').forEach(b => b.classList.toggle('on', b.dataset.p === POST_TAB));
  if (!POST) { $('#postInfo').textContent = ''; $('#postBody').innerHTML = '<div class="hint" style="margin-top:8px">Chưa có. Bấm “↻ Viết lại” để tạo tiêu đề, mô tả và hashtag từ nội dung video.</div>'; return; }
  const P = POST.platforms || {};
  $('#postInfo').textContent = `Viết bằng: ${POST.provider || '?'}` + (POST.source_title ? ` · Gốc: ${POST.source_title.slice(0, 80)}` : '');
  const tags = `<label>Hashtag</label><div class="tags">${POST.hashtags.map(t => `<span>${esc(t)}</span>`).join('')}</div>` +
    (POST.original_hashtags?.length ? `<label>Hashtag gốc (dịch)</label><div class="tags">${POST.original_hashtags.map(t => `<span>${esc(t)}</span>`).join('')}</div>` : '');
  let h = '';
  if (POST_TAB === 'tiktok') h = field('Caption TikTok', P.tiktok?.caption, 3) + `<div class="ttpub">
      <select id="ttPrivacy" hidden></select>
      <label class="chk" style="margin:0"><input type="checkbox" id="ttAigc" checked> Gắn nhãn “nội dung AI”</label>
      <button class="primary" id="ttPublish">📤 Đăng lên TikTok</button><span class="hint" id="ttPubInfo"></span></div>`;
  else if (POST_TAB === 'youtube') h = field('Tiêu đề YouTube', P.youtube?.title, 1) + field('Mô tả YouTube', P.youtube?.description, 5);
  else if (POST_TAB === 'facebook') h = field('Bài đăng Facebook', P.facebook?.caption, 5);
  else h = field('Tiêu đề', POST.title, 1) + field('Mô tả ngắn', POST.description, 3) + field('Mô tả + hashtag', POST.description + '\n\n' + POST.hashtags.join(' '), 4);
  $('#postBody').innerHTML = h + tags;
  if ($('#ttPublish')) setupTTPublish();
  $$('#postBody .cp').forEach(b => b.onclick = () => { navigator.clipboard.writeText(b.previousElementSibling.value); toast('Đã chép'); });
  $$('#postBody .tags span').forEach(sp => sp.onclick = () => { navigator.clipboard.writeText(sp.textContent); toast('Đã chép ' + sp.textContent); });
}
$$('#postTabs button').forEach(b => b.onclick = () => { POST_TAB = b.dataset.p; renderPost(); });
$('#btnPostGen').onclick = async () => {
  const b = $('#btnPostGen'); b.disabled = true; b.textContent = '⏳ Đang viết…';
  try { POST = await api(`/api/jobs/${CUR}/post`, { method: 'POST' }); renderPost(); toast('Đã viết xong'); }
  catch (e) { toast(e.message, 8000) } finally { b.disabled = false; b.textContent = '↻ Viết lại' }
};

/* ---- TikTok pre-publish check ---- */
let TT = null, TT_TAB = 'tiktok';
const TT_IC = { ok: '✅', warn: '⚠️', fail: '❌', info: 'ℹ️' };
const TT_NAME = { pass: 'Đạt chuẩn TikTok', warn: 'Đạt, có lưu ý', fail: 'Chưa đạt' };
async function loadTT() {
  const done = STATE && STATE.status === 'done';
  if (!STATE?.files?.tiktok_check) { TT = null; $('#ttCard').hidden = !done; renderTT(); return; }
  try { TT = await api(`/api/jobs/${CUR}/tiktok`); } catch { TT = null; }
  $('#ttCard').hidden = false; renderTT();
}
function renderTT() {
  $('#ttTabs').hidden = !TT?.tiktok;
  $$('#ttTabs button').forEach(b => b.classList.toggle('on', b.dataset.r === TT_TAB));
  if (!TT) { $('#ttBody').innerHTML = '<div class="hint">Chưa kiểm tra. Bấm “↻ Kiểm tra lại”.</div>'; return; }
  const r = (TT_TAB === 'tiktok' && TT.tiktok) || TT.source;
  const inf = r.info || {}, v = inf.video || {};
  const dur = Math.round(inf.duration || 0), mmss = `${Math.floor(dur / 60)}:${String(dur % 60).padStart(2, '0')}`;
  let h = `<div class="tt-head"><div class="tt-score tt-${r.verdict}">${r.score}</div><div><b class="tt-${r.verdict}">${TT_NAME[r.verdict]}</b>` +
    `<div class="hint">${esc(r.file)} · ${v.w || '?'}×${v.h || '?'} · ${mmss} · ${((inf.size || 0) / 1e6).toFixed(0)} MB · ${r.fails} lỗi, ${r.warns} lưu ý</div>`;
  if (TT.tiktok && TT_TAB === 'source') h += `<div class="hint">Đã tạo bản TikTok để sửa: ${esc(r.fixable.join(', '))}</div>`;
  if (TT.tiktok && TT_TAB === 'tiktok') h += `<div style="margin-top:4px"><a href="/files/${CUR}/output_tiktok.mp4?dl=1">⬇ Tải bản TikTok</a></div>`;
  h += '</div></div>';
  let g = '';
  for (const it of r.items) {
    if (it.group !== g) { g = it.group; h += `<div class="tt-g">${esc(g)}</div>`; }
    h += `<div class="tt-i"><span>${TT_IC[it.status] || ''}</span><span>${esc(it.label)}</span><span class="d">${esc(it.detail)}</span>` +
      (it.fix && it.status !== 'ok' ? `<span class="fx">→ ${esc(it.fix)}</span>` : '') + '</div>';
  }
  $('#ttBody').innerHTML = h;
}
$$('#ttTabs button').forEach(b => b.onclick = () => { TT_TAB = b.dataset.r; renderTT(); });
$('#btnTT').onclick = async () => {
  const b = $('#btnTT'); b.disabled = true; b.textContent = '⏳ Đang kiểm tra…';
  try { TT = await api(`/api/jobs/${CUR}/tiktok`, { method: 'POST' }); TT_TAB = TT.tiktok ? 'tiktok' : 'source'; $('#ttCard').hidden = false; renderTT(); toast('Đã kiểm tra xong'); }
  catch (e) { toast(e.message, 8000) } finally { b.disabled = false; b.textContent = '↻ Kiểm tra lại' }
};

async function ttPost(caption, privacy, aigc) {
  const st = await api('/api/tiktok/status');
  if (!st.connected) { toast('Chưa kết nối TikTok: ⚙ Cài đặt → Đăng TikTok (API chính thức) → 🔑 Đăng nhập TikTok', 9000); return; }
  const where = st.mode === 'inbox' ? `Gửi video vào hộp thư TikTok của ${st.display_name || 'bạn'} (bạn bấm đăng trong app TikTok)`
    : `ĐĂNG video lên TikTok ${st.display_name || ''} (${privacy || 'riêng tư'})`;
  if (!confirm(`${where}?\n\nCaption:\n${(caption || '(trống)').slice(0, 300)}`)) return;
  try {
    const r = await api(`/api/jobs/${CUR}/tiktok/publish`, { method: 'POST', body: { caption, privacy: privacy || null, aigc } });
    let copied = false;
    if (st.mode === 'inbox' && caption) { try { await navigator.clipboard.writeText(caption); copied = true; } catch { } }
    toast(`Đang tải ${r.file} lên TikTok — theo dõi trong nhật ký job` +
      (st.mode === 'inbox' ? (copied ? '. Caption đã chép vào clipboard — dán vào khi đăng trong app TikTok.' : '. Hộp thư TikTok không nhận caption — nhớ dán caption khi đăng.') : ''), 9000);
  } catch (e) { toast(e.message, 8000) }
}
async function quickTTPublish() {
  let post = POST; if (!post) { try { post = await api(`/api/jobs/${CUR}/post`); } catch { post = null; } }
  ttPost(post?.platforms?.tiktok?.caption || post?.title || STATE?.title || '', null, true);
}
async function setupTTPublish() {
  let st; try { st = await api('/api/tiktok/status'); } catch { return }
  const info = $('#ttPubInfo');
  if (!st.connected) { info.textContent = 'Chưa kết nối TikTok — ⚙ Cài đặt → Đăng TikTok'; $('#ttPublish').disabled = true; return; }
  info.textContent = st.mode === 'inbox' ? `→ vào hộp thư TikTok của ${st.display_name || 'bạn'}` : `→ đăng thẳng lên ${st.display_name || 'tài khoản'}`;
  if (st.mode === 'direct') {
    try {
      const c = await api('/api/tiktok/creator');
      const VI = { PUBLIC_TO_EVERYONE: 'Công khai', MUTUAL_FOLLOW_FRIENDS: 'Bạn bè', FOLLOWER_OF_CREATOR: 'Người theo dõi', SELF_ONLY: 'Chỉ mình tôi' };
      $('#ttPrivacy').innerHTML = (c.privacy_level_options || ['SELF_ONLY']).map(o => `<option value="${o}">${VI[o] || o}</option>`).join('');
      $('#ttPrivacy').hidden = false;
    } catch (e) { info.textContent = e.message; }
  }
  $('#ttPublish').onclick = () => ttPost($('#postBody textarea').value.trim(), $('#ttPrivacy').value, $('#ttAigc').checked);
}

/* ---- hardware monitor popover ---- */
const SYSH = { cpu: [], ram: [], gpu: [], vram: [] };
let SYS_T = null;
const gb = b => (b / 1073741824).toFixed(1);
function spark(id, arr) {
  const c = $('#' + id), w = c.clientWidth, h = c.clientHeight; if (!w) return;
  c.width = w * devicePixelRatio; c.height = h * devicePixelRatio;
  const g = c.getContext('2d'); g.scale(devicePixelRatio, devicePixelRatio);
  const col = getComputedStyle(document.documentElement).getPropertyValue('--run').trim() || '#4f8cff';
  g.strokeStyle = col; g.lineWidth = 1.5; g.fillStyle = col + '22'; g.beginPath();
  const n = 60, step = w / (n - 1), pts = arr.slice(-n), off = n - pts.length;
  pts.forEach((v, i) => { const x = (i + off) * step, y = h - 2 - (h - 4) * Math.min(v, 100) / 100; i ? g.lineTo(x, y) : g.moveTo(x, y); });
  g.stroke(); if (pts.length) { g.lineTo(w, h); g.lineTo(off * step, h); g.closePath(); g.fill(); }
}
async function sysTick() {
  if (document.hidden) return;                 // tab in the background: no polling
  let d; try { d = await api('/api/system'); } catch { return }
  const push = (k, v) => { SYSH[k].push(v ?? 0); if (SYSH[k].length > 60) SYSH[k].shift(); };
  push('cpu', d.cpu); push('ram', d.ram_pct);
  const g = d.gpu; push('gpu', g?.util); push('vram', g ? 100 * g.mem_used / g.mem_total : 0);
  $('#sysMini').textContent = g ? `GPU ${g.util ?? '?'}% · ${g.temp ?? '?'}°C` : `CPU ${Math.round(d.cpu)}%`;
  if ($('#sysPop').hidden) return;
  $('#sCpu').textContent = `${d.cpu.toFixed(0)}% · ${d.cpu_count} luồng${d.cpu_freq ? ' · ' + (d.cpu_freq / 1000).toFixed(1) + ' GHz' : ''}`; $('#bCpu').style.width = d.cpu + '%';
  $('#sRam').textContent = `${gb(d.ram_used)} / ${gb(d.ram_total)} GB (${d.ram_pct.toFixed(0)}%)`; $('#bRam').style.width = d.ram_pct + '%';
  if (g) {
    $('#sGpuName').textContent = g.name.replace('NVIDIA GeForce ', '');
    $('#sGpu').textContent = `${g.util ?? '?'}% · ${g.temp ?? '?'}°C`; $('#bGpu').style.width = (g.util || 0) + '%';
    $('#sVram').textContent = `${(g.mem_used / 1024).toFixed(1)} / ${(g.mem_total / 1024).toFixed(1)} GB`; $('#bVram').style.width = (100 * g.mem_used / g.mem_total) + '%';
  } else { $('#sGpu').textContent = 'không đọc được (nvidia-smi)'; }
  $('#sysGrid').innerHTML = [
    g && ['Điện GPU', `${g.power?.toFixed(0) ?? '?'} / ${g.power_limit?.toFixed(0) ?? '?'} W`], g && ['Quạt', g.fan != null ? g.fan + '%' : '—'],
    g && ['Xung GPU', g.clock ? g.clock + ' MHz' : '—'], g && ['NVENC', g.enc != null ? g.enc + '%' : '—'],
    ['App dùng RAM', `${gb(d.app_rss)} GB (${d.app_procs} tiến trình)`], ['Ổ dữ liệu trống', `${gb(d.disk_free)} / ${gb(d.disk_total)} GB`],
  ].filter(Boolean).map(([k, v]) => `<div>${k}<br><b>${v}</b></div>`).join('');
  $('#sysJob').innerHTML = d.job ? `▶ Đang chạy: <b>${esc(d.job.title || d.job.id)}</b> — bước ${esc(d.job.step)} ${Math.round(100 * d.job.progress)}%` : 'Không có job đang chạy';
  spark('kCpu', SYSH.cpu); spark('kRam', SYSH.ram); spark('kGpu', SYSH.gpu); spark('kVram', SYSH.vram);
}
function sysLoop() { clearInterval(SYS_T); SYS_T = setInterval(sysTick, $('#sysPop').hidden ? 10000 : 1000); sysTick(); }
document.addEventListener('visibilitychange', () => { if (!document.hidden) sysTick(); });
$('#sysBtn').onclick = e => { e.stopPropagation(); $('#sysPop').hidden = !$('#sysPop').hidden; sysLoop(); };
$('#sysClose').onclick = () => { $('#sysPop').hidden = true; sysLoop(); };
document.addEventListener('click', e => { if (!$('#sysPop').hidden && !$('#sysPop').contains(e.target) && e.target !== $('#sysBtn')) { $('#sysPop').hidden = true; sysLoop(); } });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('#sysPop').hidden) { $('#sysPop').hidden = true; sysLoop(); } });
sysLoop();

/* ---- watermark editor: auto-detected boxes + boxes you draw ---- */
let WM = null;
async function loadWM() {
  if (!STATE?.files?.source) return;
  try { WM = await api(`/api/jobs/${CUR}/watermarks`); } catch { WM = null; $('#wmCard').hidden = true; return }
  $('#wmCard').hidden = false;
  const r = $('#wmTime'); r.max = Math.max(0.1, WM.duration - 0.1).toFixed(1);
  const first = WM.items.find(i => i.enabled);
  r.value = first ? Math.min(first.start + 1, WM.duration - 0.1).toFixed(1) : Math.min(1, WM.duration / 2).toFixed(1);
  wmFrame();
}
function wmFrame() { const t = +$('#wmTime').value; $('#wmTimeO').textContent = fmtT(t); $('#wmImg').src = `/api/jobs/${CUR}/frame?t=${t}`; wmRender(); }
$('#wmTime').addEventListener('change', wmFrame);
$('#wmTime').addEventListener('input', () => { $('#wmTimeO').textContent = fmtT(+$('#wmTime').value); wmBoxes(); });
function wmBoxAt(it, t) {           // moving watermark: box at time t along its track
  const tr = it.track; if (!tr) return it.box;
  let b = tr[0]; for (const p of tr) if (p[0] <= t) b = p; return b.slice(1);
}
function wmBoxes() {
  const t = +$('#wmTime').value, st = $('#wmStage');
  $$('#wmStage .wmbox').forEach(e => e.remove());
  WM.items.forEach((it, i) => {
    if (t < it.start || t > it.end) return;
    const [x0, y0, x1, y1] = wmBoxAt(it, t), d = document.createElement('div');
    d.className = 'wmbox' + (it.enabled ? '' : ' off');
    Object.assign(d.style, { left: x0 * 100 + '%', top: y0 * 100 + '%', width: (x1 - x0) * 100 + '%', height: (y1 - y0) * 100 + '%' });
    d.innerHTML = `<span>#${i + 1}</span>`; st.appendChild(d);
  });
}
function wmRender() {
  if (!WM) return;
  wmBoxes();
  $('#wmList').innerHTML = WM.items.length ? WM.items.map((it, i) => `<div class="wmrow">
      <label class="chk" style="margin:0"><input type="checkbox" data-wme="${i}" ${it.enabled ? 'checked' : ''}> Xoá #${i + 1}</label>
      <span class="src" style="flex:1;min-width:120px">${esc(it.src || '')} ${it.auto ? '<span class="hint">(tự tìm' + (it.track ? ', chuyển động' : '') + ')</span>' : '<span class="hint">(bạn vẽ)</span>'}</span>
      từ <input type="number" step="0.1" min="0" data-wms="${i}" value="${(+it.start).toFixed(1)}"> đến <input type="number" step="0.1" min="0" data-wmt="${i}" value="${(+it.end).toFixed(1)}"> giây
      <button class="shrink" data-wmgo="${i}" title="Xem khung hình">👁</button>
      ${it.auto ? '' : `<button class="shrink danger" data-wmdel="${i}">✕</button>`}</div>`).join('')
    : `<div class="hint">${WM.detected ? 'Không tìm thấy watermark nào.' : 'Chưa dò watermark (job cũ hoặc đang tắt).'} Vẽ trên ảnh nếu video có logo/tên kênh cần xoá.</div>`;
  $$('#wmList [data-wme]').forEach(c => c.onchange = () => { WM.items[+c.dataset.wme].enabled = c.checked; wmBoxes(); });
  $$('#wmList [data-wms]').forEach(c => c.onchange = () => { WM.items[+c.dataset.wms].start = +c.value; wmBoxes(); });
  $$('#wmList [data-wmt]').forEach(c => c.onchange = () => { WM.items[+c.dataset.wmt].end = +c.value; wmBoxes(); });
  $$('#wmList [data-wmdel]').forEach(b => b.onclick = () => { WM.items.splice(+b.dataset.wmdel, 1); wmRender(); });
  $$('#wmList [data-wmgo]').forEach(b => b.onclick = () => { const it = WM.items[+b.dataset.wmgo]; $('#wmTime').value = Math.min(it.start + 0.5, WM.duration - 0.1).toFixed(1); wmFrame(); });
}
(() => {   // draw a new box by dragging on the frame
  const st = $('#wmStage'); let a = null, ghost = null;
  const pos = e => { const r = $('#wmImg').getBoundingClientRect(); return [Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), Math.min(1, Math.max(0, (e.clientY - r.top) / r.height))]; };
  st.addEventListener('pointerdown', e => { if (!WM) return; a = pos(e); ghost = document.createElement('div'); ghost.className = 'wmbox'; st.appendChild(ghost); st.setPointerCapture(e.pointerId); });
  st.addEventListener('pointermove', e => { if (!a) return; const b = pos(e);
    Object.assign(ghost.style, { left: Math.min(a[0], b[0]) * 100 + '%', top: Math.min(a[1], b[1]) * 100 + '%', width: Math.abs(b[0] - a[0]) * 100 + '%', height: Math.abs(b[1] - a[1]) * 100 + '%' }); });
  st.addEventListener('pointerup', e => { if (!a) return; const b = pos(e); ghost.remove();
    const box = [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])]; a = null;
    if (box[2] - box[0] < 0.01 || box[3] - box[1] < 0.01) return;
    WM.items.push({ box, start: 0, end: WM.duration, enabled: true, src: 'Vùng tự vẽ' }); wmRender(); });
})();
$('#btnSaveWM').onclick = async () => {
  try { await api(`/api/jobs/${CUR}/watermarks`, { method: 'PUT', body: { items: WM.items } }); toast('Đã lưu, đang render lại…'); WM = null; }
  catch (e) { toast(e.message) }
};
$('#brand_enabled').addEventListener('change', () => $('#brandBox').style.opacity = $('#brand_enabled').checked ? 1 : .5);

$('#btnSaveLines').onclick = async () => {
  const edits = LINES.map((l, i) => ({ tgt: $(`#lines textarea[data-i="${i}"]`).value, gender: $(`#lines select[data-g="${i}"]`)?.value, role: $(`#lines select[data-r="${i}"]`)?.value }));
  try { await api(`/api/jobs/${CUR}/lines`, { method: 'PUT', body: { lines: edits } }); toast('Đã lưu, đang render lại…'); }
  catch (e) { toast(e.message) }
};
$('#showDebug').onchange = () => $$('#log [data-debug]').forEach(d => d.hidden = !$('#showDebug').checked);
$('#btnCancel').onclick = () => api(`/api/jobs/${CUR}/cancel`, { method: 'POST' });
$('#btnApprove').onclick = () => api(`/api/jobs/${CUR}/approve`, { method: 'POST' }).catch(e => toast(e.message));
$('#btnRerun').onclick = () => api(`/api/jobs/${CUR}/rerun`, { method: 'POST', body: { from_step: $('#rerunStep').value, options: formOptions() } })
  .then(() => toast('Đang chạy lại với các tuỳ chọn hiện tại bên trái')).catch(e => toast(e.message));
$('#btnSwitchMode').onclick = () => {
  const mode = $('#switchMode').value;
  // from 'render': the server works out the earliest step this mode needs and reuses the rest
  api(`/api/jobs/${CUR}/rerun`, { method: 'POST', body: { from_step: 'render', options: { ...formOptions(), output_mode: mode } } })
    .then(r => toast(`Đang làm bản mới từ bước “${r.from_step}” (dùng lại kết quả đã có)…`)).catch(e => toast(e.message));
};
$('#btnDelete').onclick = async () => {
  if (!confirm('Xoá job này và toàn bộ file của nó?')) return;
  await api(`/api/jobs/${CUR}`, { method: 'DELETE' }); if (ES) ES.close(); CUR = null; showDefaultSettings();
  $('#jobView').hidden = true; $('#emptyDetail').hidden = false; loadJobs();
};
