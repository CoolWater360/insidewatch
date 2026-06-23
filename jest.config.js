/** @type {import('jest').Config} */
module.exports = {
  preset: "ts-jest",
  testEnvironment: "node",
  testMatch: ["**/tests/internal/**/*.test.ts"],
  moduleNameMapper: {
    "^@/(.*)$": "<rootDir>/$1",
  },
  // Do not transform node_modules except supabase-js (ESM).
  transformIgnorePatterns: ["node_modules/(?!(@supabase)/)"],
};
