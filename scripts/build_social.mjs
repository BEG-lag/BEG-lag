// Generate 1280x640 social preview images (the card GitHub shows when a repo link is shared).
// Usage: node scripts/build_social.mjs
// Needs Google Chrome and playwright-core (set PLAYWRIGHT_CORE to its folder if it is not resolvable).
// Reads scripts/projects.json and writes assets/social/<slug>.png.

import { readFileSync, mkdirSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE ?? "playwright-core");
const fonts = process.env.FONTSOURCE; // optional folder with @fontsource packages

const glyphs = {
  "mcp-dof": ["{ }", "mono"],
  "leyes-mx": ["§", "serif"],
  "dof-diario": ["DOF", "mono"],
  "finiquito-mx": ["$", "serif"],
  "hoy-circulo": ["7·8", "mono"],
  "facturas-cfdi": ["XML", "mono"],
  "segundo-cerebro": ["[[ ]]", "mono"],
  "tema-clawds": ["64", "mono"],
  "portafolio-ia": ["IA", "serif"],
};

const esc = (s) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function face(family, file, style = "normal", weight = "400") {
  if (!fonts) return "";
  return `@font-face{font-family:"${family}";src:url("${pathToFileURL(join(fonts, file))}") format("woff2");font-style:${style};font-weight:${weight}}`;
}

function html(p) {
  const [glyph, kind] = glyphs[p.slug] ?? ["{ }", "mono"];
  return `<!doctype html><html><head><meta charset="utf-8"><style>
${face("JetBrains Mono", "jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2")}
${face("JetBrains Mono", "jetbrains-mono/files/jetbrains-mono-latin-700-normal.woff2", "normal", "700")}
*{margin:0;box-sizing:border-box}
body{width:1280px;height:640px;background:#0a0c10;color:#eceee6;overflow:hidden;position:relative;
  font-family:Inter,ui-sans-serif,system-ui,sans-serif}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(236,238,230,.045) 1px,transparent 1px),
  linear-gradient(90deg,rgba(236,238,230,.045) 1px,transparent 1px);background-size:40px 40px}
.glow{position:absolute;right:-60px;top:70px;width:560px;height:560px;border-radius:50%;
  background:radial-gradient(circle,${p.accent}38 0%,${p.accent}00 68%)}
.ring{position:absolute;right:120px;top:170px;width:300px;height:300px;border-radius:50%;
  border:2px dashed ${p.accent}55;display:flex;align-items:center;justify-content:center}
.glyph{font-size:${kind === "serif" ? 210 : glyph.length > 3 ? 78 : 104}px;line-height:1;color:${kind === "serif" ? "#eceee6" : p.accent};
  font-family:${kind === "serif" ? '"Cormorant Garamond",Georgia,serif;font-style:italic' : '"JetBrains Mono",Menlo,monospace;font-weight:700'}}
.bar{position:absolute;left:0;top:0;width:100%;height:8px;background:${p.accent}}
.wrap{position:absolute;left:84px;top:96px;width:720px}
.who{font-family:"JetBrains Mono",Menlo,monospace;font-size:24px;color:#969dab}
.kicker{margin-top:44px;font-family:"JetBrains Mono",Menlo,monospace;font-size:22px;letter-spacing:4px;color:${p.accent};text-transform:uppercase}
.kicker::before{content:"";display:inline-block;width:12px;height:12px;border-radius:50%;background:${p.accent};margin-right:16px;vertical-align:2px}
h1{margin-top:14px;font-family:"JetBrains Mono",Menlo,monospace;font-weight:700;letter-spacing:-2px;white-space:nowrap;
  font-size:${Math.min(84, Math.floor(700 / (0.58 * p.title.length)))}px}
p{margin-top:22px;font-size:29px;line-height:1.38;color:#b9bec8}
.tags{position:absolute;left:84px;bottom:70px;display:flex;gap:12px}
.tag{font-family:"JetBrains Mono",Menlo,monospace;font-size:20px;padding:8px 18px;border-radius:999px;border:1.5px solid ${p.accent}88}
.foot{position:absolute;right:84px;bottom:78px;font-family:"JetBrains Mono",Menlo,monospace;font-size:20px;color:#969dab}
</style></head><body>
<div class="grid"></div><div class="glow"></div><div class="bar"></div>
<div class="ring"><span class="glyph">${esc(glyph)}</span></div>
<div class="wrap"><div class="who">BEG-lag /</div><div class="kicker">${esc(p.kicker)}</div>
<h1>${esc(p.title)}</h1><p>${esc(p.desc)}</p></div>
<div class="tags">${p.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>
<div class="foot">Benjamín Esponda García · pivotek.mx</div>
</body></html>`;
}

const projects = JSON.parse(readFileSync(join(root, "scripts/projects.json"), "utf8"));
const out = join(root, "assets/social");
mkdirSync(out, { recursive: true });
const browser = await chromium.launch(process.env.CHROME_PATH ? { executablePath: process.env.CHROME_PATH } : { channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1280, height: 640 } });
for (const p of projects) {
  await page.setContent(html(p), { waitUntil: "load" });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: join(out, `${p.slug}.png`) });
  console.log(`assets/social/${p.slug}.png`);
}
await browser.close();
