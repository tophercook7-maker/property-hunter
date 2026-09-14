/* Radar membership on a static site: the Monday email carries a personal link (?key=…).
   The key is kept in this browser; the site checks its hash against data/members.json,
   which the Mac rebuilds from Stripe each week. No account, no password, nothing sent. */
window.PH = window.PH || {};
(function () {
  var KEY = "ph-member-key";
  var p = new URLSearchParams(location.search);
  if (p.get("key")) { try { localStorage.setItem(KEY, p.get("key")); } catch (e) {} }
  async function sha256(s) {
    var buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
    return Array.from(new Uint8Array(buf)).map(function (b) { return b.toString(16).padStart(2, "0"); }).join("");
  }
  var cached = null;
  PH.member = async function () {
    if (cached !== null) return cached;
    var key = null; try { key = localStorage.getItem(KEY); } catch (e) {}
    if (!key) { cached = false; return false; }
    try {
      var m = await (await fetch("data/members.json", { cache: "no-store" })).json();
      var h = await sha256(key);
      var hit = (m.members || []).find(function (x) { return x.hash === h; });
      cached = hit ? { plan: hit.plan, counties: hit.counties || [] } : false;
    } catch (e) { cached = false; }
    return cached;
  };
  PH.forget = function () { try { localStorage.removeItem(KEY); } catch (e) {} cached = null; };
})();
