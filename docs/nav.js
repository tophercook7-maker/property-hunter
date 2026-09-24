/* One navigation for every page: a row of links on wide screens, an app-style bar at the bottom on phones. */
(function () {
  var pages = [
    ["index.html", "Today", "⌂"],
    ["discover.html", "Discover", "✦"],
    ["hunt.html", "Hunt", "⌖"],
    ["find.html", "Find", "⌕"],
    ["lookup.html", "Look up", "▦"],
    ["state-lands.html", "Tax sale", "⚑"],
    ["signup.html", "Weekly brief", "✉"],
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
  var PHONE = ["index.html", "lookup.html", "find.html", "state-lands.html", "samples.html", "get-a-file.html"];   // what fits on a phone
  var here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  var css = document.createElement("style");
  css.textContent =
    "nav[data-nav]{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap}" +
    "nav[data-nav] a{text-decoration:none;font-weight:500;font-size:13px;padding:6px 10px;border-radius:2px;color:var(--ink,#172029)}" +
    "nav[data-nav] a:hover{background:var(--surface2,#f3f5f7)}" +
    "nav[data-nav] a.on{background:var(--accent,#0E7C73);color:var(--accent-ink,#fff)}" +
    "nav[data-nav] a i{display:none}" +
    "nav[data-nav] button.more{display:none}" +
    "@media (max-width:700px){" +
    " nav[data-nav]{position:fixed;left:0;right:0;bottom:0;z-index:600;margin:0;background:var(--surface,#fff);border-top:1px solid var(--rule,#d3dae0);display:grid;grid-template-columns:repeat(7,1fr);gap:0;padding:4px 0 max(4px,env(safe-area-inset-bottom))}" +
    " nav[data-nav] a.phone-hide{display:none}" +
    " nav[data-nav] button.more{display:flex}" +
    " nav[data-nav] button.more{background:none;border:0;color:var(--ink,#172029);font:inherit;font-size:9.5px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:44px;padding:4px 0;cursor:pointer}" +
    " nav[data-nav] button.more i{font-style:normal;font-size:18px;line-height:1}" +
    " nav[data-nav].open{grid-template-columns:repeat(4,1fr)} nav[data-nav].open a.phone-hide{display:flex} nav[data-nav].open a{min-height:52px}" +
    " nav[data-nav] a{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:9.5px;padding:4px 0;border-radius:0;min-height:44px;justify-content:center}" +
    " nav[data-nav] a i{display:block;font-style:normal;font-size:18px;line-height:1}" +
    " nav[data-nav] a.on{background:none;color:var(--accent,#0E7C73)}" +
    " body{padding-bottom:64px}" +
    "}";
  document.head.appendChild(css);
  var nav = document.querySelector("nav[data-nav]");
  if (!nav) return;
  nav.innerHTML = pages.map(function (p) {
      var on = p[0].toLowerCase() === here ? " on" : "";
      var hide = PHONE.indexOf(p[0]) === -1 ? " phone-hide" : "";
      return '<a class="' + on.trim() + hide + '" href="' + p[0] + '"><i>' + p[2] + '</i>' + p[1] + '</a>';
    }).join("") + '<button type="button" class="more" aria-expanded="false"><i>⋯</i>More</button>';
    var more = nav.querySelector("button.more");
    more.onclick = function () { var open = nav.classList.toggle("open"); more.setAttribute("aria-expanded", open ? "true" : "false"); more.innerHTML = open ? "<i>×</i>Less" : "<i>⋯</i>More"; };
})();
