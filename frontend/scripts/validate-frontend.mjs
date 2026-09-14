/**
 * Drive the real UI and check that what it SHOWS matches what the API SAID.
 *
 *   ORCA_API_TARGET=http://127.0.0.1:8021 npx vite --port 5181 --strictPort
 *   node scripts/validate-frontend.mjs
 *
 * Adjust UI/API below if you run on different ports.
 *
 * The point is not that the page renders. It is that a number a coordinator
 * would act on — the distress line, the track spacing, the ETA — is the number
 * the backend computed, and not a placeholder, a stale value, or a unit
 * conversion that went the wrong way.
 */

import { chromium } from "playwright-core";

const UI = "http://localhost:5173";
const API = "http://127.0.0.1:8010";

const results = [];
const log = (ok, name, detail = "") => {
  results.push({ ok, name, detail });
  console.log(
    `${ok ? "PASS" : "FAIL"}  ${name}${detail ? `  — ${detail}` : ""}`,
  );
};

const browser = await chromium.launch({ channel: "chrome" });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

const consoleErrors = [];
const notFound = [];
page.on("console", (m) => {
  if (m.type() === "error") {
    const at = m.location()?.url ?? "";
    consoleErrors.push(`${m.text().slice(0, 120)} @ ${at.slice(0, 120)}`);
  }
});
page.on("pageerror", (e) =>
  consoleErrors.push(`pageerror: ${String(e).slice(0, 160)}`),
);

// Record every /api call the page actually makes, so we can compare rendered
// text against the exact payload the page received rather than a fresh one.
const apiCalls = [];
page.on("response", async (res) => {
  const url = res.url();
  if (res.status() === 404) notFound.push(url.replace(UI, ""));
  if (!url.includes("/api/")) return;
  const entry = { url: url.split("/api")[1], status: res.status(), body: null };
  try {
    if (res.headers()["content-type"]?.includes("json"))
      entry.body = await res.json();
  } catch {
    /* streaming or aborted */
  }
  apiCalls.push(entry);
});

await page.goto(UI, { waitUntil: "networkidle", timeout: 90000 });
await page.waitForTimeout(3000);
log(true, "app loads", `title "${await page.title()}"`);

// ---------------------------------------------------------------- researcher
const researcherBtn = page.getByRole("button", { name: /researcher/i }).first();
const hasResearcher = (await researcherBtn.count()) > 0;
log(hasResearcher, "researcher button is in the masthead");

if (hasResearcher) {
  await researcherBtn.click();
  await page.waitForTimeout(3500);
  const body = await page.textContent("body");
  log(/catalogue|discover/i.test(body ?? ""), "researcher workspace opens");

  // The catalogue tab must actually list datasets in the page, not just link out.
  const catalogueTab = page
    .getByRole("button", { name: /^catalogue$/i })
    .first();
  if (await catalogueTab.count()) {
    await catalogueTab.click();
    await page.waitForTimeout(2500);
    const cat = apiCalls
      .filter((c) => c.url?.includes("/research/catalogue"))
      .at(-1);
    const shown = await page.textContent("body");
    if (cat?.body?.datasets?.length) {
      const names = cat.body.datasets
        .slice(0, 5)
        .map((d) => d.name ?? d.title ?? d.id);
      const rendered = names.filter((n) => n && shown.includes(n));
      log(
        rendered.length >= Math.min(3, names.length),
        "catalogue datasets are rendered in the page",
        `${rendered.length}/${names.length} of the first datasets appear; API returned ${cat.body.datasets.length}`,
      );
    } else {
      log(
        false,
        "catalogue datasets are rendered in the page",
        "no catalogue payload captured",
      );
    }
    // The download lives on an expanded dataset card, so expand one first.
    // Checking the collapsed list would have "passed" a workspace with no
    // download at all, which is the thing actually being claimed.
    const cards = page.locator("article button").first();
    if (await cards.count()) {
      await cards.click();
      await page.waitForTimeout(1500);
    }
    const dl = await page
      .getByRole("button", { name: /download|csv|xlsx|excel|export/i })
      .count();
    log(
      dl > 0,
      "researcher can download from the frontend",
      `${dl} download control(s)`,
    );

    const cite = await page
      .getByRole("button", { name: /copy citation/i })
      .count();
    log(cite > 0, "a dataset carries a copyable citation, not just a file");
  }

  // ---- build a dataset: the tab that answers "can I get this as a file" ----
  const buildTab = page.getByRole('button', { name: /build a dataset/i }).first();
  if (await buildTab.count()) {
    await buildTab.click();
    await page.waitForTimeout(2500);

    const vars = apiCalls.filter((c) => c.url?.includes('/research/build/variables')).at(-1);
    const offered = vars?.body?.variables ?? [];
    log(
      offered.length >= 13,
      'the builder offers the full variable set',
      `${offered.length} variables`,
    );

    const preview = page.getByRole('button', { name: /build and preview/i }).first();
    log((await preview.count()) > 0, 'the builder has a preview control');

    if (await preview.count()) {
      await preview.click();
      await page.waitForTimeout(30000);

      const built = apiCalls.filter((c) => c.url?.includes('/research/build')).at(-1);
      log(built?.status === 200, 'the dataset builds', `HTTP ${built?.status}`);

      if (built?.body?.rows?.length) {
        const rows = built.body.rows;
        const summary = built.body.summary;
        const table = await page.locator('table').count();
        log(table > 0, 'the rows are rendered as a table in the page');

        // The preview must show real values, not just headers.
        const firstVar = summary.variables[0];
        const firstValue = rows.find((r) => r[firstVar] != null)?.[firstVar];
        const shown = await page.textContent('body');
        log(
          firstValue != null && shown.includes(String(firstValue)),
          'a real value from the payload is visible in the table',
          `${firstVar} = ${firstValue}`,
        );

        // Fill rate: the point of the whole change.
        const filled = summary.variables.filter(
          (v) => (summary.missing_by_variable[v] ?? 0) < summary.rows,
        );
        log(
          filled.length >= Math.min(3, summary.variables.length),
          'the requested columns actually contain data',
          `${filled.length}/${summary.variables.length} columns non-empty over ${summary.rows} rows`,
        );

        // Provenance must cover every delivered column, not just the ERDDAP one.
        const attributed = new Set(
          (summary.datasets ?? []).flatMap((d) => d.columns ?? []),
        );
        const unattributed = summary.variables.filter((v) => !attributed.has(v));
        log(
          unattributed.length === 0,
          'every delivered column is attributed to a source',
          unattributed.length ? `unattributed: ${unattributed.join(', ')}` : 'all attributed',
        );
        log(
          (summary.datasets ?? []).length >= 2,
          'more than one provider is cited',
          (summary.datasets ?? []).map((d) => d.provider).join(' + '),
        );
      }

      // And the actual file.
      const [dl] = await Promise.all([
        page.waitForEvent('download', { timeout: 60000 }).catch(() => null),
        page.getByRole('button', { name: /^CSV$/i }).first().click(),
      ]);
      log(dl !== null, 'the CSV downloads', dl ? await dl.suggestedFilename() : 'no download event');
      if (dl) {
        const path = await dl.path();
        const text = path ? (await import('node:fs')).readFileSync(path, 'utf8') : '';
        log(
          text.includes('# source:') && text.split(String.fromCharCode(10)).length > 5,
          'the downloaded CSV carries its provenance preamble',
          `${text.split(String.fromCharCode(10)).length} lines`,
        );
      }
    }
  } else {
    log(false, 'the researcher workspace has a dataset builder tab');
  }

  // Escape must work: it is a full-screen overlay and the close button is one
  // small target in a corner. If this leaves the overlay up, every check after
  // it fails on an intercepted click, which is exactly what happened.
  await page.keyboard.press("Escape");
  await page.waitForTimeout(1200);
  const stillOpen = await page.locator(".fixed.inset-0.z-50").count();
  log(stillOpen === 0, "Escape closes the researcher workspace");
  if (stillOpen) {
    await page
      .getByRole("button", { name: /close the researcher workspace/i })
      .first()
      .click();
    await page.waitForTimeout(1000);
  }
}

// ------------------------------------------------ harbour advisory board
const harbourBtn = page.getByRole('button', { name: /^harbours$/i }).first();
log((await harbourBtn.count()) > 0, 'harbours button is in the masthead');

if (await harbourBtn.count()) {
  await harbourBtn.click();
  await page.waitForTimeout(25000);

  const boardCall = apiCalls.filter((c) => c.url?.includes('/harbours/board')).at(-1);
  log(boardCall?.status === 200, 'the harbour board builds', `HTTP ${boardCall?.status}`);

  if (boardCall?.body?.rows) {
    const b = boardCall.body;
    const shown = await page.textContent('body');

    log(
      b.harbours_assessed >= 50,
      'the board covers the whole coast',
      `${b.harbours_assessed} harbours, ${b.classes.length} boat classes`,
    );

    // Coastal order is the whole point — a sortable grid would destroy it.
    const coasts = b.rows.map((r) => r.coast);
    const firstEast = coasts.indexOf('east');
    log(
      firstEast === -1 || !coasts.slice(firstEast).includes('west'),
      'rows are in coastal order, west block then east',
      `${coasts.filter((c) => c === 'west').length} west then ${coasts.filter((c) => c === 'east').length} east`,
    );

    // The advisory sentence must be on screen, not just the grid.
    const trad = b.stretches['IND-TRAD'] ?? [];
    const longest = [...trad].sort((a, b2) => b2.harbours.length - a.harbours.length)[0];
    log(
      longest != null && shown.includes(longest.sentence),
      'the advisory sentence is rendered',
      longest?.sentence,
    );

    // A stretch must never span both coasts: a swell on one side does not
    // apply to the other, and an advisory that said so would be wrong.
    const spanning = trad.filter((st) => {
      const rows = b.rows.filter((r) => st.harbours.includes(r.name));
      return new Set(rows.map((r) => r.coast)).size > 1;
    });
    log(spanning.length === 0, 'no stretch spans both coasts', `${trad.length} stretches checked`);

    // A named harbour and its verdict must appear in the grid.
    const sample = b.rows[0];
    log(shown.includes(sample.name), 'harbour rows are rendered', sample.name);

    // The verdicts must come from the rule engine, never a model.
    const allVerdicts = b.rows.flatMap((r) => Object.values(r.verdicts).map((v) => v.verdict));
    const legal = allVerdicts.every((v) =>
      ['GO', 'CAUTION', 'NO-GO', 'UNVERIFIABLE'].includes(v),
    );
    log(legal, 'every cell carries a legal verdict', `${allVerdicts.length} cells`);

    const unverifiable = allVerdicts.filter((v) => v === 'UNVERIFIABLE').length;
    log(
      unverifiable / allVerdicts.length < 0.2,
      'the board is not mostly UNVERIFIABLE',
      `${unverifiable}/${allVerdicts.length} cells unverifiable`,
    );

    log(
      shown.includes('Not an official advisory') || shown.includes('legal force'),
      'the board says it is not an official advisory',
    );

    console.log(
      `
  [context] ${JSON.stringify(b.counts['IND-TRAD'])} traditional, ` +
        `${JSON.stringify(b.counts['IND-MECH-S'])} small mechanised
`,
    );
  }

  await page.keyboard.press('Escape');
  await page.waitForTimeout(600);
  const closeBoard = page.getByRole('button', { name: /close the harbour advisory board/i }).first();
  if (await closeBoard.count()) await closeBoard.click();
  await page.waitForTimeout(1200);
}

// ---------------------------------------------------------------- distress
// Pick a sea point first: the panel refuses to act without one, which is itself
// worth checking.
await page.mouse.click(1050, 520);
await page.waitForTimeout(6000);

// The rail exposes role="tab", not "button" — mutually-exclusive panels, which
// is the correct ARIA for what it is. Querying for a button silently found
// nothing and reported the feature missing.
const distressRail = page.getByRole("tab", { name: /^Distress$/i }).first();
const hasDistress = (await distressRail.count()) > 0;
log(hasDistress, "distress button is on the rail");

if (hasDistress) {
  await distressRail.click();
  await page.waitForTimeout(1500);

  const raise = page
    .getByRole("button", { name: /raise distress response/i })
    .first();
  const canRaise = (await raise.count()) > 0;
  log(canRaise, "distress panel opens with a sea point selected");

  if (canRaise) {
    await raise.click();
    // The transit routes a live lattice; give it room.
    await page.waitForTimeout(45000);

    const alert = apiCalls
      .filter((c) => c.url?.includes("/distress/alert"))
      .at(-1);
    log(
      alert?.status === 200,
      "POST /distress/alert succeeds",
      `HTTP ${alert?.status}`,
    );

    if (alert?.body) {
      const d = alert.body;
      const shown = await page.textContent("body");

      log(
        shown.includes("1554"),
        "the distress number is on screen",
        "NMSAR nationwide line",
      );

      log(
        shown.includes(d.first_call.nearest_centre),
        "the nearest rescue centre is named",
        d.first_call.nearest_centre,
      );

      // A real telephone number from the NMSAR plan, not a placeholder.
      const landline = d.notify[0].telephone[1];
      log(
        !!landline && shown.includes(landline),
        "a real NMSAR landline is rendered",
        landline,
      );

      // Rendered track spacing must equal the computed one to the shown precision.
      const spacing = d.search_plan.track_spacing_km.toFixed(2);
      log(
        shown.includes(`${spacing} km`),
        "track spacing shown matches the computed value",
        `${spacing} km`,
      );

      const hours = d.search_plan.search_hours.toFixed(1);
      log(
        shown.includes(`${hours} h`),
        "search time shown matches",
        `${hours} h`,
      );

      const pod = `${(d.search_plan.probability_of_detection * 100).toFixed(0)}%`;
      log(shown.includes(pod), "detection odds shown match", pod);

      const area = d.search_plan.area_km2.toFixed(0);
      log(
        shown.includes(`${area} km²`),
        "search area shown matches",
        `${area} km²`,
      );

      log(
        shown.includes(d.search_plan.pattern.name),
        "the IAMSAR pattern is named",
        `${d.search_plan.pattern.code} ${d.search_plan.pattern.name}`,
      );

      if (d.transit?.total_hours != null) {
        const eta = d.transit.total_hours.toFixed(1);
        log(
          shown.includes(`${eta} h`),
          "transit ETA shown matches",
          `${eta} h`,
        );
      }

      log(
        shown.includes("notified nobody") ||
          shown.includes("assembles the call"),
        'the "not a dispatch" disclaimer is visible',
      );

      // The datum must NOT be the headline. It should appear, but not in the
      // largest type, and must carry its warning.
      log(
        shown.includes("Searching the datum alone"),
        "the datum carries its warning rather than reading as an answer",
      );

      // Sanity on the numbers themselves, independent of rendering.
      const sp = d.search_plan;
      const swept =
        sp.track_spacing_nm * sp.unit.search_speed_kn * sp.search_hours;
      log(
        Math.abs(swept - sp.area_sq_nm) / sp.area_sq_nm < 0.03,
        "A = S x V x T holds in the payload the page received",
        `swept ${swept.toFixed(1)} vs area ${sp.area_sq_nm} sq NM`,
      );

      console.log(
        `\n  [context] ${d.incident.object_class.label}, ${d.incident.hours_since_last_known} h` +
          ` · wind ${d.conditions_at_datum.wind_kn} kn, wave ${d.conditions_at_datum.wave_m} m,` +
          ` vis ${d.conditions_at_datum.visibility_km?.toFixed(1)} km` +
          `\n  [context] transit ${d.transit.routed ? "routed" : "FALLBACK"}` +
          ` ${d.transit.distance_nm} NM, limits: ${sp.limits.length}\n`,
      );
    }

    await page.screenshot({ path: "distress-panel.png" });
  }
}

// ---------------------------------------------------------------- hygiene
const realErrors = consoleErrors.filter(
  (e) => !/favicon|ResizeObserver|Download the React DevTools/i.test(e),
);
log(
  realErrors.length === 0,
  "no console errors",
  realErrors.slice(0, 3).join(" | ") || "clean",
);
if (notFound.length)
  console.log(`  [404s] ${[...new Set(notFound)].slice(0, 6).join(", ")}`);

const failedApi = apiCalls.filter((c) => c.status >= 400);
log(
  failedApi.length === 0,
  "no failing API calls",
  failedApi
    .map((c) => `${c.status} ${c.url}`)
    .slice(0, 4)
    .join(", ") || "clean",
);

await browser.close();

const failed = results.filter((r) => !r.ok);
console.log(
  `\n${results.length - failed.length}/${results.length} checks passed`,
);
if (failed.length) {
  console.log("FAILED:");
  for (const f of failed)
    console.log(`  - ${f.name}${f.detail ? ` (${f.detail})` : ""}`);
  process.exit(1);
}
