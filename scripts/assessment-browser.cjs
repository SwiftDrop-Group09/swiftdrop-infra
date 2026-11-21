const assert = require('node:assert/strict');
const { chromium } = require(process.env.SWIFTDROP_PLAYWRIGHT_MODULE || 'playwright');

let stage = "browser startup";

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const input = JSON.parse(Buffer.concat(chunks));
  const browser = await chromium.launch({
    executablePath: process.env.SWIFTDROP_BROWSER_EXECUTABLE || undefined,
  });
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
    stage = "trusted tracking page";
    await page.goto(input.origin + '/track?code=' + encodeURIComponent(input.trackingNumber));
    stage = 'initial SSE frame';
    await page.getByText('Live updates connected').waitFor({ timeout: 10000 });
    stage = "proof upload";
    const response = await page.evaluate(async ({ path, token, key, headers, body }) => {
      const bytes = Uint8Array.from(atob(body), (character) => character.charCodeAt(0));
      const result = await fetch(path, {
        method: 'POST',
        headers: { ...headers, Authorization: 'Bearer ' + token, 'Idempotency-Key': key },
        body: bytes,
        credentials: 'omit',
      });
      if (!result.ok) return { httpStatus: result.status };
      return { httpStatus: result.status, body: await result.json() };
    }, input);
    assert.equal(response.httpStatus, 200, 'Browser proof upload failed');
    stage = 'delivered SSE update';
    await page.getByText('Parcel Successfully Delivered').waitFor({ timeout: 10000 });
    process.stdout.write(JSON.stringify(response.body));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  process.stderr.write(stage + ": " + error.name + '\n');
  process.exitCode = 1;
});