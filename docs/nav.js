/* One navigation for every page.
   Five public doors, in the order a visitor uses them: look a parcel up, see the
   State sale, read a finished file, get or watch one, stay in the loop weekly.
   Everything else is the workbench: it opens with one switch and stays out of a
   visitor's way. Every public page ends with a "next stop" so there is a path. */
(function () {
  var PUBLIC = [
    ["index.html", "Home", "⌂", "Today's record: what moved, and where to start."],
    ["lookup.html", "Look up", "▦", "Any Arkansas parcel by address, parcel number, or the road you are on."],
    ["state-lands.html", "Tax sale", "⚑", "The State's tax-sale inventory, county by county, with the starting bid."],
    ["samples.html", "Samples", "▥", "Finished Property Files, so you know what you get."],
    ["get-a-file.html", "Get a file", "✚", "A full workup on a parcel you are looking at, or a watch on it."],
    ["signup.html", "Weekly brief", "✉", "What changed on the public record this week, in one email."]
  ];
  var BENCH = [
    ["discover.html", "Discover", "✦"],
    ["hunt.html", "Hunt", "⌖"],
    ["find.html", "Find", "⌕"],
    ["watch.html", "Watch", "◉"],
    ["campaign.html", "Outreach", "✉"],
    ["investigation.html", "Cases", "▣"],
    ["taxes.html", "Taxes", "$"],
    ["owners.html", "Owners", "◍"],
    ["divestitures.html", "Big land", "▲"],
    ["garland.html", "Scan", "▤"],
    ["pro.html", "Radar", "◎"],
    ["request.html", "Request", "✎"],
    ["feedback.html", "Feedback", "✎"]
  ];
  // The path through the public site. Each page names the next stop.
  var NEXT = {
    "index.html": ["lookup.html", "Start with a parcel: look one up"],
    "lookup.html": ["state-lands.html", "See what the State is selling near it"],
    "state-lands.html": ["samples.html", "See what a finished file looks like"],
    "samples.html": ["get-a-file.html", "Get a file on a parcel you are looking at"],
    "get-a-file.html": ["signup.html", "Get the weekly brief while you wait"],
    "signup.html": ["weekly.html", "Read this week's brief now"],
    "weekly.html": ["lookup.html", "Look up a parcel from the brief"],
    "sample.html": ["get-a-file.html", "Get a file like this on your parcel"],
    "feedback.html": ["index.html", "Back to today's record"]
  };
  var here = (location.pathname.split("/").pop() || "index.html").toLowerCase();
  var step = PUBLIC.map(function (p) { return p[0]; }).indexOf(here);
  var onBench = BENCH.some(function (p) { return p[0] === here; });
  var benchOpen = onBench;
  try { benchOpen = benchOpen || localStorage.getItem("ph-bench") === "1"; } catch (e) {}

  var css = document.createElement("style");
  css.textContent =
    "nav[data-nav]{display:flex;gap:4px;margin-left:auto;flex-wrap:wrap;align-items:center}" +
    "nav[data-nav] a{text-decoration:none;font-weight:500;font-size:13px;padding:6px 10px;border-radius:2px;color:var(--ink,#172029)}" +
    "nav[data-nav] a:hover{background:var(--surface2,#f3f5f7)}" +
    "nav[data-nav] a.on{background:var(--accent,#0E7C73);color:var(--accent-ink,#fff)}" +
    "nav[data-nav] a i{display:none}" +
    "nav[data-nav] a b{display:none}" +
    "nav[data-nav] button.more{display:none}" +
    "nav[data-nav] .bench{display:none;flex-basis:100%;gap:2px;flex-wrap:wrap;align-items:center;padding-top:4px;border-top:1px dashed var(--rule,#d3dae0)}" +
    "nav[data-nav].bench-open .bench{display:flex}" +
    "nav[data-nav] .bench a{font-size:12px;padding:4px 8px;color:var(--muted,#5b6770)}" +
    "nav[data-nav] .bench a.on{color:var(--accent-ink,#fff)}" +
    "nav[data-nav] button.bench-toggle{background:none;border:1px solid var(--rule,#d3dae0);border-radius:2px;color:var(--muted,#5b6770);font:inherit;font-size:11px;letter-spacing:.06em;text-transform:uppercase;padding:5px 8px;cursor:pointer;margin-left:6px}" +
    "nav[data-nav].bench-open button.bench-toggle{color:var(--ink,#172029)}" +
    ".ph-next{max-width:1100px;margin:28px auto 0;padding:14px 16px;border-top:1px solid var(--rule,#d3dae0);display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap;font-size:13px;color:var(--muted,#5b6770)}" +
    ".ph-next .steps{display:flex;gap:6px;align-items:center;flex-wrap:wrap}" +
    ".ph-next .steps a{color:var(--muted,#5b6770);text-decoration:none;padding:3px 7px;border:1px solid var(--rule,#d3dae0);border-radius:2px;font-size:11px;letter-spacing:.04em}" +
    ".ph-next .steps a.on{background:var(--accent,#0E7C73);color:var(--accent-ink,#fff);border-color:var(--accent,#0E7C73)}" +
    ".ph-next .steps a.done{opacity:.6}" +
    ".ph-next a.go{color:var(--ink,#172029);font-weight:600;text-decoration:none;font-size:14px;padding:8px 12px;border:1px solid var(--ink,#172029);border-radius:2px}" +
    ".ph-next a.go:hover{background:var(--accent,#0E7C73);color:var(--accent-ink,#fff);border-color:var(--accent,#0E7C73)}" +
    "@media (max-width:760px){" +
    " nav[data-nav]{position:fixed;left:0;right:0;bottom:0;z-index:600;margin:0;background:var(--surface,#fff);border-top:1px solid var(--rule,#d3dae0);display:grid;grid-template-columns:repeat(7,1fr);gap:0;padding:4px 0 max(4px,env(safe-area-inset-bottom))}" +
    " nav[data-nav] button.bench-toggle{display:none}" +
    " nav[data-nav] .bench{display:none;grid-column:1/-1;border-top:1px dashed var(--rule,#d3dae0);padding:0}" +
    " nav[data-nav].open .bench{display:grid;grid-template-columns:repeat(4,1fr)}" +
    " nav[data-nav] .bench a{min-height:52px}" +
    " nav[data-nav] button.more{display:flex}" +
    " nav[data-nav] button.more{background:none;border:0;color:var(--ink,#172029);font:inherit;font-size:9.5px;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;min-height:44px;padding:4px 0;cursor:pointer}" +
    " nav[data-nav] button.more i{font-style:normal;font-size:18px;line-height:1}" +
    " nav[data-nav] a{display:flex;flex-direction:column;align-items:center;gap:2px;font-size:9.5px;padding:4px 0;border-radius:0;min-height:44px;justify-content:center}" +
    " nav[data-nav] a i{display:block;font-style:normal;font-size:18px;line-height:1}" +
    " nav[data-nav] a.on{background:none;color:var(--accent,#0E7C73)}" +
    " nav[data-nav] .bench a.on{color:var(--accent,#0E7C73)}" +
    " .ph-next{margin-bottom:8px}" +
    "}";
  document.head.appendChild(css);

  var nav = document.querySelector("nav[data-nav]");
  if (nav) {
    var link = function (p, cls) {
      var on = p[0].toLowerCase() === here ? " on" : "";
      return '<a class="' + (cls + on).trim() + '" href="' + p[0] + '" title="' + (p[3] || p[1]) + '"><i>' + p[2] + '</i>' + p[1] + '</a>';
    };
    nav.innerHTML = PUBLIC.map(function (p) { return link(p, ""); }).join("") +
      '<button type="button" class="more" aria-expanded="false"><i>⋯</i>More</button>' +
      '<button type="button" class="bench-toggle" aria-expanded="' + (benchOpen ? "true" : "false") + '" title="The working pages: cases, outreach, scans, and the rest. Most need the local app.">Workbench</button>' +
      '<div class="bench">' + BENCH.map(function (p) { return link(p, ""); }).join("") + '</div>';
    if (benchOpen) nav.classList.add("bench-open");
    var more = nav.querySelector("button.more");
    more.onclick = function () { var open = nav.classList.toggle("open"); more.setAttribute("aria-expanded", open ? "true" : "false"); more.innerHTML = open ? "<i>×</i>Less" : "<i>⋯</i>More"; };
    var bt = nav.querySelector("button.bench-toggle");
    bt.onclick = function () {
      var open = nav.classList.toggle("bench-open");
      bt.setAttribute("aria-expanded", open ? "true" : "false");
      try { localStorage.setItem("ph-bench", open ? "1" : "0"); } catch (e) {}
    };
  }

  // The next stop, at the bottom of every public page.
  var nx = NEXT[here];
  if (nx && !onBench) {
    var host = document.querySelector("main") || document.body;
    var strip = document.createElement("div");
    strip.className = "ph-next";
    var steps = PUBLIC.slice(1).map(function (p, i) {
      var cls = p[0] === here ? "on" : (step > 0 && i + 1 < step ? "done" : "");
      return '<a class="' + cls + '" href="' + p[0] + '" title="' + p[3] + '">' + (i + 1) + " · " + p[1] + "</a>";
    }).join("");
    strip.innerHTML = '<div class="steps"><span>The path:</span>' + steps + "</div>" +
      '<a class="go" href="' + nx[0] + '">Next: ' + nx[1] + " →</a>";
    host.appendChild(strip);
  }
})();
