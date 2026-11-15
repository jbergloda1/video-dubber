/* ---------------- 🚀 Link hot ---------------- */
const HOT = { plat: 'tiktok', mode: 'top', cat: 'Công nghệ', dcat: '', items: [], poll: null, ttcats: [], dcats: [] };
try { Object.assign(HOT, JSON.parse(localStorage.getItem('hot') || '{}'), { items: [], poll: null }) } catch { }
const fmtV = n => { n = +n || 0; const f = (x, u) => (x >= 100 ? Math.round(x) : x.toFixed(1).replace('.0', '').replace('.', ',')) + ' ' + u; return n >= 1e9 ? f(n / 1e9, 'tỷ') : n >= 1e6 ? f(n / 1e6, 'Tr') : n >= 1e3 ? f(n / 1e3, 'N') : String(Math.round(n)); };
const ago = t => { if (!t) return ''; const h = (Date.now() / 1000 - t) / 3600; return h < 24 ? Math.max(1, Math.round(h)) + ' giờ trước' : h < 24 * 60 ? Math.round(h / 24) + ' ngày trước' : Math.round(h / 24 / 30) + ' tháng trước' };
function hotSave() { try { localStorage.setItem('hot', JSON.stringify({ plat: HOT.plat, mode: HOT.mode, cat: HOT.cat, dcat: HOT.dcat })) } catch { } }
async function loadHot() {
  if (!HOT.dcats.length) { try { HOT.dcats = (await api('/api/trending')).categories } catch { } }
  await hotRefresh();
}
async function hotRefresh() {
  hotSave();
  $$('#hPlat button').forEach(b => b.classList.toggle('on', b.dataset.p === HOT.plat));
  $$('#hMode button').forEach(b => b.classList.toggle('on', b.dataset.m === HOT.mode));
  $('#hTopBar').hidden = HOT.mode === 'ins'; $('#hKwBar').hidden = HOT.mode !== 'kw'; $('#hIns').hidden = HOT.mode !== 'ins';
  const q = HOT.mode === 'kw' ? $('#hQ').value.trim() : '';
  const cat = HOT.mode === 'kw' ? '' : HOT.plat === 'tiktok' ? (HOT.cat === 'Tất cả' ? 'Tất cả' : HOT.cat) : HOT.dcat;
  const p = new URLSearchParams({ platform: HOT.plat, cat, q, sort: $('#hSort').value, limit: $('#hLimit').value, max_dur: +$('#hMaxDur').value || 0 });
  let r;
  try { r = await api('/api/hot?' + p); } catch (e) { return toast(e.message, 6000) }
  HOT.ttcats = r.categories; $('#hCC').href = r.creative_center;
  hotCats(); hotState(r.state);
  $('#hTitle').textContent = HOT.plat === 'tiktok' ? 'TikTok · ' + HOT.cat : 'Douyin · ' + (HOT.dcats.find(c => c.id === HOT.dcat)?.name || 'Tất cả');
  $('#hCrawl').textContent = HOT.plat === 'tiktok' ? '🔄 Lấy video mới' : '🔄 Lấy từ Douyin';
  $('#hSugg').innerHTML = (r.suggest || []).map(s => `<button data-q="${esc(s)}">${esc(s)}</button>`).join('');
  $$('#hSugg [data-q]').forEach(b => b.onclick = () => { $('#hQ').value = b.dataset.q; hotRefresh(); });
  if (HOT.mode === 'ins') {
    const ins = await api('/api/hot/insights');
    $('#hHot').innerHTML = ins.douyin_hot.length ? ins.douyin_hot.map(h => `<a href="${esc(h.url)}" target="_blank" rel="noopener" title="Mở chủ đề trên Douyin"><b>${h.rank || ''}</b><span>${esc(h.title)}</span><small>🔥 ${fmtN(h.hot_value)}</small></a>`).join('')
      : '<div class="hint">Chưa có — bấm 🔄 Làm mới.</div>';
    $('#hInsSugg').innerHTML = ins.suggest.length ? ins.suggest.map(s => `<button data-q="${esc(s)}">${esc(s)}</button>`).join('') : '<span class="hint">Tìm thử một từ khoá ở mục 🔎 Từ khoá, TikTok sẽ gợi ý thêm.</span>';
    $$('#hInsSugg [data-q]').forEach(b => b.onclick = () => { HOT.mode = 'kw'; $('#hQ').value = b.dataset.q; hotRefresh(); });
    HOT.items = ins.tiktok_rising;
  } else HOT.items = r.items;
  hotGrid(r.total);
}
function hotCats() {
  const rows = HOT.plat === 'tiktok'
    ? HOT.ttcats.map(c => `<div class="tcat ${HOT.cat === c ? 'on' : ''}" data-c="${esc(c)}"><span>${esc(c)}</span></div>`)
    : [`<div class="tcat ${!HOT.dcat ? 'on' : ''}" data-c=""><span>📚 Tất cả</span></div>`].concat(HOT.dcats.map(n => `<div class="tcat ${HOT.dcat === n.id ? 'on' : ''}" data-c="${n.id}" style="padding-left:${8 + n.depth * 16}px"><span>${esc(n.name)}</span></div>`));
  $('#hCats').innerHTML = rows.join('');
  $$('#hCats .tcat').forEach(el => el.onclick = () => { if (HOT.plat === 'tiktok') HOT.cat = el.dataset.c; else HOT.dcat = el.dataset.c; if (HOT.mode === 'ins') HOT.mode = 'top'; hotRefresh(); });
}
function hotGrid(total) {
  const xs = HOT.items;
  $('#hEmpty').hidden = xs.length > 0;
  $('#hEmpty').innerHTML = HOT.mode === 'kw' ? ($('#hQ').value.trim() ? 'Chưa có video khớp trong các video đã lấy. Bấm <b>🔎 Tìm trên TikTok</b> (cần đăng nhập TikTok), hoặc lấy thêm video theo thể loại.' : 'Nhập từ khoá.')
    : HOT.mode === 'ins' ? 'Chưa có video TikTok nào — lấy video ở mục 🏆 Top theo thể loại trước.'
    : HOT.plat === 'tiktok' ? `Chưa có video cho “${esc(HOT.cat)}”. Bấm <b>🔄 Lấy video mới</b> (chỉ lấy danh sách, khoảng 20–40 giây).` : 'Chưa có video Douyin trong mục này. Bấm <b>🔄 Lấy từ Douyin</b>.';
  $('#hGrid').innerHTML = xs.map((it, k) => {
    const js = JOBST[it.job_status];
    return `<div class="tcard ${HSEL.has(it.key) ? 'sel' : ''}" data-k="${esc(it.key)}">
      <div class="cv" style="background-image:url('${esc(it.cover || '')}')" data-pv="${esc(it.key)}" title="Xem trước">
        <div class="ck" data-sel="${esc(it.key)}" title="Chọn để chạy hàng loạt">${HSEL.has(it.key) ? '✓' : ''}</div>
        <span class="rank">#${k + 1}</span>${it.duration ? `<span class="dur">${fmtDur(it.duration)}</span>` : ''}
        ${js ? `<span class="badge st ${js[1]}" style="background:rgba(0,0,0,.6)">${js[0]}</span>` : ''}
      </div>
      <div class="bd">
        <div class="tt" title="${esc(it.title)}">${esc(it.title || '(không có mô tả)')}</div>
        <div class="mt">${it.views ? `<span title="Lượt xem">👁 ${fmtV(it.views)}</span>` : ''}${it.likes ? `<span title="Lượt thích">❤ ${fmtV(it.likes)}</span>` : ''}
          ${it.vph ? `<span title="Lượt xem mỗi giờ kể từ lúc đăng">⚡ ${fmtV(it.vph)}/giờ</span>` : ''}${it.created ? `<span>🕒 ${ago(it.created)}</span>` : ''}
          ${it.author ? `<span>👤 ${esc(it.author)}</span>` : ''}<span>${it.platform === 'tiktok' ? '🎵 TikTok' : '🇨🇳 Douyin'}</span></div>
        <div class="lk"><button data-pv="${esc(it.key)}" style="color:var(--text)">▶ Xem trước</button>
          <button data-copy="${esc(it.url)}">📋 Link</button>
          <button data-dub="${esc(it.url)}" style="color:var(--text)">🎙 Lồng tiếng</button>
          <a href="${esc(it.url)}" target="_blank" rel="noopener">↗</a></div>
      </div></div>`;
  }).join('');
  $$('#hGrid [data-pv]').forEach(b => b.onclick = () => hotPreview(b.dataset.pv));
  $$('#hGrid [data-sel]').forEach(b => b.onclick = e => { e.stopPropagation(); const k = b.dataset.sel; if (HSEL.has(k)) HSEL.delete(k); else HSEL.set(k, HOT.items.find(i => i.key === k)); hotGrid(); });
  $('#hActions').hidden = !HSEL.size || $('#hotView').hidden; $('#hSelN').textContent = `Đã chọn ${HSEL.size}`;
  $$('#hGrid [data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy));
  $$('#hGrid [data-dub]').forEach(b => b.onclick = () => hotDub(b.dataset.dub));
}
function copyText(t) { navigator.clipboard?.writeText(t).then(() => toast('Đã copy link'), () => prompt('Copy link:', t)) || prompt('Copy link:', t); }
function hotDub(url) {
  if ($('#dlgHotPv').open) hotPvClose();
  newJob(); $('#url').value = url; $('#url').dispatchEvent(new Event('input'));
  toast('Đã điền link — chọn kiểu kết quả rồi bấm Bắt đầu');
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
async function hotDouyinCrawl(body) {
  try { await api('/api/trending/crawl', { method: 'POST', body: { ...body, pages: +$('#hPages').value || 3 } }); }
  catch (e) { return toast(e.message, 7000) }
  $('#hState').innerHTML = '<span class="hint">⏳ Đang lấy từ Douyin (chạy ở tab Trending)…</span>';
  const iv = setInterval(async () => {
    const r = await api('/api/trending'); $('#hState').innerHTML = r.state.running ? `<div class="bar"><i style="width:${r.state.progress * 100}%"></i></div> ${esc(r.state.message)}` : '';
    if (!r.state.running) { clearInterval(iv); toast(r.state.error || r.state.message, 6000); hotRefresh(); }
  }, 2000);
}
$('#hCrawl').onclick = async () => {
  if (HOT.plat === 'douyin') return HOT.dcat ? hotDouyinCrawl({ category: HOT.dcat }) : toast('Chọn một thể loại Douyin bên trái trước');
  try { await api('/api/hot/crawl', { method: 'POST', body: { kind: 'cat', arg: HOT.cat, pages: +$('#hPages').value || 3 } }); hotRefresh(); }
  catch (e) { toast(e.message, 7000) }
};
$('#hSearch').onclick = async () => {
  const q = $('#hQ').value.trim(); if (!q) return toast('Nhập từ khoá');
  try { await api('/api/hot/crawl', { method: 'POST', body: { kind: 'search', arg: q, pages: +$('#hPages').value || 3 } }); hotRefresh(); }
  catch (e) { toast(e.message, 7000) }
};
$('#hQ').addEventListener('keydown', e => { if (e.key === 'Enter') hotRefresh(); });
$('#hQ').addEventListener('input', () => { clearTimeout(HOT.qt); HOT.qt = setTimeout(hotRefresh, 400); });
$('#hStop').onclick = () => api('/api/hot/cancel', { method: 'POST' }).then(() => toast('Đang dừng sau trang hiện tại…'));
$('#hHotRefresh').onclick = () => hotDouyinCrawl({ targets: [['douyin', 'hot']] });
$('#hCopyAll').onclick = () => HOT.items.length ? copyText(HOT.items.map(i => i.url).join('\n')) : toast('Chưa có video');
$$('#hPlat button').forEach(b => b.onclick = () => { HOT.plat = b.dataset.p; if (HOT.mode === 'ins') HOT.mode = 'top'; hotRefresh(); });
$$('#hMode button').forEach(b => b.onclick = () => { HOT.mode = b.dataset.m; hotRefresh(); if (HOT.mode === 'kw') $('#hQ').focus(); });
['#hSort', '#hLimit', '#hMaxDur'].forEach(s => $(s).addEventListener('change', hotRefresh));
const HSEL = new Map();          // key -> item (kept across categories / searches)
$('#hClear').onclick = () => { HSEL.clear(); hotGrid(); };
$('#hCopySel').onclick = () => copyText([...HSEL.values()].map(i => i.url).join('\n'));
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
    $('#hpMsg').textContent = '⏳ Đang lấy link video từ Douyin (khoảng 10–20 giây)…';
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
$('#hpDub').onclick = () => { const u = HPV?.url; hotPvClose(); u && hotDub(u); };
