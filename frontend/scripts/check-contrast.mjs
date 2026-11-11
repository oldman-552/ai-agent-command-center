// WCAG 2.2 contrast check for design-token pairs actually used in the UI, light and dark.
// Text pairs need 4.5:1 (AA normal text). Non-text UI (icons, focus ring, borders of inputs) need 3:1.
import { readFileSync } from "node:fs";

const css = readFileSync("src/app/globals.css", "utf8");
const block = (sel) => {
  const m = css.match(new RegExp(`${sel}\\s*\\{([^}]*)\\}`));
  return Object.fromEntries([...m[1].matchAll(/--([\w-]+):\s*(#[0-9a-fA-F]{6})/g)].map((x) => [x[1], x[2]]));
};
const themes = { light: block(":root"), dark: block("\\.dark") };

const lum = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const ratio = (a, b) => {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
};
// Blend fg over bg at alpha (for tinted backgrounds like bg-primary/10)
const mix = (fg, bg, a) => "#" + [1, 3, 5].map((i) => {
  const v = Math.round(parseInt(fg.slice(i, i + 2), 16) * a + parseInt(bg.slice(i, i + 2), 16) * (1 - a));
  return v.toString(16).padStart(2, "0");
}).join("");

// [foreground token, background token or {tint: token, alpha, over}, minimum, where used]
const pairs = [
  ["foreground", "background", 4.5, "page text"],
  ["card-foreground", "card", 4.5, "card text"],
  ["muted-foreground", "background", 4.5, "secondary text on page"],
  ["muted-foreground", "card", 4.5, "secondary text in cards"],
  ["muted-foreground", "muted", 4.5, "secondary text on muted"],
  ["primary-foreground", "primary", 4.5, "primary button"],
  ["secondary-foreground", "secondary", 4.5, "secondary button"],
  ["primary", "card", 4.5, "links / active nav text"],
  ["primary", { tint: "primary", alpha: 0.1, over: "card" }, 4.5, "active nav item text on tint"],
  ["destructive", "card", 4.5, "error text"],
  ["destructive", { tint: "destructive", alpha: 0.1, over: "background" }, 4.5, "error alert text"],
  ["destructive-foreground", "destructive", 4.5, "destructive button"],
  ["foreground", "muted", 4.5, "text of neutral status chips (the status colour tints only the icon)"],
  ["success", "card", 4.5, "figures in the success colour (workforce report)"],
  ["warning", "card", 4.5, "figures in the warning colour (workforce report)"],
  ["success", "muted", 3, "success icon in a neutral chip"],
  ["warning", "muted", 3, "warning icon in a neutral chip"],
  ["success", { tint: "success", alpha: 0.1, over: "card" }, 3, "success icon on tint"],
  ["warning", { tint: "warning", alpha: 0.1, over: "card" }, 3, "warning icon on tint"],
  ["warning", "card", 3, "warning icon"],
  ["ring", "background", 3, "focus ring"],
  ["ring", "card", 3, "focus ring in cards"],
  ["input", "card", 3, "form control border (WCAG 1.4.11)"],
  ["input", "background", 3, "form control border on page"],
];

let failed = 0;
for (const [name, t] of Object.entries(themes)) {
  console.log(`\n${name.toUpperCase()}`);
  for (const [fg, bg, min, where] of pairs) {
    const bgHex = typeof bg === "string" ? t[bg] : mix(t[bg.tint], t[bg.over], bg.alpha);
    const r = ratio(t[fg], bgHex);
    const ok = r >= min;
    if (!ok) failed++;
    console.log(`${ok ? "PASS" : "FAIL"} ${r.toFixed(2).padStart(5)}:1 (min ${min}) ${fg} on ${typeof bg === "string" ? bg : bg.tint + "/" + bg.alpha} — ${where}`);
  }
}
if (failed) { console.error(`\n${failed} contrast failures`); process.exit(1); }
console.log("\nAll contrast checks passed");
