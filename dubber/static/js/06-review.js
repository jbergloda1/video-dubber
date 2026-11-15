/* ---------------- 📋 Đánh giá & sửa ---------------- */
let RV = null, RV_TAB = 'sum', RV_T = null;
const RV_KIND = { script: '✍ Kịch bản', tech: '🛠 Kỹ thuật', other: '📝 Khác' };
const RV_ST = { open: 'Mới', proposed: 'AI đã đề xuất', applied: 'Đã áp dụng', queued: 'Chờ Claude sửa', fixed: 'Claude đã sửa', dismissed: 'Bỏ qua' };
const RV_WHERE = { line: 'Câu', narration: 'Lời kể', hook_overlay: 'Chữ hook', title_bar: 'Thanh tiêu đề', loop_vo: 'Câu loop' };
async function loadReview() {
  const done = STATE && ['done', 'error', 'awaiting_approval'].includes(STATE.status);
  $('#rvCard').hidden = !done; if (!done) return;
  try { RV = await api(`/api/jobs/${CUR}/review`); } catch (e) { return }
  if (!$('#rvArea').options.length) $('#rvArea').innerHTML = Object.entries(RV.areas).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
  renderReview();
}
function rvVideo() { return [...$$('#videos video')].find(v => v.offsetParent) || $('#videos video'); }
function renderReview() {
  if (!RV) return;
  $$('#rvTabs button').forEach(b => b.classList.toggle('on', b.dataset.r === RV_TAB));
  $('#rvSum').hidden = RV_TAB !== 'sum'; $('#rvNotes').hidden = RV_TAB !== 'notes'; $('#rvMusic').hidden = RV_TAB !== 'music';
  const open = RV.notes.filter(n => !['applied', 'fixed', 'dismissed'].includes(n.status)).length;
  $('#rvNotesN').textContent = RV.notes.length ? `(${open}/${RV.notes.length})` : '';
  $('#rvSaved').textContent = RV.updated ? 'Lưu lúc ' + new Date(RV.updated * 1000).toLocaleString('vi-VN', { hour: '2-digit', minute: '2-digit', day: '2-digit', month: '2-digit' }) : '';
  // summary: what the pipeline did, so you know what to look at
  const st = STATE.steps.filter(s => s.status === 'done' && s.detail);
  $('#rvFacts').innerHTML = `<div><b>Thời lượng</b><span>${RV.duration ? fmtDur(RV.duration) : '—'}</span></div>` +
    st.map(s => `<div><b>${esc(s.name)}</b><span>${esc(s.detail)}</span></div>`).join('');
  const warns = [...new Set([...$$('#log .l-warn, #log .l-error')].map(e => e.textContent.slice((e.querySelector('time')?.textContent || '').length)))].slice(-8);
  $('#rvWarn').innerHTML = warns.length ? `<details><summary class="hint">⚠ ${warns.length} cảnh báo trong nhật ký — nên kiểm tra kỹ các chỗ này</summary><div class="hint">${warns.map(esc).join('<br>')}</div></details>` : '';
  $('#rvRate').innerHTML = Object.entries(RV.cats).map(([k, name]) => `<div class="row"><span>${esc(name)}</span><span class="stars" data-k="${k}">${[1, 2, 3, 4, 5].map(i => `<button data-v="${i}" class="${(RV.ratings[k] || 0) >= i ? 'on' : ''}">★</button>`).join('')}</span></div>`).join('');
  $$('#rvRate .stars button').forEach(b => b.onclick = () => { const k = b.parentElement.dataset.k, v = +b.dataset.v; RV.ratings[k] = RV.ratings[k] === v ? 0 : v; renderReview(); });
  if (document.activeElement !== $('#rvComment')) $('#rvComment').value = RV.comment || '';
  // notes
  $('#rvArea').hidden = $('#rvKind').value !== 'tech';
  $('#rvKindHint').textContent = { script: 'AI sẽ viết lại đúng các câu bạn nhắc tới, hiện bản cũ/mới để bạn chọn áp dụng — rồi chỉ tạo lại giọng + render.', tech: 'Bấm “Duyệt → gửi Claude” để đưa lỗi vào hàng chờ (kèm khung hình tại mốc, cảnh báo trong log). Sau đó bảo Claude Code “sửa các lỗi trong hàng chờ”.', other: 'Chỉ lưu lại để tham khảo.' }[$('#rvKind').value];
  $('#rvTv').textContent = RV_T == null ? 'không gắn mốc' : fmtDur(RV_T) || '0:00';
  $('#rvList').innerHTML = RV.notes.slice().reverse().map(n => {
    const p = n.proposal;
    const diff = n.status === 'proposed' && p ? `<div class="hint">${esc(p.summary || '')}${p.changes.length ? '' : ' — AI không thấy chỗ nào cần đổi'}</div><div class="rv-diff">${p.changes.map((c, k) => `<label><input type="checkbox" data-keep="${k}" checked><span><b>${RV_WHERE[c.where] || c.where}${c.i != null ? ' ' + (c.i + 1) : ''}${c.t != null ? ' · ' + fmtDur(c.t) : ''}</b><br><span class="o">${esc(c.old)}</span><br><span class="n">${esc(c.new)}</span></span></label>`).join('')}</div>` : '';
    const applied = n.status === 'applied' && n.applied ? `<div class="hint">Đã áp dụng ${n.applied.length} chỗ sửa.</div>` : '';
    const fix = n.fix ? `<div class="hint">🛠 ${esc(n.fix)}</div>` : '';
    const acts = [];
    if (n.kind === 'script' && ['open', 'proposed', 'applied'].includes(n.status)) acts.push(`<button data-a="propose" data-id="${n.id}">✍ ${n.status === 'open' ? 'AI đề xuất sửa' : 'Đề xuất lại'}</button>`);
    if (n.kind === 'script' && n.status === 'proposed' && p?.changes.length) acts.push(`<button class="primary" data-a="apply" data-id="${n.id}">✅ Áp dụng các chỗ đã chọn &amp; render lại</button>`);
    if (n.kind === 'tech' && n.status === 'open') acts.push(`<button class="primary" data-a="approve" data-id="${n.id}">✅ Duyệt → gửi Claude sửa</button>`);
    if (n.t != null) acts.push(`<button data-a="seek" data-t="${n.t}">▶ Xem ${fmtDur(n.t)}</button>`);
    if (!['dismissed', 'fixed', 'applied'].includes(n.status)) acts.push(`<button data-a="dismiss" data-id="${n.id}">Bỏ qua</button>`);
    acts.push(`<button data-a="del" data-id="${n.id}" class="danger">🗑</button>`);
    return `<div class="rv-note"><div class="h"><span>${RV_KIND[n.kind]}${n.kind === 'tech' && n.area ? ' · ' + esc(RV.areas[n.area] || '') : ''}</span>${n.t != null ? `<span>⏱ ${fmtDur(n.t)}</span>` : ''}<span class="sp"></span><span class="rv-st ${n.status}">${RV_ST[n.status] || n.status}</span></div>
      <div class="tx">${esc(n.text)}</div>${diff}${applied}${fix}${n.frame ? `<img src="/feedback/frame/${n.id}" style="max-width:220px;border-radius:6px;margin:4px 0" alt="">` : ''}<div class="act">${acts.join('')}</div></div>`;
  }).join('') || '<div class="hint" style="margin-top:8px">Chưa có ghi chú nào.</div>';
  $$('#rvList [data-a]').forEach(b => b.onclick = () => rvAct(b));
  // music
  const sg = RV.music.suggestion;
  $('#rvMusicSug').innerHTML = sg ? `<div class="rv-mus">
      <div><b>${esc(sg.mood)}</b> · ${esc(sg.genre)} · ${Math.round(sg.bpm || 0)} BPM · năng lượng ${esc(sg.energy || '')} · dài <b>${fmtDur(sg.duration)}</b> (${Math.round(sg.duration)} giây)</div>
      ${sg.instruments?.length ? `<div class="hint">Nhạc cụ: ${sg.instruments.map(esc).join(', ')}</div>` : ''}
      ${sg.why ? `<div class="hint">${esc(sg.why)}</div>` : ''}
      ${sg.structure?.length ? `<div class="hint" style="margin-top:4px">${sg.structure.map(x => `${fmtDur(x.from)}–${fmtDur(x.to)}: ${esc(x.desc)}`).join('<br>')}</div>` : ''}
      ${sg.avoid ? `<div class="hint">Tránh: ${esc(sg.avoid)}</div>` : ''}
      <div style="margin-top:8px;display:flex;justify-content:space-between;align-items:center"><b>Prompt (tiếng Anh — dán vào Suno/Udio…)</b><button data-copy="en">📋 Copy</button></div><pre>${esc(sg.prompt_en)}</pre>
      ${sg.prompt_vi ? `<div style="display:flex;justify-content:space-between;align-items:center"><b>Bản tiếng Việt</b><button data-copy="vi">📋 Copy</button></div><pre>${esc(sg.prompt_vi)}</pre>` : ''}
      ${sg.search_keywords?.length ? `<div class="hint">Tìm nhạc có sẵn: ${sg.search_keywords.map(esc).join(' · ')}</div>` : ''}</div>` : '';
  $$('#rvMusicSug [data-copy]').forEach(b => b.onclick = () => copyText(b.dataset.copy === 'en' ? sg.prompt_en : sg.prompt_vi));
  $('#rvOffBtn').hidden = !RV.bgm_enabled;
  $('#rvMusicState').textContent = RV.bgm_enabled && RV.music.file ? `🎵 Đang dùng nhạc của bạn: ${RV.music.file} (âm lượng ${Math.round((RV.music.volume || 1) * 100)}%, giữ nền gốc ${Math.round((RV.music.keep_original || 0) * 100)}%)` : 'Đang dùng nhạc nền gốc của video.';
}
async function rvAct(b) {
  const id = b.dataset.id, a = b.dataset.a;
  try {
    if (a === 'seek') { const v = rvVideo(); if (v) { v.currentTime = +b.dataset.t; v.play(); v.scrollIntoView({ block: 'center' }); } return; }
    if (a === 'del') { if (!confirm('Xoá ghi chú này?')) return; await api(`/api/jobs/${CUR}/review/notes/${id}`, { method: 'DELETE' }); }
    if (a === 'dismiss') await api(`/api/jobs/${CUR}/review/notes/${id}/dismiss`, { method: 'POST' });
    if (a === 'approve') { b.disabled = true; b.textContent = '⏳ Đang lưu khung hình…'; await api(`/api/jobs/${CUR}/review/notes/${id}/approve`, { method: 'POST' }); toast('Đã đưa vào hàng chờ — bảo Claude Code “sửa các lỗi trong hàng chờ”', 7000); }
    if (a === 'propose') { b.disabled = true; b.textContent = '⏳ AI đang đọc kịch bản…'; await api(`/api/jobs/${CUR}/review/notes/${id}/propose`, { method: 'POST' }); }
    if (a === 'apply') {
      const keep = [...b.closest('.rv-note').querySelectorAll('[data-keep]')].filter(c => c.checked).map(c => +c.dataset.keep);
      if (!keep.length) return toast('Chưa chọn chỗ sửa nào');
      const r = await api(`/api/jobs/${CUR}/review/notes/${id}/apply`, { method: 'POST', body: { keep } });
      toast(`Đã áp dụng — đang chạy lại từ bước ${STEP_NAMES[r.from_step] || r.from_step}`, 6000);
    }
  } catch (e) { toast(e.message, 8000) }
  loadReview();
}
$$('#rvTabs button').forEach(b => b.onclick = () => { RV_TAB = b.dataset.r; renderReview(); });
$('#rvKind').onchange = renderReview;
$('#rvT').onclick = () => { const v = rvVideo(); RV_T = RV_T == null && v ? Math.round(v.currentTime * 10) / 10 : null; renderReview(); };
$('#rvAdd').onclick = async () => {
  const text = $('#rvText').value.trim(); if (!text) return toast('Ghi nội dung cần sửa');
  try {
    const n = await api(`/api/jobs/${CUR}/review/notes`, { method: 'POST', body: { kind: $('#rvKind').value, text, t: RV_T, area: $('#rvArea').value } });
    $('#rvText').value = ''; RV_T = null; await loadReview();
    if (n.kind === 'script') { const b = $(`#rvList [data-a="propose"][data-id="${n.id}"]`); b && rvAct(b); }       // straight to the AI's proposal
  } catch (e) { toast(e.message, 6000) }
};
$('#rvSaveRate').onclick = async () => {
  try { await api(`/api/jobs/${CUR}/review/ratings`, { method: 'PUT', body: { ratings: RV.ratings, comment: $('#rvComment').value } }); toast('Đã lưu đánh giá'); loadReview(); }
  catch (e) { toast(e.message, 6000) }
};
$('#rvSuggest').onclick = async () => {
  const b = $('#rvSuggest'); b.disabled = true; b.textContent = '⏳ AI đang nghe nội dung…';
  try { RV.music.suggestion = await api(`/api/jobs/${CUR}/review/music/suggest`, { method: 'POST' }); renderReview(); }
  catch (e) { toast(e.message, 8000) }
  b.disabled = false; b.textContent = '🎵 Gợi ý lại';
};
$('#rvVol').oninput = () => $('#rvVolO').textContent = Math.round($('#rvVol').value * 100) + '%';
$('#rvKeep').oninput = () => $('#rvKeepO').textContent = Math.round($('#rvKeep').value * 100) + '%';
$('#rvUse').onclick = async () => {
  const f = $('#rvFile').files[0]; if (!f) return toast('Chọn file nhạc trước');
  const fd = new FormData(); fd.append('file', f); fd.append('volume', $('#rvVol').value); fd.append('keep_original', $('#rvKeep').value); fd.append('offset', $('#rvOff').value || 0);
  const b = $('#rvUse'); b.disabled = true; b.textContent = '⏳ Đang xử lý nhạc…';
  try {
    const r = await fetch(`/api/jobs/${CUR}/review/music`, { method: 'POST', body: fd });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    toast('Đã thay nhạc nền — đang render lại', 6000);
  } catch (e) { toast(e.message, 8000) }
  b.disabled = false; b.textContent = '⬆ Dùng nhạc này & render lại'; loadReview();
};
$('#rvOffBtn').onclick = async () => {
  if (!confirm('Bỏ nhạc của bạn, quay về nhạc nền gốc và render lại?')) return;
  try { await api(`/api/jobs/${CUR}/review/music/off`, { method: 'POST' }); toast('Đang render lại với nhạc gốc'); loadReview(); } catch (e) { toast(e.message, 6000) }
};
