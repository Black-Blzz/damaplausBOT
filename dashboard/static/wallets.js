/* Fleet Wallets — one row per account, read from the site itself. */
'use strict';

const $ = (id) => document.getElementById(id);
const POLL_MS = 5000;
const LOW_FLOOR = 10;

let greeted = '';

const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const GAME_NAMES = {
  'dama-tankegna': 'Dama Tankegna',
  'dama-egregna': 'Dama Egregna',
  'xo': 'XO',
  'chess': 'Chess',
};

function ago(stamp) {
  if (!stamp) return 'never';
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - stamp));
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

const money = (value) => (value === null || value === undefined ? '—' : Number(value).toLocaleString());

let toastTimer;
function toast(message, kind = '') {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast ${kind}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 5000);
}

async function post(path, body) {
  const response = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  const data = await response.json().catch(() => ({}));
  if (data.login_required) { window.location.replace('/login'); throw new Error('Signed out.'); }
  if (!response.ok || data.error) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

function greet(name) {
  if (!name || name === greeted) return;
  greeted = name;
  $('greeting').textContent = `${name}'s wallets`;
  document.title = `Fleet Wallets · ${name}`;
}

function render(payload) {
  const rows = payload.wallets || [];
  const summary = payload.summary || {};

  $('statTotal').textContent = money(summary.total);
  $('statAccounts').textContent = summary.accounts || 0;
  $('statSignedOut').textContent = summary.signed_out || 0;
  $('statLow').textContent = rows.filter(
    (r) => r.reachable && r.balance !== null && r.balance < LOW_FLOOR).length;
  $('statChecked').textContent = ago(summary.checked_at);

  const feed = $('feed');
  if (!rows.length) { feed.className = 'feed'; $('feedLabel').textContent = 'no accounts'; }
  else if (summary.reachable === 0) { feed.className = 'feed down'; $('feedLabel').textContent = 'none reachable'; }
  else if (summary.signed_out) { feed.className = 'feed stale'; $('feedLabel').textContent = `${summary.signed_out} signed out`; }
  else { feed.className = 'feed live'; $('feedLabel').textContent = 'all reachable'; }

  $('empty').hidden = rows.length > 0;
  $('wallets').tBodies[0].innerHTML = rows.map((row) => {
    let session;
    if (row.reachable) session = '<span class="pill pill-go"><span>Live</span></span>';
    else if (row.signed_out) session = '<span class="pill pill-fault"><span>Signed out</span></span>';
    else session = `<span class="pill pill-hold" title="${esc(row.error)}"><span>Unreachable</span></span>`;

    const low = row.reachable && row.balance !== null && row.balance < LOW_FLOOR;
    return `<tr>
      <td class="name">${esc(row.account_id)}</td>
      <td>${esc(GAME_NAMES[row.game] || row.game)}</td>
      <td>${row.display_name ? esc(row.display_name) : '<span class="dim">—</span>'}
          ${row.phone_tail ? `<span class="dim mono"> ····${esc(row.phone_tail)}</span>` : ''}</td>
      <td class="num ${low ? 'money-low' : ''}">${money(row.balance)}</td>
      <td class="num dim">${money(row.bonus)}</td>
      <td>${session}</td>
      <td class="num dim">${ago(row.checked_at)}</td>
    </tr>`;
  }).join('');
}

async function refresh() {
  try {
    const response = await fetch('/api/wallets');
    if (response.status === 401) { window.location.replace('/login'); return; }
    const payload = await response.json();
    greet(payload.user);
    render(payload);
  } catch {
    $('feed').className = 'feed down';
    $('feedLabel').textContent = 'console offline';
  }
}

$('refresh').addEventListener('click', async () => {
  const button = $('refresh');
  button.disabled = true;
  button.textContent = 'Checking…';
  try {
    toast((await post('/api/wallets/refresh', {})).message, 'ok');
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    button.disabled = false;
    button.textContent = 'Re-check now';
    refresh();
  }
});

$('signOut').addEventListener('click', async () => {
  try { await post('/api/logout', {}); } catch { /* leaving anyway */ }
  window.location.replace('/login');
});

refresh();
setInterval(refresh, POLL_MS);
