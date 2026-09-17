const $ = id => document.getElementById(id);
const p = new URLSearchParams(location.search);
const roomName = (p.get('name') || 'demo').slice(0, 64);
const kind = ['xml', 'csharp', 'html'].includes(p.get('kind')) ? p.get('kind') : 'xml';

let ws = null, peers = 0, typingTimer = null, sTimer = null, cTimer = null, rTimer = null;
let myName = '', peerNames = [];

function namesUI() {
  $('fname').textContent = (peerNames.length ? ('ДРУГОЙ · ' + peerNames.join(', ')) : 'ДРУГОЙ');
  $('mname').textContent = myName ? ('· ' + myName) : '';
  $('cpeers').textContent = 'в сети: ' + peers + (peerNames.length ? (' · ' + peerNames.join(', ')) : '');
}

$('ptitle').textContent = 'Комната «' + roomName + '»';
$('ckind').textContent = kind.toUpperCase();
$('ckind').className = 'chip kind-' + kind;
$('ccopy').onclick = () => {
  navigator.clipboard.writeText(location.href);
  $('ccopy').innerHTML = '<svg><use href="#i-copy"/></svg> скопировано';
  setTimeout(() => $('ccopy').innerHTML = '<svg><use href="#i-link"/></svg> ссылка', 1500);
};
$('clogout').addEventListener('click', async () => {
  try { await fetch('/api/logout', { method: 'POST' }); } catch (e) { }
  location.href = '/';
});

const PRESETS = {
  xml: `<?xml version="1.0" encoding="UTF-8"?>
<catalogue>
  <book id="1">
    <title>Мастер и Маргарита</title>
    <author>Булгаков</author>
    <price currency="rub">540</price>
  </book>
</catalogue>`,
  csharp: `using System;

class Program
{
    static void Main()
    {
        Console.WriteLine("Привет, мир!");
        for (int i = 1; i <= 5; i++)
            Console.WriteLine(i + " * 2 = " + (i * 2));
    }
}`,
  html: `<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="utf-8"/>
  <style>
    body { font-family: sans-serif; padding: 20px; }
    h1 { color: #2563eb; }
    .card { background: #f1f5f9; border-radius:0; padding: 16px; margin-top: 12px; }
    button { padding: 8px 16px; border-radius:0; border: none;
             background: #2563eb; color: #fff; cursor: pointer; }
  </style>
</head>
<body>
  <h1>Привет, мир!</h1>
  <div class="card">
    <p>Это <b>совместный</b> HTML-редактор.</p>
    <p>Печатай слева — результат виден справа и у друга.</p>
    <button onclick="alert('Привет!')">Нажми меня</button>
  </div>
</body>
</html>`
};
$('mine').value = PRESETS[kind];

function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.onopen = () => { setStatus(true); join(); };
  ws.onclose = () => { setStatus(false); qlive(false); setTimeout(connect, 2000); };
  ws.onmessage = e => { let m; try { m = JSON.parse(e.data) } catch (_) { return } handle(m); };
}

function join() {
  send({ type: 'join', room: roomName, kind, xml: $('mine').value });
}

function send(o) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(o));
}

function handle(m) {
  if (m.type === 'welcome') {
    setStatus(true); setPeers(m.peers);
    myName = m.name || ''; peerNames = m.names || []; namesUI();
    initChat(m.chat);
    if (m.kind && m.kind !== kind) {
      location.href = '/room.html?name=' + encodeURIComponent(roomName) + '&kind=' + m.kind;
    }
  }
  if (m.type === 'peers') { setPeers(m.count); if (m.names) peerNames = m.names; namesUI(); }
  if (m.type === 'error') { showInitError(m.msg); }
  if (m.type === 'peer') {
    $('friend').value = m.xml;
    qlive(true);
    clearTimeout(typingTimer); typingTimer = setTimeout(() => qlive(false), 900);
  }
  if (m.type === 'cursor') { $('friend').selectionStart = $('friend').selectionEnd = Math.min(m.pos || 0, $('friend').value.length); }
  if (m.type === 'peerf') { peerFile = m.file || null; if ($('ftbody')) fileRefresh(); }
  if (m.type === 'files') { fileRefresh(); }
  if (m.type === 'run') { renderRun(m.result); }
  if (m.type === 'chat') { addChatMsg(m); }
}

function setStatus(on) { $('cdot').classList.toggle('on', on); }
function setPeers(n) { peers = n; $('cpeers').textContent = 'в сети: ' + n; }
function qlive(on) { $('flive').textContent = on ? 'печатает…' : ''; $('mlive').textContent = on ? '' : ''; }
function showInitError(msg) {
  $('verdict').textContent = '✗ ' + msg; $('verdict').className = 'err';
  $('mine').disabled = true; $('friend').placeholder = 'Комната занята другим типом.';
  $('runbtn').style.display = 'none';
}

$('mine').addEventListener('input', () => {
  scheduleSend();
  if (kind === 'xml') scheduleCompile();
  else if (kind === 'html') schedulePreview();
  else if (kind === 'csharp' && $('autorun').checked) scheduleRun();
});

function scheduleSend() { clearTimeout(sTimer); sTimer = setTimeout(() => send({ type: 'edit', xml: $('mine').value }), 40); }
function scheduleCompile() { clearTimeout(cTimer); cTimer = setTimeout(compileXML, 180); }
function schedulePreview() { clearTimeout(rTimer); rTimer = setTimeout(updatePreview, 100); }
function scheduleRun() { clearTimeout(rTimer); rTimer = setTimeout(runCSharp, 2500); }
function sendCursor() {
  clearTimeout(cTimer);
  const pos = $('mine').selectionStart;
  cTimer = setTimeout(() => send({ type: 'cursor', pos }), 60);
}

['pointerup', 'keyup', 'click'].forEach(ev => $('mine').addEventListener(ev, sendCursor));
$('autorun').addEventListener('change', () => {
  $('hint').textContent = $('autorun').checked ? 'авто-запуск включён' : '';
  if ($('autorun').checked) runCSharp();
});

function compileXML() {
  const xml = $('mine').value;
  try {
    const doc = new DOMParser().parseFromString(xml, 'application/xml');
    const pe = doc.querySelector('parsererror');
    if (pe) {
      verdict('✗ ошибка разбора', 'err');
      $('tree').textContent = '// невалидный XML';
      $('errors').textContent = (pe.textContent || 'Ошибка разбора XML').trim().split('\n')[0];
      return;
    }
    if (doc.documentElement === null) { verdict('✗ пусто', 'err'); $('tree').textContent = ''; $('errors').textContent = 'Документ пуст'; return; }
    let tags = 0, depth = 0;
    (function walk(n, d) {
      if (n.nodeType === 1) { tags++; depth = Math.max(depth, d); }
      for (const c of n.childNodes) walk(c, n.nodeType === 1 ? d + 1 : d);
    })(doc, 0);
    $('tree').textContent = xml;
    verdict('✓ валиден', 'ok');
    $('stats').textContent = `тегов: ${tags} · глубина: ${depth} · корень: <${doc.documentElement.nodeName}>`;
    $('errors').textContent = 'Документ корректен.';
  } catch (e) { verdict('✗ ошибка', 'err'); $('errors').textContent = String(e); }
}

function verdict(text, cls) { $('verdict').textContent = text; $('verdict').className = cls; }

$('runbtn').style.display = kind === 'csharp' ? 'inline-block' : 'none';
$('autorunbox').style.display = kind === 'csharp' ? 'inline-flex' : 'none';

function runCSharp() {
  if (kind !== 'csharp') return;
  runBtnSet(true);
  verdict('компиляция и запуск…', 'neutral');
  send({ type: 'compile', xml: $('mine').value });
}

function runBtnSet(loading) {
  const b = $('runbtn');
  b.disabled = loading;
  b.innerHTML = loading ? 'выполняется…' : '<svg><use href="#i-play"/></svg> Выполнить';
}

function renderRun(r) {
  runBtnSet(false);
  if (!r) return;
  const out = r.stdout || '';
  $('tree').textContent = '// stdout:\n' + (out || '(пусто)');
  const ok = r.success === true;
  verdict(ok ? '✓ успешно (exit 0)' : '✗ ошибка', ok ? 'ok' : 'err');
  $('errors').textContent = (r.stderr || '');
}

$('runbtn').addEventListener('click', runCSharp);

$('htmlwrap').classList.toggle('on', kind === 'html');
$('outcol').style.display = kind === 'html' ? 'none' : 'flex';

function updatePreview() {
  if (kind !== 'html') return;
  $('preview').srcdoc = $('mine').value;
  verdict('✓ рендер', 'ok');
  $('stats').textContent = 'символов: ' + $('mine').value.length;
}

const chat = [];
let apending = false;
$('atok').textContent = 'контекст: текущий ' + kind.toUpperCase() + ' (отправляется с вопросом)';

function copyText(t) {
  function legacy() {
    const ta = document.createElement('textarea');
    ta.value = t; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch (_) { }
    document.body.removeChild(ta);
    return ok;
  }
  if (navigator.clipboard && navigator.clipboard.writeText)
    return navigator.clipboard.writeText(t).catch(() => legacy());
  return Promise.resolve(legacy());
}

function flashBtn(b, ok, onTxt) {
  const prev = b.textContent;
  b.textContent = ok ? 'скопировано' : 'не удалось';
  setTimeout(() => { b.textContent = prev; }, 1200);
}

function makeFence(code) {
  const box = document.createElement('div'); box.className = 'fence';
  const bar = document.createElement('div'); bar.className = 'fbar';
  const b1 = document.createElement('button'); b1.innerHTML = '<svg><use href="#i-copy"/></svg> копировать';
  b1.addEventListener('click', () => {
    copyText(code).then(ok => { b1.textContent = ok ? 'скопировано' : 'ошибка'; setTimeout(() => b1.innerHTML = '<svg><use href="#i-copy"/></svg> копировать', 1200); });
  });
  const b2 = document.createElement('button'); b2.innerHTML = '<svg><use href="#i-open"/></svg> вставить в редактор';
  b2.addEventListener('click', () => {
    const m = $('mine');
    m.value = code;
    flashBtn(b2, true, '');
    m.dispatchEvent(new Event('input', { bubbles: true }));
    b2.textContent = 'вставлено';
    setTimeout(() => b2.innerHTML = '<svg><use href="#i-open"/></svg> вставить в редактор', 1600);
  });
  bar.appendChild(b1); bar.appendChild(b2);
  const pre = document.createElement('pre'); pre.className = 'fcode';
  const c = document.createElement('code'); c.textContent = code; pre.appendChild(c);
  box.appendChild(bar); box.appendChild(pre);
  return box;
}

function renderText(s) {
  const frag = document.createDocumentFragment();
  const re = /```([\s\S]*?)```/g;
  let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) frag.appendChild(document.createTextNode(s.slice(last, m.index)));
    frag.appendChild(makeFence(m[1]));
    last = m.index + m[0].length;
  }
  if (last < s.length) frag.appendChild(document.createTextNode(s.slice(last)));
  return frag;
}

function addMsg(role, text) {
  const d = document.createElement('div');
  d.className = 'msg ' + role;
  if (role === 'assist') { d.appendChild(renderText(text)); }
  else if (role === 'user') { d.appendChild(document.createTextNode(text)); }
  else if (role === 'err') { d.appendChild(document.createTextNode(text)); }
  if (role === 'assist' || role === 'user') {
    const cb = document.createElement('button'); cb.className = 'mcopy';
    cb.innerHTML = '<svg><use href="#i-copy"/></svg>';
    cb.title = 'копировать текст';
    cb.addEventListener('click', () => { copyText(text).then(ok => flashBtn(cb, ok, '')); });
    d.appendChild(cb);
  }
  $('amsgs').appendChild(d);
  $('amsgs').scrollTop = $('amsgs').scrollHeight;
}

function setAUI(busy) {
  apending = busy;
  $('asend').disabled = busy;
  $('asend').textContent = busy ? '…' : 'Отправить';
  $('aistate').classList.toggle('on', busy);
}

async function ask() {
  const inp = $('ainput');
  const q = inp.value.trim();
  if (!q || apending) return;
  chat.push({ role: 'user', content: q });
  addMsg('user', q);
  inp.value = '';
  setAUI(true);
  addMsg('wait', 'думаю…');
  const ac = new AbortController();
  const to = setTimeout(() => ac.abort(), 130000);
  try {
    const r = await fetch('/api/chat', {
      method: 'POST', signal: ac.signal,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ messages: chat, kind, code: $('mine').value })
    });
    let j = {};
    try { j = await r.json() } catch (_) { }
    $('amsgs').removeChild($('amsgs').lastChild);
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    chat.push({ role: 'assistant', content: j.text });
    addMsg('assist', j.text);
  } catch (e) {
    $('amsgs').removeChild($('amsgs').lastChild);
    let m = e && e.message;
    if (!m) m = e;
    if (m === 'Failed to fetch' || m === 'NetworkError when attempting to fetch resource')
      addMsg('err', 'Сеть/сервер недоступны. Обновите страницу и попробуйте снова.');
    else if (e.name === 'AbortError')
      addMsg('err', 'Сервер не ответил за 130 секунд. Попробуйте ещё раз.');
    else
      addMsg('err', m);
  } finally {
    clearTimeout(to);
  }
  while (chat.length > 30) chat.shift();
  setAUI(false);
}

$('asend').addEventListener('click', ask);
$('ainput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask(); }
});
$('aclear').addEventListener('click', () => {
  chat.length = 0;
  $('amsgs').innerHTML = '';
  addMsg('assist', 'Диалог очищен. Задай новый вопрос.');
});

function tidyTime(ts) {
  const d = new Date(ts * 1000);
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

function addChatMsg(m) {
  const wrap = $('cmsgs');
  const d = document.createElement('div');
  d.className = 'cmsg';
  const who = document.createElement('div');
  who.className = 'cwho' + (m.name === myName && myName ? ' me' : '');
  who.textContent = m.name || 'аноним';
  const t = document.createElement('div'); t.className = 'ctext'; t.textContent = m.text;
  const ti = document.createElement('div'); ti.className = 'ctime'; ti.textContent = tidyTime(m.time);
  d.appendChild(who); d.appendChild(t); d.appendChild(ti);
  wrap.appendChild(d);
  wrap.scrollTop = wrap.scrollHeight;
}

function initChat(list) {
  $('cmsgs').innerHTML = '';
  (list || []).forEach(m => addChatMsg(m));
}

function sendChatMsg() {
  const inp = $('cinput');
  const text = inp.value.trim();
  if (!text) return;
  send({ type: 'chat', text });
  inp.value = '';
  inp.focus();
}

$('csend').addEventListener('click', sendChatMsg);
$('cinput').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChatMsg(); }
});

function setTab(tab, opt) {
  document.querySelectorAll('.stab').forEach(b => b.classList.toggle('on', b.dataset.tab === tab));
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.toggle('on', p.id === 'tab-' + tab));
  if (tab === 'files') fileRefresh();
}

document.querySelectorAll('.stab').forEach(b => {
  b.addEventListener('click', () => { setTab(b.dataset.tab); });
});

let fCurr = '';
let myFile = null, peerFile = null;
const F_ICONS = { html: 'i-code', csharp: 'i-hash', xml: 'i-tag', txt: 'i-text', css: 'i-brush', js: 'i-zap', json: 'i-braces', md: 'i-mark' };

function fileIcon(name) {
  const ext = name.split('.').pop().toLowerCase();
  if (name === 'index.html') return '<svg><use href="#i-home"/></svg>';
  return '<svg><use href="#' + (F_ICONS[ext] || 'i-file') + '"/></svg>';
}

function setMyFile(name) {
  myFile = name || null;
  send({ type: 'fsel', file: myFile });
  if ($('ftbody')) fileRefresh();
}

function fileRefreshUI(files) {
  $('fwho').textContent = peerFile ? ((peerNames[0] || 'друг') + ': ' + peerFile) : '';
  $('fstatus').textContent = files.length ? ('файлов: ' + files.length) : 'пока нет файлов';
}

const api = async (path, data) => {
  const r = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(Object.assign({ room: roomName }, data || {}))
  });
  if (r.status === 401) { location.href = '/'; throw new Error('401'); }
  let j = {}; try { j = await r.json() } catch (_) { }
  return { ok: r.ok && j.ok, j };
};

function fileRefresh() {
  fetch('/api/files/list?room=' + encodeURIComponent(roomName))
    .then(r => { if (r.status === 401) { location.href = '/'; throw new Error('401'); } return r.json(); }).then(j => {
      const tb = $('ftbody'); tb.innerHTML = '';
      (j.files || []).forEach(f => {
        const tr = document.createElement('tr');
        const td0 = document.createElement('td'); td0.className = 'n-ico'; td0.innerHTML = fileIcon(f.name);
        const td1 = document.createElement('td'); td1.textContent = f.name;
        const td2 = document.createElement('td'); td2.textContent = f.size + ' б';
        const td3 = document.createElement('td'); td3.textContent = f.name.split('.').pop().toUpperCase();
        const who = document.createElement('td');
        const marks = [];
        if (f.name === myFile) marks.push('ты');
        if (f.name === peerFile && peerFile) marks.push('друг');
        who.textContent = marks.join(' · ') || '';
        who.style.color = (f.name === myFile) ? 'var(--green)' : 'var(--accent)';
        tr.appendChild(td0); tr.appendChild(td1); tr.appendChild(td2); tr.appendChild(td3); tr.appendChild(who);
        tr.addEventListener('click', () => fileOpen(f.name));
        if (f.name === fCurr) { tr.classList.add('sel'); }
        tb.appendChild(tr);
      });
      fileRefreshUI(j.files || []);
    }).catch(() => { $('fstatus').textContent = 'не удалось загрузить список файлов'; });
}

async function fileOpen(name) {
  fCurr = name;
  $('fsel').textContent = name;
  $('fename').textContent = name;
  $('fopen').style.display = $('fsave').style.display = $('fdelete').style.display = 'inline-block';
  setMyFile(name);
  const r = await fetch('/api/files/read?name=' + encodeURIComponent(name) + '&room=' + encodeURIComponent(roomName));
  const j = await r.json();
  if (j.ok) { $('ftext').value = j.text; $('fstatus').textContent = j.text.length + ' символов'; }
  else { $('ftext').value = ''; $('fstatus').textContent = j.error || 'не прочитать'; }
  fileRefresh();
}

$('fcreate').addEventListener('click', async () => {
  const n = $('fnew').value.trim().toLowerCase().replace(/\s+/g, '-');
  if (!n) { $('fstatus').textContent = 'введите имя файла'; return; }
  if (!/^[a-z0-9._-]+$/.test(n)) { $('fstatus').textContent = 'только латиница, цифры, . _ -'; return; }
  const r = await api('/api/files/write', { name: n, text: '' });
  if (r.ok) { $('fnew').value = ''; $('fstatus').textContent = 'создан: ' + n; fileRefresh(); }
  else $('fstatus').textContent = r.j.error || 'ошибка';
});

$('fsave').addEventListener('click', async () => {
  if (!fCurr) return;
  const txt = $('ftext').value;
  const r = await api('/api/files/write', { name: fCurr, text: txt });
  $('fstatus').textContent = r.ok ? ('сохранено: ' + fCurr) : (r.j.error || 'ошибка');
  if (r.ok) {
    setMyFile(fCurr);
    if (fCurr === 'index.html') {
      $('mine').value = txt;
      $('mine').dispatchEvent(new Event('input', { bubbles: true }));
    }
  }
  fileRefresh();
});

$('fdelete').addEventListener('click', async () => {
  if (!fCurr) return;
  const r = await api('/api/files/delete', { name: fCurr });
  if (r.ok) {
    if (myFile === fCurr) setMyFile(null);
    $('ftext').value = ''; fCurr = ''; $('fsel').textContent = 'не выбран';
    $('fename').textContent = ''; $('fopen').style.display = $('fsave').style.display = $('fdelete').style.display = 'none';
    $('fstatus').textContent = 'удалено';
  }
  else $('fstatus').textContent = r.j.error || 'ошибка';
  fileRefresh();
});

$('freset').addEventListener('click', async () => {
  const r = await api('/api/files/reset', {});
  $('fstatus').textContent = r.ok ? 'все файлы удалены' : (r.j.error || 'ошибка');
  if (r.ok && myFile) setMyFile(null);
  fileRefresh();
});

$('fopen').addEventListener('click', async () => {
  if (!fCurr) return;
  const r = await fetch('/api/files/read?name=' + encodeURIComponent(fCurr) + '&room=' + encodeURIComponent(roomName));
  const j = await r.json();
  if (j.ok) {
    $('mine').value = j.text;
    $('mine').dispatchEvent(new Event('input', { bubbles: true }));
    setMyFile(fCurr);
    setTab('editor');
    $('fstatus').textContent = 'открыт в редакторе: ' + fCurr;
  }
});

const titles = { xml: 'результат проверки', csharp: 'вывод / ошибки компиляции', html: 'исходный HTML' };
if ($('compTitle')) $('compTitle').textContent = titles[kind];
$('foot').textContent = kind === 'csharp'
  ? 'Компиляция C# выполняется на сервере (dotnet 8). Результат видят оба участника комнаты.'
  : kind === 'html'
    ? 'Страница рендерится прямо в браузере и видна обоим.'
    : 'XML проверяется прямо в браузере у каждого участника.';

if (kind === 'xml') compileXML();
else if (kind === 'html') updatePreview();
else verdict('нажми «Выполнить»', 'neutral');

connect();
