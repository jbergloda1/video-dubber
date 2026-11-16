/* ---------------- trending (Kho nguồn & Bộ lọc sâu) ---------------- */
let TITEMS = [], TSEL = new Set(), TPOLL = null, TPAGE = 1;
const fmtN = n => fmtV(n);          // one number format in the whole app (Vietnamese: N = nghìn, Tr = triệu)
const fmtDur = s => !s ? '' : `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const JOBST = { queued: ['Chờ', 'b-queued'], running: ['Đang chạy', 'b-running'], done: ['Xong', 'b-done'], error: ['Lỗi', 'b-error'], cancelled: ['Huỷ', 'b-cancelled'], awaiting_approval: ['Chờ duyệt', 'b-awaiting_approval'], deleted: ['Job đã xoá', 'b-cancelled'] };
const trendUrl = it => !it ? '' : ((it.platform || 'douyin') !== 'douyin' || (it.url || '').includes('/hot/') ? (it.url || '') : 'https://www.douyin.com/jingxuan?modal_id=' + it.id);

let TCATS = [], TCOUNTS = {}, TCAT = '', SOURCES = [];
async function loadTrending() {
  const r = await api('/api/trending'); TITEMS = r.items; TCATS = r.categories; TCOUNTS = r.counts; TDEPTH = r.depth || {};
  if (!SOURCES.length) { try { SOURCES = await api('/api/sources'); fillFeeds(); } catch { } }
  renderTState(r.state); renderCats(); renderTrending();
  if (r.state.running && !TPOLL) TPOLL = setInterval(async () => {
    const r2 = await api('/api/trending'); renderTState(r2.state);
    if (!r2.state.running) {
      clearInterval(TPOLL); TPOLL = null; TITEMS = r2.items; TCOUNTS = r2.counts; renderCats(); renderTrending();
      if (r2.state.error) toast(r2.state.error, 9000); else toast(r2.state.message);
    }
  }, 1500);
}
function updateFeedInputHint() {
  const inp = $('#tFeedInput'); if (!inp) return;
  const v = $('#tFeed').value || '';
  if (v === 'search') {
    inp.placeholder = 'Nhập từ khoá tìm trên Douyin (vd: 足球, 科技, 搞笑)…';
    inp.focus();
  } else if (v.endsWith('|user:')) {
    inp.placeholder = 'Dán link kênh Douyin (douyin.com/user/…) hoặc sec_uid…';
    inp.focus();
  } else if (v.endsWith('|page:')) {
    inp.placeholder = 'Dán link Page Facebook (vd: https://www.facebook.com/espn)…';
    inp.focus();
  } else {
    inp.placeholder = 'Hoặc dán thẳng link kênh Douyin / Page Facebook / từ khoá…';
  }
}
function fillFeeds() {
  $('#tFeed').innerHTML = SOURCES.map(src => `<optgroup label="${esc(src.label)}">` +
    src.feeds.map(f => `<option value="${esc(src.id + '|' + f.feed)}">${esc(f.label)}</option>`).join('') + '</optgroup>').join('') +
    '<option value="search">🔎 Tìm kiếm Douyin theo từ khoá…</option>';
  $('#tSource').innerHTML = '<option value="">Mọi nguồn</option>' + SOURCES.map(src => `<option value="platform:${src.id}">${esc(src.label)}</option>`).join('') +
    '<option value="hot">热榜</option><option value="featured">精选</option><option value="tab">Tab chủ đề</option>';
  updateFeedInputHint();
}
$('#tFeed').addEventListener('change', updateFeedInputHint);

function renderCats() {
  const rows = [`<div class="tcat ${TCAT === '' ? 'on' : ''}" data-cat=""><span>📚 Tất cả</span><small>${TITEMS.filter(i => !i.hidden).length}</small></div>`]
    .concat(TCATS.map(n => `<div class="tcat ${TCAT === n.id ? 'on' : ''}" data-cat="${n.id}" style="padding-left:${8 + n.depth * 16}px">
      <span>${n.depth ? '' : '▸ '}${esc(n.name)}</span><small>${TCOUNTS[n.id] || 0}</small></div>`));
  $('#tCats').innerHTML = rows.join('');
  $$('#tCats .tcat').forEach(el => el.onclick = () => { TCAT = el.dataset.cat; TPAGE = 1; renderCats(); renderTrending(); });
  const cur = TCATS.find(n => n.id === TCAT);
  $('#tCatTitle').textContent = cur ? cur.name : 'Tất cả video';
  const feeds = cur ? Object.entries(cur.feeds).flatMap(([p, fs]) => fs.map(f => `${p}: ${f}`)) : [];
  $('#crawlCat').hidden = !cur || !feeds.length;
  $('#crawlCat').title = feeds.length ? 'Sẽ lấy từ: ' + feeds.join(', ') : '';
  const deep = cur ? Object.entries(cur.feeds).flatMap(([p, fs]) => fs.map(f => TDEPTH[p + '|' + f])).filter(Boolean) : [];
  $('#crawlMore').title = deep.length ? `Đã lấy tới trang ${Math.max(...deep.map(d => d.pages))}; bấm để cuộn sâu hơn` : 'Lấy sâu hơn từ đúng nguồn vừa lấy';
}
let LAST_CRAWL = null, TDEPTH = {};
function renderTState(st) {
  $('#crawlCat').disabled = $('#crawlFeed').disabled = st.running;
  $('#crawlStop').hidden = !st.running; $('#crawlMore').hidden = st.running || !LAST_CRAWL;
  const errs = (st.errors || []).length ? `<div class="hint" style="color:var(--warn)">${st.errors.map(esc).join('<br>')}</div>` : '';
  $('#tState').innerHTML = st.running ? `<div class="bar"><i style="width:${st.progress * 100}%"></i></div> ${esc(st.message)}`
    : st.error ? `<span style="color:var(--err)">⚠ ${esc(st.error)}</span>` : st.finished ? `<div>Lần crawl gần nhất: ${new Date(st.finished * 1000).toLocaleTimeString('vi-VN')} — ${esc(st.message)}${errs}</div>` : '';
}
async function crawl(body) {
  try {
    const r = await api('/api/trending/crawl', { method: 'POST', body: { ...body, pages: +$('#crawlPages').value || 3 } });
    LAST_CRAWL = { ...body, more: false }; TPAGE = 1; loadTrending();
  } catch (e) { toast(e.message, 7000) }
}
$('#crawlMore').onclick = () => LAST_CRAWL && crawl({ ...LAST_CRAWL, more: true });
$('#crawlStop').onclick = () => api('/api/trending/cancel', { method: 'POST' }).then(() => toast('Đang dừng sau trang hiện tại…'));
$('#crawlCat').onclick = () => crawl({ category: TCAT });
$('#crawlFeed').onclick = () => {
  const inp = ($('#tFeedInput')?.value || '').trim();
  let v = $('#tFeed').value;
  if (inp) {
    if (/^https?:\/\/(www\.|m\.)?(facebook\.com|fb\.watch)\//i.test(inp) || v.endsWith('|page:')) {
      if (!/^https?:\/\/(www\.|m\.)?(facebook\.com|fb\.watch)\//i.test(inp)) return toast('URL Page Facebook không hợp lệ (vd: https://www.facebook.com/espn)');
      return crawl({ targets: [['facebook', 'page:' + inp]] });
    }
    if (/douyin\.com\/user\/|^user:|^MS4wLjAB/i.test(inp) || v.endsWith('|user:')) {
      return crawl({ targets: [['douyin', 'user:' + inp.replace(/^user:/i, '').trim()]] });
    }
    return crawl({ targets: [['douyin', 'search:' + inp.replace(/^search:/i, '').trim()]] });
  }
  if (v === 'search' || v.endsWith('|user:') || v.endsWith('|page:')) {
    $('#tFeedInput')?.focus();
    return toast('Hãy nhập từ khoá hoặc dán link kênh / Page vào ô bên cạnh trước khi bấm Lấy video');
  }
  const [src, ...rest] = v.split('|'); crawl({ targets: [[src, rest.join('|')]] });
};
if ($('#tFeedInput')) $('#tFeedInput').addEventListener('keydown', e => { if (e.key === 'Enter') $('#crawlFeed').click(); });
['#tSearch', '#tSource', '#tSort', '#tMinLikes', '#tMaxDur', '#tHideDone', '#tShowHidden'].forEach(s => $(s).addEventListener('input', () => { TPAGE = 1; renderTrending(); }));
if ($('#tPerPage')) $('#tPerPage').addEventListener('change', () => { TPAGE = 1; renderTrending(); });

/* category editor */
$('#btnEditCats').onclick = async () => { const r = await api('/api/categories'); $('#catsJson').value = JSON.stringify(r.categories, null, 2); $('#dlgCats').showModal(); };
$('#closeCats').onclick = $('#resetCatsView').onclick = () => $('#dlgCats').close();
$('#saveCats').onclick = async () => {
  let cats; try { cats = JSON.parse($('#catsJson').value) } catch (e) { return toast('JSON lỗi: ' + e.message, 7000) }
  try { await api('/api/categories', { method: 'PUT', body: { categories: cats } }); $('#dlgCats').close(); toast('Đã lưu danh mục'); loadTrending(); }
  catch (e) { toast(e.message, 7000) }
};

function filteredTrending() {
  const q = $('#tSearch').value.trim().toLowerCase(), src = $('#tSource').value, minL = +$('#tMinLikes').value || 0, maxD = +$('#tMaxDur').value || 0;
  const srcOk = it => !src || (src.startsWith('platform:') ? (it.platform || 'douyin') === src.slice(9)
    : src === 'tab' ? (it.feeds || []).some(f => f.startsWith('tab:')) : (it.sources || [it.source]).includes(src));
  let xs = TITEMS.filter(it =>
    (!TCAT || (it.categories || []).includes(TCAT)) &&
    (!q || (it.title + ' ' + it.author + ' ' + (it.topic || '')).toLowerCase().includes(q)) &&
    srcOk(it) &&
    (!minL || (it.likes || 0) >= minL) &&
    (!maxD || !it.duration || it.duration <= maxD) &&
    ($('#tShowHidden').checked || !it.hidden) &&
    (!$('#tHideDone').checked || !it.job_id || ['error', 'cancelled', 'deleted'].includes(it.job_status)));
  const key = { hot: it => -(it.hot_value || 0) * 10 - (it.likes || 0), likes: it => -(it.likes || 0), new: it => -(it.first_seen || 0), short: it => it.duration || 1e9, created: it => -(it.created || 0) }[$('#tSort').value];
  return xs.sort((a, b) => key(a) - key(b));
}

function trendingPageItems() {
  const all = filteredTrending();
  const per = +($('#tPerPage')?.value ?? 24) || 0;
  if (per <= 0) return { pageItems: all, startOffset: 0, per: 0, total: all.length };
  const pages = Math.max(1, Math.ceil(all.length / per));
  if (TPAGE > pages) TPAGE = pages;
  if (TPAGE < 1) TPAGE = 1;
  const startOffset = (TPAGE - 1) * per;
  return { pageItems: all.slice(startOffset, startOffset + per), startOffset, per, total: all.length };
}

function renderTrending() {
  const { pageItems: xs, per, total } = trendingPageItems();
  $('#tCount').textContent = `${total} / ${TITEMS.length} video`;
  $('#tEmpty').hidden = total > 0;
  $('#tGrid').innerHTML = xs.map(it => {
    const js = JOBST[it.job_status];
    const cover = it.has_cover ? `/trending/cover/${it.id}` : (it.cover_url || '');
    const u = trendUrl(it);
    return `<div class="tcard ${TSEL.has(it.id) ? 'sel' : ''} ${it.hidden ? 'hid' : ''}" data-id="${esc(it.id)}">
      <div class="cv" style="background-image:url('${esc(cover)}')" data-pv="${esc(it.id)}" title="Bấm để xem trước">
        <div class="ck" data-sel="${esc(it.id)}" title="Chọn để chạy hoặc lưu hàng loạt">${TSEL.has(it.id) ? '✓' : ''}</div>
        ${js ? `<span class="badge st ${js[1]}" style="background:rgba(0,0,0,.6)">${js[0]}</span>` : ''}
        ${it.rank ? `<span class="rank">#${it.rank}</span>` : ''}
        ${it.duration ? `<span class="dur">${fmtDur(it.duration)}</span>` : ''}
      </div>
      <div class="bd">
        <div class="tt" title="${esc(it.title)}">${esc(it.title || '(không có mô tả)')}</div>
        <div class="mt">${it.author ? `<span>👤 ${esc(it.author)}</span>` : ''}
          ${it.hot_value ? `<span>🔥 ${fmtN(it.hot_value)}</span>` : ''}${it.likes ? `<span>❤ ${fmtN(it.likes)}</span>` : ''}
          ${it.views ? `<span>👁 ${fmtN(it.views)}</span>` : ''}<span>${(it.platform || 'douyin') === 'facebook' ? '📘 Facebook' : (it.feeds && it.feeds.length ? it.feeds : (it.sources || [it.source])).map(s => s === 'hot' ? '热榜' : s === 'featured' ? '精选' : String(s).replace('tab:', '')).join(' · ')}</span></div>
        <div class="lk"><button data-pv="${esc(it.id)}" style="color:var(--text)">▶ Xem thử</button>
          <button data-dub="${esc(u)}" style="color:var(--text)">🎙 Làm ngay</button>
          <button data-lib="${esc(u)}" title="Tải về Kho video & tự phân tích AI">📦 Lưu kho</button>
          <button data-copy="${esc(u)}">📋 Link</button>
          <a href="${esc(u)}" target="_blank" rel="noopener" title="Mở trên web">↗</a>
          ${it.job_id && it.job_status !== 'deleted' ? `<button data-job="${esc(it.job_id)}">Mở job</button>` : ''}</div>
      </div></div>`;
  }).join('');
  $$('#tGrid [data-sel]').forEach(b => b.onclick = e => { e.stopPropagation(); const id = b.dataset.sel; TSEL.has(id) ? TSEL.delete(id) : TSEL.add(id); renderTrending(); });
  $$('#tGrid [data-job]').forEach(b => b.onclick = () => { showView('main'); openJob(b.dataset.job); });
  $$('#tGrid [data-pv]').forEach(b => b.onclick = () => openPreview(b.dataset.pv));
  $$('#tGrid [data-dub]').forEach(b => b.onclick = () => hotDub(b.dataset.dub));
  $$('#tGrid [data-lib]').forEach(b => b.onclick = () => addToLib(b.dataset.lib));
  $$('#tGrid [data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy));
  if ($('#tSelAll')) {
    const allSel = xs.length > 0 && xs.every(i => TSEL.has(i.id));
    $('#tSelAll').textContent = allSel ? '☐ Bỏ chọn trang' : '☑ Chọn trang này';
  }
  const extraLbl = LAST_CRAWL ? '➕ Tải sâu thêm từ nguồn vừa lấy' : (!($('#crawlCat')?.hidden) ? '🔄 Lấy thêm video cho chủ đề này' : '🔄 Lấy thêm từ nguồn đang chọn');
  const extraFn = () => LAST_CRAWL ? $('#crawlMore').click() : (!($('#crawlCat')?.hidden) ? $('#crawlCat').click() : $('#crawlFeed').click());
  renderPager($('#tPager'), TPAGE, total, per, p => {
    TPAGE = p; renderTrending();
    $('#tCatTitle')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, extraLbl, extraFn);
  $('#tActions').hidden = !TSEL.size || $('#trendView').hidden;
  $('#tSelN').textContent = `Đã chọn ${TSEL.size}`;
}
if ($('#tSelAll')) $('#tSelAll').onclick = () => {
  const { pageItems: xs } = trendingPageItems();
  if (!xs.length) return toast('Chưa có video để chọn');
  const allSel = xs.every(i => TSEL.has(i.id));
  xs.forEach(i => allSel ? TSEL.delete(i.id) : TSEL.add(i.id));
  renderTrending();
};
let PVID = null;
async function openPreview(id) {
  const it = TITEMS.find(x => x.id === id) || {};
  PVID = id;
  const d = $('#dlgPreview'), v = $('#pvVideo');
  $('#pvTitle').textContent = it.title || id;
  $('#pvMsg').textContent = '⏳ Đang lấy link video…'; $('#pvMsg').hidden = false;
  v.hidden = true; v.removeAttribute('src'); v.load();
  $('#pvSelect').textContent = TSEL.has(id) ? '✓ Đã chọn' : '+ Chọn video này';
  d.showModal();
  try {
    const r = await api(`/api/preview/${id}`);
    if (PVID !== id || !d.open) return;
    $('#pvMsg').hidden = true; v.hidden = false; v.src = r.stream; v.play().catch(() => { });
  } catch (e) { if (PVID === id) $('#pvMsg').textContent = '⚠ ' + e.message; }
}
function closePreview() { const v = $('#pvVideo'); v.pause(); v.removeAttribute('src'); v.load(); PVID = null; $('#dlgPreview').close(); }
$('#pvClose').onclick = closePreview;
$('#dlgPreview').addEventListener('cancel', e => { e.preventDefault(); closePreview(); });
$('#pvSelect').onclick = () => { if (!PVID) return; TSEL.has(PVID) ? TSEL.delete(PVID) : TSEL.add(PVID); $('#pvSelect').textContent = TSEL.has(PVID) ? '✓ Đã chọn' : '+ Chọn video này'; renderTrending(); };
if ($('#pvCopy')) $('#pvCopy').onclick = () => { const it = TITEMS.find(x => x.id === PVID); if (it) copyText(trendUrl(it)); };
if ($('#pvLib')) $('#pvLib').onclick = () => { const it = TITEMS.find(x => x.id === PVID); if (it) addToLib(trendUrl(it)); };
if ($('#pvDub')) $('#pvDub').onclick = () => { const it = TITEMS.find(x => x.id === PVID); const u = trendUrl(it); closePreview(); if (u) hotDub(u); };
$('#tClear').onclick = () => { TSEL.clear(); renderTrending(); };
if ($('#tCopySel')) $('#tCopySel').onclick = () => copyText([...TSEL].map(id => trendUrl(TITEMS.find(x => x.id === id))).filter(Boolean).join('\n'));
if ($('#tToLib')) $('#tToLib').onclick = async () => {
  await addToLib([...TSEL].map(id => trendUrl(TITEMS.find(x => x.id === id))).filter(Boolean));
  TSEL.clear(); renderTrending();
};
$('#tHide').onclick = async () => { await api('/api/trending/hide', { method: 'POST', body: { ids: [...TSEL], hidden: true } }); TSEL.clear(); loadTrending(); };
$('#tDel').onclick = async () => {
  if (!confirm(`Xoá ${TSEL.size} video khỏi danh sách trending? (chỉ xoá khỏi danh sách, không xoá job đã chạy)`)) return;
  await api('/api/trending/delete', { method: 'POST', body: { ids: [...TSEL] } }); TSEL.clear(); loadTrending();
};
$('#tRun').onclick = () => {
  const n = TSEL.size, ids = [...TSEL];
  RUN_REC = '';
  askRunSettings(`Chạy ${n} video trending`, $('#tMode').value, opts => runTrending(ids, opts));
};
async function runTrending(ids, opts) {
  const n = ids.length, mode = opts.output_mode;
  const go = async () => {
    const r = await api('/api/trending/queue', { method: 'POST', body: { ids, options: opts } });
    toast(`Đã thêm ${r.jobs.length} job vào hàng đợi`); TSEL.clear(); loadTrending(); loadJobs();
  };
  const durs = ids.map(id => (TITEMS.find(t => t.id === id) || {}).duration || 0);
  if (durs.every(d => d > 0)) return showEstimate({ durations: durs, options: opts }, `Ước tính cho ${n} video (chạy lần lượt)`, go);
  if (!confirm(`Đưa ${n} video vào hàng đợi xử lý (${({ dub: 'lồng tiếng + sub', voiceover: 'thuyết minh', sub: 'chỉ sub', text: 'chỉ dịch chữ' })[mode] || mode})?\nCác video chạy lần lượt trên GPU, giãn cách tải để tránh bị Douyin chặn.`)) return;
  try { await go() } catch (e) { toast(e.message) }
};
setInterval(() => { if (!$('#trendView').hidden && !TPOLL && TITEMS.some(it => ['queued', 'running'].includes(it.job_status))) loadTrending(); }, 10000);

/* ---- restart the server to load new code (run.bat brings it back) ---- */
let ADMIN = null;
async function adminTick() {
  if (document.hidden) return;
  try { ADMIN = await api('/api/admin/status'); } catch { return }
  $('#btnRestart').hidden = !ADMIN.stale;
  if (ADMIN.stale) $('#btnRestart').title = ADMIN.busy_jobs || ADMIN.busy_library
    ? `Có code mới — sẽ khởi động lại được khi xong ${ADMIN.busy_jobs} job / ${ADMIN.busy_library} video trong kho`
    : 'Có code mới trên ổ đĩa: bấm để khởi động lại server (vài giây, không cần đóng run.bat)';
}
async function restartServer() {
  if (!confirm('Khởi động lại server để nạp code mới? (vài giây; không có job nào đang chạy)')) return;
  const b = $('#btnRestart'); b.hidden = false; b.disabled = true; b.textContent = '⏳ Đang khởi động lại…';
  if ($('#dlgSettings').open) $('#dlgSettings').close();
  try { await api('/api/admin/restart', { method: 'POST' }); }
  catch (e) { b.disabled = false; b.textContent = '🔄 Cập nhật'; return toast(e.message, 9000); }
  const t0 = Date.now(), old = ADMIN?.started;
  while (Date.now() - t0 < 60000) {
    await new Promise(r => setTimeout(r, 1500));
    try { const s = await api('/api/admin/status'); if (s.started !== old) { location.reload(); return; } } catch { }
  }
  b.disabled = false; b.textContent = '🔄 Cập nhật'; toast('Server chưa lên lại sau 60 giây — xem cửa sổ run.bat', 9000);
}
$('#btnRestart').onclick = restartServer;
$('#btnRestart2').onclick = restartServer;
adminTick(); setInterval(adminTick, 20000);

/* ---- 🤖 autopilot ---- */
const AP_KEYS = ['enabled', 'start_hour', 'end_hour', 'max_per_day', 'mode', 'library', 'trending', 'min_likes',
  'max_age_days', 'refresh', 'min_duration', 'max_duration'];
let AP = null;
function apFill(r) {
  const st = r.settings;
  AP_KEYS.forEach(k => setVal($('#ap_' + k), st['autopilot_' + k]));
  const want = new Set(st.autopilot_categories || []);
  $('#ap_categories').innerHTML = (r.categories || []).map(c =>
    `<label class="${c.depth ? 'sub' : ''}"><input type="checkbox" data-cat="${c.id}" ${want.has(c.id) ? 'checked' : ''}>${esc(c.name)}</label>`).join('');
  $('#apTrendBox').style.display = $('#ap_trending').checked ? '' : 'none';
}
function apStatus(r) {
  const s = r.settings;
  const state = !s.autopilot_enabled ? '⏸ Đang tắt' : r.in_window ? '▶ Đang trong giờ chạy' : `🌙 Chờ đến ${s.autopilot_start_hour}h`;
  $('#apStatus').innerHTML = `<div>Trạng thái<b>${state}</b></div><div>Hôm nay đã chạy<b>${r.today} / ${r.today + r.left}</b></div>` +
    `<div>Video khớp bộ lọc<b>${r.candidates}</b></div>`;
  $('#apLog').innerHTML = r.log.length ? r.log.slice().reverse().map(l =>
    `<div><time>${new Date(l.t * 1000).toLocaleString('vi-VN', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' })}</time><span>${esc(l.msg)}</span></div>`).join('') : 'Chưa có.';
}
function apPick(r) {
  const MODE = { dub: 'Lồng tiếng', voiceover: 'Thuyết minh', sub: 'Chỉ Sub', text: 'Chỉ dịch chữ' };
  $('#apPick').innerHTML = !r.left ? 'Đã đủ số video hôm nay.' : !r.would_pick.length ? 'Không có video nào khớp bộ lọc.'
    : `<div class="appick">` + r.would_pick.map((c, i) => `<span>${i + 1}.</span><span title="${esc(c.title)}">${esc(c.title.slice(0, 70))}</span>` +
      `<span class="src">${esc(c.why.replace(/gợi ý (\w+)/, (m, k) => 'gợi ý ' + (MODE[k] || k)))}${c.duration ? ' · ' + fmtDur(c.duration) : ''}</span>`).join('') + '</div>';
}
async function loadAuto(preview) {
  try { AP = await api('/api/autopilot'); } catch (e) { return toast(e.message) }
  if (!preview) apFill(AP);
  apStatus(AP); if (preview) apPick(AP);
}
function apCollect() {
  const o = {};
  AP_KEYS.forEach(k => { o['autopilot_' + k] = getVal($('#ap_' + k)); });
  o.autopilot_categories = $$('#ap_categories [data-cat]').filter(x => x.checked).map(x => x.dataset.cat);
  return o;
}
$('#ap_enabled').addEventListener('change', async () => {
  try { await api('/api/settings', { method: 'PUT', body: apCollect() }); toast($('#ap_enabled').checked ? 'Đã bật tự chạy' : 'Đã tắt tự chạy'); loadAuto(true); }
  catch (e) { toast(e.message, 6000) }
});
$('#ap_trending').addEventListener('change', () => $('#apTrendBox').style.display = $('#ap_trending').checked ? '' : 'none');
$('#apSave').onclick = async () => {
  try { await api('/api/settings', { method: 'PUT', body: apCollect() }); toast('Đã lưu cài đặt tự chạy'); loadAuto(true); }
  catch (e) { toast(e.message, 6000) }
};
$('#apPreview').onclick = async () => { await api('/api/settings', { method: 'PUT', body: apCollect() }); loadAuto(true); };
$('#apRun').onclick = async () => {
  await api('/api/settings', { method: 'PUT', body: apCollect() });
  const r = await api('/api/autopilot'); apPick(r);
  if (!r.would_pick.length) return toast('Không có video nào để chạy');
  if (!confirm(`Đưa ${r.would_pick.length} video ở danh sách “Sẽ chọn” vào hàng đợi ngay bây giờ?`)) return;
  try { const x = await api('/api/autopilot/run', { method: 'POST', body: {} }); toast(`Đã đưa ${x.jobs.length} video vào hàng đợi`); loadAuto(true); loadJobs(); }
  catch (e) { toast(e.message, 6000) }
};
