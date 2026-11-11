/* Arena Local Bridge GUI — Arabic / English display layer (added by ai-agent-command-center).
 * Changes only what the page SHOWS: text, placeholders, titles and the browser's confirm/alert texts.
 * Nothing that the bridge sends to Arena or to clients is touched. The choice is kept in this browser only.
 * Remove it with: tools/arena-bridge-i18n/apply.sh --undo */
(function () {
  "use strict";
  var D = __ACC_DICT__;
  var KEYS = Object.keys(D).sort(function (a, b) { return b.length - a.length; });
  var CJK = /[　-〿一-鿿＀-￯]/;
  var IDX = { en: 0, ar: 1 };
  var lang = "ar";
  try { lang = localStorage.getItem("acc-alb-lang") || "ar"; } catch (e) { /* private mode */ }
  var originals = new WeakMap();   // text node -> original (Chinese) text
  var attrOriginals = new WeakMap(); // element -> {attr: original}
  var busy = false;

  function tr(s) {
    if (lang === "zh" || !s || !CJK.test(s)) return s;
    var out = s;
    for (var i = 0; i < KEYS.length; i++) {
      var k = KEYS[i];
      if (out.indexOf(k) !== -1) out = out.split(k).join(D[k][IDX[lang]]);
      if (!CJK.test(out)) break;
    }
    return out;
  }

  function doText(node) {
    var orig = originals.has(node) ? originals.get(node) : node.nodeValue;
    if (!originals.has(node)) {
      if (!CJK.test(orig || "")) return;
      originals.set(node, orig);
    }
    var next = tr(orig);
    if (node.nodeValue !== next) node.nodeValue = next;
  }

  var ATTRS = ["placeholder", "title", "aria-label", "data-tip"];
  function doAttrs(el) {
    var store = attrOriginals.get(el);
    for (var i = 0; i < ATTRS.length; i++) {
      var a = ATTRS[i];
      if (!el.hasAttribute || !el.hasAttribute(a)) continue;
      var cur = el.getAttribute(a);
      if (!store) { store = {}; attrOriginals.set(el, store); }
      if (!(a in store)) { if (!CJK.test(cur)) continue; store[a] = cur; }
      var next = tr(store[a]);
      if (cur !== next) el.setAttribute(a, next);
    }
  }

  function walk(root) {
    if (!root) return;
    if (root.nodeType === 3) { doText(root); return; }
    if (root.nodeType !== 1 && root.nodeType !== 9 && root.nodeType !== 11) return;
    if (root.nodeType === 1) {
      var tag = root.tagName;
      if (tag === "SCRIPT" || tag === "STYLE" || root.id === "acc-lang") return;
      doAttrs(root);
    }
    var w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT, {
      acceptNode: function (n) {
        if (n.nodeType === 1 && (n.tagName === "SCRIPT" || n.tagName === "STYLE" || n.id === "acc-lang")) return NodeFilter.FILTER_REJECT;
        return NodeFilter.FILTER_ACCEPT;
      }
    });
    var n;
    while ((n = w.nextNode())) { if (n.nodeType === 3) doText(n); else doAttrs(n); }
  }

  function applyAll() {
    busy = true;
    document.documentElement.lang = lang === "zh" ? "zh-CN" : lang;
    if (!originals.has(document)) originals.set(document, document.title);
    document.title = tr(originals.get(document));
    walk(document.body);
    obs.takeRecords(); // our own edits are not page changes
    busy = false;
    var bar = document.getElementById("acc-lang");
    if (bar) bar.querySelectorAll("button").forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.lang === lang)); });
  }

  var obs = new MutationObserver(function (list) {
    if (busy) return;
    busy = true;
    for (var i = 0; i < list.length; i++) {
      var m = list[i];
      if (m.type === "characterData") { originals.delete(m.target); doText(m.target); }
      else if (m.type === "attributes") { var s = attrOriginals.get(m.target); if (s) delete s[m.attributeName]; doAttrs(m.target); }
      else for (var j = 0; j < m.addedNodes.length; j++) walk(m.addedNodes[j]);
    }
    obs.takeRecords();
    busy = false;
  });
  obs.observe(document.documentElement, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ATTRS });

  ["alert", "confirm", "prompt"].forEach(function (fn) {
    var orig = window[fn];
    if (typeof orig !== "function") return;
    window[fn] = function (msg) { var a = Array.prototype.slice.call(arguments); a[0] = tr(String(msg == null ? "" : msg)); return orig.apply(window, a); };
  });

  function bar() {
    var b = document.createElement("div");
    b.id = "acc-lang";
    b.setAttribute("role", "group");
    b.setAttribute("aria-label", "Language / اللغة");
    b.style.cssText = "position:fixed;bottom:14px;right:14px;z-index:99999;display:flex;gap:4px;padding:4px;border-radius:10px;" +
      "background:rgba(17,24,39,.92);box-shadow:0 6px 20px rgba(0,0,0,.25);font:13px system-ui,sans-serif";
    [["ar", "العربية"], ["en", "English"], ["zh", "中文"]].forEach(function (x) {
      var btn = document.createElement("button");
      btn.type = "button"; btn.dataset.lang = x[0]; btn.textContent = x[1];
      btn.style.cssText = "border:0;border-radius:7px;padding:6px 10px;cursor:pointer;color:#fff;background:transparent";
      btn.addEventListener("click", function () {
        lang = x[0];
        try { localStorage.setItem("acc-alb-lang", lang); } catch (e) { /* ignore */ }
        applyAll(); paint();
      });
      b.appendChild(btn);
    });
    function paint() { b.querySelectorAll("button").forEach(function (x) { x.style.background = x.dataset.lang === lang ? "#2563eb" : "transparent"; }); }
    document.body.appendChild(b); paint();
  }

  function start() { bar(); applyAll(); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
