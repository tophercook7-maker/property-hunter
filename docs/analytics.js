/* Visitor counting. Two ways, both off until configured, neither one tracking.

   The site is static on GitHub Pages, which keeps no access log you can read,
   so without something here there is no way to tell whether anyone has ever
   opened it.

   1. SELF-HOSTED (preferred here): posts the page path and the referring
      address to a small counter on Topher's own Mac, behind the Cloudflare
      Tunnel that already serves mixedmakershop.com. Nothing leaves his
      hardware, there is no account and no third party. Set the endpoint in
      analytics-endpoint.txt.

   2. CLOUDFLARE WEB ANALYTICS: free, no cookie, needs a token from the
      dashboard. Set it in analytics-token.txt.

   Neither sets a cookie, reads one, or stores anything that follows a person
   between visits. The counter keeps the day, the path, the referring host and
   a coarse device word, and refuses the IP and the user agent. So this counts
   page views and cannot count unique people -- a limit worth keeping rather
   than engineering around.

   Bots, previews and prerenders are not counted: a page nobody looked at is
   not a visit. Everything is wrapped so that counting can never break a page. */
(function () {
  function txt(name) {
    return fetch(name, { cache: 'no-store' })
      .then(function (r) { return r.ok ? r.text() : ''; })
      .then(function (t) { return (t || '').trim(); })
      .catch(function () { return ''; });
  }

  // A prerender or a hidden tab is not somebody reading the page.
  if (document.visibilityState === 'prerender') return;

  txt('analytics-endpoint.txt').then(function (url) {
    if (/^https:\/\/[^\s"']+$/.test(url)) {
      var body = JSON.stringify({
        p: location.pathname.replace(/^.*\/property-hunter/, '') || '/',
        r: document.referrer || ''
      });
      // keepalive so the count survives the reader clicking straight through
      fetch(url, {
        method: 'POST', keepalive: true, mode: 'cors',
        headers: { 'Content-Type': 'application/json' }, body: body
      }).catch(function () { /* counting is never worth breaking a page over */ });
      return;
    }
    return txt('analytics-token.txt').then(function (t) {
      if (!/^[0-9a-f]{20,40}$/i.test(t)) return;        // absent or placeholder: count nothing
      var s = document.createElement('script');
      s.defer = true;
      s.src = 'https://static.cloudflareinsights.com/beacon.min.js';
      s.setAttribute('data-cf-beacon', JSON.stringify({ token: t }));
      document.head.appendChild(s);
    });
  }).catch(function () { /* never break a page */ });
})();
