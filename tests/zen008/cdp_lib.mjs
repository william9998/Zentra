// 共用的零依賴 CDP 驅動（node 24 內建 WebSocket，不加任何 npm 依賴 —— ADR-0006）。
// 這段 plumbing 與 tests/zen006a/cdp_console_e2e.mjs 是同一份；沒有測試邏輯。

import { spawn } from 'node:child_process';
import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

export const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

export class Session {
  constructor(ws, sessionId) {
    this.ws = ws;
    this.sessionId = sessionId;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
  }

  send(method, params = {}) {
    const id = this.nextId++;
    const message = { id, method, params };
    if (this.sessionId) message.sessionId = this.sessionId;
    this.ws.send(JSON.stringify(message));
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('CDP timeout: ' + method)), 60000);
      this.pending.set(id, { resolve, reject, timer });
    });
  }

  on(method, handler) {
    if (!this.listeners.has(method)) this.listeners.set(method, []);
    this.listeners.get(method).push(handler);
  }

  handle(message) {
    if (message.id !== undefined && this.pending.has(message.id)) {
      const { resolve, reject, timer } = this.pending.get(message.id);
      this.pending.delete(message.id);
      clearTimeout(timer);
      if (message.error) reject(new Error(message.method + ': ' + JSON.stringify(message.error)));
      else resolve(message.result);
      return;
    }
    for (const handler of this.listeners.get(message.method) || []) handler(message.params);
  }

  /** Wait for a CDP event, optionally filtered. The handler unregisters itself. */
  once(method, predicate = () => true, timeoutMs = 30000) {
    return new Promise((resolve, reject) => {
      let settled = false;
      const timer = setTimeout(() => {
        if (settled) return;
        settled = true;
        this.off(method, handler);
        reject(new Error('timeout waiting for ' + method));
      }, timeoutMs);
      const handler = (params) => {
        if (settled || !predicate(params)) return;
        settled = true;
        clearTimeout(timer);
        this.off(method, handler);
        resolve(params);
      };
      this.on(method, handler);
    });
  }

  off(method, handler) {
    const handlers = this.listeners.get(method) || [];
    const index = handlers.indexOf(handler);
    if (index !== -1) handlers.splice(index, 1);
  }
}

export async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('ws error ' + url)), { once: true });
  });
  return ws;
}

export async function launchChrome() {
  const profile = mkdtempSync(join(tmpdir(), 'zentra-qa-chrome-'));
  const port = 9222 + Math.floor(Math.random() * 700);
  const child = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--disable-features=Translate,MediaRouter', '--window-size=1440,1000',
    'about:blank',
  ], { stdio: 'ignore' });

  let version = null;
  for (let i = 0; i < 100; i += 1) {
    try {
      version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json();
      break;
    } catch (_) {
      await sleep(100);
    }
  }
  if (!version) throw new Error('Chrome did not expose a debugging endpoint');
  return { child, profile, version };
}

/** A fresh page with network/console/exception recording already wired up. */
export async function newPage(browser) {
  const { targetId } = await browser.send('Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await browser.send('Target.attachToTarget', { targetId, flatten: true });
  const page = new Session(browser.ws, sessionId);
  browser.on('__dispatch__' + sessionId, (message) => page.handle(message));

  const log = { requests: [], responses: [], consoleErrors: [], exceptions: [], dialogs: [] };
  page.on('Network.requestWillBeSent', (p) => log.requests.push({
    url: p.request.url, method: p.request.method, type: p.type, at: Date.now(),
  }));
  page.on('Network.responseReceived', (p) => log.responses.push({
    url: p.response.url, status: p.response.status,
  }));
  page.on('Runtime.consoleAPICalled', (p) => {
    if (p.type === 'error') {
      log.consoleErrors.push(p.args.map((a) => a.value ?? a.description ?? a.type).join(' '));
    }
  });
  page.on('Runtime.exceptionThrown', (p) => {
    log.exceptions.push(p.exceptionDetails.exception?.description
      || p.exceptionDetails.text || 'unknown');
  });
  page.on('Page.javascriptDialogOpening', (p) => {
    log.dialogs.push({ type: p.type, message: p.message });
    page.send('Page.handleJavaScriptDialog', { accept: true }).catch(() => {});
  });

  await page.send('Page.enable');
  await page.send('Runtime.enable');
  await page.send('Network.enable');
  await page.send('Log.enable');
  await page.send('Emulation.setDeviceMetricsOverride', {
    width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false,
  });
  return { page, log, targetId };
}


export async function navigate(page, url) {
  const loaded = page.once('Page.loadEventFired', () => true, 30000);
  await page.send('Page.navigate', { url });
  await loaded;
  await sleep(150);
}

export async function evaluate(page, expression) {
  const result = await page.send('Runtime.evaluate', {
    expression: `(() => { ${expression} })()`,
    returnByValue: true, awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error('evaluate failed: ' + JSON.stringify(result.exceptionDetails));
  }
  return result.result.value;
}

export async function waitFor(page, expression, timeoutMs = 60000, intervalMs = 120) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await evaluate(page, `return (${expression});`);
    if (last) return last;
    await sleep(intervalMs);
  }
  throw new Error('waitFor timed out: ' + expression + ' (last=' + JSON.stringify(last) + ')');
}

export { sleep };
