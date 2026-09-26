/* Visitor counting, off by default.

   The site is static on GitHub Pages, which keeps no access log you can read,
   so without something like this there is no way to tell whether anybody has
   ever opened it. Repo traffic on github.com is a different number and is not
   it.

   Cloudflare Web Analytics is the one used here: free, no cookie, no consent
   banner needed, and it never sees a visitor's identity. Nothing loads until a
   token is set, so the default state of this file is "counts nothing".

   To turn it on: create a site in Cloudflare Web Analytics, copy its token,
   and put it in docs/analytics-token.txt (one line, nothing else). It is a
   public token by design -- it appears in the page source either way.

   To turn it off again: empty that file. */
(function () {
  fetch('analytics-token.txt', { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.text() : ''; })
    .then(function (t) {
      t = (t || '').trim();
      if (!/^[0-9a-f]{20,40}$/i.test(t)) return;          // absent or placeholder: count nothing
      var s = document.createElement('script');
      s.defer = true;
      s.src = 'https://static.cloudflareinsights.com/beacon.min.js';
      s.setAttribute('data-cf-beacon', JSON.stringify({ token: t }));
      document.head.appendChild(s);
    })
    .catch(function () { /* counting is never worth breaking a page over */ });
})();
