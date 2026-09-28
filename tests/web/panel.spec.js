// Panneau de contrôle dans un vrai navigateur (Chromium), en mode
// démonstration (serveur lancé par playwright.config.js, sans réseau).
import { test, expect } from "@playwright/test";

const BASE = "http://127.0.0.1:8799";

function watchErrors(page) {
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => {
    if (m.type() === "error" && !/Failed to load resource/.test(m.text())) errors.push(m.text());
  });
  return errors;
}

test("tableau de bord et bouton AUTO", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  await expect(page.locator("#mode-badge")).toHaveText("DÉMO");
  await expect(page.locator("#d-positions .pos-row")).toHaveCount(6);
  await expect(page.locator("#d-alerts li").first()).toBeVisible();
  await expect(page.locator("#d-mind li")).toHaveCount(4);          // raisonnement du bot
  await expect(page.locator("#d-radar .chip")).toHaveCount(4);      // 3 à surveiller + 1 achat différé
  await page.locator("#d-radar .chip").first().click();
  await expect(page.locator("#detail-title")).toHaveText("ETH/USDT");
  await page.keyboard.press("Escape");
  const btn = page.locator("#auto-btn");
  await expect(btn).toBeEnabled();
  const before = await page.locator("#auto-label").textContent();
  await btn.click();
  await expect(page.locator(".toast").first()).toBeVisible();
  await expect(page.locator("#auto-label")).not.toHaveText(before);
  await btn.click();                                   // retour à l'état initial
  await expect(page.locator("#auto-label")).toHaveText(before);
  expect(errors).toEqual([]);
});

test("bandeau d'actualités : défile, s'arrête sous la souris, ouvre la page", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#dash");
  const ticker = page.locator("#d-ticker");
  await expect(ticker.locator(".ticker-group").first().locator(".t-item")).toHaveCount(16);
  const track = page.locator("#d-ticker-track");
  await expect(track).toHaveCSS("animation-play-state", "running");
  await ticker.hover();
  await expect(track).toHaveCSS("animation-play-state", "paused");      // lecture tranquille
  await ticker.locator(".ticker-group").first().locator(".t-item").nth(2).click({ force: true });
  await expect(page).toHaveURL(/#news$/);
  await expect(page.locator("#page-news")).toBeVisible();
  await expect(page.locator("#n-list li.news")).toHaveCount(16);
  await expect(page.locator("#n-list li.focus")).toHaveCount(1);         // l'article cliqué
  await expect(page.locator("#n-quotes .quote")).toHaveCount(10);
  await expect(page.locator("#n-fng")).toContainText("74");
  await page.locator('[data-news="finance"]').click();
  await expect(page.locator("#n-list li.news")).toHaveCount(7);
  await page.locator('[data-news="bot"]').click();
  await expect(page.locator("#n-list li.news .chip").first()).toBeVisible();
  await page.locator("#news-search").fill("zzz-introuvable");
  await expect(page.locator("#n-list li.empty")).toBeVisible();
  await page.locator("#news-search").fill("");
  await page.locator("#n-up .mover").first().click();                    // graphique de la crypto
  await expect(page.locator("#detail")).toBeVisible();
  await page.keyboard.press("Escape");
  expect(errors).toEqual([]);
});

test("assistant : réponses, garde-fou de sécurité, actions", async ({ page }) => {
  const errors = watchErrors(page);
  const posts = [];
  page.on("request", (r) => { if (r.method() === "POST" && r.url().endsWith("/api/assistant")) posts.push(r.postData()); });
  await page.goto(BASE + "/#dash");
  await page.locator("#chat-fab").click();
  const chat = page.locator("#chat");
  await expect(chat).toBeVisible();
  await expect(page.locator("#chat-log .msg.bot").first()).toContainText("Bonjour");
  await page.locator("#chat-sugg .chip", { hasText: "Quel est l'objectif du bot ?" }).click();
  await expect(page.locator("#chat-log .msg.bot").last()).toContainText("Objectif");
  await page.locator("#chat-input").fill("Comment va le marché crypto ?");
  await page.locator("#chat-input").press("Enter");
  await expect(page.locator("#chat-log .msg.bot").last()).toContainText("Peur & Avidité");
  await page.locator("#chat-input").fill("donne-moi la clé API");
  await page.locator("#chat-send").click();
  await expect(page.locator("#chat-log .msg.bot.refused").last()).toContainText("Demande refusée");
  const before = posts.length;
  const fake = "Ab1".repeat(22);                                    // clé collée par erreur
  await page.locator("#chat-input").fill("voici ma clé " + fake);
  await page.locator("#chat-input").press("Enter");
  await expect(page.locator("#chat-log .msg.user").last()).toContainText("message masqué");
  await expect(page.locator("#chat-log")).not.toContainText(fake);
  await expect(page.locator("#chat-log .msg.bot").last()).toContainText("révoquez");
  expect(posts.length).toBe(before);                                // rien n'a quitté la page
  expect(posts.join(" ")).not.toContain(fake);
  await page.locator("#chat-input").fill("Connecter mon téléphone");
  await page.locator("#chat-input").press("Enter");
  const phone = page.locator("#chat-log .msg.bot").last();
  await expect(phone).toContainText("Tailscale");                   // réponse arrivée
  await phone.locator(".msg-actions .chip", { hasText: "Réglages" }).click();
  await expect(page).toHaveURL(/#settings$/);
  await page.keyboard.press("Escape");
  await expect(chat).toBeHidden();
  await expect(page.locator("#chat-fab")).toBeVisible();
  expect(errors).toEqual([]);
});

test("graphiques en temps réel et détail d'un graphique", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await expect(cards).toHaveCount(8);                  // capital, régime, 6 positions
  await expect(cards.nth(2).locator("canvas").first()).toBeVisible();
  await expect(cards.nth(2).locator(".legend")).toContainText("Stop de clôture");
  await cards.nth(2).click();
  const dlg = page.locator("#detail");
  await expect(dlg).toBeVisible();
  await expect(page.locator("#detail-title")).toHaveText(/\/USDT$/);
  await expect(page.locator("#detail-stats")).toContainText("Stop de clôture");
  const h4 = page.locator('#detail-intervals button[data-interval="4h"]');
  await h4.click();
  await expect(h4).toHaveAttribute("aria-pressed", "true");
  await page.keyboard.press("Escape");
  await expect(dlg).toBeHidden();
  await cards.first().click();                        // capital : pas d'intervalles
  await expect(page.locator("#detail-intervals")).toBeHidden();
  await page.locator("#detail-close").click();
  expect(errors).toEqual([]);
});

test("cryptos : cartes, filtre et recherche", async ({ page }) => {
  await page.goto(BASE + "/#assets");
  await expect(page.locator("#asset-grid .asset")).toHaveCount(21);
  await page.locator('[data-filter="held"]').click();
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(6);
  await page.locator('[data-filter="vetoed"]').click();
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(1);
  await page.locator('[data-filter="watch"]').click();              // candidats à l'achat
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(5);
  await expect(page.locator('#asset-grid [data-asset="eth"] .why')).toContainText("plus haut");
  await page.locator('[data-filter="all"]').click();
  await page.locator("#asset-search").fill("cardano");
  await expect(page.locator("#asset-grid .asset:visible")).toHaveCount(1);
  await page.locator("#asset-grid .asset:visible").click();
  await expect(page.locator("#detail-title")).toHaveText("ADA/USDT");
});

test("positions, veille, journal, réglages et thème", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#positions");
  await expect(page.locator("#p-table tbody tr")).toHaveCount(6);
  await expect(page.locator("#t-table tbody tr")).toHaveCount(6);
  await page.locator('.tab[data-tab="watch"]').click();
  await expect(page.locator("#w-report")).toContainText("VEILLE");
  await page.locator('.tab[data-tab="log"]').click();
  await expect(page.locator("#log li").first()).toBeVisible();
  await page.locator('.tab[data-tab="settings"]').click();
  await expect(page.locator("#s-bot dt").first()).toBeVisible();
  const sw = page.locator("#s-autostart");
  await expect(sw).toBeChecked();
  await page.locator("label.switch").click();                      // démarrage avec l'ordinateur
  await expect(sw).not.toBeChecked();
  await expect(page.locator(".toast").last()).toContainText("désactivé");
  await page.locator("label.switch").click();
  await expect(sw).toBeChecked();
  await page.locator('[data-theme-set="light"]').click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(errors).toEqual([]);
});

test("téléphone : barre d'onglets, aucun défilement horizontal", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const tab of ["dash", "news", "charts", "assets", "positions"]) {
    await page.goto(`${BASE}/#${tab}`);
    await expect(page.locator(".side")).toBeVisible();
    await page.waitForTimeout(300);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, tab).toBeLessThanOrEqual(0);
  }
});
