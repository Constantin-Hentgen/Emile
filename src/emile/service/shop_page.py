"""The shop page: one self-contained HTML document (no build step, no external request).

It holds no secret and no data. The shop link's secret arrives in the URL fragment (``/shop#shop_…``), which browsers
never send to the server; the script keeps it in ``localStorage`` and sends it as a bearer token to ``/v1/shop``.
Every piece of text coming from the list is inserted with ``textContent``, never as HTML.
"""

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="referrer" content="no-referrer">
<title>Shopping list</title>
<style>
:root { --bg:#fbfaf7; --fg:#1c1b19; --muted:#6b675f; --line:#e4e0d6; --card:#fff; --accent:#2f7d4f; --warn:#a4452c; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#161513; --fg:#ece9e1; --muted:#9b968b; --line:#2c2a26; --card:#1f1d1a; --accent:#6cc38c; --warn:#e48a6f; }
}
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg); font:17px/1.35 system-ui, sans-serif;
  padding:0 16px max(24px, env(safe-area-inset-bottom)); }
main { max-width:560px; margin:0 auto; }
header { padding:20px 0 8px; }
h1 { font-size:22px; margin:0 0 2px; }
#summary, #progress { color:var(--muted); font-size:15px; margin:0; }
#bar { height:6px; background:var(--line); border-radius:3px; margin:12px 0 4px; overflow:hidden; }
#bar > div { height:100%; width:0; background:var(--accent); transition:width .2s; }
#banner { display:none; margin:12px 0; padding:10px 12px; border-radius:10px; background:var(--card);
  border:1px solid var(--warn); color:var(--warn); font-size:15px; }
h2 { font-size:13px; font-weight:600; letter-spacing:.04em; text-transform:uppercase; color:var(--muted);
  margin:22px 0 6px; }
ul { list-style:none; margin:0; padding:0; background:var(--card); border:1px solid var(--line); border-radius:12px;
  overflow:hidden; }
li + li { border-top:1px solid var(--line); }
button.item { all:unset; box-sizing:border-box; display:flex; gap:12px; align-items:center; width:100%;
  min-height:52px; padding:10px 14px; cursor:pointer; -webkit-tap-highlight-color:transparent; }
button.item:focus-visible { outline:2px solid var(--accent); outline-offset:-2px; }
.box { flex:none; width:24px; height:24px; border:2px solid var(--muted); border-radius:7px; display:grid;
  place-items:center; color:transparent; font-size:16px; }
.done .box { background:var(--accent); border-color:var(--accent); color:#fff; }
.done .label { text-decoration:line-through; color:var(--muted); }
#reset { all:unset; display:block; margin:22px auto 0; padding:10px 16px; color:var(--muted); font-size:15px;
  cursor:pointer; text-decoration:underline; }
#empty { text-align:center; color:var(--muted); padding:48px 0; }
</style>
</head>
<body>
<main>
  <header>
    <h1 id="title">Shopping list</h1>
    <p id="summary"></p>
    <div id="bar"><div></div></div>
    <p id="progress"></p>
  </header>
  <div id="banner" role="status"></div>
  <div id="groups"></div>
  <p id="empty" hidden></p>
  <button id="reset" hidden></button>
</main>
<script>
"use strict";
const TEXT = {
  fr: { title: "Courses", progress: "{done} sur {total}", noList: "Pas de liste de courses en cours.",
        noLink: "Ouvre le lien de courses complet (celui qui finit par #shop_…).",
        revoked: "Ce lien a été révoqué ou n'existe plus.", stale: "La liste a été remplacée : mise à jour.",
        offline: "Pas de connexion : réessai en cours.", reset: "Tout décocher",
        confirm: "Tout décocher ? Les articles repartent du stock." },
  en: { title: "Shopping", progress: "{done} of {total}", noList: "No shopping list right now.",
        noLink: "Open the full shop link (the one ending in #shop_…).",
        revoked: "This link was revoked or no longer exists.", stale: "The list was replaced: refreshed.",
        offline: "No connection: retrying.", reset: "Untick everything",
        confirm: "Untick everything? The items leave the stock again." },
};
const KEY = "emile-shop-token";
const $ = (id) => document.getElementById(id);
let token = null, lang = (navigator.language || "en").startsWith("fr") ? "fr" : "en", view = null, busy = 0;

try {
  const fromUrl = location.hash.replace(/^#/, "");
  if (fromUrl.startsWith("shop_")) { localStorage.setItem(KEY, fromUrl); history.replaceState(null, "", "/shop"); }
  token = localStorage.getItem(KEY);
} catch (e) { token = location.hash.replace(/^#/, "") || null; }

const t = (key) => TEXT[lang][key];
function banner(message) { const el = $("banner"); el.textContent = message || ""; el.style.display = message ? "block" : "none"; }

async function call(method, path) {
  const res = await fetch(path, { method, headers: { Authorization: "Bearer " + token }, cache: "no-store" });
  let body = null;
  try { body = await res.json(); } catch (e) { /* not JSON */ }
  return { status: res.status, body };
}

function render(next) {
  view = next; lang = next.language;
  document.documentElement.lang = lang;
  document.title = $("title").textContent = t("title");
  $("summary").textContent = next.summary;
  $("progress").textContent = t("progress").replace("{done}", next.done).replace("{total}", next.total);
  $("bar").firstElementChild.style.width = next.total ? (100 * next.done / next.total) + "%" : "0";
  $("empty").hidden = true;
  const root = $("groups"); root.replaceChildren();
  for (const group of next.groups) {
    const h = document.createElement("h2"); h.textContent = group.aisle;
    const ul = document.createElement("ul");
    for (const item of group.items) {
      const li = document.createElement("li"), b = document.createElement("button");
      b.className = "item" + (item.done ? " done" : ""); b.setAttribute("role", "checkbox");
      b.setAttribute("aria-checked", item.done ? "true" : "false");
      const box = document.createElement("span"); box.className = "box"; box.textContent = "✓";
      const label = document.createElement("span"); label.className = "label"; label.textContent = item.label;
      b.append(box, label); b.addEventListener("click", () => toggle(item.index));
      li.append(b); ul.append(li);
    }
    root.append(h, ul);
  }
  $("reset").hidden = next.done === 0; $("reset").textContent = t("reset");
}

function none(message) {
  view = null; $("groups").replaceChildren(); $("summary").textContent = ""; $("progress").textContent = "";
  $("bar").firstElementChild.style.width = "0"; $("reset").hidden = true;
  $("empty").hidden = false; $("empty").textContent = message;
}

async function handle(res) {
  if (res.status === 200) { banner(""); render(res.body); return true; }
  if (res.status === 401) { none(token ? t("revoked") : t("noLink")); try { localStorage.removeItem(KEY); } catch (e) {} return false; }
  if (res.status === 404) { banner(""); none(t("noList")); return false; }
  if (res.status === 409) { banner(t("stale")); await refresh(); return false; }
  banner(res.body && res.body.detail ? String(res.body.detail) : t("offline"));
  return false;
}

async function refresh() {
  if (!token) { none(t("noLink")); return; }
  if (busy) return;
  try { await handle(await call("GET", "/v1/shop")); } catch (e) { banner(t("offline")); }
}

async function act(path) {
  busy++;
  try { await handle(await call("POST", path)); } catch (e) { banner(t("offline")); } finally { busy--; }
}

function toggle(index) {
  if (!view) return;
  const item = view.groups.flatMap((g) => g.items).find((i) => i.index === index);
  if (item) { item.done = !item.done; view.done += item.done ? 1 : -1; render(view); }  // optimistic; the answer is the truth
  act("/v1/shop/lists/" + view.list_id + "/items/" + index + "/toggle");
}

$("reset").addEventListener("click", () => { if (view && confirm(t("confirm"))) act("/v1/shop/lists/" + view.list_id + "/reset"); });
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
setInterval(refresh, 10000);
refresh();
</script>
</body>
</html>
"""
