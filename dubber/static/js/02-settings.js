/* ---------------- settings dialog ---------------- */
(function buildSetNav() {
  const short = s => s.replace(/\s*\(.*$/, '').replace(/:.*$/, '');
  $('#setNav').innerHTML = '';
  $$('#setBody > fieldset').forEach(fs => {
    const lg = fs.querySelector('legend'); if (!lg) return;
    const b = document.createElement('button'); b.textContent = short(lg.textContent.trim()); b.title = lg.textContent.trim();
    b.onclick = () => fs.scrollIntoView({ behavior: 'smooth', block: 'start' });
    $('#setNav').appendChild(b);
  });
})();
$('#btnSettings').onclick = async () => {
  const r = await api('/api/settings'); SETTINGS = r.settings;
  $$('#dlgSettings [data-k]').forEach(el => setVal(el, SETTINGS[el.dataset.k]));
  $('#bandTop').value = SETTINGS.manual_sub_band[0]; $('#bandBot').value = SETTINGS.manual_sub_band[1];
  FB_ORDER = null; loadProviders();
  $('#dlgSettings').showModal(); dtkStatus(); douyinStatus(); claudeStatus(); fbStatus(); ttwStatus(); cliStatus('codex_cli'); cliStatus('gemini_cli'); ttStatus();
};
/* ---- TikTok login (official API) ---- */
async function ttStatus() {
  let r; try { r = await api('/api/tiktok/status'); } catch { return }
  $('#ttRedirect').value = r.redirect_uri;
  $('#ttStatus').textContent = r.connected ? `✅ Đã kết nối${r.display_name ? ': ' + r.display_name : ''} · ${r.scope} · còn ${r.expires_days} ngày`
    : r.configured ? 'Chưa đăng nhập' : 'Chưa nhập Client Key / Secret';
  $('#ttLogout').hidden = !r.connected;
  return r;
}
$('#ttCopy').onclick = () => { navigator.clipboard.writeText($('#ttRedirect').value); toast('Đã chép Redirect URI'); };
$('#ttLogin').onclick = async () => {
  const o = {}; $$('#dlgSettings [data-k^="tiktok_"]').forEach(el => o[el.dataset.k] = getVal(el));
  await api('/api/settings', { method: 'PUT', body: o });
  window.open('/api/tiktok/login', '_blank');
  toast('Đăng nhập trên trang TikTok vừa mở, cho phép ứng dụng, rồi quay lại đây', 7000);
  const t0 = Date.now(); while (Date.now() - t0 < 180000) { await new Promise(r => setTimeout(r, 3000)); const r = await ttStatus(); if (r?.connected) { toast('Đã kết nối TikTok'); break; } }
};
$('#ttLogout').onclick = async () => { await api('/api/tiktok/logout', { method: 'POST' }); ttStatus(); };

/* translation providers: status + fallback order */
let PROVS = [], FB_ORDER = null;
async function loadProviders() {
  $('#provTable').innerHTML = '<tr><td class="hint">Đang kiểm tra provider…</td></tr>';
  const r = await api('/api/providers'); PROVS = r.providers;
  if (!FB_ORDER) {   // full order: chained ones first (in order), then the rest unticked
    const inChain = (SETTINGS.translate_fallbacks || []).map(x => x === 'google' ? 'google_free' : x);
    FB_ORDER = [...inChain.filter(id => PROVS.some(p => p.id === id)), ...PROVS.map(p => p.id).filter(id => !inChain.includes(id))]
      .map(id => ({ id, on: inChain.includes(id) }));
  }
  renderProviders();
}
function renderProviders() {
  const byId = Object.fromEntries(PROVS.map(p => [p.id, p]));
  $('#provTable').innerHTML = FB_ORDER.map((f, i) => {
    const p = byId[f.id]; if (!p) return '';
    const st = !p.configured ? ['⚪', 'chưa cấu hình'] : !p.reachable ? ['🔴', 'không chạy'] : p.cooldown_until ? ['🟡', 'tạm nghỉ'] : ['🟢', 'sẵn sàng'];
    return `<tr><td style="width:26px"><input type="checkbox" data-fb="${i}" ${f.on ? 'checked' : ''}></td>
      <td><b>${esc(p.label)}</b>${p.model ? ` <span class="hint">· ${esc(p.model)}</span>` : ''}${p.local ? ' <span class="badge">local</span>' : ''}
        <div class="hint">${st[0]} ${st[1]}${p.reason ? ' — ' + esc(p.reason) : ''}</div><div class="hint" data-tres="${p.id}"></div>
        <label class="hint" style="margin:2px 0 0">giãn cách giữa 2 lần gọi <input type="number" min="0" max="60" step="0.1" data-gap="${p.id}" value="${(SETTINGS.translate_min_interval_s || {})[p.id] || 0}" style="width:64px;padding:2px 6px"> giây</label></td>
      <td style="width:150px;white-space:nowrap"><button data-up="${i}" title="Lên">▲</button><button data-dn="${i}" title="Xuống">▼</button>
        <button data-test="${p.id}">Thử</button>${p.id === 'local_nllb' && !p.configured ? '<button data-mtdl="1">⬇ Tải model</button>' : ''}</td></tr>`;
  }).join('');
  $$('#provTable [data-fb]').forEach(c => c.onchange = () => { FB_ORDER[+c.dataset.fb].on = c.checked; });
  $$('#provTable [data-mtdl]').forEach(b => b.onclick = async () => {
    const model = $('#dlgSettings [data-k="local_mt_model"]').value;
    await api('/api/providers/local_nllb/download', { method: 'POST', body: { model } });
    const out = $('#provTable [data-tres="local_nllb"]');
    const poll = async () => {
      const st = await api('/api/providers/local_nllb/download');
      out.textContent = st.running ? '⏳ Đang tải model offline (0,6–1,4 GB)…' : st.error ? '❌ ' + st.error : st.downloaded ? '✅ Đã tải xong' : '';
      if (st.running) setTimeout(poll, 3000); else loadProviders();
    };
    poll();
  });
  $$('#provTable [data-up]').forEach(b => b.onclick = () => { const i = +b.dataset.up; if (i > 0) { [FB_ORDER[i - 1], FB_ORDER[i]] = [FB_ORDER[i], FB_ORDER[i - 1]]; renderProviders(); } });
  $$('#provTable [data-dn]').forEach(b => b.onclick = () => { const i = +b.dataset.dn; if (i < FB_ORDER.length - 1) { [FB_ORDER[i + 1], FB_ORDER[i]] = [FB_ORDER[i], FB_ORDER[i + 1]]; renderProviders(); } });
  $$('#provTable [data-test]').forEach(b => b.onclick = async () => {
    const out = $(`#provTable [data-tres="${b.dataset.test}"]`); out.textContent = '⏳ Đang thử…';
    const opts = {}; $$('#dlgSettings [data-k]').forEach(el => opts[el.dataset.k] = getVal(el));
    try {
      const r = await api(`/api/providers/${b.dataset.test}/test`, { method: 'POST', body: { options: opts } });
      out.textContent = r.ok ? `✅ “你好，世界！” → “${r.result}” (${r.seconds}s${r.model ? ', ' + r.model : ''})` : `❌ ${r.error}`;
    } catch (e) { out.textContent = '❌ ' + e.message }
  });
}
let CLAUDE_POLL = null;
async function claudeStatus() {
  $('#cliStatus').textContent = 'Đang kiểm tra Claude CLI…';
  let s;
  try { s = await api('/api/claude/status'); } catch (e) { $('#cliStatus').textContent = e.message; return }
  $('#btnClaudeLogin').hidden = !s.installed || s.logged_in;
  $('#btnClaudeLogout').hidden = !s.logged_in;
  $('#cliStatus').innerHTML = !s.installed ? '⚠ Chưa cài Claude Code CLI. Chạy: <code>npm i -g @anthropic-ai/claude-code</code>'
    : s.logged_in ? `🟢 Đã đăng nhập Claude${s.email ? ' — ' + esc(s.email) : ''} (${esc(s.method || '')})`
    : s.logging_in ? '🟡 Đang chờ bạn đăng nhập trong trình duyệt…' : '⚪ Claude CLI chưa đăng nhập';
  clearTimeout(CLAUDE_POLL);
  if (s.logging_in) CLAUDE_POLL = setTimeout(claudeStatus, 3000);
  else if (s.logged_in) $('#gpuHint').textContent = '';
  return s;
}
$('#btnClaudeCheck').onclick = claudeStatus;
async function cliStatus(pid) {
  const el = $('#st_' + pid); el.textContent = 'Đang kiểm tra…';
  try {
    const s = await api(`/api/cli/${pid}/status`);
    el.textContent = !s.installed ? '⚪ Chưa cài' : s.logging_in ? '🟡 Đang chờ bạn đăng nhập…' : s.logged_in ? '🟢 Đã đăng nhập' : '⚪ Chưa đăng nhập';
    if (s.logging_in) setTimeout(() => cliStatus(pid), 3000);
  } catch (e) { el.textContent = e.message }
}
$$('[data-cli-login]').forEach(b => b.onclick = async () => { await api(`/api/cli/${b.dataset.cliLogin}/login`, { method: 'POST' }); toast('Đã mở cửa sổ đăng nhập — làm theo trên trình duyệt'); setTimeout(() => cliStatus(b.dataset.cliLogin), 2500); });
$$('[data-cli-logout]').forEach(b => b.onclick = async () => { if (!confirm('Đăng xuất?')) return; await api(`/api/cli/${b.dataset.cliLogout}/logout`, { method: 'POST' }); cliStatus(b.dataset.cliLogout); });
async function fbStatus() {
  $('#fbStatus').textContent = 'Đang kiểm tra…';
  try {
    const s = await api('/api/facebook/status');
    $('#fbStatus').textContent = s.open ? '🟡 Cửa sổ đăng nhập đang mở — xong thì đóng lại' : s.error ? '🔴 ' + s.error
      : s.logged_in ? '🟢 Đã đăng nhập Facebook trong app' : '⚪ Chưa đăng nhập (vẫn dùng được Reels công khai)';
    if (s.open) setTimeout(fbStatus, 3000);
  } catch (e) { $('#fbStatus').textContent = e.message }
}
async function ttwStatus() {
  $('#ttwStatus').textContent = 'Đang kiểm tra…';
  try {
    const s = await api('/api/tiktokweb/status');
    $('#ttwStatus').textContent = s.open ? '🟡 Cửa sổ đăng nhập đang mở — xong thì đóng lại' : s.error ? '🔴 ' + s.error
      : s.logged_in ? '🟢 Đã đăng nhập TikTok' + (s.creative ? ' · Creative Center ✓' : ' · Creative Center: chưa đăng nhập (chỉ thấy vài dòng đầu)')
        : '⚪ Chưa đăng nhập TikTok';
    if (s.open) setTimeout(ttwStatus, 3000);
  } catch (e) { $('#ttwStatus').textContent = e.message }
}
$('#btnTtwLogin').onclick = async () => {
  try { await api('/api/tiktokweb/login', { method: 'POST' }); } catch (e) { return toast(e.message, 6000); }
  toast('Đang mở cửa sổ TikTok… đăng nhập xong thì đóng cửa sổ'); setTimeout(ttwStatus, 4000);
};
$('#btnFbLogin').onclick = async () => { await api('/api/facebook/login', { method: 'POST' }); toast('Đang mở cửa sổ Facebook… đăng nhập xong thì đóng cửa sổ'); setTimeout(fbStatus, 4000); };
$('#btnClaudeLogin').onclick = async () => {
  try { await api('/api/claude/login', { method: 'POST', body: {} }); toast('Đã mở cửa sổ đăng nhập Claude — làm theo trên trình duyệt'); setTimeout(claudeStatus, 2500); }
  catch (e) { toast(e.message, 8000) }
};
$('#btnClaudeLogout').onclick = async () => {
  if (!confirm('Đăng xuất Claude CLI trên máy này?')) return;
  await api('/api/claude/logout', { method: 'POST' }); claudeStatus();
};
$('#closeSettings').onclick = $('#cancelSettings').onclick = () => $('#dlgSettings').close();
async function dtkStatus() {
  const el = $('#dtkStatus'); if (!el) return;
  el.textContent = 'Đang kiểm tra DTK…';
  const opts = {};
  $$('#dlgSettings [data-k^="dtk_"]').forEach(x => opts[x.dataset.k] = getVal(x));
  try {
    const s = await api('/api/dtk/status', { method: 'POST', body: { options: opts } });
    if (s.ok) {
      el.textContent = `🟢 Đã kết nối (${s.mode}) · ${s.usable_identities != null ? s.usable_identities + ' identity sẵn sàng · ' : ''}${s.summary || ''}`;
    } else {
      el.textContent = (s.enabled === false ? '⚪ Đang tắt · ' : '⚪ Chưa kết nối được DTK (app sẽ tự dùng trình duyệt / yt-dlp) — ') + (s.error || '');
    }
  } catch (e) { el.textContent = '🔴 ' + e.message; }
}
$('#btnDtkCheck').onclick = dtkStatus;
async function douyinStatus() {
  $('#douyinStatus').textContent = 'Đang kiểm tra…';
  try {
    const s = await api('/api/douyin/status');
    $('#douyinStatus').textContent = s.error ? '🔴 ' + s.error : s.open ?'🟡 Cửa sổ đăng nhập đang mở — xong thì đóng nó lại.' : s.busy ? 'Đang bận tải video…'
      : s.logged_in ? '🟢 Đã đăng nhập Douyin' : s.logged_in === false ? '⚪ Chưa đăng nhập (vẫn tải được nếu Douyin không đòi xác minh)' : (s.error || '');
    if (s.open) setTimeout(douyinStatus, 3000);
  } catch (e) { $('#douyinStatus').textContent = e.message }
}
$('#btnDouyinLogin').onclick = async () => { await api('/api/douyin/login', { method: 'POST' }); toast('Đang mở cửa sổ Douyin… đăng nhập xong thì đóng cửa sổ'); setTimeout(douyinStatus, 4000); };
$('#saveSettings').onclick = async () => {
  const o = {}; $$('#dlgSettings [data-k]').forEach(el => o[el.dataset.k] = getVal(el));
  o.manual_sub_band = [parseFloat($('#bandTop').value), parseFloat($('#bandBot').value)];
  if (FB_ORDER) { o.translate_fallbacks = FB_ORDER.filter(f => f.on).map(f => f.id); o.translate_providers_seen = FB_ORDER.map(f => f.id); }
  const gaps = {}; $$('#provTable [data-gap]').forEach(el => { const v = parseFloat(el.value); if (v > 0) gaps[el.dataset.gap] = v; });
  if ($$('#provTable [data-gap]').length) o.translate_min_interval_s = gaps;
  SETTINGS = await api('/api/settings', { method: 'PUT', body: o }); $('#dlgSettings').close(); toast('Đã lưu cài đặt');
};

/* ---------------- search inside the settings dialog ---------------- */
$('#setSearch').oninput = () => {
  const q = $('#setSearch').value.trim().toLowerCase();
  $$('#setBody > fieldset').forEach(f => f.hidden = !!q && !f.textContent.toLowerCase().includes(q));
};
