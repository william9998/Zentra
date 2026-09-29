// Agents 頁的批次選取與刪除（ui-contract §5.7 / ADR-0013）。
//
//   node tests/zen006a/cdp_agent_bulk_delete_e2e.mjs --base http://127.0.0.1:8000
//
// 自己建三個 Agent 再刪掉它們，所以跑完不留東西。
// 印出一份 JSON 檢查表。它自己不決定通過與否。

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const args = process.argv.slice(2);
const argOf = (n, d) => { const i = args.indexOf('--' + n); return i === -1 ? d : args[i + 1]; };
const BASE = argOf('base', 'http://127.0.0.1:8000');
const TAG = 'zen015-bulk-' + Date.now();

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
  const made = [];
  for (const suffix of ['a', 'b', 'c']) {
    const created = await (await fetch(`${BASE}/v1/agents`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: `${TAG}-${suffix}`, description: '批次刪除 E2E' }),
    })).json();
    made.push(created.agent_id);
  }

  const profile = mkdtempSync(join(tmpdir(), 'bulk-chrome-'));
  const port = 9300 + Math.floor(Math.random() * 600);
  const child = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', '--window-size=1400,900', 'about:blank'], { stdio: 'ignore' });
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
      expression: `(async () => { ${expression} })()`, returnByValue: true, awaitPromise: true,
    }, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };
  const waitFor = async (expr, ms = 20000) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (await evaluate(`return (${expr});`)) return true;
      await sleep(200);
    }
    return false;
  };

  try {
    await send(ws, 'Page.enable', {}, sessionId);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents` }, sessionId);
    await sleep(1800);

    record('delete_button_starts_disabled', await evaluate(
      `return document.querySelector('[data-sel-delete]').disabled === true;`),
      '沒選任何東西時不該可以按');

    // 用搜尋把畫面縮到這次建的三個，全選只該作用在看得到的那些
    await evaluate(`
      const s = document.querySelector('#agent-search');
      s.value = ${JSON.stringify(TAG)};
      s.dispatchEvent(new Event('input', { bubbles: true }));
      return true;`);
    await sleep(400);
    record('search_narrows_to_three', await evaluate(`
      return document.querySelectorAll('[data-list] .data-row:not(.data-row--header)').length === 3;
    `));

    await evaluate(`
      document.querySelector('[data-list] .data-row--header .sel-box').click();
      return true;`);
    await sleep(300);
    record('select_all_picks_only_visible', await evaluate(`
      return (document.querySelector('[data-sel-count]').textContent || '').includes('已選 3 個');
    `), '全選只作用在搜尋後看得到的那些');
    record('delete_button_enabled_after_select', await evaluate(
      `return document.querySelector('[data-sel-delete]').disabled === false;`));

    // 取消其中一個 —— 「可以選擇不要勾」
    await evaluate(`
      const boxes = document.querySelectorAll('[data-list] .data-row:not(.data-row--header) .sel-box');
      boxes[0].click();
      return true;`);
    await sleep(300);
    record('unchecking_one_works', await evaluate(`
      return (document.querySelector('[data-sel-count]').textContent || '').includes('已選 2 個');
    `));

    // 按刪除 → 該跳確認視窗，而且要先說清楚會刪掉什麼
    await evaluate(`document.querySelector('[data-sel-delete]').click(); return true;`);
    await sleep(400);
    record('confirm_dialog_opens', await evaluate(
      `return document.querySelector('[data-sel-confirm]').open === true;`));
    record('confirm_says_it_is_irreversible', await evaluate(`
      const t = document.querySelector('[data-sel-confirm]').textContent || '';
      return t.includes('不可復原') && t.includes('image') && t.includes('Run');
    `), '要講明連 Run 與 image 都會刪掉');
    record('confirm_lists_the_names', await evaluate(`
      return document.querySelectorAll('[data-sel-confirm-names] li').length === 2;
    `));

    // 取消要真的不刪
    await evaluate(`document.querySelector('[data-sel-confirm-cancel]').click(); return true;`);
    await sleep(500);
    record('cancel_deletes_nothing', await evaluate(`
      return document.querySelectorAll('[data-list] .data-row:not(.data-row--header)').length === 3;
    `));

    // 再來一次，這次確認
    await evaluate(`document.querySelector('[data-sel-delete]').click(); return true;`);
    await sleep(400);
    await evaluate(`document.querySelector('[data-sel-confirm-ok]').click(); return true;`);
    record('confirm_deletes_the_selected', await waitFor(`
      document.querySelectorAll('[data-list] .data-row:not(.data-row--header)').length === 1
    `, 30000), '只刪勾選的那兩個，沒勾的要留著');
  } catch (error) {
    record('fatal', false, String(error));
  } finally {
    child.kill('SIGKILL');
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch {}
    for (const id of made) {
      await fetch(`${BASE}/v1/agents/${id}`, { method: 'DELETE' }).catch(() => {});
    }
  }

  console.log(JSON.stringify({ checks }, null, 2));
  process.exit(checks.every((c) => c.ok) ? 0 : 1);
}

main();
