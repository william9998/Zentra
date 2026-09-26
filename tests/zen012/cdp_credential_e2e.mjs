// ZEN-012 憑證卡的瀏覽器驗證 —— headless Chrome over CDP，零相依（ADR-0006）。
//
//   node tests/zen012/cdp_credential_e2e.mjs --base http://127.0.0.1:8000
//
// 印出一份 JSON 檢查表。它自己不決定通過與否。
//
// 最重要的一條是 `key_never_appears_in_dom`：契約保證金鑰讀不回來，
// 但畫面可能自己把使用者剛打的那串留在 DOM 裡 —— 那一樣是洩漏。

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const args = process.argv.slice(2);
const argOf = (n, d) => { const i = args.indexOf('--' + n); return i === -1 ? d : args[i + 1]; };
const BASE = argOf('base', 'http://127.0.0.1:8000');
const SECRET = 'sk-console-e2e-TAIL';

const checks = [];
const record = (name, ok, detail = null) => checks.push({ name, ok, detail });

let nextId = 1;
function send(ws, method, params = {}, sessionId = null) {
  const id = nextId++;
  return new Promise((resolve, reject) => {
    const onMessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.id !== id) return;
      ws.removeEventListener('message', onMessage);
      msg.error ? reject(new Error(method + ': ' + JSON.stringify(msg.error))) : resolve(msg.result);
    };
    ws.addEventListener('message', onMessage);
    ws.send(JSON.stringify(sessionId ? { id, method, params, sessionId } : { id, method, params }));
  });
}

async function main() {
  // 建一個乾淨的 Agent
  const created = await (await fetch(`${BASE}/v1/agents`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'console-cred-e2e-' + Date.now(), description: 'ZEN-012 E2E' }),
  })).json();
  const agentId = created.agent_id;

  const profile = mkdtempSync(join(tmpdir(), 'zen012-chrome-'));
  const port = 9300 + Math.floor(Math.random() * 600);
  const child = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu', 'about:blank',
  ], { stdio: 'ignore' });

  let version = null;
  for (let i = 0; i < 100 && !version; i += 1) {
    try { version = await (await fetch(`http://127.0.0.1:${port}/json/version`)).json(); }
    catch { await sleep(100); }
  }

  const ws = new WebSocket(version.webSocketDebuggerUrl);
  await new Promise((r, j) => {
    ws.addEventListener('open', r, { once: true });
    ws.addEventListener('error', () => j(new Error('ws')), { once: true });
  });

  const { targetId } = await send(ws, 'Target.createTarget', { url: 'about:blank' });
  const { sessionId } = await send(ws, 'Target.attachToTarget', { targetId, flatten: true });
  const evaluate = async (expression) => {
    const r = await send(ws, 'Runtime.evaluate', {
      expression: `(async () => { ${expression} })()`,
      returnByValue: true, awaitPromise: true,
    }, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const waitFor = async (expr, ms = 20000) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (await evaluate(`return (${expr});`)) return true;
      await sleep(100);
    }
    return false;
  };

  try {
    await send(ws, 'Page.enable', {}, sessionId);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents/${agentId}` }, sessionId);
    await sleep(1200);

    record('card.renders', await waitFor(`document.querySelector('#cred-api-key') !== null`));

    // 未設定是剛建立 Agent 的預設狀態（建立對話框刻意不收金鑰），
    // 所以這段文字要同時說出「現在用平台的」與「設定一次所有版本都會用」。
    record('card.empty_state_guides_the_user', await evaluate(`
      const text = document.querySelector('[data-cred-summary]').textContent || '';
      return text.includes('平台預設') && text.includes('所有版本');
    `), '未設定時要給得出下一步，不只是陳述沒設定');

    record('card.key_input_is_password_type',
      await evaluate(`return document.querySelector('#cred-api-key').type === 'password';`),
      '金鑰欄位不能是明文 input');

    record('card.submit_disabled_when_empty',
      await evaluate(`return document.querySelector('[data-cred-submit]').disabled === true;`));

    // 填入金鑰並送出
    await evaluate(`
      const input = document.querySelector('#cred-api-key');
      input.value = ${JSON.stringify(SECRET)};
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    `);
    record('card.submit_enabled_after_typing',
      await evaluate(`return document.querySelector('[data-cred-submit]').disabled === false;`));

    await evaluate(`document.querySelector('[data-cred-form]').requestSubmit(); return true;`);
    record('card.saved',
      await waitFor(`(document.querySelector('[data-cred-summary]').textContent || '').includes('TAIL')`),
      '摘要應顯示尾四碼');

    record('card.key_input_cleared_after_save',
      await evaluate(`return document.querySelector('#cred-api-key').value === '';`),
      '送出後金鑰不該留在輸入框裡');

    // 最重要的一條
    record('card.key_never_appears_in_dom',
      await evaluate(`return !document.documentElement.outerHTML.includes(${JSON.stringify(SECRET)});`),
      '整份 DOM 都不能出現金鑰明文');

    record('card.remove_button_appears_once_configured',
      await evaluate(`return document.querySelector('[data-cred-remove]').hidden === false;`));

    // 重新整理後仍是已設定，而且仍然讀不到金鑰
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents/${agentId}` }, sessionId);
    await sleep(1200);
    record('card.persists_across_reload',
      await waitFor(`(document.querySelector('[data-cred-summary]') || {}).textContent?.includes('TAIL')`));
    record('card.key_still_absent_after_reload',
      await evaluate(`return !document.documentElement.outerHTML.includes(${JSON.stringify(SECRET)});`));
  } catch (error) {
    record('fatal', false, String(error));
  } finally {
    child.kill('SIGKILL');
    rmSync(profile, { recursive: true, force: true });
    await fetch(`${BASE}/v1/agents/${agentId}`, { method: 'DELETE' }).catch(() => {});
  }

  console.log(JSON.stringify({ checks }, null, 2));
  process.exit(checks.every((c) => c.ok) ? 0 : 1);
}

main();
