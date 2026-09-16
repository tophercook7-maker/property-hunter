/* Property Hunter access client (P5.5).
   The server decides. This file never holds an activation code after the activation call returns, never
   holds an admin secret, and never exports the device private key: the key is generated non-extractable
   with Web Crypto and kept in IndexedDB; only its public half is registered. Sessions are a short access
   token (memory + sessionStorage) and a rotating refresh token (localStorage) that is useless without
   the device key, because every refresh signs a fresh server nonce. */
(function () {
  const API = (typeof window !== 'undefined' && window.PH_API_BASE) || (location.port === '8234' ? '' : 'http://127.0.0.1:8234');
  const DB = 'ph-auth', STORE = 'keys', KEY_ID = 'device';
  let access = null, accessExp = 0, refreshing = null;
  const b64u = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  function idb() {
    return new Promise((res, rej) => { const r = indexedDB.open(DB, 1); r.onupgradeneeded = () => r.result.createObjectStore(STORE); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  }
  async function getKey() {
    const db = await idb();
    return new Promise((res, rej) => { const t = db.transaction(STORE, 'readonly').objectStore(STORE).get(KEY_ID); t.onsuccess = () => res(t.result || null); t.onerror = () => rej(t.error); });
  }
  async function putKey(pair) {
    const db = await idb();
    return new Promise((res, rej) => { const t = db.transaction(STORE, 'readwrite').objectStore(STORE).put(pair, KEY_ID); t.onsuccess = () => res(true); t.onerror = () => rej(t.error); });
  }
  async function ensureKey() {
    let pair = await getKey();
    if (!pair) {
      pair = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign', 'verify']);   // private key non-extractable
      await putKey(pair);
    }
    return pair;
  }
  async function publicJwk(pair) { const j = await crypto.subtle.exportKey('jwk', pair.publicKey); return { kty: j.kty, crv: j.crv, x: j.x, y: j.y }; }
  async function sign(pair, text) { return b64u(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, pair.privateKey, new TextEncoder().encode(text))); }
  async function challenge(purpose) { const r = await fetch(API + '/api/license/challenge?purpose=' + purpose); if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || 'Connection required to verify your access.'); return r.json(); }

  function setSession(s) {
    access = s.access_token; accessExp = Date.now() + (s.access_expires_in - 30) * 1000;
    try { sessionStorage.setItem('ph-access', JSON.stringify({ t: access, e: accessExp })); localStorage.setItem('ph-refresh', s.refresh_token); localStorage.setItem('ph-license', JSON.stringify({ id: s.license_id, type: s.license_type, expires_at: s.expires_at || null })); } catch (e) {}
  }
  function loadSession() {
    if (access) return;
    try { const a = JSON.parse(sessionStorage.getItem('ph-access') || 'null'); if (a && a.e > Date.now()) { access = a.t; accessExp = a.e; } } catch (e) {}
  }
  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      const rt = localStorage.getItem('ph-refresh'); if (!rt) throw new Error('Activation required.');
      const pair = await getKey(); if (!pair) throw new Error('This device could not be verified.');
      const c = await challenge('refresh');
      const r = await fetch(API + '/api/license/refresh', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ refresh_token: rt, nonce: c.nonce, signature: await sign(pair, c.nonce) }) });
      if (!r.ok) { clear(); throw new Error((await r.json().catch(() => ({}))).detail || 'Activation required.'); }
      setSession(await r.json()); return access;
    })();
    try { return await refreshing; } finally { refreshing = null; }
  }
  function clear() { access = null; accessExp = 0; try { sessionStorage.removeItem('ph-access'); localStorage.removeItem('ph-refresh'); localStorage.removeItem('ph-license'); } catch (e) {} }

  const PHAuth = {
    api: API,
    async activate(code) {
      const pair = await ensureKey();
      const c = await challenge('activate');
      const r = await fetch(API + '/api/license/activate', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ code, device_key: await publicJwk(pair), nonce: c.nonce, signature: await sign(pair, c.nonce) }) });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(body.detail || 'This access code could not be activated.');
      setSession(body);
      return { license_id: body.license_id, license_type: body.license_type, expires_at: body.expires_at, reactivated: body.reactivated };
    },
    async token() { loadSession(); if (access && Date.now() < accessExp) return access; return refresh(); },
    async fetch(url, opts) {
      const o = Object.assign({}, opts || {}); o.headers = Object.assign({}, o.headers || {});
      let tok = null; try { tok = await PHAuth.token(); } catch (e) {}
      if (tok) o.headers['Authorization'] = 'Bearer ' + tok;
      let r = await fetch(url, o);
      if (r.status === 401 && tok) { try { const t2 = await refresh(); o.headers['Authorization'] = 'Bearer ' + t2; r = await fetch(url, o); } catch (e) {} }
      return r;
    },
    async status() { try { const t = await PHAuth.token(); const r = await fetch(API + '/api/license/me', { headers: { Authorization: 'Bearer ' + t } }); return r.ok ? await r.json() : null; } catch (e) { return null; } },
    licenseInfo() { try { return JSON.parse(localStorage.getItem('ph-license') || 'null'); } catch (e) { return null; } },
    async signOut() { try { const t = await PHAuth.token(); await fetch(API + '/api/license/logout', { method: 'POST', headers: { Authorization: 'Bearer ' + t } }); } catch (e) {} clear(); },
  };
  window.PHAuth = PHAuth;
})();
