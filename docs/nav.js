/* One navigation for every page: a row of links on wide screens, an app-style bar at the bottom on phones. */
(function () {
  var pages = [
    ["index.html", "Today", "⌂"],
    ["discover.html", "Discover", "✦"],
    ["hunt.html", "Hunt", "⌖"],
    ["find.html", "Find", "⌕"],
    ["lookup.html", "Look up", "▦"],
    ["state-lands.html", "Tax sale", "⚑"],
    ["get-a-file.html", "Get a file", "✚"],
    ["samples.html", "Samples", "▥"],
    ["campaign.html", "Outreach", "✉"],
    ["watch.html", "Watch", "◉"],
    ["taxes.html", "Taxes", "$"],
    ["investigation.html", "Cases", "▣"],
    ["garland.html", "Scan", "▤"],
    ["pro.html", "Radar", "◎"],
    ["request.html", "Request", "✎"]
  ];
  var here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  var css = document.createElement("style");
  css.textContent =
    "nav[data-nav]{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}" +
    "nav[data-nav] a{text-decoration:none;font-weight:500;font-size:13px;padding:6px 10px;border-radius:2px;color:var(--ink,#172029)}" +
    "nav[data-nav] a:hover{background:var(--surface2,#f3f5f7)}" +
    "nav[data-nav] a.on{background:var(--accent,#0E7C73);color:var(--accent-ink,#fff)}" +
    "nav[data-nav] a i{display:none}" +
    "@media (max-width:700px){" +
    " nav[data-nav]{position:fixed;left:0;right:0;bottom:0;z-index:600;margin:0;background:var(--surface,#fff);border-top:1px solid var(--rule,#d3dae0);display:grid;grid-template-columns:repeat(15,1fr);gap:0;padding:4px 0 max(4px,env(safe-area-inset-bottom))}" +
    " nav[data-nav] a{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:9.5px;padding:4px 0;border-radius:0;min-height:44px;justify-content:center}" +
    " nav[data-nav] a i{display:block;font-style:normal;font-size:18px;line-height:1}" +
    " nav[data-nav] a.on{background:none;color:var(--accent,#0E7C73)}" +
    " body{padding-bottom:64px}" +
    "}";
  document.head.appendChild(css);
  var nav = document.querySelector("nav[data-nav]");
  if (!nav) return;
  nav.innerHTML = pages.map(function (p) {
    return '<a href="' + p[0] + '"' + (here === p[0] ? ' class="on"' : "") + "><i>" + p[2] + "</i>" + p[1] + "</a>";
  }).join("");
})();
