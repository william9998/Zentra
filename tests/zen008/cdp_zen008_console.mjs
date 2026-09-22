// ZEN-008 Console E2E driver（Z8-28 註冊版本 / Z8-29 呼叫 Agent）。
//
//   node tests/zen008/cdp_zen008_console.mjs --base http://127.0.0.1:8000 \
//        --image-ref 'localhost:5010/zentra-qa/agent@sha256:...' --out result.json
//
// 只輸出具名檢查；pass/fail 由 tests/zen008/test_console_e2e.py 判定。
// 它操作的是**真後端**：建立的 Agent、註冊的版本、發起的 Run 都會真的存在。

import { rmSync, writeFileSync } from 'node:fs';
import {
  connect, evaluate, launchChrome, navigate, newPage, Session, sleep, waitFor,
} from './cdp_lib.mjs';

const args = process.argv.slice(2);
const argOf = (name, fallback) => {
  const i = args.indexOf('--' + name);
  return i === -1 ? fallback : args[i + 1];
};
const BASE = argOf('base', 'http://127.0.0.1:8000');
const OUT = argOf('out', null);
const IMAGE_REF = argOf('image-ref', null);
const BAD_IMAGE_REF = argOf('bad-image-ref', null);
const AGENT_NAME = 'zen008-e2e-' + Math.random().toString(16).slice(2, 10);

const checks = [];
const record = (id, ok, detail) => {
  checks.push({ id, ok: ok === null ? null : Boolean(ok), detail: detail === undefined ? null : detail });
};

const XSS_QUESTION = '<img src=x onerror="window.__xssFired=1"><script>window.__xssFired=2<\/script>';

// --------------------------------------------------------------- Z8-28 註冊

async function scenarioRegister(browser) {
  const { page, log } = await newPage(browser);
  await navigate(page, BASE + '/console/agents');

  // 建立 Agent metadata
  await evaluate(page, `document.querySelector('[data-create]').click(); return true;`);
  await sleep(150);
  await evaluate(page, `
    const dialog = document.querySelector('[data-dialog] dialog');
    const name = dialog.querySelector('#agent-name');
    name.value = ${JSON.stringify(AGENT_NAME)};
    name.dispatchEvent(new Event('input', { bubbles: true }));
    dialog.querySelector('form').requestSubmit();
    return true;
  `);
  const agentId = await waitFor(page, `
    (() => {
      const link = Array.from(document.querySelectorAll('a[href^="/console/agents/"]'))
        .find((a) => a.textContent.includes(${JSON.stringify(AGENT_NAME)}));
      if (link) return link.getAttribute('href').split('/').pop();
      const m = location.pathname.match(/\\/console\\/agents\\/([0-9a-f-]{36})/);
      return m ? m[1] : null;
    })()
  `, 30000);
  record('agents.create_dialog_creates_metadata_only', Boolean(agentId), { agentId });

  const createPost = log.requests.filter((r) => r.method === 'POST').pop();
  record('agents.create_uses_the_contract_endpoint',
    Boolean(createPost) && new URL(createPost.url).pathname === '/v1/agents',
    createPost || null);

  // --- 註冊版本頁
  await navigate(page, BASE + '/console/agents/' + agentId + '/versions/new');
  await waitFor(page, `document.querySelector('form') !== null`);
  const form = await evaluate(page, `
    const inputs = Array.from(document.querySelectorAll('input, textarea, select'));
    return {
      fileInputs: document.querySelectorAll('input[type=file]').length,
      dropzones: document.querySelectorAll('[data-dropzone], [ondrop]').length,
      fields: inputs.map((i) => ({ id: i.id, type: i.type, name: i.name })),
      bodyText: document.body.textContent,
    };
  `);
  const fieldBlob = JSON.stringify(form.fields).toLowerCase();
  record('version_new.has_no_upload_or_credential_fields',
    form.fileInputs === 0 && form.dropzones === 0
    && !/password|token|secret|credential|registry_auth|dockerfile/.test(fieldBlob),
    form.fields);
  const labels = await evaluate(page, `
    return Array.from(document.querySelectorAll('label, .field-hint, [placeholder]'))
      .map((n) => (n.textContent || '') + '|' + (n.getAttribute('placeholder') || ''))
      .join(' ');
  `);
  record('version_new.asks_for_semver_and_canonical_image_ref',
    /semver/i.test(labels) && /sha256/i.test(labels), labels.slice(0, 300));

  // --- 送出：request body 必須只有 semver + image_ref
  await page.send('Network.setRequestInterception', { patterns: [] }).catch(() => {});
  const bodies = [];
  page.on('Network.requestWillBeSent', (p) => {
    if (p.request.method === 'POST' && p.request.url.includes('/versions')) {
      bodies.push({ url: p.request.url, body: p.request.postData || null,
                    headers: p.request.headers });
    }
  });

  const semverField = await evaluate(page, `
    const fields = Array.from(document.querySelectorAll('input, textarea'));
    const semver = fields.find((f) => /semver|version/i.test(f.id + f.name));
    const image = fields.find((f) => /image/i.test(f.id + f.name));
    if (!semver || !image) return null;
    semver.value = '1.0.0';
    semver.dispatchEvent(new Event('input', { bubbles: true }));
    image.value = ${JSON.stringify(IMAGE_REF)};
    image.dispatchEvent(new Event('input', { bubbles: true }));
    return { semverId: semver.id, imageId: image.id };
  `);
  record('version_new.form_exposes_exactly_semver_and_image_ref', Boolean(semverField),
    semverField);

  await evaluate(page, `document.querySelector('[data-form]').requestSubmit(); return true;`);
  // P5 的 UI 落點：送出前必須有一次不可變的確認，不是一鍵就寫進 Registry
  const confirmed = await waitFor(page, `
    (() => {
      const dialog = document.querySelector('[data-confirm-dialog]');
      return dialog && dialog.open ? dialog.textContent : null;
    })()
  `, 15000, 200).catch(() => null);
  record('version_new.confirms_immutability_before_submitting',
    Boolean(confirmed) && /不可|immutable/i.test(confirmed) && confirmed.includes('sha256:'),
    confirmed ? confirmed.slice(0, 200) : null);
  await evaluate(page, `document.querySelector('[data-confirm]').click(); return true;`);
  await sleep(1500);

  const registerPost = bodies[0] || null;
  let parsed = null;
  try { parsed = registerPost ? JSON.parse(registerPost.body) : null; } catch (_) { parsed = null; }
  record('version_new.request_body_is_exactly_semver_and_image_ref',
    Boolean(parsed) && JSON.stringify(Object.keys(parsed).sort())
      === JSON.stringify(['image_ref', 'semver'])
    && parsed.image_ref === IMAGE_REF,
    { body: parsed, contentType: registerPost ? registerPost.headers['Content-Type']
      || registerPost.headers['content-type'] : null });
  const headerBlob = JSON.stringify(registerPost ? registerPost.headers : {}).toLowerCase();
  record('version_new.request_carries_no_trigger_or_credential_header',
    !/trigger|authorization|registry|password/.test(headerBlob), headerBlob.slice(0, 300));

  // --- 輪詢到終態，畫面要顯示 PUBLISHED
  const sawBuilding = await evaluate(page, `
    const status = document.querySelector('[data-status] .status');
    return status ? status.className : null;
  `);
  const statusText = await waitFor(page, `
    (() => {
      const box = document.querySelector('[data-status]');
      const status = box && !box.hidden ? box.querySelector('.status') : null;
      if (!status) return null;
      return /status--(PUBLISHED|FAILED)/.test(status.className)
        ? status.className + ' :: ' + box.textContent : null;
    })()
  `, 180000, 1000);
  record('version_new.polls_building_to_published',
    /status--PUBLISHED/.test(statusText) && /已發布/.test(statusText)
    && statusText.includes(IMAGE_REF),
    { sawBuilding, terminal: statusText.replace(/\s+/g, ' ').slice(0, 300) });

  // --- 完整 digest 顯示得出來、沒有被截斷成 tag
  const digest = await evaluate(page, `
    const text = document.body.textContent;
    const nodes = Array.from(document.querySelectorAll('*'))
      .filter((n) => n.children.length === 0 && n.textContent.includes('sha256:'));
    const overflowing = nodes.filter((n) => n.scrollWidth > n.clientWidth + 2);
    return {
      hasFullRef: text.includes(${JSON.stringify(IMAGE_REF)}),
      sha256Count: (text.match(/sha256:[0-9a-f]{64}/g) || []).length,
      truncatedCount: (text.match(/sha256:[0-9a-f]{0,20}…/g) || []).length,
      overflowing: overflowing.length,
      docOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
    };
  `);
  record('version_new.full_canonical_digest_is_available_and_does_not_overflow',
    digest.hasFullRef && digest.sha256Count >= 1 && digest.docOverflow <= 0, digest);

  await page.send('Page.close').catch(() => {});
  return { agentId, log };
}

// ------------------------------------------------------ Z8-28 失敗態

async function scenarioFailedVersion(browser, agentId) {
  const { page } = await newPage(browser);
  await navigate(page, BASE + '/console/agents/' + agentId + '/versions/new');
  await waitFor(page, `document.querySelector('form') !== null`);
  await evaluate(page, `
    const fields = Array.from(document.querySelectorAll('input, textarea'));
    const semver = fields.find((f) => /semver|version/i.test(f.id + f.name));
    const image = fields.find((f) => /image/i.test(f.id + f.name));
    semver.value = '9.9.9';
    semver.dispatchEvent(new Event('input', { bubbles: true }));
    image.value = ${JSON.stringify(BAD_IMAGE_REF)};
    image.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('[data-form]').requestSubmit();
    return true;
  `);
  await sleep(400);
  await evaluate(page, `
    const confirm = document.querySelector('[data-confirm]');
    if (confirm) confirm.click();
    return true;
  `);
  const text = await waitFor(page, `
    (() => {
      const box = document.querySelector('[data-status]');
      const status = box && !box.hidden ? box.querySelector('.status') : null;
      if (!status || !/status--(PUBLISHED|FAILED)/.test(status.className)) return null;
      return status.className + ' :: ' + box.textContent;
    })()
  `, 180000, 1000);
  record('version_new.failure_shows_build_failed_message_and_stage',
    /status--FAILED/.test(text) && /BUILD_FAILED/.test(text)
    && /\bPULL\b|\bINSPECT\b|\bVALIDATE\b/.test(text),
    text.replace(/\s+/g, ' ').slice(0, 400));

  // 本地非 canonical digest 應在送出前就被擋掉
  await navigate(page, BASE + '/console/agents/' + agentId + '/versions/new');
  await waitFor(page, `document.querySelector('form') !== null`);
  const local = await evaluate(page, `
    const fields = Array.from(document.querySelectorAll('input, textarea'));
    const semver = fields.find((f) => /semver|version/i.test(f.id + f.name));
    const image = fields.find((f) => /image/i.test(f.id + f.name));
    semver.value = '8.8.8';
    semver.dispatchEvent(new Event('input', { bubbles: true }));
    image.value = 'ghcr.io/acme/agent:latest';
    image.dispatchEvent(new Event('input', { bubbles: true }));
    image.dispatchEvent(new Event('blur', { bubbles: true }));
    return true;
  `);
  await sleep(300);
  await evaluate(page, `document.querySelector('[data-form]').requestSubmit(); return true;`);
  await sleep(500);
  const rejected = await evaluate(page, `
    const error = document.querySelector('[data-image-error]');
    const dialog = document.querySelector('[data-confirm-dialog]');
    return {
      errorHidden: error ? error.hidden : null,
      errorText: error ? error.textContent : null,
      confirmOpened: dialog ? dialog.open : null,
    };
  `);
  record('version_new.tag_only_reference_is_rejected_in_the_form',
    rejected.confirmOpened === false && rejected.errorHidden === false
    && /sha256|digest|tag-only/i.test(rejected.errorText || ''), rejected);

  await page.send('Page.close').catch(() => {});
}

// --------------------------------------------------------------- Z8-29 呼叫

async function scenarioInvoke(browser, agentId) {
  const { page, log } = await newPage(browser);
  const postBodies = [];
  page.on('Network.requestWillBeSent', (p) => {
    if (p.request.method === 'POST' && /\/runs$/.test(new URL(p.request.url).pathname)) {
      postBodies.push({ url: p.request.url, body: p.request.postData || null,
                        headers: p.request.headers });
    }
  });

  await navigate(page, BASE + '/console/agents/' + agentId);
  await waitFor(page, `document.querySelector('[data-form]') && !document.querySelector('[data-form]').hidden`);

  const detail = await evaluate(page, `
    const text = document.body.textContent;
    const endpointValues = Array.from(
      document.querySelectorAll('[data-endpoints] .endpoint-value'))
      .map((n) => n.textContent).filter(Boolean);
    return {
      endpointValues,
      hasRest: endpointValues.some((v) => v.includes('/v1/agents/') && v.endsWith('/runs')),
      hasMcp: endpointValues.some((v) => v.includes('/mcp/agents/')),
      hasOpenapi: endpointValues.some((v) => v.endsWith('/openapi.json')),
      hasDigest: /sha256:[0-9a-f]{64}/.test(text),
      versionOptions: Array.from(document.querySelectorAll('[data-version] option'))
        .map((o) => ({ value: o.value, text: o.textContent.trim(), disabled: o.disabled })),
      entrypointOptions: Array.from(document.querySelectorAll('[data-entrypoint] option'))
        .map((o) => o.value),
    };
  `);
  record('agent_detail.shows_three_facade_endpoints',
    detail.hasRest && detail.hasMcp && detail.hasOpenapi, detail);
  record('agent_detail.shows_full_digest', detail.hasDigest, detail.hasDigest);
  record('agent_detail.only_published_versions_are_selectable',
    detail.versionOptions.length > 0
    && detail.versionOptions.every((o) => !/BUILDING|FAILED/.test(o.text))
    && detail.versionOptions.some((o) => o.value === '1.0.0')
    && !detail.versionOptions.some((o) => o.value === '9.9.9'),
    detail.versionOptions);
  record('agent_detail.entrypoints_come_from_the_published_manifest',
    detail.entrypointOptions.includes('default') && detail.entrypointOptions.includes('slow'),
    detail.entrypointOptions);

  // 後端權威驗證：送一個 schema 不合的 input
  await evaluate(page, `
    const input = document.querySelector('[data-input]');
    input.value = JSON.stringify({ question: 123 });
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('[data-form]').requestSubmit();
    return true;
  `);
  await sleep(900);
  const invalid = await evaluate(page, `
    const inputError = document.querySelector('[data-input-error]');
    const errorSlot = document.querySelector('[data-error]');
    return {
      path: location.pathname,
      inputError: inputError && !inputError.hidden ? inputError.textContent : null,
      apiError: errorSlot && !errorSlot.hidden ? errorSlot.textContent : null,
    };
  `);
  const shown = (invalid.inputError || '') + (invalid.apiError || '');
  record('run_composer.schema_error_keeps_the_user_on_the_form',
    !/^\/console\/runs\/[0-9a-f-]{36}$/.test(invalid.path)
    && /VALIDATION_ERROR|schema|不符|格式|JSON/i.test(shown),
    { ...invalid, shown: shown.replace(/\s+/g, ' ').slice(0, 300) });

  // 正常送出（帶 XSS payload）
  await evaluate(page, `
    const input = document.querySelector('[data-input]');
    input.value = JSON.stringify({ question: ${JSON.stringify(XSS_QUESTION)} });
    input.dispatchEvent(new Event('input', { bubbles: true }));
    document.querySelector('[data-form]').requestSubmit();
    return true;
  `);
  await page.once('Page.loadEventFired', () => true, 30000).catch(() => null);
  await sleep(400);
  const path = await evaluate(page, `return location.pathname;`);
  const runId = path.split('/').pop();
  record('run_composer.navigates_to_the_run_observation_page',
    /^\/console\/runs\/[0-9a-f-]{36}$/.test(path), { path });

  const created = postBodies[postBodies.length - 1] || null;
  let body = null;
  try { body = created ? JSON.parse(created.body) : null; } catch (_) { body = null; }
  record('run_composer.request_body_has_no_trigger_or_container_fields',
    Boolean(body) && Object.keys(body).every(
      (k) => ['version', 'entrypoint', 'input', 'config'].includes(k))
    && !('trigger' in body) && !('image_ref' in body),
    body);
  const headerBlob = JSON.stringify(created ? created.headers : {}).toLowerCase();
  record('run_composer.request_has_no_ingress_discriminator_header',
    !/x-zentra-trigger|x-trigger|x-console/.test(headerBlob), headerBlob.slice(0, 400));

  // 真 SSE 走到終態
  const terminal = await waitFor(page, `
    (() => {
      const label = document.querySelector('.status-label');
      if (!label) return null;
      return ['已完成', '失敗', '已取消'].includes(label.textContent) ? label.textContent : null;
    })()
  `, 180000, 300);
  record('run_observation.reaches_terminal_state_over_real_sse', terminal === '已完成',
    terminal);

  const result = await evaluate(page, `
    const pre = document.querySelector('[data-result] pre.code');
    return {
      text: pre ? pre.textContent : null,
      imgCount: document.querySelectorAll('img').length,
      inlineScripts: Array.from(document.querySelectorAll('script')).filter((s) => !s.src).length,
      xssFired: window.__xssFired === undefined ? null : window.__xssFired,
    };
  `);
  let echoed = null;
  try { echoed = JSON.parse(result.text).echo; } catch (_) { echoed = null; }
  record('run_observation.payload_is_rendered_as_text_not_dom',
    echoed === XSS_QUESTION && result.imgCount === 0 && result.inlineScripts === 0
    && result.xssFired === null, { ...result, echoedMatches: echoed === XSS_QUESTION });

  const apiPaths = [...new Set(log.requests.map((r) => new URL(r.url).pathname)
    .filter((p) => p.startsWith('/v1') || p.startsWith('/internal') || p.startsWith('/mcp')))];
  record('scope.console_never_touches_internal_or_mcp',
    apiPaths.every((p) => p.startsWith('/v1')), apiPaths);

  await page.send('Page.close').catch(() => {});
  return { runId, agentId };
}

// ------------------------------------------------------------------ main

async function main() {
  if (!IMAGE_REF || !BAD_IMAGE_REF) throw new Error('--image-ref and --bad-image-ref required');
  const chrome = await launchChrome();
  const ws = await connect(chrome.version.webSocketDebuggerUrl);
  const browser = new Session(ws, null);
  browser.ws = ws;
  ws.addEventListener('message', (event) => {
    const message = JSON.parse(event.data);
    if (message.sessionId) {
      const handlers = browser.listeners.get('__dispatch__' + message.sessionId) || [];
      for (const handler of handlers) handler(message);
      return;
    }
    browser.handle(message);
  });
  record('env.chrome_version', Boolean(chrome.version.Browser), chrome.version.Browser);

  let fatal = null;
  let outcome = {};
  try {
    const registered = await scenarioRegister(browser);
    await scenarioFailedVersion(browser, registered.agentId);
    outcome = await scenarioInvoke(browser, registered.agentId);
  } catch (error) {
    fatal = String(error && error.stack ? error.stack : error);
  } finally {
    try { chrome.child.kill('SIGKILL'); } catch (_) { /* ignore */ }
    try { rmSync(chrome.profile, { recursive: true, force: true }); } catch (_) { /* ignore */ }
  }

  const text = JSON.stringify({ base: BASE, chrome: chrome.version.Browser, fatal,
                                agentName: AGENT_NAME, ...outcome, checks }, null, 2);
  if (OUT) writeFileSync(OUT, text);
  else console.log(text);
  process.exit(fatal ? 1 : 0);
}

main();
