/* ---------------- video library ---------------- */
let LITEMS = [], LSEL = new Set(), LFILTER = 'all', LPOLL = null;
const MODE_SHORT = { dub: '🎙 Lồng tiếng', voiceover: '📢 Thuyết minh', sub: '💬 Chỉ Sub', text: '🔤 Chỉ dịch chữ' };
$('#libAnalyze').onclick = async () => {
  try { const r = await api('/api/library/analyze', { method: 'POST', body: { ids: [...LSEL] } }); toast(`Đang nhận diện ${r.queued} video…`); loadLib(); }
  catch (e) { toast(e.message, 6000) }
};
const PLAT = { douyin: 'Douyin', tiktok: 'TikTok', facebook: 'Facebook', youtube: 'YouTube', web: 'Web' };
const LST = { queued: ['⏳ Chờ tải', 'badge'], downloading: ['⬇ Đang tải', 'badge'], done: ['✓ Đã tải', 'badge'], error: ['✕ Lỗi', 'badge'], cancelled: ['■ Đã huỷ', 'badge'] };
async function loadLib() {
  let r; try { r = await api('/api/library'); } catch (e) { return toast(e.message) }
  LITEMS = r.items;
  const c = r.counts, busy = (c.queued || 0) + (c.downloading || 0);
  $('#libPar').textContent = `Tải song song ${SETTINGS.library_parallel || 3} video` + (busy ? ` · đang chạy ${busy}` : '');
  const f = [['all', `Tất cả (${LITEMS.length})`], ['done', `Đã tải (${c.done || 0})`], ['active', `Đang tải (${busy})`], ['error', `Lỗi (${(c.error || 0) + (c.cancelled || 0)})`]];
  $('#libCounts').innerHTML = f.map(([k, t]) => `<button class="shrink ${LFILTER === k ? 'on' : ''}" data-f="${k}">${t}</button>`).join('') +
    (LITEMS.some(i => i.status === 'done') ? '<button class="shrink" id="libSelAll">Chọn tất cả đã tải</button>' : '');
  $$('#libCounts [data-f]').forEach(b => b.onclick = () => { LFILTER = b.dataset.f; renderLib(); loadLib(); });
  if ($('#libSelAll')) $('#libSelAll').onclick = () => { LITEMS.filter(i => i.status === 'done').forEach(i => LSEL.add(i.id)); renderLib(); };
  renderLib();
  clearTimeout(LPOLL);
  if ((busy || LITEMS.some(i => i.analyzing)) && !$('#libView').hidden) LPOLL = setTimeout(loadLib, 1500);
}
function renderLib() {
  const show = LITEMS.filter(i => LFILTER === 'all' || (LFILTER === 'done' && i.status === 'done') ||
    (LFILTER === 'active' && ['queued', 'downloading'].includes(i.status)) || (LFILTER === 'error' && ['error', 'cancelled'].includes(i.status)));
  [...LSEL].forEach(id => { if (!LITEMS.some(i => i.id === id && i.status === 'done')) LSEL.delete(id); });
  $('#libEmpty').hidden = LITEMS.length > 0;
  $('#libGrid').innerHTML = show.map(i => {
    const st = LST[i.status] || [i.status, 'badge'], done = i.status === 'done', sel = LSEL.has(i.id);
    const jobs = (i.job_status || []).map(j => `<a href="#${j.id}" data-job="${j.id}" title="Mở job">${j.status === 'done' ? '✅ Đã làm' : '⏳ ' + (STATUS_VI[j.status] || j.status)}</a>`).join('');
    return `<div class="tcard lcard ${sel ? 'sel' : ''}" data-id="${i.id}">
      <div class="cv" data-cv="${i.id}" style="${done ? `background-image:url('/library/${i.id}/thumb.jpg')` : ''}">
        ${done ? `<div class="ck">${sel ? '✓' : ''}</div>` : ''}
        <span class="badge st">${st[0]}</span>
        ${i.duration ? `<span class="dur">${fmtDur(i.duration)}</span>` : ''}
      </div>
      <div class="bd">
        <div class="tt" title="${esc(i.title || i.url)}">${esc(i.title || i.url)}</div>
        <div class="mt"><span>${PLAT[i.platform] || i.platform}</span>${i.uploader ? `<span>${esc(i.uploader)}</span>` : ''}${i.size ? `<span>${(i.size / 1048576).toFixed(0)} MB</span>` : ''}</div>
        ${['queued', 'downloading'].includes(i.status) ? `<div class="lbar"><i style="width:${Math.round(100 * (i.progress || 0))}%"></i></div><div class="hint">${esc(i.detail || '')}</div>` : ''}
        ${i.status === 'error' ? `<div class="err" title="${esc(i.error || '')}">${esc(i.detail || i.error || '')}</div>` : ''}
        ${i.analyzing ? `<div class="hint">🔎 ${i.analyzing === 'queued' ? 'Chờ nhận diện…' : esc(i.detail || 'Đang nhận diện…')}</div>` : ''}
        ${i.analysis && !i.analyzing ? `<div class="rec" title="${esc((i.analysis.genre ? i.analysis.genre + ' — ' : '') + i.analysis.reason + '\n' + (i.analysis.summary || ''))}">✨ ${esc(MODE_SHORT[i.analysis.mode] || i.analysis.mode)}${i.analysis.genre ? ` <span>· ${esc(i.analysis.genre)}</span>` : ''}</div>` : ''}
        <div class="lk">
          ${done ? `<button data-play="${i.id}">▶ Xem</button>` : ''}
          ${['queued', 'downloading'].includes(i.status) ? `<button data-cancel="${i.id}">■ Huỷ</button>` : ''}
          ${['error', 'cancelled'].includes(i.status) ? `<button data-retry="${i.id}">↻ Tải lại</button>` : ''}
          <a href="${esc(i.url)}" target="_blank" rel="noopener" title="Mở link gốc">↗ Gốc</a>${jobs}
        </div>
      </div></div>`;
  }).join('');
  // click the cover = select / unselect; watching is the ▶ Xem button
  $$('#libGrid .cv[data-cv]').forEach(cv => cv.onclick = () => {
    const it = LITEMS.find(x => x.id === cv.dataset.cv); if (!it || it.status !== 'done') return;
    LSEL.has(it.id) ? LSEL.delete(it.id) : LSEL.add(it.id); renderLib();
  });
  $$('#libGrid [data-play]').forEach(b => b.onclick = () => {
    const it = LITEMS.find(x => x.id === b.dataset.play); if (!it) return;
    $('#lpTitle').textContent = it.title || it.url; $('#lpVideo').src = `/library/${it.id}/view.mp4`; $('#dlgLib').showModal(); $('#lpVideo').play().catch(() => { });
  });
  $$('#libGrid .bd').forEach(bd => bd.onclick = e => {
    if (e.target.closest('a,button')) return;
    const id = bd.parentElement.dataset.id, it = LITEMS.find(x => x.id === id);
    if (it?.status === 'done') { LSEL.has(id) ? LSEL.delete(id) : LSEL.add(id); renderLib(); }
  });
  $$('#libGrid [data-cancel]').forEach(b => b.onclick = () => api(`/api/library/${b.dataset.cancel}/cancel`, { method: 'POST' }).then(loadLib));
  $$('#libGrid [data-retry]').forEach(b => b.onclick = () => api(`/api/library/${b.dataset.retry}/retry`, { method: 'POST' }).then(loadLib));
  $$('#libGrid [data-job]').forEach(a => a.onclick = e => { e.preventDefault(); showView('main'); openJob(a.dataset.job); });
  $('#libActions').hidden = $('#libView').hidden || !LSEL.size;
  $('#libSelN').textContent = `${LSEL.size} video`;
}
$('#libAdd').onclick = async () => {
  const text = $('#libLinks').value.trim(); if (!text) return toast('Dán link trước đã');
  try { const r = await api('/api/library/add', { method: 'POST', body: { text } }); $('#libLinks').value = ''; toast(`Đã thêm ${r.ids.length} link vào hàng tải`); LFILTER = 'all'; loadLib(); }
  catch (e) { toast(e.message, 6000) }
};
$('#libClear').onclick = () => { LSEL.clear(); renderLib(); };
$('#libDel').onclick = async () => {
  if (!confirm(`Xoá ${LSEL.size} video khỏi kho? File đã tải sẽ bị xoá (job đã xử lý vẫn giữ nguyên).`)) return;
  await api('/api/library/delete', { method: 'POST', body: { ids: [...LSEL] } }); LSEL.clear(); loadLib();
};
/* ---- "process selected videos" (library / trending): show the full settings form in a dialog first ----
   The main form card is moved into the dialog (one form, no duplicate inputs) and put back when it closes. */
let RUN_GO = null, RUN_SLOT = null, RUN_REC = '';
function runFormCard() { return $('#formTitle').parentElement; }
function askRunSettings(title, mode, onGo) {
  const card = runFormCard();
  RUN_SLOT = document.createComment('form-slot'); card.parentNode.insertBefore(RUN_SLOT, card);
  $('#runHost').appendChild(card); card.classList.add('in-run'); card.classList.remove('job-open');
  fillForm(SETTINGS); setMode(mode);
  $('#formTitle').textContent = 'Cài đặt xử lý'; $('#jobFormNote').hidden = true;
  $('#runTitle').textContent = title; $('#runGo').textContent = '🚀 Bắt đầu xử lý';
  $('#runRec').innerHTML = RUN_REC || ''; $('#runRec').hidden = !RUN_REC;
  RUN_GO = onGo; $('#dlgRun').showModal();
}
function restoreRunForm() {
  const card = runFormCard();
  if (RUN_SLOT) { RUN_SLOT.parentNode.insertBefore(card, RUN_SLOT); RUN_SLOT.remove(); RUN_SLOT = null; }
  card.classList.remove('in-run');
  if (CUR && STATE) showJobSettings(STATE); else showDefaultSettings();
}
$('#dlgRun').addEventListener('close', restoreRunForm);
$('#runCancel').onclick = $('#runX').onclick = () => { RUN_GO = null; $('#dlgRun').close(); };
$('#runGo').onclick = () => {
  const opts = formOptions(), go = RUN_GO; RUN_GO = null; $('#dlgRun').close();
  if (go) go(opts);
};

$('#libRun').onclick = () => {
  const ids = [...LSEL];
  const recs = ids.map(id => LITEMS.find(i => i.id === id)).filter(i => i && i.analysis);
  RUN_REC = recs.length ? `<span><b>✨ Kết quả nhận diện</b> (${recs.length}/${ids.length} video):<br>` + recs.slice(0, 6).map(i =>
    `• ${esc((i.title || '').slice(0, 40))}: <b>${esc(MODE_SHORT[i.analysis.mode])}</b> — ${esc(i.analysis.reason.slice(0, 140))}`).join('<br>') +
    (recs.length < ids.length ? '<br>Video chưa nhận diện sẽ được nhận diện lúc chạy (kiểu “✨ Tự nhận diện”).' : '') + '</span>' : '';
  askRunSettings(`Xử lý ${ids.length} video trong kho`, $('#libMode').value, opts => {
    const durs = ids.map(id => (LITEMS.find(i => i.id === id) || {}).duration || 60);
    showEstimate({ durations: durs, options: opts }, `Ước tính cho ${ids.length} video trong kho (chạy lần lượt)`, async () => {
      const r = await api('/api/library/process', { method: 'POST', body: { ids, options: opts } });
      toast(`Đã đưa ${r.jobs.length} video vào hàng đợi xử lý`); LSEL.clear(); loadLib(); loadJobs();
    });
  });
};
$('#lpClose').onclick = () => { $('#lpVideo').pause(); $('#dlgLib').close(); };
$('#dlgLib').addEventListener('close', () => $('#lpVideo').pause());
