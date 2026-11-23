// Graphique détaillé du panneau : calculs des indicateurs techniques
// (panel/static/js/indicateurs.js), puis l'interface dans un vrai navigateur
// (panneau en démonstration, sans réseau, serveur de playwright.config.js).
import { test, expect } from "@playwright/test";
import * as ti from "../../panel/static/js/indicateurs.js";

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

// Bougies d'une suite de clôtures : la clôture est le plus haut de la bougie.
const bars = (closes, step = 3600) => closes.map((c, i) => ({ time: 1_790_467_200 + i * step, open: c - 1, high: c, low: c - 2, close: c, volume: 10 }));

test("indicateurs : définitions de référence", () => {
  const flat = Array(60).fill(10);
  expect(ti.sma(flat, 20)[18]).toBeNull();                       // fenêtre pas encore pleine
  expect(ti.sma(flat, 20)[19]).toBe(10);
  const line = Array.from({ length: 300 }, (_, i) => 100 + 2 * i); // hausse régulière, pente 2
  // Une moyenne exponentielle suit une droite avec (n − 1) / 2 barres de retard.
  expect(ti.ema(line, 12)[299]).toBeCloseTo(line[299] - 2 * 11 / 2, 6);
  // MACD d'une droite : pente × (26 − 12) / 2 = 14, histogramme nul.
  const m = ti.macd(line);
  expect(m.macd[299]).toBeCloseTo(14, 6);
  expect(m.hist[299]).toBeCloseTo(0, 6);
  expect(m.signal[32]).toBeNull();
  expect(m.signal[33]).not.toBeNull();                           // 26 + 9 − 2
  // RSI : hausse sans repli = 100 ; autant de hausses que de baisses ≈ 50.
  expect(ti.rsi(line, 14)[13]).toBeNull();
  expect(ti.rsi(line, 14)[299]).toBe(100);
  const zigzag = Array.from({ length: 200 }, (_, i) => 100 + (i % 2));
  expect(ti.rsi(zigzag, 14)[199]).toBeGreaterThan(45);
  expect(ti.rsi(zigzag, 14)[199]).toBeLessThan(55);
  // Bollinger d'un cours immobile : bandes confondues avec la moyenne.
  const bb = ti.bollinger(flat);
  expect([bb.upper[59], bb.mid[59], bb.lower[59]]).toEqual([10, 10, 10]);
  // ATR de bougies d'amplitude fixe (2) : 2.
  const same = Array.from({ length: 40 }, (_, i) => ({ time: i, open: 10, high: 11, low: 9, close: 10, volume: 1 }));
  expect(ti.atr(same, 14)[39]).toBeCloseTo(2, 9);
  // Clôture au plus haut d'une hausse : stochastique 100, Williams %R 0.
  const rising = bars(line.slice(0, 60));
  expect(ti.stochastic(rising).k[59]).toBeCloseTo(100, 9);
  expect(ti.williamsR(rising)[59]).toBeCloseTo(0, 9);
  // Hausse régulière : acheteurs dominants (+DI > −DI), tendance nette.
  const dmi = ti.adx(bars(line.slice(0, 120)));
  expect(dmi.plusDI[119]).toBeGreaterThan(dmi.minusDI[119]);
  expect(dmi.adx[119]).toBeGreaterThan(25);
  expect(dmi.adx[26]).toBeNull();
  expect(dmi.adx[27]).not.toBeNull();                            // 2 × 14 − 1
  // Supertrend et SAR sous les prix en hausse.
  const st = ti.supertrend(rising), sar = ti.psar(rising);
  expect(st.dir[59]).toBe(1);
  expect(st.line[59]).toBeLessThan(rising[59].low);
  expect(sar.dir[59]).toBe(1);
  expect(sar.sar[59]).toBeLessThan(rising[59].low);
  // OBV : volume ajouté en hausse, retiré en baisse, inchangé sinon.
  expect(ti.obv([{ close: 1, volume: 5 }, { close: 2, volume: 3 }, { close: 1, volume: 4 }, { close: 1, volume: 9 }])).toEqual([0, 3, -1, -1]);
  // Donchian : plus haut et plus bas des n dernières bougies.
  const dc = ti.donchian(bars([5, 9, 7, 6]), 3);
  expect([dc.upper[3], dc.lower[3]]).toEqual([9, 4]);
  // Heikin Ashi : clôture = moyenne des quatre prix.
  expect(ti.heikinAshi([{ time: 1, open: 10, high: 14, low: 8, close: 12 }])[0].close).toBe(11);
  // Ichimoku : nuage projeté 25 barres en avant.
  const ich = ti.ichimoku(rising);
  expect(ich.spanA.length).toBe(60 + 25);
  expect(ich.spanA[26 - 1 + 25]).not.toBeNull();
});

test("indicateurs : pivots de la veille et retracements de Fibonacci", () => {
  const day = 86400, t0 = 1_790_467_200;                          // 00:00 UTC
  const yesterday = [[12, 9, 11], [11, 8, 9], [10, 9, 10]].map(([h, l, c], i) => ({ time: t0 + i * 3600, open: c, high: h, low: l, close: c, volume: 1 }));
  const today = [{ time: t0 + day, open: 10, high: 11, low: 9, close: 10, volume: 1 }];
  const p = ti.pivots([...yesterday, ...today], day);
  expect(p.P).toBeCloseTo((12 + 8 + 10) / 3, 9);                // H, B, C de la veille
  expect(p.R1).toBeCloseTo(2 * p.P - 8, 9);
  expect(p.S1).toBeCloseTo(2 * p.P - 12, 9);
  const up = [100, 120, 160, 200, 180].map((c, i) => ({ time: i, open: c, high: c, low: c, close: c, volume: 1 }));
  const f = ti.fibonacci(up);
  expect(f.rising).toBe(true);                                    // plus bas puis plus haut
  expect(f.levels.find((l) => l.ratio === 0.618).price).toBeCloseTo(200 - 0.618 * 100, 9);
});

test("graphique détaillé : barre d'outils, indicateurs, panneaux et lecture technique", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await cards.nth(2).click();                                     // une position détenue
  const dlg = page.locator("#detail"), panes = page.locator("#detail-chart .pane");
  await expect(page.locator("#detail-tech")).toContainText("Lecture technique");
  await expect(page.locator("#detail-tech")).toContainText("Règle du bot");
  await expect(page.locator("#detail-tech")).toContainText("Cassure 30 jours");
  await expect(page.locator("#detail-stats")).toContainText("Stop de clôture");
  await expect(page.locator("#ind-count")).toHaveText("5");       // modèle « Essentiel »
  await expect(panes).toHaveCount(3);                             // prix, volume, RSI
  await expect(page.locator("#detail-readout")).toContainText("MM 50");
  await expect(page.locator("#detail-readout")).toContainText("Cassure du bot");
  const menu = page.locator("#ind-menu");
  await page.locator("#ind-btn").click();
  await expect(menu).toBeVisible();
  await expect(page.locator("#ind-btn")).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator('#ind-presets [data-preset="essentiel"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator('#ind-presets [data-preset="elan"]').click();
  await expect(panes).toHaveCount(5);                             // volume, RSI, MACD, stochastique
  await expect(page.locator('[data-pane="macd"] .pane-legend')).toContainText("signal");
  await page.locator('#ind-families [data-ind="adx"]').click();   // cinquième panneau : refusé
  await expect(page.locator(".toast", { hasText: "4 panneaux au plus" })).toBeVisible();
  await expect(page.locator('#ind-families [data-ind="adx"]')).not.toBeChecked();
  await page.locator('#ind-families [data-ind="stoch"]').click();
  await expect(panes).toHaveCount(4);
  await page.locator('#ind-families [data-ind="bollinger"]').click();
  await expect(page.locator("#detail-readout")).toContainText("Bollinger");
  await expect(page.locator("#ind-count")).toHaveText("4");
  await page.keyboard.press("Escape");                            // Échap : la liste d'abord
  await expect(menu).toBeHidden();
  await expect(dlg).toBeVisible();
  await page.locator('#detail-types [data-ctype="heikin"]').click();
  await expect(page.locator('#detail-types [data-ctype="heikin"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator("#detail-readout")).toContainText("Heikin Ashi");
  await page.locator('#detail-scales [data-scale="log"]').click();
  await expect(page.locator('#detail-scales [data-scale="log"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator('#detail-intervals [data-interval="1d"]').click();
  await expect(page.locator('#detail-intervals [data-interval="1d"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator("#ind-btn").click();
  await expect(page.locator('#ind-families [data-ind="vwap"]')).toBeDisabled();   // bougies de moins d'un jour
  await page.keyboard.press("Escape");
  await page.keyboard.press("Escape");                            // puis le graphique
  await expect(dlg).toBeHidden();
  await cards.nth(2).click();                                     // réglages gardés
  await expect(page.locator("#ind-count")).toHaveText("4");
  await expect(page.locator('#detail-types [data-ctype="heikin"]')).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator('#detail-intervals [data-interval="1d"]')).toHaveAttribute("aria-pressed", "true");
  await page.locator("#detail-fit").click();
  await page.locator("#detail-full").click();
  await expect(page.locator("#detail-full")).toHaveAttribute("aria-pressed", "true");
  await page.locator("#detail-close").click();
  await expect(dlg).toBeHidden();
  expect(errors).toEqual([]);
});

test("capital et régime : baisse depuis le plus haut, écart à la moyenne", async ({ page }) => {
  const errors = watchErrors(page);
  await page.goto(BASE + "/#charts");
  const cards = page.locator("#chart-grid .chart-card");
  await cards.first().click();
  await expect(page.locator('[data-pane="dd"] .pane-legend')).toContainText("Baisse depuis le plus haut");
  await expect(page.locator("#detail-stats")).toContainText("Pire baisse");
  await expect(page.locator("#detail-readout")).toContainText("Capital");
  await expect(page.locator("#ind-btn")).toBeHidden();            // pas d'indicateurs sur le capital
  await page.keyboard.press("Escape");
  await cards.nth(1).click();
  await expect(page.locator('[data-pane="gap"] .pane-legend')).toContainText("Écart à la moyenne");
  await expect(page.locator("#detail-readout")).toContainText("BTC");
  await page.keyboard.press("Escape");
  expect(errors).toEqual([]);
});
