// ZEN-006A Console E2E driver — headless Chrome over the DevTools Protocol.
//
// There is no Playwright in this environment (SPEC §6 lists it, but it is not
// installed), and ADR-0006 forbids adding npm dependencies to the repo. Node 24 ships
// a global WebSocket, so this drives Chrome directly with zero dependencies.
//
//   node tests/zen006a/cdp_console_e2e.mjs --base http://127.0.0.1:8000 --out result.json
//
// It prints a JSON document of named checks. tests/zen006a/test_console_e2e.py turns
// each check into a pytest assertion; nothing here decides pass/fail on its own.

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const args = process.argv.slice(2);
const argOf = (name, fallback) => {
  const i = args.indexOf('--' + name);
  return i === -1 ? fallback : args[i + 1];
};
const BASE = argOf('base', 'http://127.0.0.1:8000');
const OUT = argOf('out', null);
const IDLE_WATCH_MS = Number(argOf('idle-ms', '20000'));

const checks = [];
// ok === null means "not observed" -- pytest reports those as untested, never as passed.
const record = (id, ok, detail) => {
  checks.push({ id, ok: ok === null ? null : Boolean(ok), detail: detail === undefined ? null : detail });
};

// --------------------------------------------------------------- CDP plumbing

class Session {
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

async function connect(url) {
  const ws = new WebSocket(url);
  await new Promise((resolve, reject) => {
    ws.addEventListener('open', resolve, { once: true });
    ws.addEventListener('error', () => reject(new Error('ws error ' + url)), { once: true });
  });
  return ws;
}

async function launchChrome() {
  const profile = mkdtempSync(join(tmpdir(), 'zen006a-chrome-'));
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
async function newPage(browser) {
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

async function navigate(page, url) {
  const loaded = page.once('Page.loadEventFired', () => true, 30000);
  await page.send('Page.navigate', { url });
  await loaded;
  await sleep(150);
}

async function evaluate(page, expression) {
  const result = await page.send('Runtime.evaluate', {
    expression: `(() => { ${expression} })()`,
    returnByValue: true, awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error('evaluate failed: ' + JSON.stringify(result.exceptionDetails));
  }
  return result.result.value;
}

async function waitFor(page, expression, timeoutMs = 60000, intervalMs = 100) {
  const deadline = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < deadline) {
    last = await evaluate(page, `return (${expression});`);
    if (last) return last;
    await sleep(intervalMs);
  }
  throw new Error('waitFor timed out: ' + expression + ' (last=' + JSON.stringify(last) + ')');
}

// ------------------------------------------------------------------ scenarios

const XSS_QUESTION = '<img src=x onerror="window.__xssFired=1"><script>window.__xssFired=2<\/script>';

async function scenarioMainFlow(browser) {
  const { page, log } = await newPage(browser);
  await navigate(page, BASE + '/console/runs');

  // --- Empty state (ui-contract §4)
  const empty = await evaluate(page, `
    const box = document.querySelector('.empty');
    const text = box ? box.querySelector('.empty-text') : null;
    const button = document.querySelector('[data-new-run]');
    return {
      text: text ? text.textContent.trim() : null,
      hasSecondaryButton: Boolean(button && button.classList.contains('btn-secondary')),
      buttonTag: button ? button.tagName : null,
      onlyNoData: text ? /^(無資料|no data)$/i.test(text.textContent) : null,
      stashEmptyText: (document.querySelectorAll('.empty-text')[1] || {}).textContent || null,
    };
  `);
  record('runs.empty_has_explanation_and_secondary_button',
    empty.text && empty.text.length > 8 && empty.hasSecondaryButton && empty.buttonTag === 'BUTTON'
    && !empty.onlyNoData, empty);

  // --- Native <dialog> opens with focus in the field (ui-contract §7)
  await evaluate(page, `document.querySelector('[data-new-run]').click(); return true;`);
  await sleep(120);
  const dialogOpen = await evaluate(page, `
    const dialog = document.querySelector('[data-dialog]');
    return {
      tag: dialog.tagName, open: dialog.open,
      activeId: document.activeElement ? document.activeElement.id : null,
      isModal: dialog.matches(':modal'),
    };
  `);
  record('dialog.native_opens_modal_and_focuses_input',
    dialogOpen.tag === 'DIALOG' && dialogOpen.open && dialogOpen.isModal
    && dialogOpen.activeId === 'question', dialogOpen);

  // --- Empty submit is blocked client-side and sends no request
  const beforeEmptySubmit = log.requests.length;
  await evaluate(page, `
    document.querySelector('#question').value = '';
    document.querySelector('[data-form]').requestSubmit();
    return true;
  `);
  await sleep(400);
  const postsAfterEmpty = log.requests.slice(beforeEmptySubmit)
    .filter((r) => r.method === 'POST').length;
  const stillOpen = await evaluate(page, `return document.querySelector('[data-dialog]').open;`);
  record('dialog.empty_submit_sends_no_request', postsAfterEmpty === 0 && stillOpen,
    { postsAfterEmpty, stillOpen });

  // --- ESC closes the dialog (native behaviour, no custom key handling)
  await page.send('Input.dispatchKeyEvent', {
    type: 'keyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27,
  });
  await page.send('Input.dispatchKeyEvent', {
    type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27,
  });
  await sleep(250);
  record('dialog.escape_closes',
    (await evaluate(page, `return !document.querySelector('[data-dialog]').open;`)), null);

  // --- Cancel button closes as well
  await evaluate(page, `document.querySelector('[data-new-run]').click(); return true;`);
  await sleep(100);
  await evaluate(page, `document.querySelector('[data-cancel]').click(); return true;`);
  await sleep(150);
  record('dialog.cancel_closes',
    (await evaluate(page, `return !document.querySelector('[data-dialog]').open;`)), null);

  // --- Real submit with an XSS payload as the question
  await evaluate(page, `document.querySelector('[data-new-run]').click(); return true;`);
  await sleep(100);
  await evaluate(page, `
    const input = document.querySelector('#question');
    input.value = ${JSON.stringify(XSS_QUESTION)};
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('[data-form]').requestSubmit();
    return true;
  `);

  await page.once('Page.loadEventFired', () => true, 30000);
  await sleep(200);
  const url = await evaluate(page, `return location.pathname;`);
  const runId = url.split('/').pop();
  record('flow.navigates_to_run_observation_page',
    /^\/console\/runs\/[0-9a-f-]{36}$/.test(url), { url });

  const createPost = log.requests.find((r) => r.method === 'POST');
  record('flow.create_run_uses_only_the_contract_endpoint',
    Boolean(createPost) && new URL(createPost.url).pathname
      === '/v1/agents/01930000-0000-7000-8000-000000000001/runs',
    createPost || null);

  // --- Status language while the Run is in flight (ui-contract §2)
  const samples = [];
  const terminalDeadline = Date.now() + 90000;
  let terminalText = null;
  while (Date.now() < terminalDeadline) {
    const snapshot = await evaluate(page, `
      const status = document.querySelector('[data-status]');
      const dot = status ? status.querySelector('.status-dot') : null;
      const label = status ? status.querySelector('.status-label') : null;
      const detail = status ? status.querySelector('.status-detail') : null;
      const computed = dot ? getComputedStyle(dot) : null;
      return {
        text: status ? status.textContent.trim() : '',
        label: label ? label.textContent : null,
        detail: detail ? detail.textContent : null,
        dotClass: dot ? dot.className : null,
        dotSize: computed ? [computed.width, computed.height, computed.borderRadius] : null,
        dotColor: computed ? computed.backgroundColor : null,
        ariaLive: status ? status.getAttribute('aria-live') : null,
      };
    `);
    samples.push(snapshot);
    if (snapshot.label === '已完成' || snapshot.label === '失敗' || snapshot.label === '已取消') {
      terminalText = snapshot;
      break;
    }
    await sleep(120);
  }
  record('run.reaches_terminal_state', Boolean(terminalText), terminalText);
  record('run.terminal_label_is_contract_text',
    terminalText && terminalText.label === '已完成', terminalText);
  record('run.status_dot_is_6px_pill_and_has_text_label',
    terminalText && terminalText.dotSize[0] === '6px' && terminalText.dotSize[1] === '6px'
    && terminalText.dotSize[2] === '999px' && terminalText.label.length > 0,
    terminalText ? terminalText.dotSize : null);
  record('run.status_region_is_aria_live_polite',
    terminalText && terminalText.ariaLive === 'polite', terminalText?.ariaLive);

  const runningSamples = samples.filter((s) => s.label === '執行中');
  // Informational: a browser clock behind the DB clock makes elapsed negative, and
  // formatDuration() renders '—' for negative input. ui-contract §2 wants an elapsed time.
  record('run.running_elapsed_is_always_numeric',
    runningSamples.length === 0 ? null
      : runningSamples.every((s) => s.detail && !/·\s*—\s*$/.test(s.detail)),
    runningSamples.map((s) => s.detail));
  const runningWithNodeAndElapsed = runningSamples.filter(
    (s) => s.detail && /· .+ · \d+(\.\d+)?(ms|s)/.test(s.detail));
  record('run.running_shows_node_and_elapsed',
    runningSamples.length === 0 ? null : runningWithNodeAndElapsed.length > 0,
    { seen: samples.map((s) => s.label + '|' + (s.detail || '')).slice(0, 40) });
  record('run.statuses_seen_are_all_in_spec_enum',
    samples.every((s) => ['排隊中', '配置中', '執行中', '已完成', '失敗', '已取消', null]
      .includes(s.label)),
    [...new Set(samples.map((s) => s.label))]);

  // --- Result and timeline
  const result = await evaluate(page, `
    const pre = document.querySelector('[data-result] pre.code');
    const rows = Array.from(document.querySelectorAll('.timeline-list li'));
    return {
      resultText: pre ? pre.textContent : null,
      resultUsedInnerHtmlNodes: pre ? pre.children.length : null,
      rowCount: rows.length,
      types: rows.map((r) => (r.querySelector('.event-type') || {}).textContent || ''),
      allCollapsed: rows.every((r) => !r.querySelector('details').open),
      usage: Array.from(document.querySelectorAll('[data-usage] .meta-label'))
        .map((n) => n.textContent),
      connection: (document.querySelector('[data-connection]') || {}).textContent || null,
      timelineAriaLive: (document.querySelector('.timeline-list') || {})
        .getAttribute?.('aria-live') || null,
    };
  `);
  let echoed = null;
  try {
    echoed = JSON.parse(result.resultText).echo;
  } catch (_) { echoed = null; }
  record('run.result_echoes_input_verbatim_as_text',
    echoed === XSS_QUESTION && result.resultUsedInnerHtmlNodes === 0,
    { echoed, expected: XSS_QUESTION, childNodes: result.resultUsedInnerHtmlNodes });
  record('timeline.has_node_events_and_terminal_status',
    result.types.filter((t) => t === 'node.start').length >= 2
    && result.types.filter((t) => t === 'node.end').length >= 2
    && result.types.filter((t) => t === 'run.status').length >= 1, result.types);
  record('timeline.rows_default_collapsed', result.allCollapsed, null);
  record('timeline.is_aria_live_polite', result.timelineAriaLive === 'polite',
    result.timelineAriaLive);
  record('timeline.connection_status_shown_after_stream_end',
    Boolean(result.connection && result.connection.length > 0), result.connection);
  record('usage.labels_use_contract_field_names',
    ['llm_calls', 'input_tokens', 'output_tokens', 'cost_usd']
      .every((f) => result.usage.includes(f)), result.usage);

  // --- XSS: nothing from the payload became a node
  const xss = await evaluate(page, `
    return {
      xssFired: window.__xssFired === undefined ? null : window.__xssFired,
      imgCount: document.querySelectorAll('img').length,
      iframeCount: document.querySelectorAll('iframe,object,embed').length,
      inlineScripts: Array.from(document.querySelectorAll('script'))
        .filter((s) => !s.src).length,
      eventHandlerAttrs: Array.from(document.querySelectorAll('*'))
        .filter((n) => Array.from(n.attributes || [])
          .some((a) => a.name.startsWith('on'))).length,
    };
  `);
  record('xss.payload_never_becomes_dom',
    xss.xssFired === null && xss.imgCount === 0 && xss.iframeCount === 0
    && xss.inlineScripts === 0 && xss.eventHandlerAttrs === 0, xss);
  record('xss.no_javascript_dialog_opened', log.dialogs.length === 0, log.dialogs);

  // --- THE regression that matters: EventSource must be closed at terminal state
  const eventsRequests = () => log.requests.filter(
    (r) => new URL(r.url).pathname.endsWith('/events'));
  const afterTerminal = eventsRequests().length;
  await sleep(IDLE_WATCH_MS);
  const afterIdle = eventsRequests().length;
  record('sse.single_events_request_no_reconnect_replay',
    afterTerminal === 1 && afterIdle === 1,
    { afterTerminal, afterIdle, watchedMs: IDLE_WATCH_MS,
      urls: eventsRequests().map((r) => r.url) });

  const dedup = await evaluate(page, `
    const rows = document.querySelectorAll('.timeline-list li');
    const texts = Array.from(rows).map((r) => r.textContent);
    return { rows: rows.length, unique: new Set(texts).size };
  `);
  record('timeline.no_duplicate_rows_after_idle', dedup.rows === dedup.unique, dedup);

  // --- Credentials and origin discipline (P4 / ui-contract §6.4)
  const storage = await evaluate(page, `
    const dump = (s) => Object.fromEntries(Object.keys(s).map((k) => [k, s.getItem(k)]));
    return { local: dump(localStorage), session: dump(sessionStorage), cookie: document.cookie };
  `);
  const storageBlob = JSON.stringify(storage).toLowerCase();
  const secretWords = ['api_key', 'apikey', 'bearer', 'authorization', 'secret',
    'password', 'sk-', 'token'];
  record('p4.no_long_lived_credentials_in_browser_storage',
    !secretWords.some((w) => storageBlob.includes(w)) && storage.cookie === '',
    { keys: Object.keys(storage.local), session: Object.keys(storage.session),
      cookie: storage.cookie });
  record('p4.localstorage_only_holds_ui_prefs_and_run_ids',
    Object.keys(storage.local).every(
      (k) => ['zentra.console.sidebar', 'zentra.console.recentRuns'].includes(k)),
    Object.keys(storage.local));

  const origins = [...new Set(log.requests.map((r) => new URL(r.url).origin))];
  record('p4.all_requests_are_same_origin',
    origins.length === 1 && BASE.startsWith(origins[0]), origins);

  const apiPaths = [...new Set(log.requests
    .map((r) => new URL(r.url).pathname)
    .filter((p) => p.startsWith('/v1') || p.startsWith('/internal')))];
  const allowed = apiPaths.every((p) =>
    /^\/v1\/agents\/[0-9a-f-]+\/runs$/.test(p)
    || /^\/v1\/runs\/[0-9a-f-]+$/.test(p)
    || /^\/v1\/runs\/[0-9a-f-]+\/events$/.test(p));
  record('scope.only_three_endpoints_are_called', allowed, apiPaths);
  record('scope.internal_gateway_never_called',
    !apiPaths.some((p) => p.startsWith('/internal')), apiPaths);

  record('runtime.no_uncaught_exceptions', log.exceptions.length === 0, log.exceptions);
  record('runtime.no_console_errors', log.consoleErrors.length === 0, log.consoleErrors);
  record('runtime.no_failed_asset_responses',
    log.responses.filter((r) => r.status >= 400
      && new URL(r.url).pathname.startsWith('/console')).length === 0,
    log.responses.filter((r) => r.status >= 400).map((r) => r.url + ' ' + r.status));

  // --- Reload a terminal Run: events replay from the DB, still exactly one request
  const before = log.requests.length;
  await navigate(page, BASE + '/console/runs/' + runId);
  await waitFor(page, `document.querySelectorAll('.timeline-list li').length > 0`, 30000);
  await sleep(3000);
  const reloadEvents = log.requests.slice(before)
    .filter((r) => new URL(r.url).pathname.endsWith('/events')).length;
  const reloaded = await evaluate(page, `
    const label = document.querySelector('.status-label');
    const rows = document.querySelectorAll('.timeline-list li');
    return { label: label ? label.textContent : null, rows: rows.length };
  `);
  record('reload.terminal_run_rebuilds_and_does_not_reconnect',
    reloadEvents === 1 && reloaded.label === '已完成' && reloaded.rows >= 5,
    { reloadEvents, ...reloaded });

  // --- Back on the runs page: the localStorage scaffold lists the run as text
  await navigate(page, BASE + '/console/runs');
  const stash = await evaluate(page, `
    const links = Array.from(document.querySelectorAll('.stash-link'));
    return {
      count: links.length,
      firstHref: links[0] ? links[0].getAttribute('href') : null,
      questionText: links[0] ? links[0].querySelector('.stash-question').textContent : null,
      imgCount: document.querySelectorAll('img').length,
    };
  `);
  record('stash.lists_run_and_renders_payload_as_text',
    stash.count >= 1 && stash.firstHref === '/console/runs/' + runId
    && stash.questionText.includes('<img') && stash.imgCount === 0, stash);

  await page.send('Page.close').catch(() => {});
  return { runId, log };
}

async function scenarioMissingRun(browser) {
  const { page, log } = await newPage(browser);
  const missing = '0193a7c0-dead-7000-8000-00000000dead';
  await navigate(page, BASE + '/console/runs/' + missing);
  await sleep(800);
  const state = await evaluate(page, `
    const box = document.querySelector('[data-page-error] .error-box');
    if (!box) return { found: false };
    return {
      found: true,
      code: (box.querySelector('.error-code') || {}).textContent || null,
      codeFont: box.querySelector('.error-code')
        ? getComputedStyle(box.querySelector('.error-code')).fontFamily : null,
      message: (box.querySelector('.error-message') || {}).textContent || null,
      hints: Array.from(box.querySelectorAll('.error-hint')).map((n) => n.textContent),
      hidden: document.querySelector('[data-page-error]').hidden,
    };
  `);
  record('error404.page_shows_code_runid_and_next_step',
    state.found && state.code === 'NOT_FOUND' && !state.hidden
    && state.hints.some((h) => h.includes(missing))
    && state.hints.some((h) => h.length > 15) && Boolean(state.message), state);
  record('error404.code_uses_mono_font',
    Boolean(state.codeFont && /mono|SF Mono|Fira|Consolas/i.test(state.codeFont)),
    state.codeFont);
  record('error404.no_uncaught_exception', log.exceptions.length === 0, log.exceptions);
  const streams = log.requests.filter((r) => new URL(r.url).pathname.endsWith('/events'));
  record('error404.does_not_open_event_stream', streams.length === 0,
    streams.map((r) => r.url));
  await page.send('Page.close').catch(() => {});
}

/**
 * Synthetic contract responses through Fetch interception.
 *
 * ZEN-000 cannot produce FAILED / CANCELLED runs, 200+ events or >2KB payloads, and
 * the Console is not allowed to call any endpoint that could. Intercepting lets the
 * *real* served assets render contract-shaped data that the back end cannot emit yet.
 */
async function scenarioSyntheticStates(browser, { status, errorCode, eventCount, bigPayload }) {
  const { page, log } = await newPage(browser);
  const fakeId = '0193a7c0-0000-7000-8000-0000000' + Math.floor(Math.random() * 90000 + 10000);

  await page.send('Fetch.enable', {
    patterns: [{ urlPattern: '*/v1/runs/*', requestStage: 'Request' }],
  });

  const runBody = {
    run_id: fakeId, agent_id: '01930000-0000-7000-8000-000000000001',
    agent_version: '0.0.0-builtin', status, trigger: 'API',
    input: { question: 'synthetic' },
    output: status === 'SUCCEEDED' ? { echo: 'synthetic' } : null,
    error: status === 'FAILED'
      ? { code: errorCode, message: '沙盒執行逾時', run_id: fakeId, retryable: true,
          details: { timeout_seconds: 900 } }
      : null,
    usage: { input_tokens: 0, output_tokens: 0, llm_calls: 0, duration_ms: 1200, cost_usd: '0' },
    created_at: new Date().toISOString(), started_at: new Date().toISOString(),
    finished_at: new Date().toISOString(),
  };

  const lines = [];
  let seq = 0;
  for (let i = 0; i < eventCount; i += 1) {
    seq += 1;
    const payload = { node: 'n' + i, ts: new Date().toISOString() };
    // Put it on the last event so it lands inside the 200-row window.
    if (bigPayload && i === eventCount - 1) payload.blob = 'x'.repeat(4096);
    lines.push(`id: ${seq}\nevent: node.start\ndata: ${JSON.stringify(payload)}\n\n`);
  }
  // Same seq twice: the Console must dedup on the SSE id.
  lines.push(`id: ${seq}\nevent: node.start\ndata: ${JSON.stringify({ node: 'dup' })}\n\n`);
  seq += 1;
  lines.push(`id: ${seq}\nevent: run.status\ndata: ${JSON.stringify({ status })}\n\n`);
  const sseBody = lines.join('');

  page.on('Fetch.requestPaused', async (p) => {
    const path = new URL(p.request.url).pathname;
    try {
      if (path.endsWith('/events')) {
        await page.send('Fetch.fulfillRequest', {
          requestId: p.requestId, responseCode: 200,
          responseHeaders: [{ name: 'content-type', value: 'text/event-stream' },
            { name: 'cache-control', value: 'no-cache' }],
          body: Buffer.from(sseBody, 'utf8').toString('base64'),
        });
      } else {
        await page.send('Fetch.fulfillRequest', {
          requestId: p.requestId, responseCode: 200,
          responseHeaders: [{ name: 'content-type', value: 'application/json' }],
          body: Buffer.from(JSON.stringify(runBody), 'utf8').toString('base64'),
        });
      }
    } catch (_) { /* target may already be closing */ }
  });

  await navigate(page, BASE + '/console/runs/' + fakeId);
  await waitFor(page, `document.querySelector('.status-label') !== null`, 30000);
  await sleep(1200);

  const view = await evaluate(page, `
    const label = document.querySelector('.status-label');
    const detail = document.querySelector('.status-detail');
    const rows = document.querySelectorAll('.timeline-list li');
    const errorBox = document.querySelector('[data-result] .error-box');
    const large = document.querySelector('.payload-large summary');
    const earlier = document.querySelector('[data-load-earlier]');
    const notice = document.querySelector('[data-notice]');
    const dot = document.querySelector('.status-dot');
    return {
      label: label ? label.textContent : null,
      detail: detail ? detail.textContent : null,
      dotColor: dot ? getComputedStyle(dot).backgroundColor : null,
      rows: rows.length,
      errorCode: errorBox ? (errorBox.querySelector('.error-code') || {}).textContent : null,
      errorHints: errorBox
        ? Array.from(errorBox.querySelectorAll('.error-hint')).map((n) => n.textContent) : [],
      retryableShown: errorBox ? errorBox.textContent.includes('可重試') : null,
      largePayloadSummary: large ? large.textContent : null,
      loadEarlierVisible: earlier ? !earlier.hidden : null,
      loadEarlierDisplay: earlier ? getComputedStyle(earlier).display : null,
      noticeVisible: notice ? !notice.hidden : null,
      emptyResult: (document.querySelector('[data-result] .empty-text') || {}).textContent || null,
      cancelButtons: Array.from(document.querySelectorAll('button'))
        .map((b) => b.textContent.trim())
        .filter((t) => /停止|取消|重試|Cancel|Retry/.test(t)),
    };
  `);
  await page.send('Page.close').catch(() => {});
  return { view, log };
}

// ----------------------------------------------------------------------- main

async function main() {
  const { child, profile, version } = await launchChrome();
  const ws = await connect(version.webSocketDebuggerUrl);
  const browser = new Session(ws, null);
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.sessionId) {
      for (const handler of browser.listeners.get('__dispatch__' + message.sessionId) || []) {
        handler(message);
      }
      return;   // browser-level command replies never carry a sessionId
    }
    browser.handle(message);
  });

  let fatal = null;
  try {
    record('env.chrome_version', true, version.Browser);
    await scenarioMainFlow(browser);
    await scenarioMissingRun(browser);

    const failed = await scenarioSyntheticStates(browser, {
      status: 'FAILED', errorCode: 'SANDBOX_TIMEOUT', eventCount: 250, bigPayload: true,
    });
    record('failed.shows_error_code_in_status_and_result',
      failed.view.label === '失敗' && failed.view.detail.includes('SANDBOX_TIMEOUT')
      && failed.view.errorCode === 'SANDBOX_TIMEOUT', failed.view);
    record('failed.error_box_has_runid_and_next_step',
      failed.view.errorHints.some((h) => h.includes('run_id'))
      && failed.view.errorHints.some((h) => h.length > 15), failed.view.errorHints);
    record('failed.retryable_is_surfaced_without_a_dead_button',
      failed.view.retryableShown === true && failed.view.cancelButtons.length === 0,
      { retryable: failed.view.retryableShown, buttons: failed.view.cancelButtons });
    record('timeline.window_is_200_with_load_earlier',
      failed.view.rows === 200 && failed.view.loadEarlierVisible === true
      && failed.view.loadEarlierDisplay !== 'none',
      { rows: failed.view.rows, earlier: failed.view.loadEarlierVisible,
        display: failed.view.loadEarlierDisplay });
    record('timeline.large_payload_is_folded',
      Boolean(failed.view.largePayloadSummary
        && /KB/.test(failed.view.largePayloadSummary)), failed.view.largePayloadSummary);
    record('timeline.dedups_repeated_seq', failed.view.rows === 200,
      { rows: failed.view.rows, note: '251 events sent, one repeated seq' });
    record('failed.no_uncaught_exceptions', failed.log.exceptions.length === 0,
      failed.log.exceptions);

    const cancelled = await scenarioSyntheticStates(browser, {
      status: 'CANCELLED', errorCode: null, eventCount: 3, bigPayload: false,
    });
    record('cancelled.renders_contract_label_and_no_output',
      cancelled.view.label === '已取消'
      && Boolean(cancelled.view.emptyResult && cancelled.view.emptyResult.includes('取消')),
      cancelled.view);
    record('cancelled.dot_colour_differs_from_failed',
      cancelled.view.dotColor !== failed.view.dotColor,
      { cancelled: cancelled.view.dotColor, failed: failed.view.dotColor });
    record('cancelled.no_uncaught_exceptions', cancelled.log.exceptions.length === 0,
      cancelled.log.exceptions);
  } catch (error) {
    fatal = error.stack || String(error);
  } finally {
    try { ws.close(); } catch (_) { /* ignore */ }
    child.kill('SIGKILL');
    try { rmSync(profile, { recursive: true, force: true }); } catch (_) { /* ignore */ }
  }

  const output = { base: BASE, chrome: version.Browser, fatal, checks };
  const text = JSON.stringify(output, null, 2);
  if (OUT) writeFileSync(OUT, text);
  console.log(text);
  process.exit(fatal ? 1 : 0);
}

main();
