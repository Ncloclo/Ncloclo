// ESLint (configuration « flat ») : script de la page d'animation et tests
// navigateur. Lancé par GitHub Actions (.github/workflows/checks.yml).
import js from "@eslint/js";
import globals from "globals";

export default [
  { ignores: ["node_modules/", "tests/web/.out/", "test-results/", "playwright-report/"] },
  js.configs.recommended,
  {
    // Script classique intégré à la page (fonction auto-exécutée).
    files: ["templates/**/*.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "script", globals: globals.browser },
    rules: { eqeqeq: ["error", "always", { null: "ignore" }], "no-var": "error", "prefer-const": "error" },
  },
  {
    files: ["tests/web/**/*.js", "eslint.config.js"],
    languageOptions: { ecmaVersion: "latest", sourceType: "module", globals: { ...globals.node, ...globals.browser } },
  },
];
