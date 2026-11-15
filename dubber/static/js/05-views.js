/* ---------------- view switch ---------------- */
function showView(v) {
  $$('.navtabs button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  if (v === 'hot' || v === 'trend') { FIND_SUB = v; v = 'find'; }          // old saved views
  const sub = v === 'find' ? FIND_SUB : '';
  $$('.navtabs button').forEach(b => b.classList.toggle('on', b.dataset.view === v));
  $('#findBar').hidden = v !== 'find'; $$('#findSeg button').forEach(b => b.classList.toggle('on', b.dataset.s === sub));
  $('#mainView').hidden = v !== 'main'; $('#trendView').hidden = sub !== 'trend'; $('#libView').hidden = v !== 'lib';
  $('#autoView').hidden = v !== 'auto'; $('#hotView').hidden = sub !== 'hot';
  if (v === 'auto') loadAuto();
  if (sub === 'hot') loadHot();
  $('#hActions').hidden = sub !== 'hot' || !HSEL.size;
  $('#libActions').hidden = v !== 'lib' || !LSEL.size;
  if (v === 'lib') loadLib();
  $('#tActions').hidden = sub !== 'trend' || !TSEL.size;
  if (sub === 'trend') loadTrending();
  try { localStorage.setItem('view', v) } catch { }
}
$$('.navtabs button').forEach(b => b.onclick = () => showView(b.dataset.view));
let FIND_SUB = 'hot'; try { FIND_SUB = localStorage.getItem('findSub') || 'hot' } catch { }
$$('#findSeg button').forEach(b => b.onclick = () => { FIND_SUB = b.dataset.s; try { localStorage.setItem('findSub', FIND_SUB) } catch { } showView('find'); });

/* ---------------- job tabs ---------------- */
let JTAB = 'result', JT_STALE = new Set();
const JT_LOAD = { result: () => loadTT(), edit: () => { loadLines(); loadStyle(); loadScreenText(); loadContent(); loadWM(); }, review: () => loadReview(), post: () => loadPost() };
function jtPick(j) {            // a job that is still working opens on its log, a finished one on the result
  let t = ['running', 'queued', 'error', 'cancelled'].includes(j.status) ? 'log' : j.status === 'awaiting_approval' ? 'edit' : 'result';
  try { const m = JSON.parse(sessionStorage.getItem('jtab') || '{}'); if (m[j.id]) t = m[j.id]; } catch { }
  jtSet(t, false);
}
function jtSet(t, remember = true) {
  JTAB = t; $('#jobView').dataset.tab = t;
  $$('#jTabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  if (remember && CUR) try { const m = JSON.parse(sessionStorage.getItem('jtab') || '{}'); m[CUR] = t; sessionStorage.setItem('jtab', JSON.stringify(m)); } catch { }
  jtLoad();
}
async function jtLoad() {
  if (JT_STALE.has(JTAB) && JT_LOAD[JTAB]) { JT_STALE.delete(JTAB); await JT_LOAD[JTAB](); }
  setTimeout(jtEmpty, 500);
}
function jtEmpty() {
  const any = [...$$(`#jobView [data-jtab="${JTAB}"]`)].some(c => !c.hidden);
  $('#jTabEmpty').hidden = any;
  $('#jTabEmpty').textContent = { result: 'Bản kiểm tra chuẩn TikTok hiện ở đây khi video xong.', edit: 'Bản dịch, Biên kịch, chữ màn hình và watermark hiện ở đây khi các bước tương ứng chạy xong.', review: 'Chấm điểm và ghi chú khi video đã xong.', post: 'Tiêu đề, mô tả và hashtag hiện ở đây khi video xong.' }[JTAB] || '';
}
$$('#jTabs button').forEach(b => b.onclick = () => jtSet(b.dataset.t));
