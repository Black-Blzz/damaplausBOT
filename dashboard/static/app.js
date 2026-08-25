/* Fleet Console — polls /api/state and drives every control on the page. */
'use strict';

const $ = (id) => document.getElementById(id);
const POLL_MS = 1500;

let state = null;
let gamesReady = false;
let stakesKey = '';
let lastLogCount = 0;
let pickerSignature = '';
let greeted = '';

/* ── helpers ─────────────────────────────────────────────────── */

const esc = (value) => String(value ?? '').replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function duration(seconds) {
  const s = Math.max(0, Math.floor(seconds || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

/* Mirrors slug_account_id on the server, so status lookups use the same key. */
const slugAccount = (raw) =>
  String(raw || '').trim().replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^[-._]+|[-._]+$/g, '').slice(0, 60);

const GAME_NAMES = {
  'dama-tankegna': 'Dama Tankegna',
  'dama-egregna': 'Dama Egregna',
  'xo': 'XO',
  'chess': 'Chess',
};
const gameName = (key) => GAME_NAMES[key] || key;

let toastTimer;
function toast(message, kind = '') {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast ${kind}`;
  node.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { node.hidden = true; }, 6000);
}

/* Time-of-day greeting, so the console addresses whoever is signed in. */
function greet(name) {
  if (name === greeted) return;
  greeted = name;
  const hour = new Date().getHours();
  const part = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  $('greeting').textContent = `${part}, ${name}`;
  $('greetSub').textContent = 'Fleet Console · damaplus.online';
  document.title = `Fleet Console · ${name}`;
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

/* How a bot's reported state should read to an operator. */
const STATES = {
  starting:          ['pill-idle',  'Starting'],
  authenticating:    ['pill-idle',  'Signing in'],
  idle:              ['pill-idle',  'Idle'],
  waiting_permit:    ['pill-hold',  'Holding'],
  waiting_turn:      ['pill-hold',  'Waiting its turn'],
  queued:            ['pill-live',  'In queue'],
  matched:           ['pill-go',    'Paired'],
  playing:           ['pill-go',    'Playing'],
  finished:          ['pill-live',  'Game over'],
  stake_unavailable: ['pill-hold',  'No such stake'],
  session_invalid:   ['pill-fault', 'Signed out'],
  error:             ['pill-fault', 'Fault'],
  stopped:           ['pill-idle',  'Stopped'],
  low_balance:       ['pill-fault', 'Out of funds'],
};

function pill(stateKey) {
  const [cls, label] = STATES[stateKey] || ['pill-idle', stateKey || 'unknown'];
  return `<span class="pill ${cls}"><span>${esc(label)}</span></span>`;
}

/* ── rendering ───────────────────────────────────────────────── */

function fillGameSelects(variants) {
  if (gamesReady || !variants.length) return;
  const options = variants
    .map((v) => `<option value="${esc(v.id)}">${esc(v.name)}</option>`).join('');
  $('fGame').innerHTML = options;
  $('aGame').innerHTML = options;
  const remembered = state?.settings?.last_variant;
  if (remembered) { $('fGame').value = remembered; $('aGame').value = remembered; }
  $('aName').placeholder = `${$('aGame').value}-bot-2`;
  gamesReady = true;
}

function fillStakes(stakes) {
  const key = stakes.join(',');
  if (key === stakesKey) return;
  stakesKey = key;
  const previous = $('fStake').value;
  $('fStake').innerHTML = stakes.map((s) => `<option value="${s}">${s} birr</option>`).join('');
  const remembered = String(state?.settings?.last_stake || '');
  const wanted = [previous, remembered].find((v) => stakes.includes(Number(v)));
  if (wanted) $('fStake').value = wanted;
}

function renderStrip(totals, lobby) {
  $('statAlive').textContent = totals.alive;
  $('statCap').textContent = `/ ${totals.max_bots}`;
  $('statPlaying').textContent = totals.playing;
  $('statMatches').textContent = totals.matches;
  $('statRecord').textContent = `${totals.wins} / ${totals.losses} / ${totals.draws}`
    + (totals.unknown ? ` (+${totals.unknown}?)` : '');
  $('statWallet').textContent = totals.wallet === null ? '—' : totals.wallet;
  $('statStakes').textContent = lobby.stake_options.length ? lobby.stake_options.join(' · ') : '—';
  $('statUptime').textContent = duration(totals.uptime_seconds);

  const feed = $('feed');
  if (lobby.error) { feed.className = 'feed down'; $('feedLabel').textContent = 'site unreachable'; }
  else if (lobby.stale) { feed.className = 'feed stale'; $('feedLabel').textContent = 'counts stale'; }
  else { feed.className = 'feed live'; $('feedLabel').textContent = `live · ${lobby.age_seconds ?? 0}s ago`; }
}

function renderAlerts(warnings) {
  const box = $('alerts');
  box.hidden = !warnings.length;
  box.innerHTML = warnings
    .map((w) => `<p class="${w.startsWith('SELF-PAIR') ? 'critical' : ''}">${esc(w)}</p>`)
    .join('');
}

function renderTables(rows) {
  $('tables').tBodies[0].innerHTML = rows.map((row) => {
    const ours = row.ours_queued + row.ours_playing;
    let entry;
    if (row.paused) {
      entry = `<span class="pill pill-fault"><span>Paused</span></span>`
        + ` <button class="btn btn-tiny" type="button" data-resume="${esc(row.key)}">Resume</button>`;
    } else if (row.holder) entry = `<span class="pill pill-live"><span>Ours entering</span></span>`;
    else if (row.enterable) entry = `<span class="pill pill-go"><span>Open</span></span>`;
    else if (row.humans === 0) entry = `<span class="pill pill-idle"><span>Empty</span></span>`;
    else entry = `<span class="pill pill-hold"><span>Even — hold</span></span>`;
    return `<tr>
      <td>${esc(gameName(row.game))}</td>
      <td class="num">${row.stake}</td>
      <td class="num">${row.online}</td>
      <td class="num ${ours ? '' : 'dim'}">${ours}</td>
      <td class="num">${row.humans}</td>
      <td>${entry}</td>
    </tr>`;
  }).join('');
}

function renderFleet(bots) {
  const body = $('fleet').tBodies[0];
  $('fleetEmpty').hidden = bots.length > 0;
  body.innerHTML = bots.map((bot) => {
    const t = bot.telemetry || {};
    const detail = t.opponent ? `vs ${t.opponent}` : (t.detail || bot.last_line || '');
    const record = `${t.wins || 0} / ${t.losses || 0} / ${t.draws || 0}`;
    const money = t.balance === null || t.balance === undefined
      ? '<span class="dim">—</span>'
      : `<span class="${t.balance < (state?.settings?.min_balance ?? 10) ? 'money-low' : ''}">${t.balance}</span>`;
    return `<tr>
      <td class="name">${esc(bot.account_id)}</td>
      <td>${esc(gameName(bot.game))}</td>
      <td class="num">${bot.stake}</td>
      <td class="doing">${pill(bot.state)}${detail ? `<b>${esc(detail)}</b>` : ''}</td>
      <td class="num">${money}</td>
      <td class="num">${t.moves || 0}</td>
      <td class="num">${record}</td>
      <td class="num">${duration(bot.uptime_seconds)}</td>
      <td class="num"><button class="btn btn-tiny" data-stop="${esc(bot.bot_id)}" type="button">Stop</button></td>
    </tr>`;
  }).join('');
}

function renderLog(entries) {
  if (entries.length === lastLogCount) return;
  lastLogCount = entries.length;
  const box = $('log');
  const atBottom = box.scrollTop + box.clientHeight >= box.scrollHeight - 40;
  box.innerHTML = entries.map((entry) => {
    const text = entry.message.toLowerCase();
    const kind = /fail|error|unusable|fault|exited/.test(text) ? 'bad'
               : /stale|not offered|expired|rate|warn/.test(text) ? 'warn' : '';
    return `<div><time>${esc(entry.at)}</time><span class="m ${kind}">${esc(entry.message)}</span></div>`;
  }).join('');
  if ($('follow').checked && atBottom) box.scrollTop = box.scrollHeight;
}

function renderAccounts(sessions, variants) {
  const items = [];
  for (const variant of variants) {
    for (const account of sessions[variant.id] || []) {
      const cls = account.status === 'active' ? 'pill-go'
                : account.status === 'missing' ? 'pill-idle' : 'pill-fault';
      const money = account.balance === null || account.balance === undefined
        ? '' : `${Number(account.balance).toLocaleString()} birr`;
      const low = account.balance !== null && account.balance !== undefined
        && account.balance < (state?.settings?.min_balance ?? 10);
      items.push(`<li>
        <span class="acct">
          <b>${esc(account.account_id)}</b>
          <em>${esc(variant.name)}${money ? ` · <span class="mono ${low ? 'money-low' : ''}">${esc(money)}</span>` : ''}</em>
        </span>
        <span class="pill ${cls}"><span>${esc(account.status_label)}</span></span>
        <button class="btn btn-tiny" type="button"
                data-del-variant="${esc(variant.id)}" data-del-account="${esc(account.account_id)}"
                title="Delete this saved sign-in">Delete</button>
      </li>`);
    }
  }
  $('accounts').innerHTML = items.join('');
  $('accountCount').textContent = items.length;
}

/* One row per saved account, so the operator picks exactly who plays. */
function renderPicker() {
  if (!state) return;
  const variant = $('fGame').value;
  const busy = new Set(state.bots.filter((b) => b.alive && b.variant === variant)
                                 .map((b) => b.account_id));
  const accounts = state.sessions[variant] || [];
  const chosen = pickedAccounts();

  // Rebuilding on every poll would fight the operator's clicks, so only redraw
  // when the set of accounts or their availability actually changes.
  const signature = variant + '|' + accounts
    .map((a) => `${a.account_id}:${a.usable}:${busy.has(a.account_id)}`).join(',');
  if (signature === pickerSignature) { updatePickedCount(); return; }
  pickerSignature = signature;

  $('fAccounts').innerHTML = accounts.length ? accounts.map((a) => {
    const running = busy.has(a.account_id);
    const why = running ? 'already running' : a.usable ? '' : a.status_label.toLowerCase();
    const disabled = running || !a.usable;
    return `<label class="pick ${disabled ? 'pick-off' : ''}">
      <input type="checkbox" value="${esc(a.account_id)}" ${disabled ? 'disabled' : ''}
             ${chosen.includes(a.account_id) && !disabled ? 'checked' : ''}>
      <span class="pick-name">${esc(a.account_id)}</span>
      ${why ? `<span class="pick-why">${esc(why)}</span>` : ''}
    </label>`;
  }).join('') : '<p class="empty">No accounts for this game yet. Add one below.</p>';

  updatePickedCount();
}

function updatePickedCount() {
  const count = pickedAccounts().length;
  $('fPickedCount').textContent = count ? `${count} selected` : 'none selected';
}

function pickedAccounts() {
  return [...$('fAccounts').querySelectorAll('input:checked')].map((i) => i.value);
}

/* Explain, before the operator presses Inject, exactly what will happen. */
function renderReadout() {
  if (!state) return;
  const variant = $('fGame').value;
  const stake = Number($('fStake').value || 0);
  const meta = state.variants.find((v) => v.id === variant);
  if (!meta) return;

  const picked = pickedAccounts();
  const table = state.tables.find((r) => r.game === meta.game && r.stake === stake);
  const room = state.totals.max_bots - state.totals.alive;

  const lines = [];
  if (table) {
    const parity = table.humans === 0 ? `<span class="hold">nobody waiting</span>`
      : table.odd ? `<span class="go">${table.humans} waiting — odd, entry open</span>`
      : `<span class="hold">${table.humans} waiting — even, bots hold</span>`;
    lines.push(`${esc(gameName(meta.game))} at ${stake} birr · <b>${table.online}</b> online · ${parity}`);
  }
  lines.push(`Fleet slots left: <b>${room}</b> of ${state.totals.max_bots}`);
  if (!picked.length) {
    lines.push('<span class="hold">Tick the accounts you want to inject.</span>');
  } else if (picked.length > room) {
    lines.push(`<span class="fault">${picked.length} selected but only ${Math.max(0, room)} slot(s) free.</span>`);
  } else {
    lines.push(`Injecting as <b>${picked.map(esc).join('</b>, <b>')}</b>`);
  }
  $('readout').innerHTML = lines.join('<br>');
  $('inject').disabled = !picked.length || picked.length > room;
}

function renderAuth(auth) {
  const variant = $('aGame').value;
  const entry = auth[`${variant}:${slugAccount($('aName').value)}`];
  const box = $('authNotice');
  if (!entry) { box.hidden = true; return; }
  const kind = entry.state === 'success' ? 'ok'
             : entry.state === 'error' ? 'bad' : 'busy';
  box.className = `notice ${kind}`;
  box.textContent = entry.message;
  box.hidden = false;
}

/* ── poll loop ───────────────────────────────────────────────── */

async function refresh() {
  let next;
  try {
    const response = await fetch('/api/state');
    // The session can lapse while the page sits open.
    if (response.status === 401) { window.location.replace('/login'); return; }
    next = await response.json();
  } catch {
    $('feed').className = 'feed down';
    $('feedLabel').textContent = 'console offline';
    return;
  }
  state = next;
  if (state.user) greet(state.user);
  fillGameSelects(state.variants);
  fillStakes(state.lobby.stake_options);
  renderStrip(state.totals, state.lobby);
  renderAlerts(state.warnings);
  renderTables(state.tables);
  renderFleet(state.bots);
  renderAccounts(state.sessions, state.variants);
  renderPicker();
  renderLog(state.logs);
  renderAuth(state.auth);
  renderReadout();
  if (document.activeElement !== $('fOddGate')) {
    $('fOddGate').checked = state.settings.odd_gate_enabled;
  }
  if (document.activeElement !== $('fMinBalance')) {
    $('fMinBalance').value = state.settings.min_balance ?? 10;
  }
}

/* ── wiring ──────────────────────────────────────────────────── */

$('fAccounts').addEventListener('change', () => {
  updatePickedCount();
  renderReadout();
});
$('fStagger').addEventListener('input', (e) => {
  $('fStaggerVal').textContent = Number(e.target.value).toFixed(1);
});
$('fGame').addEventListener('change', () => {
  pickerSignature = '';   // force a redraw for the newly selected game
  renderPicker();
  renderReadout();
});
$('fMinBalance').addEventListener('change', async (e) => {
  try { await post('/api/settings', { min_balance: Number(e.target.value) }); }
  catch (error) { toast(error.message, 'bad'); }
});
$('fStake').addEventListener('change', renderReadout);

$('fOddGate').addEventListener('change', async (e) => {
  try {
    await post('/api/settings', { odd_gate_enabled: e.target.checked });
    toast(e.target.checked
      ? 'Bots will wait for an odd queue.'
      : 'Odd-queue rule off — bots may end up facing each other.',
      e.target.checked ? 'ok' : 'bad');
  } catch (error) { toast(error.message, 'bad'); }
});

$('inject').addEventListener('click', async () => {
  const button = $('inject');
  button.disabled = true;
  try {
    const data = await post('/api/fleet/launch', {
      variant: $('fGame').value,
      stake: Number($('fStake').value),
      accounts: pickedAccounts(),
      stagger_seconds: Number($('fStagger').value),
      min_balance: Number($('fMinBalance').value),
    });
    toast(data.message, 'ok');
  } catch (error) {
    toast(error.message, 'bad');
  } finally {
    button.disabled = false;
    refresh();
  }
});

$('stopAll').addEventListener('click', async () => {
  if (state?.totals.alive && !confirm(`Stop all ${state.totals.alive} running bot(s)?`)) return;
  try { toast((await post('/api/fleet/stop', { bot_id: 'all' })).message, 'ok'); }
  catch (error) { toast(error.message, 'bad'); }
  refresh();
});

$('signOut').addEventListener('click', async () => {
  try { await post('/api/logout', {}); } catch { /* leaving anyway */ }
  window.location.replace('/login');
});

$('prune').addEventListener('click', async () => {
  try { toast((await post('/api/fleet/prune', {})).message); }
  catch (error) { toast(error.message, 'bad'); }
  refresh();
});

$('tables').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-resume]');
  if (!button) return;
  button.disabled = true;
  try {
    toast((await post('/api/tables/resume', { key: button.dataset.resume })).message, 'ok');
  } catch (error) {
    toast(error.message, 'bad');
  }
  refresh();
});

$('fleet').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-stop]');
  if (!button) return;
  button.disabled = true;
  try { await post('/api/fleet/stop', { bot_id: button.dataset.stop }); }
  catch (error) { toast(error.message, 'bad'); }
  refresh();
});

$('accounts').addEventListener('click', async (event) => {
  const button = event.target.closest('[data-del-account]');
  if (!button) return;
  const account = button.dataset.delAccount;
  const warning = `Delete the saved sign-in for "${account}"?\n\n`
    + 'The account keeps its money. This only removes the stored login, '
    + 'so it will need a fresh SMS code before it can play again.';
  if (!confirm(warning)) return;
  button.disabled = true;
  try {
    const done = await post('/api/sessions/delete',
      { variant: button.dataset.delVariant, account_id: account });
    toast(done.message, 'ok');
  } catch (error) {
    toast(error.message, 'bad');
  }
  refresh();
});

$('aGame').addEventListener('change', () => {
  $('aName').placeholder = `${$('aGame').value}-bot-2`;
});

$('aName').addEventListener('blur', (e) => {
  const slug = slugAccount(e.target.value);
  if (slug && slug !== e.target.value) e.target.value = slug;
});

$('sendCode').addEventListener('click', async () => {
  const button = $('sendCode');
  button.disabled = true;
  try {
    await post('/api/auth/send-otp', {
      variant: $('aGame').value,
      account_id: slugAccount($('aName').value),
      phone: $('aPhone').value.trim(),
      proxy: $('aProxy').value.trim(),
    });
  } catch (error) { toast(error.message, 'bad'); }
  finally { button.disabled = false; }
});

$('verifyCode').addEventListener('click', async () => {
  const button = $('verifyCode');
  button.disabled = true;
  try {
    await post('/api/auth/verify-otp', {
      variant: $('aGame').value,
      account_id: slugAccount($('aName').value),
      code: $('aCode').value.trim(),
    });
  } catch (error) { toast(error.message, 'bad'); }
  finally { button.disabled = false; }
});

refresh();
setInterval(refresh, POLL_MS);
