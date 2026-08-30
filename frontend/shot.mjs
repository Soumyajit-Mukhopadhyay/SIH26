/**
 * Screenshot the console, and report every console error and failed request.
 *
 * A clean typecheck says nothing about whether the globe painted, so this is the
 * loop for frontend work: render it, look at it, and read what the browser
 * complained about.
 *
 *   node scripts/shot.mjs [outfile] [--click x,y] [--wait ms] [--url u]
 */
import { chromium } from 'playwright';
import process from 'node:process';

const args = process.argv.slice(2);
const flag = (name, fallback) => {
  const i = args.indexOf(`--${name}`);
  return i === -1 ? fallback : args[i + 1];
};
const out = args.find((a) => !a.startsWith('--') && a.endsWith('.png')) ?? 'shot.png';
const url = flag('url', 'http://localhost:5173/');
const wait = Number(flag('wait', 9000));
const click = flag('click', null);

const browser = await chromium.launch({
  args: ['--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
});
const page = await browser.newPage({ viewport: { width: 1680, height: 945 }, deviceScaleFactor: 1 });

const errors = [];
const failed = [];
page.on('console', (m) => {
  if (m.type() === 'error' || m.type() === 'warning') errors.push(`[${m.type()}] ${m.text()}`);
});
page.on('pageerror', (e) => errors.push(`[pageerror] ${e.message}`));
page.on('requestfailed', (r) => failed.push(`${r.failure()?.errorText} ${r.url().slice(0, 120)}`));

await page.goto(url, { waitUntil: 'networkidle', timeout: 60000 }).catch((e) => errors.push(`[goto] ${e.message}`));

// Report what WebGL the browser actually gave us — the first thing to know when
// a globe is black.
const gl = await page.evaluate(() => {
  const c = document.createElement('canvas');
  const ctx = c.getContext('webgl2');
  if (!ctx) return { webgl2: false };
  const dbg = ctx.getExtension('WEBGL_debug_renderer_info');
  return {
    webgl2: true,
    renderer: dbg ? ctx.getParameter(dbg.UNMASKED_RENDERER_WEBGL) : 'unknown',
    canvases: document.querySelectorAll('canvas').length,
  };
});

await page.waitForTimeout(wait);

if (click) {
  const [x, y] = click.split(',').map(Number);
  await page.mouse.click(x, y);
  await page.waitForTimeout(6000);
}

await page.screenshot({ path: out, fullPage: false });
await browser.close();

console.log('webgl:', JSON.stringify(gl));
if (errors.length) {
  console.log(`\n--- ${errors.length} console message(s) ---`);
  for (const e of [...new Set(errors)].slice(0, 14)) console.log(' ', e.slice(0, 220));
}
if (failed.length) {
  console.log(`\n--- ${failed.length} failed request(s) ---`);
  for (const f of [...new Set(failed)].slice(0, 10)) console.log(' ', f);
}
console.log(`\nwrote ${out}`);
