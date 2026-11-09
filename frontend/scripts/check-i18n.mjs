// i18n guard: (1) ar.json and en.json have identical key sets and no empty values;
// (2) no hardcoded user-facing text in JSX (text nodes / string aria-label, title, placeholder).
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const flat = (o, p = "") =>
  Object.entries(o).flatMap(([k, v]) => (typeof v === "object" ? flat(v, `${p}${k}.`) : [[`${p}${k}`, v]]));
const ar = new Map(flat(JSON.parse(readFileSync("messages/ar.json", "utf8"))));
const en = new Map(flat(JSON.parse(readFileSync("messages/en.json", "utf8"))));
const errors = [];
for (const k of ar.keys()) if (!en.has(k)) errors.push(`missing in en.json: ${k}`);
for (const k of en.keys()) if (!ar.has(k)) errors.push(`missing in ar.json: ${k}`);
for (const [f, m] of [["ar", ar], ["en", en]]) for (const [k, v] of m) if (!String(v).trim()) errors.push(`empty ${f}: ${k}`);

const files = [];
const walk = (d) => readdirSync(d).forEach((f) => {
  const p = join(d, f);
  statSync(p).isDirectory() ? walk(p) : p.endsWith(".tsx") && files.push(p);
});
walk("src");
// Allowed literal text: digits/symbols only (e.g. "404"), or brand names in ALLOW.
const ALLOW = new Set(["404"]);
// Text between tags. The closing "<" must not start a TS generic call such as api<Foo[]>(...).
const textNode = />\s*([^<>{}\n]*[A-Za-z؀-ۿ][^<>{}\n]*?)\s*<(?![A-Z][\w[\]]*>\()/g;
const attr = /\b(aria-label|title|placeholder|alt)="([^"]*[A-Za-z؀-ۿ][^"]*)"/g;
for (const f of files) {
  const src = readFileSync(f, "utf8").replace(/\/\/.*$/gm, "").replace(/\/\*[\s\S]*?\*\//g, "");
  // only inspect JSX-ish regions: skip generic type params like <T>(... by requiring return/JSX context
  for (const m of src.matchAll(textNode)) {
    const s = m[1].trim();
    // skip JS fragments: "&& (", "a ? (" etc. — a JSX expression opening, not user-facing text
    if (!s || ALLOW.has(s) || /^[&|]/.test(s) || /\($/.test(s) || /^[\w.]+\s*[=(]/.test(s) || /[;=]|=>|&&|\|\|/.test(s)) continue;
    errors.push(`${f}: hardcoded text "${s}"`);
  }
  for (const m of src.matchAll(attr)) errors.push(`${f}: hardcoded ${m[1]}="${m[2]}"`);
}
if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}
console.log(`i18n OK: ${ar.size} keys in ar/en, ${files.length} components scanned`);
