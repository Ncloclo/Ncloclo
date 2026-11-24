// Organisation du panneau : onglets rangés, cartes de graphiques vivantes,
// courbes animées, contrôles harmonisés (panneau en démonstration, sans réseau).
import { test, expect } from "@playwright/test";

const BASE = "http://127.0.0.1:8799";

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { try { localStorage.setItem("tg:wait", "0"); } catch { /* bloqué */ } });
});

function watchErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  return errors;
}

test("onglets rangés par usage, nombre de positions sur l'onglet", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  await expect(page.locator(".tab-group")).toHaveText(["Mon bot", "Marchés", "Analyse"]);
  // Le bot d'abord, puis les marchés (une page par classe d'actifs), l'analyse, Réglages à part en bas.
  await expect(page.locator(".tab")).toHaveText(["Tableau de bord", /^Positions/, "Journal", "Cryptos", "Matières premières",
    "Devises", "Indices", "Actions", "Obligations", "Graphiques", "Actualités", "Veille", "Réglages"]);
  await expect(page.locator("#tab-pos")).toHaveText("6");
  await page.locator('.tab[data-tab="positions"]').click();
  await expect(page.locator('.tab[data-tab="positions"]')).toHaveAttribute("aria-current", "page");
  await expect(page.locator("#page-title")).toHaveText("Positions et trades");
  expect(errors).toEqual([]);
});

test("cartes de graphiques vivantes : moyennes, volume, direct, lecture rapide", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await expect(cards).toHaveCount(11);
  const position = cards.nth(2);
  await expect(position.locator(".legend")).toContainText("MM 20");
  await expect(position.locator(".legend")).toContainText("MM 50");
  await expect(position.locator(".legend")).toContainText("Volume");
  await expect(position.locator(".live-tag")).toHaveText("Direct");
  await expect(position.locator(".mini-reading .tag")).toHaveCount(4);          // tendance, RSI, MACD, volume
  await expect(position.locator(".mini-reading")).toContainText("RSI");
  await expect(cards.first().locator(".mini-reading")).toContainText("Baisse max");   // capital
  await expect(cards.nth(1).locator(".mini-reading")).toContainText("Écart");         // régime BTC
  await expect(cards.first().locator(".live-tag")).toHaveCount(0);              // capital : pas un cours de marché
  expect(errors).toEqual([]);
});

test("courbes des cryptos : tracées, dernier point qui bat", async ({ page }) => {
  await page.goto(BASE + "/#assets");
  const first = page.locator("#asset-grid .asset").first();
  await expect(first.locator(".spark .spark-dot")).toHaveCount(1);
  await expect(first.locator(".spark .spark-halo")).toHaveCount(1);
  await expect(first.locator(".spark")).toHaveClass(/reveal/);
});

test("contrôles harmonisés : même hauteur pour les boutons, groupes et listes", async ({ page }) => {
  await page.goto(BASE + "/#assets");
  await expect(page.locator("#asset-grid .asset")).toHaveCount(21);
  const heights = await page.evaluate(() => {
    const h = (sel) => Math.round(document.querySelector(sel).getBoundingClientRect().height);
    return { seg: h("#page-assets .toolbar .seg"), select: h("#asset-sort"), search: h("#asset-search"), icon: h("#theme-btn") };
  });
  expect(new Set(Object.values(heights)).size, JSON.stringify(heights)).toBe(1);
});

// ---------- Zoom et période de toutes les courbes (panel/static/js/zoom.js) ----------

test("zoom des cartes et période de la page, qui glisse avec le temps", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await expect(cards).toHaveCount(11);
  await expect(page.locator("#chart-grid .zoom-bar")).toHaveCount(11);
  const first = cards.first(), range = first.locator(".zoom-range");
  await expect(range).toContainText("→");
  const before = await range.textContent();
  await first.getByRole("button", { name: "Zoomer : voir moins de temps" }).click();
  await expect(page.locator("#detail")).toBeHidden();             // la barre n'ouvre pas le détail
  await expect(range).not.toHaveText(before);
  await first.getByRole("button", { name: "Recadrer : revenir à la période choisie" }).click();
  await expect(range).toHaveText(before);
  await page.locator('#charts-windows [data-cwin="7d"]').click();
  await expect(page.locator('#charts-windows [data-cwin="7d"]')).toHaveAttribute("aria-pressed", "true");
  await expect(cards.first().locator(".zoom-range")).toContainText("· 7 j");
  await expect(cards.nth(2).locator("h2")).toContainText("· 1 h");   // unité de la période
  expect(errors).toEqual([]);
});

test("détail d'une crypto : la période choisit l'unité de temps, le zoom à la main se recadre", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  await page.locator("#chart-grid .chart-card").nth(2).click();
  await expect(page.locator("#detail-windows button")).toHaveText(["24 h", "7 j", "1 mois", "3 mois", "1 an", "Tout"]);
  await expect(page.locator('#detail-windows [data-win="all"]')).toHaveAttribute("aria-pressed", "true");
  // 24 h en bougies d'une heure : l'unité choisie convient, elle reste.
  await page.locator('#detail-windows [data-win="1d"]').click();
  await expect(page.locator('#detail-intervals [data-interval="1h"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#detail-range")).toContainText("· 24 h");
  await page.locator("#detail-zoom-in").click();                  // zoom à la main : plus de période choisie
  await expect(page.locator('#detail-windows [data-win="1d"]')).toHaveAttribute("aria-pressed", "false");
  await page.locator("#detail-fit").click();                      // ↺ : la période revient
  await expect(page.locator('#detail-windows [data-win="1d"]')).toHaveAttribute("aria-pressed", "true");
  // 1 an : trop de bougies d'une heure, l'unité passe au jour ; puis 24 h :
  // une seule bougie d'un jour, l'unité passe à 15 minutes.
  await page.locator('#detail-windows [data-win="1y"]').click();
  await expect(page.locator('#detail-intervals [data-interval="1d"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#detail-range")).toContainText("mois");
  await page.locator('#detail-windows [data-win="1d"]').click();
  await expect(page.locator('#detail-intervals [data-interval="15m"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#detail-range")).toContainText("· 24 h");
  await page.locator("#detail-close").click();
  expect(errors).toEqual([]);
});

test("tableau de bord : période et zoom de la courbe du capital", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  const bar = page.locator("#d-equity-side .zoom-bar");
  await expect(bar.locator(".zoom-windows button")).toHaveText(["24 h", "7 j", "1 mois", "3 mois", "Tout"]);
  await expect(bar.locator('[data-win="1m"]')).toHaveAttribute("aria-pressed", "true");
  await bar.locator('[data-win="7d"]').click();
  await expect(bar.locator('[data-win="7d"]')).toHaveAttribute("aria-pressed", "true");
  await expect(bar.locator(".zoom-range")).toContainText("· 7 j");
  expect(errors).toEqual([]);
});

test("cryptos : durée des courbes au choix, courbes retracées", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#assets");
  const spark = page.locator("#asset-grid .asset").first().locator(".spark");
  await expect(spark).toHaveAttribute("data-win", "2d");
  await page.locator('[data-spark="7d"]').click();
  await expect(page.locator('[data-spark="7d"]')).toHaveAttribute("aria-pressed", "true");
  await expect(spark).toHaveAttribute("data-win", "7d");
  await expect(spark.locator(".spark-dot")).toHaveCount(1);
  expect(errors).toEqual([]);
});
