const { chromium } = require('playwright');

(async () => {
  const browser = await chromium.launch({
    headless: true
  });

  const context = await browser.newContext({
    storageState: 'auth.json'
  });

  const page = await context.newPage();

  await page.goto('https://sam.gov', {
    waitUntil: 'domcontentloaded',
    timeout: 60000
  });

  console.log('Page title:', await page.title());

  await page.screenshot({
    path: 'sam-session-test.png',
    fullPage: true
  });

  await browser.close();
})();
