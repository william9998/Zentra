// ZEN-015 的瀏覽器驗證 —— headless Chrome over CDP，零相依（ADR-0006）。
//
//   node tests/zen015/cdp_api_keys_e2e.mjs --base http://127.0.0.1:8000
//
// 印出一份 JSON 檢查表。它自己不決定通過與否。
//
// 最重要的兩條：
//   `plaintext_is_gone_after_dismiss`  關掉那塊之後畫面上不該還留著明文
//   `plaintext_never_returns_on_reload` 平台存的是雜湊，重整之後它**不可能**回來。
//                                       如果回來了，代表某處存了不該存的東西。

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const args = process.argv.slice(2);
const argOf = (n, d) => { const i = args.indexOf('--' + n); return i === -1 ? d : args[i + 1]; };
const BASE = argOf('base', 'http://127.0.0.1:8000');
const KEY_NAME = 'e2e-downstream';

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
  const created = await (await fetch(`${BASE}/v1/agents`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name: 'console-keys-e2e-' + Date.now(), description: 'ZEN-015 E2E' }),
  })).json();
  const agentId = created.agent_id;

  const profile = mkdtempSync(join(tmpdir(), 'zen015-chrome-'));
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

  let plaintext = null;
  try {
    await send(ws, 'Page.enable', {}, sessionId);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents/${agentId}` }, sessionId);
    await sleep(1200);

    record('keys.card_renders', await waitFor(`document.querySelector('#keys-name') !== null`));

    record('keys.empty_state_names_the_cost', await evaluate(`
      const text = document.querySelector('[data-keys-list]').textContent || '';
      return text.includes('未歸戶');
    `), '空狀態要說出「沒發 key 就分不出是誰打的」');

    record('keys.issue_disabled_when_empty',
      await evaluate(`return document.querySelector('[data-keys-issue]').disabled === true;`));

    await evaluate(`
      const input = document.querySelector('#keys-name');
      input.value = ${JSON.stringify(KEY_NAME)};
      input.dispatchEvent(new Event('input', { bubbles: true }));
      return true;
    `);
    record('keys.issue_enabled_after_typing',
      await evaluate(`return document.querySelector('[data-keys-issue]').disabled === false;`));

    await evaluate(`document.querySelector('[data-keys-form]').requestSubmit(); return true;`);
    record('keys.plaintext_shown_once',
      await waitFor(`document.querySelector('.keys-plaintext') !== null`),
      '發完之後明文要出現在畫面上');

    plaintext = await evaluate(`
      const node = document.querySelector('.keys-plaintext');
      return node ? node.textContent : null;
    `);
    record('keys.plaintext_looks_like_a_key',
      typeof plaintext === 'string' && plaintext.startsWith('zk_live_'), plaintext ? 'ok' : 'null');

    record('keys.reveal_warns_it_is_the_only_time', await evaluate(`
      const text = document.querySelector('[data-keys-reveal]').textContent || '';
      return text.includes('只會出現這一次');
    `));

    record('keys.row_shows_prefix_only', await evaluate(`
      const text = document.querySelector('[data-keys-list]').textContent || '';
      return text.includes(${JSON.stringify(KEY_NAME)}) && !text.includes(${JSON.stringify(plaintext || 'zzz')});
    `), '清單只能顯示前綴，不能顯示整把');

    // 關掉揭露區塊之後，畫面上不該還留著明文
    await evaluate(`
      const buttons = Array.from(document.querySelectorAll('[data-keys-reveal] button'));
      const dismiss = buttons.find((b) => (b.textContent || '').includes('複製好了'));
      if (dismiss) dismiss.click();
      return true;
    `);
    await sleep(300);
    record('keys.plaintext_is_gone_after_dismiss',
      await evaluate(`return !document.documentElement.outerHTML.includes(${JSON.stringify(plaintext)});`));

    // 用量面板該拿到 key 清單（卡片推過去的，不是自己抓的）
    record('usage.key_filter_is_populated', await waitFor(`
      Array.from(document.querySelectorAll('[data-usage-key] option'))
        .some((o) => (o.textContent || '').includes(${JSON.stringify(KEY_NAME)}))
    `), '卡片要把 key 清單推給用量面板');

    record('usage.totals_render',
      await waitFor(`(document.querySelector('[data-usage-totals]').textContent || '').includes('費用')`));

    // 撤銷：那一列必須留著，狀態變成已撤銷
    await evaluate(`
      const rows = Array.from(document.querySelectorAll('[data-keys-list] .data-row'));
      const row = rows.find((r) => (r.textContent || '').includes(${JSON.stringify(KEY_NAME)}));
      const button = row && Array.from(row.querySelectorAll('button'))
        .find((b) => (b.textContent || '').includes('撤銷'));
      if (button) button.click();
      return true;
    `);
    await sleep(300);
    await evaluate(`document.querySelector('[data-keys-confirm-ok]').click(); return true;`);
    // waitFor 把字串包成 `return (expr)`，所以這裡只能是**單一運算式**，不能有 const
    record('keys.revoked_row_stays_listed', await waitFor(`
      (document.querySelector('[data-keys-list]').textContent || '')
        .includes(${JSON.stringify(KEY_NAME)})
      && (document.querySelector('[data-keys-list]').textContent || '').includes('已撤銷')
    `), '撤銷過的 key 不該從清單消失——它的用量還掛著');

    // 平台存的是雜湊，所以重整之後明文不可能回來
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents/${agentId}` }, sessionId);
    await sleep(1500);
    record('keys.plaintext_never_returns_on_reload',
      await evaluate(`return !document.documentElement.outerHTML.includes(${JSON.stringify(plaintext)});`));
  } catch (error) {
    record('fatal', false, String(error));
  } finally {
    child.kill('SIGKILL');
    // Chrome 有時候還在寫 profile，刪不掉不是測試失敗 —— 不要讓它蓋掉上面的結果
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
    catch { /* 暫存目錄留著就留著 */ }
    await fetch(`${BASE}/v1/agents/${agentId}`, { method: 'DELETE' }).catch(() => {});
  }

  console.log(JSON.stringify({ checks }, null, 2));
  process.exit(checks.every((c) => c.ok) ? 0 : 1);
}

main();
