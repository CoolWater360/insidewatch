/** @type {import('jest').Config} */
const nextJest = require("next/jest");
const createJestConfig = nextJest({ dir: "./" });

module.exports = createJestConfig({
  testEnvironment: "node",
  testMatch: ["**/tests/internal/**/*.test.ts"],
  moduleNameMapper: { "^@/(.*)$": "<rootDir>/$1" },
  transformIgnorePatterns: ["node_modules/(?!(@supabase)/)"],
});
