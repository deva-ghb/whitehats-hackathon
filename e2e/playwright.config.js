import { defineConfig, devices } from '@playwright/test'

// The app itself is started by the fixture in fixtures.js, on a fresh database.
export default defineConfig({
  testDir: './tests',
  timeout: 60_000,
  workers: 1,
  reporter: 'line',
  snapshotPathTemplate: 'screenshots/P0.8/{arg}{ext}',
  expect: {
    toHaveScreenshot: { animations: 'disabled', caret: 'hide', maxDiffPixelRatio: 0.01 },
  },
  use: {
    ...devices['Desktop Chrome'],
    viewport: { width: 1440, height: 900 },
  },
})
