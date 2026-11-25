// Page « Courtiers » du panneau, dans un vrai navigateur, en mode
// démonstration (sans réseau) : comptes de démonstration, plan miroir à
// blanc, cours comparés à Binance et annuaire filtrable.
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

test("courtiers : comptes démo, plan miroir, cours comparés et annuaire", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#brokers");
  const sec = page.locator("#page-brokers");
  await expect(page.locator("#page-title")).toHaveText("Courtiers");
  await expect(sec.locator('[data-acc="mt5"] .acc-state')).toHaveText("démo branchée");
  await expect(sec.locator('[data-acc="mt5"]')).toContainText("MetaQuotes-Demo");
  await expect(sec.locator('[data-acc="alpaca"] .acc-state')).toHaveText("non branché");
  await expect(sec.locator('[data-acc="alpaca"] code')).toHaveText("python trendguard_bot.py courtiers set-key alpaca");
  await expect(sec.locator('[data-acc="testnets"]')).toContainText("Bybit");
  await expect(sec.locator('[data-bk="accounts"] strong')).toHaveText("2");
  // Plan miroir, à blanc : quatre positions, des lots arrondis au pas du courtier
  const mirror = sec.locator("#br-mirror tbody tr");
  await expect(mirror).toHaveCount(4);
  await expect(mirror.nth(1)).toContainText("US500 · 0,30 lot");
  await expect(mirror.first()).toContainText("trop petit pour ce compte");
  // Cours comparés : les 21 cryptos de la démonstration, ZEC en écart notable
  const prices = sec.locator("#br-prices tbody tr");
  await expect(prices).toHaveCount(21);
  await sec.locator('[data-brgap="gaps"]').click();
  await expect(prices).toHaveCount(1);
  await expect(prices.first()).toContainText("ZEC");
  await expect(sec.locator('[data-bk="gap"] .sub')).toContainText("ZEC");
  // Annuaire : type, plateformes avec démo ou testnet, recherche
  const rows = sec.locator("#br-directory tbody tr");
  await sec.locator('[data-brkind="Forex et CFD (MetaTrader 5)"]').click();
  await expect(rows).toHaveCount(15);
  await expect(sec.locator('#br-directory a[href="https://www.exness.com"]')).toHaveAttribute("rel", "noopener noreferrer");
  await sec.locator('[data-brkind="Cryptos"]').click();
  await sec.locator(".br-search").fill("kraken");
  await expect(rows).toHaveCount(2);
  await sec.locator(".br-demo").click();                                          // testnet seulement
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("Kraken Futures");
  await sec.locator(".br-demo").click();
  await sec.locator(".br-search").fill("");
  await sec.locator('[data-brkind="BRVM (Abidjan)"]').click();
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("SGI agréées");
  expect(errors).toEqual([]);
});

test("téléphone : la page Courtiers tient dans l'écran", async ({ page }) => {
  const errors = watchErrors(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(BASE + "/#brokers");
  await expect(page.locator("#br-prices tbody tr").first()).toBeVisible();
  await expect(page.locator("#br-directory tbody tr").first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)).toBeLessThanOrEqual(0);
  expect(errors).toEqual([]);
});
