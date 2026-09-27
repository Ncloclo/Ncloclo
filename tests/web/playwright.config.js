// Tests de la page d'animation dans Chromium (page d'exemple produite par
// tests/web/build_sample.py, données synthétiques, sans réseau).
import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: ".",
  testMatch: "*.spec.js",
  timeout: 30_000,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [["github"], ["list"]] : "list",
  use: { browserName: "chromium", viewport: { width: 1280, height: 900 } },
});
