(() => {
'use strict';

/* ======================================================= helpers */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = n => Number(n || 0).toLocaleString();
const bytes = n => { n = Number(n || 0); const u = ['B','KB','MB','GB']; let i = 0; while (n >= 1024 && i < 3) { n /= 1024; i++; } return (i ? n.toFixed(1) : n) + ' ' + u[i]; };
const when = iso => iso ? new Date(iso).toLocaleString([], {dateStyle: 'medium', timeStyle: 'short'}) : '—';
const ago = iso => {
  if (!iso) return '—';
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return 'just now'; if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago'; return Math.floor(s / 86400) + ' d ago';
};
const uptime = s => { const d = Math.floor(s / 86400), h = Math.floor(s % 86400 / 3600), m = Math.floor(s % 3600 / 60); return (d ? d + 'd ' : '') + (h ? h + 'h ' : '') + m + 'm'; };
const initial = s => esc((s || '?').trim().charAt(0).toUpperCase() || '?');

async function api(path, {method = 'GET', json, form} = {}) {
  const opts = {method, headers: {'X-Requested-With': 'tgadmin'}, credentials: 'same-origin'};
  if (json !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(json); }
  if (form) opts.body = form;
  const r = await fetch(path, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* not json */ }
  if (r.status === 401 && path !== '/api/login') { showLogin(); throw new Error('Please log in again'); }
  if (!r.ok || !data || data.ok === false) throw new Error((data && data.error) || ('Request failed (' + r.status + ')'));
  return data;
}

function toast(msg, kind = '') {
  const t = document.createElement('div');
  t.className = 'toast ' + kind; t.textContent = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => t.remove(), kind === 'err' ? 6500 : 3800);
}

function modal(html) {
  const root = $('#modal-root');
  root.innerHTML = '<div class="overlay"><div class="modal">' + html + '</div></div>';
  const close = () => { root.innerHTML = ''; };
  $('.overlay', root).addEventListener('mousedown', e => { if (e.target.classList.contains('overlay')) close(); });
  return {el: $('.modal', root), close};
}

function confirmBox({title, text, ok = 'Confirm', danger = false, type = null}) {
  return new Promise(res => {
    const m = modal(`<h2>${esc(title)}</h2><p class="muted">${text}</p>
      ${type ? `<label class="f">Type <code>${esc(type)}</code> to confirm<input type="text" id="cf-type" autocomplete="off"></label>` : ''}
      <div class="actions"><button class="btn" data-x="no">Cancel</button>
      <button class="btn ${danger ? 'danger' : 'primary'}" data-x="yes" ${type ? 'disabled' : ''}>${esc(ok)}</button></div>`);
    const yes = $('[data-x=yes]', m.el);
    if (type) $('#cf-type', m.el).addEventListener('input', e => { yes.disabled = e.target.value !== type; });
    m.el.addEventListener('click', e => {
      const x = e.target.closest('[data-x]'); if (!x) return;
      m.close(); res(x.dataset.x === 'yes');
    });
  });
}

async function busy(btn, fn) {
  const old = btn ? btn.innerHTML : '';
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spin"></span>'; }
  try { return await fn(); }
  catch (e) { toast(e.message, 'err'); }
  finally { if (btn) { btn.disabled = false; btn.innerHTML = old; } }
}

function collect(root) {
  const out = {};
  $$('[data-f]', root).forEach(el => {
    const path = el.dataset.f.split('.');
    let v;
    if (el.type === 'checkbox') v = el.checked;
    else if (el.dataset.t === 'lines') v = el.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    else v = el.value;
    let o = out;
    path.slice(0, -1).forEach(k => { o = o[k] = o[k] || {}; });
    o[path[path.length - 1]] = v;
  });
  return out;
}

function chart(data) {
  const W = 640, H = 170, top = 14, bot = 26, max = Math.max(1, ...data.map(d => d.count));
  const bw = W / Math.max(1, data.length);
  const bars = data.map((d, i) => {
    const h = Math.max(d.count ? 3 : 0, (H - top - bot) * d.count / max);
    const x = i * bw + bw * .18, y = H - bot - h;
    const label = i % 2 === 0 || data.length < 8 ? `<text x="${x + bw * .32}" y="${H - 8}" text-anchor="middle">${esc(d.date.slice(5))}</text>` : '';
    const val = d.count ? `<text x="${x + bw * .32}" y="${y - 4}" text-anchor="middle">${d.count}</text>` : '';
    return `<rect class="bar" x="${x}" y="${y}" width="${bw * .64}" height="${h}" rx="5"><title>${esc(d.date)}: ${d.count}</title></rect>${label}${val}`;
  }).join('');
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" width="100%" role="img" aria-label="New users per day">
    <defs><linearGradient id="g" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="var(--accent2)"/><stop offset="1" stop-color="var(--accent)"/></linearGradient></defs>
    <line x1="0" x2="${W}" y1="${H - bot}" y2="${H - bot}"/>${bars}</svg>`;
}

const statCard = (lbl, val, hint = '') => `<div class="card stat"><div class="lbl">${esc(lbl)}</div><div class="val">${val}</div><div class="hint">${esc(hint)}</div></div>`;
const loading = '<div class="loading"><span class="spin"></span></div>';
const pill = (txt, kind = '') => `<span class="pill ${kind}">${txt}</span>`;
const runPill = b => b.running ? pill('<i class="dot"></i> Running', 'ok') : (b.enabled ? pill('<i class="dot"></i> Starting / error', 'warn') : pill('Disabled'));

/* ======================================================= shell & routing */
let routeId = 0;
const ACT = {};

function showLogin() {
  $('#root').innerHTML = `<div class="login"><form class="card" id="login-form">
    <div class="logo">🤖</div><h1>TG Control</h1><p class="muted">Sign in to manage your Telegram bots.</p>
    <label class="f" style="margin-top:18px">Password<input type="password" id="pw" autocomplete="current-password" autofocus></label>
    <button class="btn primary" style="width:100%;justify-content:center;margin-top:16px" type="submit">Sign in</button></form></div>`;
  $('#login-form').addEventListener('submit', async e => {
    e.preventDefault();
    await busy($('button', e.target), async () => {
      await api('/api/login', {method: 'POST', json: {password: $('#pw').value}});
      boot();
    });
  });
}

function showShell() {
  $('#root').innerHTML = `<div class="app"><aside class="sidebar">
    <div class="brand"><div class="logo">🤖</div><span>TG Control</span></div>
    <nav class="nav">
      <a href="#/dashboard" data-nav="dashboard"><span class="ico">📊</span>Dashboard</a>
      <a href="#/bots" data-nav="bots"><span class="ico">🤖</span>Bots</a>
      <a href="#/backups" data-nav="backups"><span class="ico">💾</span>Backups</a>
      <a href="#/system" data-nav="system"><span class="ico">⚙️</span>System</a>
    </nav>
    <div class="side-foot">
      <button data-act="theme"><span class="ico">🌓</span><span class="lbl-t">Theme</span></button>
      <button data-act="logout"><span class="ico">🚪</span><span class="lbl-t">Sign out</span></button>
    </div></aside><main class="main" id="view"></main></div>`;
}

async function route() {
  if (!$('#view')) return;
  const my = ++routeId;
  const [page, id, tab] = (location.hash.replace(/^#\/?/, '') || 'dashboard').split('/');
  $$('[data-nav]').forEach(a => a.classList.toggle('active', a.dataset.nav === page));
  const v = $('#view');
  v.innerHTML = loading;
  try {
    const guard = () => my === routeId;
    if (page === 'dashboard') await pageDashboard(v, guard);
    else if (page === 'bots' && !id) await pageBots(v, guard);
    else if (page === 'bots') await pageBot(v, decodeURIComponent(id), tab || 'overview', guard);
    else if (page === 'backups') await pageBackups(v, guard);
    else if (page === 'system') await pageSystem(v, guard);
    else location.hash = '#/dashboard';
  } catch (e) {
    if (my === routeId) v.innerHTML = `<div class="card"><div class="callout bad">${esc(e.message)}</div></div>`;
  }
}

/* ======================================================= dashboard */
async function pageDashboard(v, guard) {
  const d = await api('/api/overview');
  if (!guard()) return;
  const t = d.totals;
  v.innerHTML = `<div class="page-head"><div><h1>Dashboard</h1><div class="sub">Everything across all your bots</div></div>
      <button class="btn right" data-act="refresh">↻ Refresh</button><a class="btn primary" href="#/backups">💾 Backup data</a></div>
    <div class="grid g5">
      ${statCard('Bots', num(t.bots), t.running + ' running')}${statCard('Total users', num(t.users))}
      ${statCard('New today', num(t.new_today))}${statCard('Active (24h)', num(t.active_24h))}${statCard('Blocked', num(t.blocked))}</div>
    <div class="card" style="margin-top:16px"><h2>New users — last 14 days</h2><div class="desc">All bots combined</div>${chart(d.daily)}</div>
    <div class="card" style="margin-top:16px"><div class="row"><h2>Your bots</h2><a class="btn sm right" href="#/bots">Manage →</a></div>
    ${botTable(d.bots)}</div>`;
}

function botTable(bots) {
  if (!bots.length) return '<div class="empty">No bots yet. <a href="#/bots">Add your first bot →</a></div>';
  return `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>Bot</th><th>Status</th><th>Mode</th><th>Group Start</th><th>Users</th><th>Groups</th><th>Channels</th></tr></thead><tbody>
    ${bots.map(b => `<tr class="click" data-go="#/bots/${encodeURIComponent(b.id)}"><td><b>${esc(b.name)}</b><div class="muted small">${b.username ? '@' + esc(b.username) : esc(b.id)}</div></td>
      <td>${runPill(b)}</td><td>${pill(b.mode, b.mode === 'ON' ? 'ok' : '')}</td><td>${pill(b.group_start_button, b.group_start_button === 'ON' ? 'acc' : '')}</td>
      <td>${num(b.users.total)}</td><td>${b.groups}</td><td>${b.channels}</td></tr>`).join('')}</tbody></table></div>`;
}

/* ======================================================= bots list */
async function pageBots(v, guard) {
  const d = await api('/api/bots');
  if (!guard()) return;
  v.innerHTML = `<div class="page-head"><div><h1>Bots</h1><div class="sub">Create and configure your Telegram bots</div></div>
    <button class="btn primary right" data-act="add-bot">＋ Add bot</button></div>
    ${d.bots.length ? `<div class="grid g3">${d.bots.map(b => `<div class="card bot-card" data-go="#/bots/${encodeURIComponent(b.id)}">
      <div class="row"><div class="avatar">${initial(b.name)}</div><div><b>${esc(b.name)}</b><div class="muted small">${b.username ? '@' + esc(b.username) : esc(b.id)}</div></div><div class="right">${runPill(b)}</div></div>
      <div class="row small muted"><span>👥 ${num(b.users.total)} users</span><span>🛰 ${b.groups} groups</span><span>📢 ${b.channels} channels</span></div>
      <div class="row">${pill('Mode ' + b.mode, b.mode === 'ON' ? 'ok' : '')}${pill('Group Start ' + b.group_start_button, b.group_start_button === 'ON' ? 'acc' : '')}</div></div>`).join('')}</div>`
      : '<div class="card"><div class="empty">No bots yet.<br><br><button class="btn primary" data-act="add-bot">＋ Add your first bot</button></div></div>'}`;
}

ACT['add-bot'] = () => {
  const m = modal(`<h2>Add a bot</h2><p class="muted">Create the bot with @BotFather first, then paste its token here.</p>
    <div class="stack" id="nb"><label class="f">Bot ID<input type="text" data-f="id" placeholder="mybot" autocomplete="off"><span class="help">Letters, numbers, _ and - only. Cannot be changed later.</span></label>
    <label class="f">Display name<input type="text" data-f="name" placeholder="My Bot"></label>
    <label class="f">Bot token<input type="text" data-f="token" placeholder="123456:ABC-DEF…" autocomplete="off"></label>
    <div class="row"><button class="btn sm" data-act="check-token" data-scope="#nb">Check token</button><span id="tk-res" class="small"></span></div>
    <label class="f">Your Telegram user ID (admin)<input type="text" data-f="admin_id" placeholder="123456789"><span class="help">Message @userinfobot to find it. Only this user gets the admin menu.</span></label></div>
    <div class="actions"><button class="btn" data-act="close-modal">Cancel</button><button class="btn primary" data-act="create-bot">Create bot</button></div>`);
  $('[data-f=id]', m.el).focus();
};
ACT['close-modal'] = () => { $('#modal-root').innerHTML = ''; };
ACT['create-bot'] = async el => {
  const payload = collect($('#nb'));
  await busy(el, async () => {
    const r = await api('/api/bots', {method: 'POST', json: payload});
    $('#modal-root').innerHTML = '';
    toast('Bot created', 'ok');
    location.hash = '#/bots/' + encodeURIComponent(r.id) + '/settings';
  });
};
ACT['check-token'] = async el => {
  const scope = $(el.dataset.scope);
  const f = collect(scope);
  const out = $('#tk-res');
  out.textContent = 'Checking…';
  try {
    const t = f.token || (f.bot && f.bot.token) || '';
    const r = await api('/api/telegram/check', {method: 'POST', json: {token: t, api_server: (f.telegram || {}).api_server || '', api_server_fallback: (f.telegram || {}).api_server_fallback || ''}});
    out.innerHTML = `<span style="color:var(--ok)">✓ @${esc(r.username)} (${esc(r.name)})</span>`;
  } catch (e) { out.innerHTML = `<span style="color:var(--bad)">✗ ${esc(e.message)}</span>`; }
};

/* ======================================================= single bot */
const TABS = [['overview', 'Overview'], ['settings', 'Settings'], ['sources', 'Sources & targets'], ['messages', 'Messages'], ['users', 'Users'], ['broadcast', 'Broadcast'], ['logs', 'Activity']];
let CUR = null;

async function loadBot(id) {
  CUR = await api('/api/bots/' + encodeURIComponent(id));
  return CUR;
}

async function pageBot(v, id, tab, guard) {
  const d = await loadBot(id);
  if (!guard()) return;
  const b = d.bot;
  v.innerHTML = `<div class="page-head"><a class="btn sm" href="#/bots">←</a><div class="avatar">${initial(b.name)}</div>
      <div><h1>${esc(b.name)}</h1><div class="sub">${b.username ? '@' + esc(b.username) + ' · ' : ''}<span class="mono">${esc(b.id)}</span></div></div>
      <div class="right row">${runPill(b)}${b.cron_running ? pill('<span class="spin" style="width:10px;height:10px;border-width:2px"></span> Cron running', 'acc') : ''}
      <button class="btn sm" data-act="run-cron">▶ Run cron</button>
      <label class="switch" title="Enable / disable this bot"><input type="checkbox" id="en-sw" ${b.enabled ? 'checked' : ''}><i></i></label></div></div>
    ${b.error && !b.running && b.enabled ? `<div class="callout bad" style="margin-bottom:16px">⚠ The bot could not start: ${esc(b.error)}</div>` : ''}
    <div class="tabs">${TABS.map(([k, n]) => `<a href="#/bots/${encodeURIComponent(id)}/${k}" class="${k === tab ? 'active' : ''}">${n}</a>`).join('')}</div>
    <div id="tab"></div>`;
  $('#en-sw').addEventListener('change', async e => {
    try { await api('/api/bots/' + encodeURIComponent(id), {method: 'PUT', json: {enabled: e.target.checked}}); toast(e.target.checked ? 'Bot enabled — starting…' : 'Bot disabled', 'ok'); setTimeout(route, 2500); }
    catch (err) { toast(err.message, 'err'); e.target.checked = !e.target.checked; }
  });
  const t = $('#tab');
  const fn = {overview: tabOverview, settings: tabSettings, sources: tabSources, messages: tabMessages, users: tabUsers, broadcast: tabBroadcast, logs: tabLogs}[tab] || tabOverview;
  await fn(t, d, guard);
}

const seg = (key, val, opts) => `<div class="seg">${opts.map(([o, label, cls]) => `<button type="button" class="${val === o ? 'on ' + (cls || '') : ''}" data-act="quick" data-key="${key}" data-val="${o}">${label}</button>`).join('')}</div>`;

async function tabOverview(t, d) {
  const b = d.bot, s = d.stats, tr = d.tracking;
  t.innerHTML = `<div class="grid g5">${statCard('Users', num(s.total))}${statCard('Active', num(s.active), 'not blocked')}${statCard('New today', num(s.new_today), num(s.new_7d) + ' this week')}${statCard('Active 24h', num(s.active_24h), num(s.active_7d) + ' in 7 days')}${statCard('Blocked', num(s.blocked))}</div>
  <div class="grid g2" style="margin-top:16px">
    <div class="card"><h2>Quick controls</h2><div class="desc">Changes apply immediately.</div>
      <div class="stack">
        <div><div class="small muted" style="margin-bottom:6px"><b>Group “🚀 Start” button</b> — shown in your groups so members can register</div>${seg('settings.group_start_button', b.settings.group_start_button, [['ON', 'ON', 'good'], ['OFF', 'OFF', 'bad']])}</div>
        <div class="row"><button class="btn sm" data-act="start-post" ${b.settings.group_start_button === 'OFF' ? 'disabled' : ''}>📌 Post button in all groups</button><button class="btn sm" data-act="start-clear">🧹 Remove from all groups</button></div>
        <div><div class="small muted" style="margin-bottom:6px"><b>Mode</b> — ON: video to groups &amp; channels · OFF: video to groups, image to channels</div>${seg('settings.mode', b.settings.mode, [['ON', 'ON', 'good'], ['OFF', 'OFF', 'bad']])}</div>
      </div></div>
    <div class="card"><h2>Cron position</h2><div class="desc">Where the video pipeline will continue.</div>
      <dl class="kv"><dt>Old API</dt><dd>page <b>${tr.old_page}</b> · last <code>${esc(tr.old_last_slug || '—')}</code></dd>
      <dt>VID65 API</dt><dd>page <b>${tr.vid65_page}</b> · index <b>${tr.vid65_index}</b></dd>
      <dt>Targets</dt><dd>${d.groups_total} group(s) · ${new Set([...b.apis.old.channels, ...b.apis.vid65.channels]).size} channel(s)</dd>
      <dt>Cron URL</dt><dd class="small"><code>${esc(location.origin)}/run_cron?bot=${esc(b.id)}&amp;secret=${esc(b.cron_secret)}&amp;source=both</code></dd></dl></div></div>
  <div class="card" style="margin-top:16px"><h2>New users — last 14 days</h2><div class="desc">&nbsp;</div>${chart(d.daily)}</div>
  <div class="card" style="margin-top:16px"><h2>Recent broadcasts</h2>
    ${d.broadcasts.length ? `<div class="table-wrap" style="margin-top:12px"><table><thead><tr><th>When</th><th>Total</th><th>Sent</th><th>Blocked</th><th>Failed</th></tr></thead><tbody>${d.broadcasts.map(x => `<tr><td>${when(x.created_at)}</td><td>${x.total}</td><td>${x.sent}</td><td>${x.blocked}</td><td>${x.failed}</td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No broadcasts yet</div>'}</div>`;
}

ACT.quick = async el => {
  const key = el.dataset.key.split('.'), json = {};
  json[key[0]] = {[key[1]]: el.dataset.val};
  await busy(el, async () => {
    const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id), {method: 'PUT', json});
    toast(r.sweep ? `Saved. Start button removed from ${r.sweep.cleared} group(s)` + (r.sweep.failed ? `, ${r.sweep.failed} failed` : '') : 'Saved', 'ok');
    route();
  });
};
ACT['start-post'] = async el => { await busy(el, async () => { const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/start-button', {method: 'POST', json: {action: 'post'}}); toast(`Posted in ${r.done} group(s)` + (r.failed ? `, ${r.failed} failed` : ''), r.failed ? 'err' : 'ok'); }); };
ACT['start-clear'] = async el => { await busy(el, async () => { const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/start-button', {method: 'POST', json: {action: 'clear'}}); toast(`Removed from ${r.done} group(s)` + (r.failed ? `, ${r.failed} failed` : ''), r.failed ? 'err' : 'ok'); }); };

ACT['run-cron'] = () => {
  const m = modal(`<h2>Run cron now</h2><p class="muted">Sends the next video from the chosen source. Progress is posted in the admin's Telegram chat.</p>
    <div class="stack"><button class="btn" data-act="cron-go" data-src="old">1️⃣ Old API</button><button class="btn" data-act="cron-go" data-src="vid65">2️⃣ VID65 API</button><button class="btn primary" data-act="cron-go" data-src="both">▶ Run both</button></div>
    <div class="actions"><button class="btn" data-act="close-modal">Close</button></div>`);
  return m;
};
ACT['cron-go'] = async el => {
  await busy(el, async () => {
    await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/cron', {method: 'POST', json: {source: el.dataset.src}});
    $('#modal-root').innerHTML = '';
    toast('Cron started — watch your Telegram chat', 'ok');
    setTimeout(route, 1500);
  });
};

/* ---- settings tab */
async function tabSettings(t, d) {
  const b = d.bot;
  t.innerHTML = `<div id="sf" class="stack">
    <div class="card"><h2>General</h2><div class="desc">Identity and access.</div><div class="form-grid">
      <label class="f">Display name<input type="text" data-f="name" value="${esc(b.name)}"></label>
      <label class="f">Admin Telegram ID<input type="text" data-f="admin_id" value="${esc(b.admin_id)}"><span class="help">Only this user sees the admin menu and receives cron progress.</span></label>
      <label class="f full">Bot token<input type="text" data-f="token" value="${esc(b.token)}" autocomplete="off"></label>
      <div class="full row"><button class="btn sm" data-act="check-token" data-scope="#sf">Check token</button><span id="tk-res" class="small"></span></div>
      <label class="f full">Cron secret<input type="text" data-f="cron_secret" value="${esc(b.cron_secret)}"><span class="help">Protects the external cron link shown on the Overview tab.</span></label></div></div>
    <div class="card"><h2>Behaviour</h2><div class="desc">How videos are posted.</div><div class="form-grid">
      <label class="f">Mode<select data-f="settings.mode"><option value="ON" ${b.settings.mode === 'ON' ? 'selected' : ''}>ON — video to groups and channels</option><option value="OFF" ${b.settings.mode === 'OFF' ? 'selected' : ''}>OFF — video to groups, image to channels</option></select></label>
      <label class="f">Group “🚀 Start” button<select data-f="settings.group_start_button"><option value="ON" ${b.settings.group_start_button !== 'OFF' ? 'selected' : ''}>ON — show in groups</option><option value="OFF" ${b.settings.group_start_button === 'OFF' ? 'selected' : ''}>OFF — hide from groups</option></select></label>
      <label class="f">Posts to keep per channel<input type="number" min="1" max="500" data-f="settings.max_channel_messages" value="${esc(b.settings.max_channel_messages)}"><span class="help">Older bot posts are deleted from the channel.</span></label>
      <label class="f">Channel preview blur (%)<input type="number" min="0" max="100" data-f="settings.blur_percentage" value="${esc(b.settings.blur_percentage)}"></label>
      <label class="f">Max video size (MB)<input type="number" min="1" max="4096" data-f="settings.max_size_mb" value="${Math.round((b.settings.max_size || 2147483648) / 1048576)}"><span class="help">Bigger files fall back to image + link.</span></label></div></div>
    <div class="card"><h2>Telegram API servers</h2><div class="desc">Leave the first empty to use the official server. Use a local Bot API server for files over 50 MB.</div><div class="form-grid">
      <label class="f">Primary server<input type="text" data-f="telegram.api_server" value="${esc(b.telegram.api_server)}" placeholder="https://your-bot-api.example.com"></label>
      <label class="f">Fallback server<input type="text" data-f="telegram.api_server_fallback" value="${esc(b.telegram.api_server_fallback)}" placeholder="https://api.telegram.org"></label></div></div>
    <div class="row"><button class="btn primary" data-act="save-settings">💾 Save settings</button></div>
    <div class="card" style="border-color:var(--bad)"><h2 style="color:var(--bad)">Danger zone</h2><div class="desc">Deleting a bot removes all of its users, history and logs from Firebase. Make a backup first.</div>
      <div class="row"><button class="btn danger" data-act="delete-bot">🗑 Delete this bot</button><a class="btn" href="#/backups">💾 Go to backups</a></div></div></div>`;
}

ACT['save-settings'] = async el => {
  const payload = collect($('#sf'));
  await busy(el, async () => {
    const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id), {method: 'PUT', json: payload});
    toast(r.sweep ? `Saved. Start button removed from ${r.sweep.cleared} group(s)` : 'Settings saved', 'ok');
    setTimeout(route, 600);
  });
};
ACT['delete-bot'] = async () => {
  if (!await confirmBox({title: 'Delete this bot?', text: 'All users, history and logs of <b>' + esc(CUR.bot.name) + '</b> will be permanently erased from Firebase.', ok: 'Delete forever', danger: true, type: CUR.bot.id})) return;
  try { await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '?confirm=' + encodeURIComponent(CUR.bot.id), {method: 'DELETE'}); toast('Bot deleted', 'ok'); location.hash = '#/bots'; }
  catch (e) { toast(e.message, 'err'); }
};

/* ---- sources tab */
async function tabSources(t, d) {
  const b = d.bot, tr = d.tracking;
  const src = (k, label, extra) => `<div class="card"><h2>${label}</h2><div class="desc">Where videos come from and where they are sent.</div><div class="stack">
    <label class="f">API URL<input type="text" data-f="apis.${k}.url" value="${esc(b.apis[k].url)}" placeholder="https://…"></label>
    <div class="form-grid"><label class="f">Target groups<textarea class="mono" data-t="lines" data-f="apis.${k}.groups" placeholder="-1001234567890">${esc(b.apis[k].groups.join('\n'))}</textarea><span class="help">One chat ID per line. Groups are added automatically when the bot joins.</span></label>
    <label class="f">Target channels<textarea class="mono" data-t="lines" data-f="apis.${k}.channels" placeholder="@mychannel or -100123…">${esc(b.apis[k].channels.join('\n'))}</textarea><span class="help">One per line. The bot must be an admin there.</span></label></div>${extra}</div></div>`;
  t.innerHTML = `<div id="sf" class="stack">
    ${src('old', '1️⃣ Old API', '')}${src('vid65', '2️⃣ VID65 API', '')}
    <div class="row"><button class="btn primary" data-act="save-settings">💾 Save sources</button></div></div>
    <div class="card" style="margin-top:16px" id="trk"><h2>Cron position</h2><div class="desc">Change where the pipeline continues. Normally you don't need to touch this.</div>
      <div class="form-grid"><label class="f">Old API — page<input type="number" min="1" data-f="old_page" value="${tr.old_page}"></label>
      <label class="f">Old API — last sent slug<input type="text" data-f="old_last_slug" value="${esc(tr.old_last_slug)}"></label>
      <label class="f">VID65 — page<input type="number" min="1" data-f="vid65_page" value="${tr.vid65_page}"></label>
      <label class="f">VID65 — index on page<input type="number" min="0" data-f="vid65_index" value="${tr.vid65_index}"></label></div>
      <div class="row" style="margin-top:14px"><button class="btn" data-act="save-tracking">Save position</button><button class="btn danger" data-act="reset-sent">Forget sent videos</button></div>
      <div class="help small muted" style="margin-top:8px">“Forget sent videos” clears the duplicate protection list, so old videos can be sent again.</div></div>`;
}
ACT['save-tracking'] = async el => {
  const p = collect($('#trk'));
  await busy(el, async () => { await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/tracking', {method: 'PUT', json: p}); toast('Position saved', 'ok'); });
};
ACT['reset-sent'] = async el => {
  if (!await confirmBox({title: 'Forget sent videos?', text: 'The duplicate-protection list is cleared. Videos that were already posted may be posted again.', ok: 'Clear list', danger: true})) return;
  await busy(el, async () => { const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/reset-sent', {method: 'POST', json: {}}); toast(r.deleted + ' entries removed', 'ok'); });
};

/* ---- messages tab */
async function tabMessages(t, d) {
  const m = d.bot.messages || {};
  const f = (k, label, help) => `<label class="f">${label}<textarea data-f="messages.${k}">${esc(m[k] || '')}</textarea><span class="help">${help}</span></label>`;
  t.innerHTML = `<div id="sf" class="stack"><div class="card"><h2>Texts</h2><div class="desc">HTML is allowed: &lt;b&gt;bold&lt;/b&gt;, &lt;i&gt;italic&lt;/i&gt;, &lt;a href=""&gt;link&lt;/a&gt;.</div><div class="stack">
    ${f('start', 'Reply to “🚀 Start” / /start in private chat', 'Use <code>{first_name}</code> for the user\'s name.')}
    ${f('welcome_dm', 'Welcome DM when someone joins a group', 'Sent to the new member if they have opened the bot before. <code>{first_name}</code> works here too.')}
    ${f('group_caption', 'Video caption in groups', '<code>{title}</code> is replaced with the video title.')}
    ${f('channel_caption', 'Caption in channels', '<code>{title}</code> is replaced with the video title.')}</div></div>
    <div class="row"><button class="btn primary" data-act="save-settings">💾 Save messages</button></div></div>`;
}

/* ---- users tab */
const U = {after: null, q: '', status: '', rows: 0};
async function tabUsers(t, d) {
  U.after = null; U.rows = 0;
  t.innerHTML = `<div class="card"><div class="row" style="margin-bottom:14px">
    <input type="search" id="u-q" placeholder="Search @username or user ID…" style="max-width:300px" value="${esc(U.q)}">
    <select id="u-st" style="max-width:160px"><option value="">All users</option><option value="active">Active</option><option value="blocked">Blocked</option></select>
    <a class="btn right" href="/api/bots/${encodeURIComponent(d.bot.id)}/users.csv">⬇ Export CSV</a></div>
    <div class="table-wrap"><table><thead><tr><th>User</th><th>ID</th><th>Status</th><th>Source</th><th>Joined</th><th>Last seen</th></tr></thead><tbody id="u-body"></tbody></table></div>
    <div class="row" style="margin-top:14px;justify-content:center"><button class="btn" id="u-more" data-act="users-more">Load more</button></div></div>`;
  $('#u-st').value = U.status;
  let timer;
  $('#u-q').addEventListener('input', e => { clearTimeout(timer); timer = setTimeout(() => { U.q = e.target.value; usersReload(); }, 350); });
  $('#u-st').addEventListener('change', e => { U.status = e.target.value; usersReload(); });
  await usersLoad(true);
}
async function usersReload() { if (!$('#u-body')) return; U.after = null; U.rows = 0; $('#u-body').innerHTML = ''; await usersLoad(true); }
async function usersLoad(first) {
  const qs = new URLSearchParams({limit: 50, q: U.q, status: U.status});
  if (U.after) qs.set('after', U.after);
  try {
    const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/users?' + qs);
    if (!$('#u-body')) return; // user navigated away meanwhile
    $('#u-body').insertAdjacentHTML('beforeend', r.users.map(u => `<tr><td><b>${esc(u.first_name || '—')}</b>${u.username ? '<div class="muted small">@' + esc(u.username) + '</div>' : ''}</td>
      <td class="mono">${esc(u.id)}</td><td>${u.status === 'blocked' ? pill('Blocked', 'bad') : pill('Active', 'ok')}</td><td class="small muted">${esc(u.source || '—')}</td><td class="small">${when(u.joined_at)}</td><td class="small muted">${ago(u.last_seen)}</td></tr>`).join(''));
    U.rows += r.users.length; U.after = r.next;
    if (first && !U.rows) $('#u-body').innerHTML = '<tr><td colspan="6" class="empty">No users found</td></tr>';
    $('#u-more').classList.toggle('hide', !r.next);
  } catch (e) { toast(e.message, 'err'); }
}
ACT['users-more'] = async el => { await busy(el, () => usersLoad(false)); };

/* ---- broadcast tab */
async function tabBroadcast(t, d) {
  t.innerHTML = `<div class="card"><h2>Broadcast a message</h2><div class="desc">Sends to all <b>${num(d.stats.active)}</b> active users of this bot. HTML formatting is allowed.</div>
    <div class="stack"><textarea id="bc-text" style="min-height:150px" placeholder="Write your message…"></textarea>
    <div class="row"><button class="btn primary" data-act="bc-send">📢 Send broadcast</button><span class="muted small">Users who blocked the bot are skipped and marked as blocked.</span></div>
    <div id="bc-prog" class="hide"><div class="progress"><div id="bc-bar" style="width:0%"></div></div><div class="small muted" id="bc-txt" style="margin-top:8px"></div></div></div></div>`;
}
ACT['bc-send'] = async el => {
  const text = $('#bc-text').value.trim();
  if (!text) return toast('Write the message first', 'err');
  if (!await confirmBox({title: 'Send to all users?', text: 'This sends the message to <b>' + num(CUR.stats.active) + '</b> users and cannot be undone.', ok: 'Send now'})) return;
  await busy(el, async () => {
    const r = await api('/api/bots/' + encodeURIComponent(CUR.bot.id) + '/broadcast', {method: 'POST', json: {text}});
    $('#bc-prog').classList.remove('hide');
    const tick = async () => {
      const j = (await api('/api/jobs/' + r.job)).job;
      if (!$('#bc-bar')) return; // navigated away
      const done = j.sent + j.blocked + j.failed;
      $('#bc-bar').style.width = (j.total ? Math.round(done * 100 / j.total) : 0) + '%';
      $('#bc-txt').textContent = `${j.state === 'done' ? '✅ Finished' : 'Sending…'} — ${j.sent} sent, ${j.blocked} blocked, ${j.failed} failed of ${j.total}`;
      if (j.state !== 'done' && $('#bc-bar')) setTimeout(() => tick().catch(() => {}), 1000);
    };
    await tick();
  });
};

/* ---- logs tab */
async function tabLogs(t, d) {
  const r = await api('/api/bots/' + encodeURIComponent(d.bot.id) + '/logs');
  t.innerHTML = `<div class="card"><h2>Activity</h2><div class="desc">Important events for this bot (kept 30 days).</div>
    ${r.logs.length ? r.logs.map(l => `<div class="log"><time>${when(l.created_at)}</time><span>${l.level === 'warn' ? '⚠️ ' : ''}${esc(l.text)}</span></div>`).join('') : '<div class="empty">Nothing yet</div>'}</div>`;
}

/* ======================================================= backups */
async function pageBackups(v, guard) {
  const d = await api('/api/backups');
  if (!guard()) return;
  v.innerHTML = `<div class="page-head"><div><h1>Backups</h1><div class="sub">Keep a safe copy of all your Firebase data</div></div></div>
    <div class="grid g2">
    <div class="card"><h2>💾 Back up everything</h2><div class="desc">Bots, settings, users, groups, history and logs — all in one file.</div>
      <label class="row" style="margin-bottom:14px;cursor:pointer"><span class="switch"><input type="checkbox" id="bk-sec" checked><i></i></span><span>Include bot tokens &amp; secrets <span class="muted small">(needed for a full restore)</span></span></label>
      <div class="row"><button class="btn primary" data-act="bk-download">⬇ Create backup &amp; download</button><button class="btn" data-act="bk-server">Save on server only</button></div>
      <div class="small muted" style="margin-top:12px">${d.auto_hours > 0 ? `Automatic backup every <b>${d.auto_hours} h</b>, newest <b>${d.keep}</b> kept.` : 'Automatic backups are off.'}</div></div>
    <div class="card"><h2>♻️ Restore from a file</h2><div class="desc">Upload a backup file. Data in the file overwrites the same items in Firebase; other data is kept. A safety backup is made first.</div>
      <input type="file" id="bk-file" accept=".json,.gz,application/json" style="margin-bottom:12px"><div><button class="btn" data-act="bk-restore-file">♻️ Restore</button></div></div></div>
    <div class="card" style="margin-top:16px"><h2>Saved on the server</h2><div class="desc">Stored in the app's <code>backups</code> folder.</div>
    ${d.backups.length ? `<div class="table-wrap"><table><thead><tr><th>File</th><th>Type</th><th>Size</th><th>Created</th><th></th></tr></thead><tbody>
      ${d.backups.map(b => `<tr><td class="mono">${esc(b.name)}</td><td>${pill(b.kind, b.kind === 'auto' ? 'acc' : b.kind === 'pre' ? 'warn' : '')}</td><td>${bytes(b.size)}</td><td>${when(b.modified)}</td>
        <td class="row" style="justify-content:flex-end"><a class="btn sm" href="/api/backups/${encodeURIComponent(b.name)}">⬇ Download</a><button class="btn sm" data-act="bk-restore" data-name="${esc(b.name)}">♻️ Restore</button><button class="btn sm danger" data-act="bk-delete" data-name="${esc(b.name)}">🗑</button></td></tr>`).join('')}</tbody></table></div>` : '<div class="empty">No backups yet — create your first one above.</div>'}</div>
    <div class="callout" style="margin-top:16px">🔒 Backups with secrets contain your bot tokens. Keep the files private.</div>`;
}
ACT['bk-download'] = async el => {
  await busy(el, async () => {
    const r = await api('/api/backups', {method: 'POST', json: {include_secrets: $('#bk-sec').checked, gz: false}});
    toast(`Backup ready: ${r.counts.bots} bot(s), ${r.counts.users || 0} users (${bytes(r.size)})`, 'ok');
    window.location.href = '/api/backups/' + encodeURIComponent(r.name);
    setTimeout(route, 1200);
  });
};
ACT['bk-server'] = async el => {
  await busy(el, async () => {
    const r = await api('/api/backups', {method: 'POST', json: {include_secrets: $('#bk-sec').checked, gz: true}});
    toast('Saved on server: ' + r.name, 'ok'); route();
  });
};
ACT['bk-delete'] = async el => {
  if (!await confirmBox({title: 'Delete this backup?', text: '<code>' + esc(el.dataset.name) + '</code> will be removed from the server.', ok: 'Delete', danger: true})) return;
  await busy(el, async () => { await api('/api/backups/' + encodeURIComponent(el.dataset.name), {method: 'DELETE'}); toast('Deleted', 'ok'); route(); });
};
async function doRestore(el, call, label) {
  if (!await confirmBox({title: 'Restore data?', text: 'Restoring <b>' + esc(label) + '</b> will overwrite matching bots, users and settings in Firebase. A safety backup is created first.', ok: 'Restore', danger: true})) return;
  await busy(el, async () => {
    const r = await call();
    toast(`Restored ${r.restored.bots} bot(s), ${r.restored.users || 0} users. Safety copy: ${r.safety_backup}`, 'ok');
    route();
  });
}
ACT['bk-restore'] = el => doRestore(el, () => api('/api/restore', {method: 'POST', json: {name: el.dataset.name}}), el.dataset.name);
ACT['bk-restore-file'] = el => {
  const f = $('#bk-file').files[0];
  if (!f) return toast('Choose a backup file first', 'err');
  return doRestore(el, () => { const fd = new FormData(); fd.append('file', f); return api('/api/restore', {method: 'POST', form: fd}); }, f.name);
};

/* ======================================================= system */
async function pageSystem(v, guard) {
  const s = await api('/api/system');
  if (!guard()) return;
  v.innerHTML = `<div class="page-head"><div><h1>System</h1><div class="sub">Health of the app and its connections</div></div><button class="btn right" data-act="refresh">↻ Refresh</button></div>
    <div class="grid g3">${statCard('Firebase', s.firestore.ok ? '✓ Connected' : '✗ Error', s.firestore.ok ? s.firestore.latency_ms + ' ms · ' + s.firestore.project : s.firestore.error)}
    ${statCard('Uptime', uptime(s.uptime), 'since last start')}${statCard('Bots running', s.running.length, s.running.join(', ') || '—')}</div>
    <div class="card" style="margin-top:16px"><dl class="kv"><dt>App version</dt><dd>${esc(s.version)}</dd><dt>Python</dt><dd>${esc(s.python)}</dd><dt>Timezone</dt><dd>${esc(s.timezone)}</dd>
      <dt>Auto backup</dt><dd>${s.auto_backup_hours > 0 ? 'every ' + s.auto_backup_hours + ' h' : 'off'}</dd><dt>Last backup</dt><dd>${s.last_backup ? esc(s.last_backup.name) + ' · ' + ago(s.last_backup.modified) : 'none yet'}</dd></dl></div>`;
}

/* ======================================================= global events */
ACT.refresh = () => route();
ACT.logout = async () => { try { await api('/api/logout', {method: 'POST', json: {}}); } catch (e) { /* ignore */ } showLogin(); };
ACT.theme = () => {
  const cur = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
  document.documentElement.dataset.theme = cur;
  try { localStorage.setItem('theme', cur); } catch (e) { /* ignore */ }
};

document.addEventListener('click', e => {
  const a = e.target.closest('[data-act]');
  if (a && ACT[a.dataset.act]) { e.preventDefault(); ACT[a.dataset.act](a, e); return; }
  const g = e.target.closest('[data-go]');
  if (g) location.hash = g.dataset.go;
});
window.addEventListener('hashchange', route);

async function boot() {
  try {
    await api('/api/me');
  } catch (e) { return showLogin(); }
  showShell();
  route();
}

(function initTheme() {
  let t = null;
  try { t = localStorage.getItem('theme'); } catch (e) { /* ignore */ }
  if (!t) t = window.matchMedia && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  document.documentElement.dataset.theme = t;
})();

window.__tg = {boot, route};
boot();
})();
