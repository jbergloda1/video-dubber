/* ---------------- Biên kịch (content plan) ---------------- */
let CT = null;
async function loadContent() {
  try { CT = await api(`/api/jobs/${CUR}/content`); } catch { CT = null; }
  const show = CT && (CT.plan || CT.enabled);
  $('#ctCard').hidden = !show; if (!show) return;
  const opt = (o, cur) => Object.entries(o).map(([k, v]) => `<option value="${k}" ${k === cur ? 'selected' : ''}>${esc(k)} — ${esc(v.slice(0, 60))}</option>`).join('');
  const p = CT.plan || {};
  $('#ctType').innerHTML = opt(CT.types, p.video_type_set || p.video_type || CT.timeline.video_type_rule);
  $('#ctPersona').innerHTML = '<option value="auto">✨ AI tự chọn</option>' + opt(CT.personas, (p._meta || {}).persona);
  $('#ctTone').innerHTML = '<option value="auto">✨ AI tự chọn</option>' + opt(CT.tones, (p._meta || {}).tone);
  $('#ctTypeWhy').textContent = p.video_type_why || CT.timeline.video_type_rule_why || '';
  $('#ctApprove').hidden = !CT.awaiting;
  $('#ctNote').hidden = !(CT.awaiting || (p && !p.approved));
  $('#ctNote').textContent = CT.awaiting ? '⏸ Job đang chờ bạn duyệt kịch bản: sửa nếu cần rồi bấm ✅ Duyệt & render.'
    : (CT.plan ? '⚠ Kịch bản chưa được duyệt — xem lại trước khi đăng.' : '');
  renderContent();
}
const ctT = t => `<button title="Xem khung hình" data-fr="${t}">🖼</button>`;
function ctList(key, cols) {
  const arr = (CT.plan || {})[key] || [];
  return arr.map((x, i) => `<div class="ct-item ${x.enabled === false ? 'off' : ''}" data-k="${key}" data-i="${i}">
    <input type="checkbox" data-f="enabled" ${x.enabled === false ? '' : 'checked'} title="Bật/tắt">${cols(x, i)}</div>`).join('');
}
function renderContent() {
  const p = CT.plan;
  if (!p) { $('#ctBody').innerHTML = '<div class="hint">Chưa có kịch bản — chạy lại từ bước “Biên kịch (AI)” hoặc bấm ✍ AI viết lại.</div>'; return; }
  const h = p.hook || {}, e = p.ending || {}, c = p.cover || {}, po = p.post || {};
  const num = (f, v) => `<input type="number" step="0.1" data-f="${f}" value="${v ?? ''}">`;
  const txt = (f, v, ph = '') => `<input type="text" data-f="${f}" value="${esc(v || '')}" placeholder="${ph}">`;
  const hooks = p.hooks || [];
  const hookOpts = hooks.length > 1 ? `<div class="ct-hooks">${hooks.map((x, i) => `<label class="ct-hook ${i === (p.hook_choice || 0) ? 'on' : ''}">
      <input type="radio" name="ctHook" value="${i}" ${i === (p.hook_choice || 0) ? 'checked' : ''}>
      <span><b>Phương án ${i + 1}${x.angle ? ' · ' + esc(x.angle) : ''}</b><br>🖋 ${esc(x.overlay || '—')}<br>🎙 ${esc(x.vo || '—')}</span></label>`).join('')}</div>` : '';
  const issues = (p._issues || []).length ? `<div class="hint" style="color:var(--warn,#b45309)">⚠ AI chưa sửa hết: ${esc(p._issues.slice(0, 4).join(' · '))}</div>` : '';
  $('#ctBody').innerHTML = `${issues}
    <div class="ct-sec">Hook (0–3 giây) ${ctT(h.start || 0)}</div>
    ${hookOpts}
    <div class="ct-item" data-k="hook">${txt('overlay', h.overlay, 'Chữ to giữa màn hình (≤ 10 chữ)')}</div>
    <div class="ct-item" data-k="hook">${txt('vo', h.vo, 'Giọng kể đè lên cảnh mở')}<button data-say="${esc(h.vo || '')}">▶</button></div>
    <div class="ct-sec">Thanh tiêu đề (bản 9:16)</div>
    <div class="ct-item" data-k="_">${'<input type="text" id="ctTitle" value="' + esc(p.title_bar || '') + '">'}</div>
    <div class="ct-sec">Lời kể (${(p.narration || []).length} câu) <button id="ctAddNar">＋ câu</button></div>
    ${ctList('narration', x => `${num('start', x.start)}${txt('text', x.text)}<button data-say="${esc(x.text)}">▶</button>${ctT(x.start)}`)}
    <div class="ct-sec">Chỗ cắt khoảng lặng (${(p.cuts || []).filter(x => x.enabled !== false).length} đoạn)</div>
    ${ctList('cuts', x => `${num('from', x.from)}${num('to', x.to)}<span class="hint" style="flex:1">${esc(x.reason || '')}</span>${ctT(x.from)}`)}
    <div class="ct-sec">Chữ giữ chân</div>
    ${ctList('retention_texts', x => `${num('at', x.at)}${txt('text', x.text)}${ctT(x.at)}`)}
    <div class="ct-sec">Khối chữ nhỏ đọc thành lời (twist)</div>
    ${ctList('replace_screentext', x => `<span class="hint">#${x.id}</span><textarea data-f="vo" rows="${Math.max(2, (x.vo || []).length)}">${esc((x.vo || []).join('\n'))}</textarea>`)}
    <div class="ct-sec">Cái kết + câu lặp (loop)</div>
    <div class="ct-item" data-k="ending"><input type="checkbox" data-f="enabled" ${e.enabled === false ? '' : 'checked'}>${txt('overlay', e.overlay, 'Câu chốt trên nền tối')}</div>
    <div class="ct-item" data-k="ending">${txt('loop_vo', e.loop_vo, 'Câu cuối nối về cảnh mở')}<button data-say="${esc(e.loop_vo || '')}">▶</button></div>
    <div class="ct-sec">Ảnh bìa ${ctT(c.at || 0)}</div>
    <div class="ct-item" data-k="cover">${num('at', c.at)}${txt('text', c.text, 'Chữ trên ảnh bìa')}</div>
    ${CT.files.cover ? `<img src="/files/${CUR}/cover.jpg?${Date.now()}" style="max-width:180px;border-radius:8px">` : ''}
    <div class="ct-sec">Đăng bài</div>
    <div class="ct-item" data-k="post"><textarea data-f="caption" rows="3" placeholder="Caption: câu đầu có từ khoá, cuối là câu hỏi gây tranh cãi">${esc(po.caption || '')}</textarea></div>
    <div class="ct-item" data-k="post">${txt('hashtags', (po.hashtags || []).join(' '), '#phimngan #...')}</div>
    <div class="ct-item" data-k="post">${txt('pinned_comment', po.pinned_comment, 'Bình luận ghim')}</div>
    <div class="ct-item" data-k="post">${txt('credit', po.credit, 'Nguồn: … (Douyin)')}<label class="chk" style="margin:0"><input type="checkbox" data-f="ai_label" ${po.ai_label === false ? '' : 'checked'}> Nhãn “Nội dung do AI tạo”</label></div>
    <div class="ct-sec">Clip mồi</div>
    ${(p.teasers || []).map((x, i) => `<div class="ct-item" data-k="teasers" data-i="${i}">${num('from', x.from)}${num('to', x.to)}${txt('overlay', x.overlay)}${CT.files['teaser_' + (i + 1)] ? `<a href="/files/${CUR}/teaser_${i + 1}.mp4?dl=1">⬇</a>` : ''}</div>`).join('') || '<div class="hint">Không có</div>'}`;
  $$('#ctBody [data-fr]').forEach(b => b.onclick = () => {
    const t = +b.dataset.fr; $('#ctFrame').src = `/api/jobs/${CUR}/frame?t=${t}`; $('#ctFrameT').textContent = `Khung hình ở ${fmtT(t)} (video gốc)`; });
  $$('#ctBody [data-say]').forEach(b => b.onclick = async () => {
    const text = b.parentElement.querySelector('input[type=text]')?.value || b.dataset.say; if (!text) return;
    b.disabled = true;
    try { const r = await fetch(`/api/jobs/${CUR}/content/voice-test`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
      if (!r.ok) throw new Error((await r.json()).detail); const a = new Audio(URL.createObjectURL(await r.blob())); a.play(); }
    catch (e) { toast(e.message, 6000) } b.disabled = false; });
  $$('#ctBody input[name=ctHook]').forEach(r => r.onchange = () => {
    const p2 = collectContent(), i = +r.value, x = (p2.hooks || [])[i] || {};
    p2.hook = { ...(p2.hook || {}), vo: x.vo || '', overlay: x.overlay || '' }; p2.hook_choice = i;
    CT.plan = p2; renderContent();
  });
  $('#ctAddNar').onclick = () => { collectContent(); (CT.plan.narration = CT.plan.narration || []).push({ start: 0, text: '', duck_original: 0.3 }); renderContent(); };
}
function collectContent() {
  const p = JSON.parse(JSON.stringify(CT.plan));
  const val = el => el.type === 'checkbox' ? el.checked : el.type === 'number' ? parseFloat(el.value) : el.value;
  $$('#ctBody .ct-item').forEach(row => {
    const k = row.dataset.k, i = row.dataset.i;
    row.querySelectorAll('[data-f]').forEach(el => {
      const f = el.dataset.f; let v = val(el);
      if (f === 'vo' && k === 'replace_screentext') v = v.split('\n').map(x => x.trim()).filter(Boolean);
      if (f === 'hashtags') v = v.split(/\s+/).filter(Boolean);
      if (i !== undefined) { p[k][+i][f] = v; } else if (k !== '_') { p[k] = p[k] || {}; p[k][f] = v; }
    });
  });
  p.title_bar = $('#ctTitle')?.value || '';
  p.narration = (p.narration || []).filter(n => (n.text || '').trim());
  if (p.video_type !== $('#ctType').value) p.video_type_set = $('#ctType').value;
  return p;
}
$('#ctSave').onclick = async () => {
  try { const r = await api(`/api/jobs/${CUR}/content`, { method: 'PUT', body: { plan: collectContent(), render: true } });
    toast('Đã lưu kịch bản — đang render lại (chỉ đọc lại câu kể đã đổi)' + (r.problems.length ? ' · ' + r.problems.join('; ') : ''), 8000); }
  catch (e) { toast(e.message, 6000) }
};
$('#ctApprove').onclick = async () => {
  try { await api(`/api/jobs/${CUR}/content`, { method: 'PUT', body: { plan: collectContent(), approve: true, render: true } });
    toast('Đã duyệt kịch bản — đang render', 5000); }
  catch (e) { toast(e.message, 6000) }
};
$('#ctRewrite').onclick = async () => {
  if (!confirm('AI viết lại kịch bản (bỏ các chỗ bạn đã sửa)? Không xem lại phim, chỉ viết lại.')) return;
  try { await api(`/api/jobs/${CUR}/content/rewrite`, { method: 'POST', body: { content_persona: $('#ctPersona').value, content_tone: $('#ctTone').value, video_type: $('#ctType').value } });
    toast('AI đang viết lại kịch bản…', 5000); }
  catch (e) { toast(e.message, 6000) }
};
