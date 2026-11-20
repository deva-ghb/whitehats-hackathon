/** Sign in as one of the seeded demo accounts. The password is the product default. */

export const DEMO_PASSWORD = 'WhiteHats!2026'

// The Studio opens the isometric scene on a wide screen (STUDIO.2). The older journeys drive the flat plan, so a sign in
// remembers the plan view unless a test asks for something else: 'scene', or null to leave the product's own default alone.
export async function signIn(page, baseURL, email = 'ceo@acme.example', view = 'plan') {
  await page.goto(baseURL)
  if (view) await page.evaluate((choice) => window.localStorage.setItem('wh-studio-view', choice), view)
  await page.locator('#email').fill(email)
  await page.locator('#password').fill(DEMO_PASSWORD)
  await page.getByRole('button', { name: 'Sign in' }).click()
  await page.getByRole('heading', { level: 1, name: 'Projects' }).waitFor()
  await page.getByRole('link', { name: 'Open Fintech shop' }).click()
  await page.getByRole('heading', { level: 1, name: 'Overview' }).waitFor()
}
