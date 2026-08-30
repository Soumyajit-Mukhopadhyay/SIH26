/**
 * Render a local HTML document to PDF.
 *
 * Chromium's own print engine rather than a PDF library: the document is styled
 * with print CSS (`@page`, page-break rules, running page numbers), and the
 * browser is the only thing that implements those faithfully. A PDF library
 * would mean maintaining a second, divergent layout.
 *
 *   node docs/make-pdf.mjs docs/ORCA_Architecture_Explained.html
 */
import process from 'node:process';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

process.env.PLAYWRIGHT_BROWSERS_PATH ??= 'D:/orca-playwright';

// Playwright is a frontend dev dependency, and Node resolves modules relative to
// the SCRIPT's directory rather than the working directory — so a bare
// `import 'playwright'` from docs/ fails no matter where you run it from.
// Resolving through the frontend's own package.json says exactly which
// installation is being used, which is better than a NODE_PATH the caller has to
// remember to set.
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const fromFrontend = createRequire(path.join(repoRoot, 'frontend', 'package.json'));
// `require`, not a dynamic import: Playwright's entry point is CommonJS, and
// importing it through a file URL hands back a namespace whose named exports are
// not detected — `chromium` comes out undefined.
const { chromium } = fromFrontend('playwright');

const input = process.argv[2];
if (!input) {
  console.error('usage: node docs/make-pdf.mjs <file.html> [out.pdf]');
  process.exit(1);
}
const source = path.resolve(input);
const out = path.resolve(process.argv[3] ?? source.replace(/\.html$/i, '.pdf'));

const browser = await chromium.launch();
const page = await browser.newPage();

const problems = [];
page.on('pageerror', (e) => problems.push(`[pageerror] ${e.message}`));
page.on('requestfailed', (r) => problems.push(`[failed] ${r.url().slice(0, 120)}`));

await page.goto(pathToFileURL(source).href, { waitUntil: 'networkidle' });
// Fonts have to be resolved before layout is measured, or the pagination in the
// PDF differs from what the CSS was written against.
await page.evaluate(() => document.fonts.ready);

await page.pdf({
  path: out,
  format: 'A4',
  printBackground: true,
  // The margins live in the stylesheet's `@page` rule; setting them here as well
  // would add them twice.
  margin: { top: '20mm', bottom: '18mm', left: '18mm', right: '18mm' },
  displayHeaderFooter: true,
  headerTemplate: '<div></div>',
  footerTemplate: `
    <div style="width:100%;font:8pt 'Segoe UI',Arial,sans-serif;color:#8a929c;
                padding:0 18mm;display:flex;justify-content:space-between;">
      <span>ORCA · Marine EcOsystem Reasoning with Collaborative Agents</span>
      <span class="pageNumber"></span>
    </div>`,
});

const pages = await page.evaluate(() => document.querySelectorAll('section').length);
console.log(`wrote ${out}  (${pages} sections)`);
if (problems.length) console.log('--- problems ---\n' + problems.join('\n'));
await browser.close();
