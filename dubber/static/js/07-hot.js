/* ---------------- 🚀 Link hot (Khám phá & Tìm nhanh) ---------------- */
const HOT = { plat: 'tiktok', mode: 'top', cat: 'Công nghệ', dcat: '', page: 1, items: [], poll: null, ttcats: [], dcats: [] };
try { Object.assign(HOT, JSON.parse(localStorage.getItem('hot') || '{}'), { page: 1, items: [], poll: null }) } catch { }
if (HOT.mode === 'kw') HOT.mode = 'top';
const fmtV = n => { n = +n || 0; const f = (x, u) => (x >= 100 ? Math.round(x) : x.toFixed(1).replace('.0', '').replace('.', ',')) + ' ' + u; return n >= 1e9 ? f(n / 1e9, 'tỷ') : n >= 1e6 ? f(n / 1e6, 'Tr') : n >= 1e3 ? f(n / 1e3, 'N') : String(Math.round(n)); };
const ago = t => { if (!t) return ''; const h = (Date.now() / 1000 - t) / 3600; return h < 24 ? Math.max(1, Math.round(h)) + ' giờ trước' : h < 24 * 60 ? Math.round(h / 24) + ' ngày trước' : Math.round(h / 24 / 30) + ' tháng trước' };
function hotSave() { try { localStorage.setItem('hot', JSON.stringify({ plat: HOT.plat, mode: HOT.mode, cat: HOT.cat, dcat: HOT.dcat })) } catch { } }

function renderPager(el, page, total, perPage, onPage, extraLabel, onExtra) {
  if (!el) return;
  if (!total) { el.hidden = true; el.innerHTML = ''; return; }
  const pages = perPage > 0 ? Math.max(1, Math.ceil(total / perPage)) : 1;
  const cur = Math.min(Math.max(1, page || 1), pages);
  const start = perPage > 0 ? (cur - 1) * perPage + 1 : 1;
  const end = perPage > 0 ? Math.min(total, cur * perPage) : total;
  const nums = [];
  if (pages > 1) {
    const push = p => { if (nums[nums.length - 1] !== p) nums.push(p); };
    push(1);
    if (cur - 2 > 2) nums.push('…');
    for (let p = Math.max(2, cur - 2); p <= Math.min(pages - 1, cur + 2); p++) push(p);
    if (cur + 2 < pages - 1) nums.push('…');
    if (pages > 1) push(pages);
  }
  el.hidden = false;
  el.innerHTML = `<span class="pg-info">Trang <b>${cur}/${pages}</b> · Hiện <b>${start}–${end}</b> / tổng <b>${total}</b> video</span>` +
    (pages > 1 ? `<div class="pg-btns">
      <button data-pg="${cur - 1}" ${cur <= 1 ? 'disabled' : ''}>‹ Trước</button>
      ${nums.map(n => n === '…' ? `<span class="pg-dots">…</span>` : `<button data-pg="${n}" class="${n === cur ? 'on' : ''}">${n}</button>`).join('')}
      <button data-pg="${cur + 1}" ${cur >= pages ? 'disabled' : ''}>Sau ›</button>
    </div>` : '') +
    (extraLabel ? `<button class="pg-more" data-extra="1">${esc(extraLabel)}</button>` : '');
  el.querySelectorAll('[data-pg]').forEach(b => b.onclick = () => {
    const p = +b.dataset.pg;
    if (p >= 1 && p <= pages && p !== cur) onPage(p);
  });
  const ex = el.querySelector('[data-extra]');
  if (ex && onExtra) ex.onclick = onExtra;
}

function updateHotSearchBtn() {
  const q = $('#hQ').value.trim();
  if ($('#hQClear')) $('#hQClear').hidden = !q;
  if (/douyin\.com\/user\/|^user:|^MS4wLjAB/i.test(q)) {
    $('#hSearch').textContent = '👤 Quét kênh Douyin';
  } else if (/^@|tiktok\.com\/@/i.test(q)) {
    $('#hSearch').textContent = '👤 Quét kênh TikTok';
  } else {
    $('#hSearch').textContent = HOT.plat === 'douyin' ? '🔎 Tìm trên Douyin' : '🔎 Tìm trên TikTok';
  }
}

async function loadHot() {
  if (!HOT.dcats.length) { try { HOT.dcats = (await api('/api/trending')).categories } catch { } }
  await hotRefresh();
}

async function hotRefresh() {
  hotSave();
  $$('#hPlat button').forEach(b => b.classList.toggle('on', b.dataset.p === HOT.plat));
  $$('#hMode button').forEach(b => b.classList.toggle('on', b.dataset.m === HOT.mode));
  $('#hTopBar').hidden = HOT.mode === 'ins';
  $('#hKwBar').hidden = false;
  $('#hIns').hidden = HOT.mode !== 'ins';
  updateHotSearchBtn();
  const q = $('#hQ').value.trim();
  const cat = q ? '' : (HOT.plat === 'tiktok' ? (HOT.cat === 'Tất cả' ? 'Tất cả' : HOT.cat) : HOT.dcat);
  const p = new URLSearchParams({ platform: HOT.plat, cat, q, sort: $('#hSort').value, limit: 0, max_dur: +$('#hMaxDur').value || 0 });
  let r;
  try { r = await api('/api/hot?' + p); } catch (e) { return toast(e.message, 6000) }
  HOT.ttcats = r.categories; $('#hCC').href = r.creative_center;
  hotCats(); hotState(r.state);
  const catName = HOT.plat === 'tiktok' ? 'TikTok · ' + HOT.cat : 'Douyin · ' + (HOT.dcats.find(c => c.id === HOT.dcat)?.name || 'Tất cả');
  $('#hTitle').textContent = q ? `${HOT.plat === 'tiktok' ? 'TikTok' : 'Douyin'} · Lọc “${q}” (${r.items.length})` : `${catName} (${r.items.length})`;
  $('#hCrawl').textContent = HOT.plat === 'tiktok' ? `🔄 Lấy video “${HOT.cat}”` : '🔄 Lấy từ Douyin';
  $('#hSugg').innerHTML = (r.suggest || []).map(s => `<button data-q="${esc(s)}">${esc(s)}</button>`).join('');
  $$('#hSugg [data-q]').forEach(b => b.onclick = () => { $('#hQ').value = b.dataset.q; HOT.page = 1; hotRefresh(); });
  if (HOT.mode === 'ins') {
    const ins = await api('/api/hot/insights');
    $('#hHot').innerHTML = ins.douyin_hot.length ? ins.douyin_hot.map(h => `<a href="${esc(h.url)}" target="_blank" rel="noopener" title="Mở chủ đề trên Douyin"><b>${h.rank || ''}</b><span>${esc(h.title)}</span><small>🔥 ${fmtN(h.hot_value)}</small></a>`).join('')
      : '<div class="hint">Chưa có — bấm 🔄 Làm mới.</div>';
    $('#hInsSugg').innerHTML = ins.suggest.length ? ins.suggest.map(s => `<button data-q="${esc(s)}">${esc(s)}</button>`).join('') : '<span class="hint">Tìm thử một từ khoá ở ô tìm kiếm phía trên, TikTok sẽ gợi ý thêm.</span>';
    $$('#hInsSugg [data-q]').forEach(b => b.onclick = () => { HOT.mode = 'top'; $('#hQ').value = b.dataset.q; HOT.page = 1; hotRefresh(); });
    HOT.items = ins.tiktok_rising;
  } else HOT.items = r.items;
  hotGrid();
}

function hotCats() {
  const rows = HOT.plat === 'tiktok'
    ? HOT.ttcats.map(c => `<div class="tcat ${HOT.cat === c ? 'on' : ''}" data-c="${esc(c)}"><span>${esc(c)}</span></div>`)
    : [`<div class="tcat ${!HOT.dcat ? 'on' : ''}" data-c=""><span>📚 Tất cả</span></div>`].concat(HOT.dcats.map(n => `<div class="tcat ${HOT.dcat === n.id ? 'on' : ''}" data-c="${n.id}" style="padding-left:${8 + n.depth * 16}px"><span>${esc(n.name)}</span></div>`));
  $('#hCats').innerHTML = rows.join('');
  $$('#hCats .tcat').forEach(el => el.onclick = () => {
    if (HOT.plat === 'tiktok') HOT.cat = el.dataset.c; else HOT.dcat = el.dataset.c;
    if (HOT.mode === 'ins') HOT.mode = 'top';
    if ($('#hQ').value.trim()) $('#hQ').value = '';
    HOT.page = 1;
    hotRefresh();
  });
}

function hotPageItems() {
  const all = HOT.items || [];
  const per = +$('#hLimit').value || 0;
  if (per <= 0) return { pageItems: all, startOffset: 0, per: 0, total: all.length };
  const pages = Math.max(1, Math.ceil(all.length / per));
  if (HOT.page > pages) HOT.page = pages;
  if (HOT.page < 1) HOT.page = 1;
  const startOffset = (HOT.page - 1) * per;
  return { pageItems: all.slice(startOffset, startOffset + per), startOffset, per, total: all.length };
}

function hotGrid() {
  const { pageItems: xs, startOffset, per, total } = hotPageItems();
  const q = $('#hQ').value.trim();
  $('#hEmpty').hidden = total > 0;
  $('#hEmpty').innerHTML = q
    ? `Chưa có video khớp với <b>“${esc(q)}”</b> trong danh sách đã lấy. Bấm nút <b>${esc($('#hSearch').textContent)}</b> ở thanh trên cùng để tìm trực tiếp từ mạng.`
    : HOT.mode === 'ins' ? 'Chưa có video TikTok nào — lấy video ở mục 🏆 Theo chủ đề trước.'
    : HOT.plat === 'tiktok' ? `Chưa có video cho “${esc(HOT.cat)}”. Bấm <b>🔄 Lấy video “${esc(HOT.cat)}”</b> (chỉ lấy danh sách, khoảng 15–30 giây).` : 'Chưa có video Douyin trong mục này. Bấm <b>🔄 Lấy từ Douyin</b> hoặc chọn một chủ đề bên trái.';
  $('#hGrid').innerHTML = xs.map((it, k) => {
    const js = JOBST[it.job_status];
    return `<div class="tcard ${HSEL.has(it.key) ? 'sel' : ''}" data-k="${esc(it.key)}">
      <div class="cv" style="background-image:url('${esc(it.cover || '')}')" data-pv="${esc(it.key)}" title="Bấm để xem trước">
        <div class="ck" data-sel="${esc(it.key)}" title="Chọn để chạy hoặc lưu hàng loạt">${HSEL.has(it.key) ? '✓' : ''}</div>
        <span class="rank">#${startOffset + k + 1}</span>${it.duration ? `<span class="dur">${fmtDur(it.duration)}</span>` : ''}
        ${js ? `<span class="badge st ${js[1]}" style="background:rgba(0,0,0,.6)">${js[0]}</span>` : ''}
      </div>
      <div class="bd">
        <div class="tt" title="${esc(it.title)}">${esc(it.title || '(không có mô tả)')}</div>
        <div class="mt">${it.views ? `<span title="Lượt xem">👁 ${fmtV(it.views)}</span>` : ''}${it.likes ? `<span title="Lượt thích">❤ ${fmtV(it.likes)}</span>` : ''}
          ${it.vph ? `<span title="Lượt xem mỗi giờ kể từ lúc đăng">⚡ ${fmtV(it.vph)}/giờ</span>` : ''}${it.created ? `<span>🕒 ${ago(it.created)}</span>` : ''}
          ${it.author ? `<span>👤 ${esc(it.author)}</span>` : ''}<span>${it.platform === 'tiktok' ? '🎵 TikTok' : '🇨🇳 Douyin'}</span></div>
        <div class="lk"><button data-pv="${esc(it.key)}" style="color:var(--text)">▶ Xem thử</button>
          <button data-dub="${esc(it.url)}" style="color:var(--text)">🎙 Làm ngay</button>
          <button data-lib="${esc(it.url)}" title="Tải về Kho video & tự phân tích AI">📦 Lưu kho</button>
          <button data-copy="${esc(it.url)}">📋 Link</button>
          <a href="${esc(it.url)}" target="_blank" rel="noopener" title="Mở bài gốc">↗</a></div>
      </div></div>`;
  }).join('');
  $$('#hGrid [data-pv]').forEach(b => b.onclick = () => hotPreview(b.dataset.pv));
  $$('#hGrid [data-sel]').forEach(b => b.onclick = e => { e.stopPropagation(); const k = b.dataset.sel; if (HSEL.has(k)) HSEL.delete(k); else HSEL.set(k, HOT.items.find(i => i.key === k)); hotGrid(); });
  if ($('#hSelAll')) {
    const allSel = xs.length > 0 && xs.every(i => HSEL.has(i.key));
    $('#hSelAll').textContent = allSel ? '☐ Bỏ chọn trang' : '☑ Chọn trang này';
  }
  const moreLabel = q ? $('#hSearch').textContent : $('#hCrawl').textContent;
  renderPager($('#hPager'), HOT.page, total, per, p => {
    HOT.page = p; hotGrid();
    $('#hTopBar')?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  }, moreLabel, () => (q ? $('#hSearch') : $('#hCrawl')).click());
  $('#hActions').hidden = !HSEL.size || $('#hotView').hidden; $('#hSelN').textContent = `Đã chọn ${HSEL.size}`;
  $$('#hGrid [data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy));
  $$('#hGrid [data-dub]').forEach(b => b.onclick = () => hotDub(b.dataset.dub));
  $$('#hGrid [data-lib]').forEach(b => b.onclick = () => addToLib(b.dataset.lib));
}

function copyText(t) { navigator.clipboard?.writeText(t).then(() => toast('Đã copy link'), () => prompt('Copy link:', t)) || prompt('Copy link:', t); }

function hotDub(url) {
  if ($('#dlgHotPv')?.open) hotPvClose();
  newJob(); $('#url').value = url; $('#url').dispatchEvent(new Event('input'));
  toast('Đã điền link — chọn kiểu kết quả rồi bấm Bắt đầu');
}

async function addToLib(urls) {
  const list = (Array.isArray(urls) ? urls : [urls]).map(u => String(u || '').trim()).filter(Boolean);
  if (!list.length) return toast('Chưa có link video để lưu');
  try {
    const r = await api('/api/library/add', { method: 'POST', body: { text: list.join('\n') } });
    toast(`📦 Đã thêm ${r.ids.length} video vào Kho video (tự tải & phân tích AI ngầm)`);
  } catch (e) { toast(e.message, 6000); }
}

function hotState(st) {
  $('#hCrawl').disabled = $('#hSearch').disabled = st.running; $('#hStop').hidden = !st.running;
  $('#hState').innerHTML = st.running ? `<div class="bar"><i style="width:${(st.progress || 0) * 100}%"></i></div> ${esc(st.message)}`
    : st.error ? `<span style="color:var(--err)">⚠ ${esc(st.error)}</span>` : st.finished ? `<span class="hint">${esc(st.message)}</span>` : '';
  if (st.running && !HOT.poll) HOT.poll = setInterval(async () => {
    const r = await api('/api/hot?limit=1'); hotState(r.state);
    if (!r.state.running) { clearInterval(HOT.poll); HOT.poll = null; hotRefresh(); }
  }, 1500);
}

async function hotDouyinCrawl(body, clearQueryOnDone = false) {
  try { await api('/api/trending/crawl', { method: 'POST', body: { ...body, pages: +$('#hPages').value || 3 } }); }
  catch (e) { return toast(e.message, 7000) }
  $('#hState').innerHTML = '<span class="hint">⏳ Đang lấy video từ Douyin…</span>';
  const iv = setInterval(async () => {
    const r = await api('/api/trending'); $('#hState').innerHTML = r.state.running ? `<div class="bar"><i style="width:${r.state.progress * 100}%"></i></div> ${esc(r.state.message)}` : '';
    if (!r.state.running) {
      clearInterval(iv);
      if (clearQueryOnDone) { $('#hQ').value = ''; HOT.dcat = ''; }
      HOT.page = 1;
      toast(r.state.error || r.state.message, 6000);
      hotRefresh();
    }
  }, 1800);
}

$('#hCrawl').onclick = async () => {
  if (HOT.plat === 'douyin') {
    return HOT.dcat ? hotDouyinCrawl({ category: HOT.dcat }) : hotDouyinCrawl({ targets: [['douyin', 'featured']] });
  }
  try { await api('/api/hot/crawl', { method: 'POST', body: { kind: 'cat', arg: HOT.cat, pages: +$('#hPages').value || 3 } }); HOT.page = 1; hotRefresh(); }
  catch (e) { toast(e.message, 7000) }
};

$('#hSearch').onclick = async () => {
  const q = $('#hQ').value.trim();
  if (!q) { $('#hQ').focus(); return toast('Nhập từ khoá, @username TikTok hoặc dán link kênh Douyin'); }
  if (/douyin\.com\/user\/|^user:|^MS4wLjAB/i.test(q)) {
    HOT.plat = 'douyin';
    const uid = q.replace(/^user:/i, '').trim();
    return hotDouyinCrawl({ targets: [['douyin', 'user:' + uid]] }, true);
  }
  if (HOT.plat === 'douyin' && !(/^@|tiktok\.com\/@/i.test(q))) {
    return hotDouyinCrawl({ targets: [['douyin', 'search:' + q.replace(/^search:/i, '').trim()]] });
  }
  HOT.plat = 'tiktok';
  try { await api('/api/hot/crawl', { method: 'POST', body: { kind: 'search', arg: q, pages: +$('#hPages').value || 3 } }); HOT.page = 1; hotRefresh(); }
  catch (e) { toast(e.message, 7000) }
};

if ($('#hQClear')) $('#hQClear').onclick = () => { $('#hQ').value = ''; HOT.page = 1; hotRefresh(); $('#hQ').focus(); };
$('#hQ').addEventListener('keydown', async e => {
  if (e.key === 'Enter') {
    clearTimeout(HOT.qt);
    HOT.page = 1;
    await hotRefresh();
    if (!HOT.items.length && $('#hQ').value.trim()) $('#hSearch').click();
  }
});
$('#hQ').addEventListener('input', () => { updateHotSearchBtn(); clearTimeout(HOT.qt); HOT.qt = setTimeout(() => { HOT.page = 1; hotRefresh(); }, 350); });
$('#hStop').onclick = () => api('/api/hot/cancel', { method: 'POST' }).then(() => toast('Đang dừng sau trang hiện tại…'));
$('#hHotRefresh').onclick = () => hotDouyinCrawl({ targets: [['douyin', 'hot']] });
$('#hCopyAll').onclick = () => HOT.items.length ? copyText(HOT.items.map(i => i.url).join('\n')) : toast('Chưa có video');
if ($('#hSelAll')) $('#hSelAll').onclick = () => {
  const { pageItems: xs } = hotPageItems();
  if (!xs.length) return toast('Chưa có video để chọn');
  const allSel = xs.every(i => HSEL.has(i.key));
  xs.forEach(i => allSel ? HSEL.delete(i.key) : HSEL.set(i.key, i));
  hotGrid();
};
$$('#hPlat button').forEach(b => b.onclick = () => { HOT.plat = b.dataset.p; if (HOT.mode === 'ins') HOT.mode = 'top'; HOT.page = 1; hotRefresh(); });
$$('#hMode button').forEach(b => b.onclick = () => { HOT.mode = b.dataset.m; HOT.page = 1; hotRefresh(); });
['#hSort', '#hMaxDur'].forEach(s => $(s).addEventListener('change', () => { HOT.page = 1; hotRefresh(); }));
$('#hLimit').addEventListener('change', () => { HOT.page = 1; hotGrid(); });
const HSEL = new Map();          // key -> item (kept across categories / searches)
$('#hClear').onclick = () => { HSEL.clear(); hotGrid(); };
$('#hCopySel').onclick = () => copyText([...HSEL.values()].map(i => i.url).join('\n'));
if ($('#hToLib')) $('#hToLib').onclick = async () => {
  await addToLib([...HSEL.values()].map(i => i.url));
  HSEL.clear(); hotGrid();
};
$('#hRun').onclick = () => {
  const items = [...HSEL.values()];
  RUN_REC = '';
  askRunSettings(`Chạy ${items.length} video đã chọn`, $('#hRunMode').value, async opts => {
    let n = 0;
    try {
      const dy = items.filter(i => i.platform === 'douyin').map(i => i.id);        // Douyin: through the library (marks them as done there)
      if (dy.length) n += (await api('/api/trending/queue', { method: 'POST', body: { ids: dy, options: opts } })).jobs.length;
      for (const i of items.filter(i => i.platform !== 'douyin')) { await api('/api/jobs', { method: 'POST', body: { url: i.url, options: opts } }); n++; }
      toast(`Đã thêm ${n} job vào hàng đợi`); HSEL.clear(); hotGrid(); loadJobs();
    } catch (e) { toast(`Đã thêm ${n} job; lỗi: ${e.message}`, 8000) }
  });
};
let HPV = null;
async function hotPreview(key) {
  const it = HOT.items.find(i => i.key === key); if (!it) return;
  HPV = it;
  $('#hpTitle').textContent = it.title || it.url; $('#hpOpen').href = it.url;
  const f = $('#hpFrame'), v = $('#hpVideo'); f.hidden = v.hidden = true; f.src = 'about:blank'; v.removeAttribute('src');
  $('#dlgHotPv').showModal();
  if (it.platform === 'tiktok') {               // TikTok's official embed player
    $('#hpMsg').textContent = ''; f.hidden = false;
    f.src = `https://www.tiktok.com/player/v1/${it.id}?autoplay=1&description=1&rel=0`;
  } else {
    $('#hpMsg').textContent = '⏳ Đang lấy link video từ Douyin…';
    try {
      const r = await api(`/api/preview/${it.id}`);
      if (HPV !== it) return;
      $('#hpMsg').textContent = ''; v.hidden = false; v.src = r.stream; v.play().catch(() => { });
    } catch (e) { if (HPV === it) $('#hpMsg').textContent = '⚠ ' + e.message }
  }
}
function hotPvClose() { const f = $('#hpFrame'), v = $('#hpVideo'); f.src = 'about:blank'; v.pause(); v.removeAttribute('src'); v.load(); HPV = null; $('#dlgHotPv').close(); }
$('#hpClose').onclick = hotPvClose;
$('#dlgHotPv').addEventListener('cancel', e => { e.preventDefault(); hotPvClose(); });
$('#hpCopy').onclick = () => HPV && copyText(HPV.url);
if ($('#hpLib')) $('#hpLib').onclick = () => HPV && addToLib(HPV.url);
$('#hpDub').onclick = () => { const u = HPV?.url; hotPvClose(); u && hotDub(u); };
