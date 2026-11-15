/* ---------------- boot ---------------- */
(async () => {
  const r = await api('/api/settings'); SETTINGS = r.settings;
  fillForm(SETTINGS); await loadJobs();
  if (SETTINGS.translate_provider === 'claude_cli')
    api('/api/claude/status').then(s => {
      if (!s.installed) $('#gpuHint').innerHTML = '⚠ Chưa cài Claude Code CLI — xem ⚙ Cài đặt';
      else if (!s.logged_in) $('#gpuHint').innerHTML = '⚠ Claude CLI chưa đăng nhập — ⚙ Cài đặt → 🔑 Đăng nhập Claude';
    }).catch(() => { });
  const h = location.hash.slice(1); if (h) openJob(h);
  let v = 'main'; try { v = localStorage.getItem('view') || 'main' } catch { }
  if (v !== 'main' && !h) showView(v);
})();
