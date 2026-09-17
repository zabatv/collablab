const $ = id => document.getElementById(id);
let rooms = [], filter = '', mode = 'login';
const KIND_LABEL = { xml: 'XML', csharp: 'C#', html: 'HTML' };

function setMode(m) {
  mode = m;
  $('tab-l').classList.toggle('on', m === 'login');
  $('tab-r').classList.toggle('on', m === 'reg');
  $('asub').textContent = m === 'login' ? 'Войти' : 'Создать аккаунт';
  $('aok').textContent = '';
  $('aerr').textContent = '';
  const reg = m === 'reg';
  $('namelab').classList.toggle('hidden', !reg);
  $('aname').classList.toggle('hidden', !reg);
  $('rem').classList.toggle('hidden', !reg);
  if (reg) $('aname').focus();
  $('apass').placeholder = m === 'login' ? 'пароль' : 'минимум 6 символов';
}

async function doAuth() {
  const email = $('aemail').value.trim().toLowerCase();
  const pw = $('apass').value;
  const name = ($('aname').value || '').trim().slice(0, 32);
  $('aerr').textContent = '';
  $('aok').textContent = '';
  if (!email || email.indexOf('@') < 1) { $('aerr').textContent = 'Введите корректную почту'; return; }
  if (pw.length < 6) { $('aerr').textContent = 'Пароль: минимум 6 символов'; return; }
  if (mode === 'reg' && !name) { $('aerr').textContent = 'Введите никнейм'; return; }
  const btn = $('asub'); btn.disabled = true; btn.textContent = '…';
  try {
    if (mode === 'reg') {
      const r = await fetch('/api/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email, password: pw, name })
      });
      const j = await r.json();
      if (!r.ok) { $('aerr').textContent = j.error || 'Ошибка регистрации'; return; }
    }
    const r = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, password: pw, remember: $('arem').checked })
    });
    const j = await r.json();
    if (!r.ok || !j.ok) { $('aerr').textContent = j.error || 'Ошибка входа'; return; }
    showApp(j.name || name || email.split('@')[0], email);
  } catch (e) {
    $('aerr').textContent = 'Сеть/сервер недоступны';
  } finally {
    btn.disabled = false;
    btn.textContent = mode === 'login' ? 'Войти' : 'Создать аккаунт';
  }
}

function showApp(name, email) {
  $('authwrap').classList.add('hidden');
  $('app').classList.remove('hidden');
  $('who').classList.remove('hidden');
  $('logout').classList.remove('hidden');
  $('whoName').textContent = (name || email);
  refresh();
  if (!window._timer) window._timer = setInterval(refresh, 2000);
}

function showAuth() {
  $('app').classList.add('hidden');
  $('who').classList.add('hidden');
  $('logout').classList.add('hidden');
  $('authwrap').classList.remove('hidden');
  if (window._timer) { clearInterval(window._timer); window._timer = null; }
}

$('logout').addEventListener('click', async () => {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { }
  showAuth();
});

$('aemail').addEventListener('keydown', e => { if (e.key === 'Enter') $('apass').focus(); });
$('apass').addEventListener('keydown', e => { if (e.key === 'Enter') doAuth(); });

(async () => {
  try {
    const r = await fetch('/api/me', { cache: 'no-store' });
    if (r.ok) {
      const j = await r.json();
      if (j.ok) { showApp(j.name, j.email); return; }
    }
  } catch (e) { }
  showAuth();
})();

async function refresh() {
  try {
    const r = await fetch('/api/rooms', { cache: 'no-store' });
    const d = await r.json();
    rooms = d.rooms || [];
  } catch (e) { }
  render();
}

function setFilter(k) {
  filter = k;
  document.querySelectorAll('.filter').forEach(b => b.classList.toggle('on', b.dataset.k === k));
  render();
}

function rel(u) {
  if (u < 6) return 'сейчас';
  if (u < 60) return Math.floor(u) + ' с назад';
  if (u < 3600) return Math.floor(u / 60) + ' мин назад';
  return Math.floor(u / 3600) + ' ч назад';
}

function render() {
  $('total').textContent = 'комнат: ' + rooms.length;
  const rows = $('rows');
  rows.innerHTML = '';
  const list = rooms.filter(r => !filter || r.kind === filter);
  $('empty').style.display = list.length ? 'none' : 'block';
  for (const r of list) {
    const d = document.createElement('div');
    d.className = 'rrow';
    d.innerHTML = `
      <div class="rtype"><span class="kbadge k${r.kind}">${KIND_LABEL[r.kind] || r.kind}</span></div>
      <span class="roomname" title="${r.name.replace(/"/g, '&quot;')}">${r.name.replace(/</g, '&lt;')}</span>
      <span class="peers"><span class="pdot ${r.peers > 1 ? 'g' : 'y'}"></span>${r.peers}</span>
      <span class="activity">${rel(r.updated)}</span>
      <a class="open" href="/room.html?name=${encodeURIComponent(r.name)}&kind=${r.kind}">открыть →</a>`;
    rows.appendChild(d);
  }
}

function create() {
  const name = $('name').value.trim().slice(0, 64) || ('room' + Math.floor(Math.random() * 900 + 100));
  const kind = $('kind').value;
  location.href = '/room.html?name=' + encodeURIComponent(name) + '&kind=' + kind;
}

$('livein').addEventListener('click', refresh);
