// Aqua Code private site: sign in with Google, see and download everything Aqua Code synced from your computers.
// Everything is read straight from YOUR Firestore; the rules let only your Google account in.
const SDK = 'https://www.gstatic.com/firebasejs/10.12.2/';
// config.js is written by tools/deploy_sync_site.py from your own Firebase project (public web settings, not secrets: the Firestore rules protect the data).
const CFG = window.AQUA_CFG || {};
const $ = (id) => document.getElementById(id);
const show = (...ids) => ['loading', 'signin', 'denied', 'linkcard', 'app'].forEach((x) => { $(x).hidden = !ids.includes(x); });
const toast = (t) => { const e = $('toast'); e.textContent = t; e.classList.add('show'); setTimeout(() => e.classList.remove('show'), 3200); };
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const fmtSize = (n) => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : n < 1073741824 ? (n / 1048576).toFixed(1) + ' MB' : (n / 1073741824).toFixed(2) + ' GB';
const ago = (ms) => { const s = Math.max(0, Math.round((Date.now() - ms) / 1000)); return s < 60 ? 'just now' : s < 3600 ? Math.round(s / 60) + ' min ago' : s < 86400 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago'; };

let auth, db, fs, authM, user = null, DATA = null;

async function boot() {
  if (!CFG.apiKey || !CFG.projectId) { $('loading-msg').textContent = 'This site has no Firebase settings yet. Run tools/deploy_sync_site.py --yes in Aqua Code.'; return; }
  try {
    const [appM, a, f] = await Promise.all([import(SDK + 'firebase-app.js'), import(SDK + 'firebase-auth.js'), import(SDK + 'firebase-firestore.js')]);
    authM = a; fs = f;
    const app = appM.initializeApp(CFG);
    auth = a.getAuth(app); db = f.getFirestore(app);
  } catch (e) {
    $('loading-msg').textContent = 'Could not load Firebase. Check your internet connection and reload.';
    return;
  }
  authM.onAuthStateChanged(auth, async (u) => {
    user = u;
    $('who').hidden = !u;
    if (!u) { show('signin'); return; }
    $('who-mail').textContent = u.email || '';
    if (linkTarget()) { show('linkcard'); }
    else { await load(); }
  });
}

function linkTarget() {
  const m = /^#link=(\d{2,5})\.([\w-]{8,})$/.exec(location.hash);
  return m ? { port: m[1], code: m[2] } : null;
}

$('signin-btn').onclick = async () => {
  $('signin-err').textContent = '';
  try { await authM.signInWithPopup(auth, new authM.GoogleAuthProvider()); }
  catch (e) { $('signin-err').textContent = e && e.code === 'auth/unauthorized-domain' ? 'This web address is not allowed for sign-in yet. Add it under Authentication, Settings, Authorized domains in the Firebase console.' : (e && e.message) || 'Sign-in did not finish.'; }
};
$('signout').onclick = $('denied-out').onclick = () => authM.signOut(auth);
$('refresh').onclick = () => load();

$('link-btn').onclick = async () => {
  const t = linkTarget(); if (!t || !user) return;
  $('link-msg').textContent = 'Linking...';
  try {
    const r = await fetch('http://127.0.0.1:' + t.port + '/api/sync/link', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: t.code, apiKey: CFG.apiKey, projectId: CFG.projectId, refreshToken: user.refreshToken, email: user.email, uid: user.uid })
    });
    const j = await r.json();
    if (j.ok) { $('link-msg').textContent = 'Linked. You can close this tab and go back to Aqua Code.'; history.replaceState(null, '', location.pathname); $('link-btn').disabled = true; }
    else { $('link-msg').textContent = j.error || 'Aqua Code refused the link.'; }
  } catch (e) {
    $('link-msg').textContent = 'Could not reach Aqua Code on this computer. Is it open? Then press Link this computer in Aqua Code again.';
  }
};

// ------------------------------------------------------------------ data
async function load() {
  show('loading'); $('loading-msg').textContent = 'Reading your projects...';
  try {
    const [files, chats, devs] = await Promise.all([
      fs.getDocs(fs.collection(db, 'aqua_files')), fs.getDocs(fs.collection(db, 'aqua_chats')), fs.getDoc(fs.doc(db, 'aqua_meta', 'devices'))]);
    DATA = { files: files.docs.map((d) => ({ id: d.id, ...d.data() })), chats: chats.docs.map((d) => ({ id: d.id, ...d.data() })), devs: devs.exists() ? (devs.data().list || {}) : {} };
  } catch (e) {
    if (e && e.code === 'permission-denied') { $('denied-mail').textContent = user.email || ''; show('denied'); return; }
    $('loading-msg').textContent = e && /not been used|disabled|NOT_FOUND/i.test(e.message || '') ? 'Firestore is not enabled in your Firebase project yet.' : 'Could not read: ' + ((e && e.message) || e);
    return;
  }
  render(); show('app');
}

function projectsOf() {
  const m = new Map();
  for (const f of DATA.files) {
    if (f.d) continue;
    const p = m.get(f.p) || { name: f.p, files: [], size: 0, last: 0 };
    p.files.push(f); p.size += f.s || 0; p.last = Math.max(p.last, f.u || 0); m.set(f.p, p);
  }
  return [...m.values()].sort((a, b) => b.last - a.last);
}
const devName = (id) => (DATA.devs[id] && DATA.devs[id].name) || (id ? id.slice(0, 6) : 'a computer');

function render() {
  const ps = projectsOf(), files = ps.reduce((n, p) => n + p.files.length, 0), size = ps.reduce((n, p) => n + p.size, 0);
  const chats = DATA.chats.filter((c) => !c.d);
  $('st-projects').textContent = ps.length; $('st-files').textContent = files; $('st-size').textContent = fmtSize(size); $('st-chats').textContent = chats.length;
  $('all-zip').onclick = () => zipDownload(ps, chats, 'aqua-code-everything.zip');

  const box = $('projects'); box.replaceChildren();
  if (!ps.length) box.append(el('div', 'item', 'No projects yet. Link a computer in Aqua Code (AI menu, Sync my projects) and wait a moment.'));
  for (const p of ps) {
    const it = el('div', 'item'), row = el('div', 'row'), left = el('div'), right = el('div');
    left.append(el('b', '', p.name), el('br'), el('small', '', p.files.length + ' files, ' + fmtSize(p.size) + ', changed ' + ago(p.last)));
    const open = el('button', 'btn ghost', 'Files'), zip = el('button', 'btn gold', 'Download ZIP');
    right.append(open, document.createTextNode(' '), zip); row.append(left, right); it.append(row);
    const list = el('div', 'files'); list.hidden = true; it.append(list);
    open.onclick = () => {
      list.hidden = !list.hidden;
      if (!list.hidden && !list.childElementCount) {
        for (const f of p.files.sort((a, b) => a.f.localeCompare(b.f))) {
          const r = el('div', 'file'), path = el('span', 'p', f.f), sm = el('small', '', fmtSize(f.s || 0) + ' - ' + ago(f.u || 0)), dl = el('button', 'btn ghost', 'Download');
          dl.onclick = async () => { try { saveBlob(new Blob([await fileBytes(f)]), f.f.split('/').pop()); } catch (e) { toast(e.message); } };
          r.append(path, sm, dl); list.append(r);
        }
      }
    };
    zip.onclick = () => zipDownload([p], [], p.name.replace(/[^\w.-]+/g, '_') + '.zip');
    box.append(it);
  }

  const ch = $('changes'); ch.replaceChildren();
  const all = [...DATA.files.map((f) => ({ t: f.u || 0, what: f.p + '/' + f.f, dev: f.dev, kind: f.d ? 'del' : (f.r > 1 ? 'edit' : 'new') })),
    ...DATA.chats.map((c) => ({ t: c.u || 0, what: 'chat: ' + (c.t || c.id), dev: c.dev, kind: c.d ? 'del' : (c.r > 1 ? 'edit' : 'new') }))].sort((a, b) => b.t - a.t).slice(0, 25);
  if (!all.length) ch.append(el('div', 'chg', 'Nothing yet.'));
  for (const c of all) {
    const r = el('div', 'chg'), l = el('span'), tag = el('span', 'tag ' + c.kind, c.kind === 'del' ? 'removed' : c.kind === 'edit' ? 'changed' : 'new');
    l.append(tag, document.createTextNode(' ' + c.what)); r.append(l, el('small', '', devName(c.dev) + ' - ' + ago(c.t))); ch.append(r);
  }

  const cb = $('chats'); cb.replaceChildren();
  if (!chats.length) cb.append(el('div', 'item', 'No chats synced yet.'));
  for (const c of chats.sort((a, b) => (b.u || 0) - (a.u || 0))) {
    const it = el('div', 'item'), row = el('div', 'row'), left = el('div'), right = el('div');
    left.append(el('b', '', c.t || '(untitled chat)'), el('br'), el('small', '', (c.p || 'no project') + ' - ' + ago(c.u || 0)));
    const view = el('button', 'btn ghost', 'Read'), dl = el('button', 'btn ghost', 'Download');
    right.append(view, document.createTextNode(' '), dl); row.append(left, right); it.append(row);
    const msgs = el('div', 'msgs'); msgs.hidden = true; it.append(msgs);
    view.onclick = async () => {
      msgs.hidden = !msgs.hidden;
      if (!msgs.hidden && !msgs.childElementCount) {
        try {
          const j = JSON.parse(new TextDecoder().decode(await fileBytes(c)));
          for (const m of (j.messages || []).slice(0, 300)) { const t = typeof m.content === 'string' ? m.content : JSON.stringify(m.content || m.tool_calls || ''); msgs.append(el('div', m.role === 'user' ? 'user' : '', (m.role || '') + ': ' + t.slice(0, 2500))); }
        } catch (e) { msgs.append(el('div', '', e.message)); }
      }
    };
    dl.onclick = async () => { try { saveBlob(new Blob([await fileBytes(c)], { type: 'application/json' }), (c.t || c.id).replace(/[^\w.-]+/g, '_') + '.json'); } catch (e) { toast(e.message); } };
    cb.append(it);
  }

  const dv = $('devices'); dv.replaceChildren();
  const list = Object.entries(DATA.devs);
  if (!list.length) dv.append(el('div', 'chg', 'No computer has reported yet.'));
  for (const [id, d] of list) { const r = el('div', 'chg'); r.append(el('span', '', d.name + ' (' + d.os + ')'), el('small', '', 'last seen ' + ago(d.seen || 0))); dv.append(r); }
}

// ------------------------------------------------------------------ downloading
let libs;
function loadScript(src) { return new Promise((ok, no) => { const s = document.createElement('script'); s.src = src; s.onload = ok; s.onerror = () => no(new Error('Could not load ' + src)); document.head.append(s); }); }
async function needLibs() {
  if (libs) return libs;
  await Promise.all([loadScript('https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js'), loadScript('https://cdnjs.cloudflare.com/ajax/libs/pako/2.1.0/pako.min.js')]);
  return (libs = true);
}
const unb64 = (s) => { const b = atob(s), u = new Uint8Array(b.length); for (let i = 0; i < b.length; i++) u[i] = b.charCodeAt(i); return u; };
const hex = (buf) => [...new Uint8Array(buf)].map((x) => x.toString(16).padStart(2, '0')).join('');

async function fileBytes(d) {                    // works for file documents and chat documents
  await needLibs();
  let b64 = d.z || '';
  if ((d.n || 1) > 1) {
    const parts = await Promise.all(Array.from({ length: d.n }, (_, i) => fs.getDoc(fs.doc(db, 'aqua_chunks', d.id + '_' + d.r + '_' + i))));
    if (parts.some((p) => !p.exists())) throw new Error('A part of this file is missing in the cloud.');
    b64 = parts.map((p) => p.data().d).join('');
  }
  const raw = b64 ? window.pako.inflate(unb64(b64)) : new Uint8Array(0);
  if (d.h && hex(await crypto.subtle.digest('SHA-256', raw)) !== d.h) throw new Error('The cloud copy is damaged (checksum differs).');
  return raw;
}

function saveBlob(blob, name) {
  const a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = name; document.body.append(a); a.click();
  setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 4000);
}

async function zipDownload(projects, chats, name) {
  const total = projects.reduce((n, p) => n + p.files.length, 0) + chats.length;
  if (!total) { toast('Nothing to download yet.'); return; }
  const bar = $('progress'); bar.hidden = false; let done = 0;
  const upd = (m) => { bar.firstElementChild.style.width = Math.round(100 * done / total) + '%'; $('progress-msg').textContent = m || (done + ' of ' + total); };
  try {
    await needLibs();
    const zip = new window.JSZip(), problems = [];
    const queue = [
      ...projects.flatMap((p) => p.files.map((f) => ({ d: f, path: p.name + '/' + f.f, date: f.m ? new Date(f.m) : new Date() }))),
      ...chats.map((c) => ({ d: c, path: '_chats/' + (c.t || c.id).replace(/[^\w.-]+/g, '_') + '-' + c.id + '.json', date: new Date(c.u || Date.now()) }))];
    let i = 0;
    await Promise.all(Array.from({ length: 4 }, async () => {           // four at a time keeps the phone happy
      while (i < queue.length) {
        const q = queue[i++];
        try { zip.file(q.path, await fileBytes(q.d), { date: q.date }); } catch (e) { problems.push(q.path + ': ' + e.message); }
        done++; upd();
      }
    }));
    if (problems.length) zip.file('_problems.txt', problems.join('\n'));
    upd('Packing the ZIP...');
    saveBlob(await zip.generateAsync({ type: 'blob', compression: 'DEFLATE' }, (m) => { bar.firstElementChild.style.width = Math.round(m.percent) + '%'; }), name);
    toast(problems.length ? 'Downloaded with ' + problems.length + ' problem(s), see _problems.txt' : 'Downloaded ' + total + ' items');
  } catch (e) { toast(e.message || String(e)); }
  setTimeout(() => { bar.hidden = true; }, 1500);
}

boot();
