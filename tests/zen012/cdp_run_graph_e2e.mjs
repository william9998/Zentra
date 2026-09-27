// ZEN-014 流程圖版面的瀏覽器驗證 —— headless Chrome over CDP，零相依。
//
//   node tests/zen012/cdp_run_graph_e2e.mjs --base http://127.0.0.1:8000 --run <run_id>
//
// 最重要的一條是 `back_edge_drawn`：retry 迴圈必須在圖上看得見，
// 否則這個版面只是把時間軸換個形狀，沒有買到任何東西。

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const args = process.argv.slice(2);
const argOf = (n, d) => { const i = args.indexOf('--' + n); return i === -1 ? d : args[i + 1]; };
const BASE = argOf('base', 'http://127.0.0.1:8000');
const RUN_ID = argOf('run', '');

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
  const profile = mkdtempSync(join(tmpdir(), 'zen014-chrome-'));
  const port = 9400 + Math.floor(Math.random() * 500);
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
      await sleep(120);
    }
    return false;
  };

  try {
    await send(ws, 'Page.enable', {}, sessionId);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/runs/${RUN_ID}` }, sessionId);
    await sleep(2000);

    record('toggle.exists',
      await waitFor(`document.querySelectorAll('[data-view]').length === 2`));

    record('toggle.timeline_is_default', await evaluate(`
      const t = document.querySelector('[data-view="timeline"]');
      return t.getAttribute('aria-pressed') === 'true'
        && document.querySelector('[data-graph-card]').hidden === true;
    `), '預設是時間軸');

    // 切到流程圖
    await evaluate(`document.querySelector('[data-view="graph"]').click(); return true;`);
    await sleep(400);

    record('graph.card_shown', await evaluate(`
      return document.querySelector('[data-graph-card]').hidden === false
        && document.querySelector('[data-timeline]').hidden === true;
    `));

    record('graph.aria_pressed_moved', await evaluate(`
      return document.querySelector('[data-view="graph"]').getAttribute('aria-pressed') === 'true'
        && document.querySelector('[data-view="timeline"]').getAttribute('aria-pressed') === 'false';
    `), '切換要用 aria-pressed 表示，不只靠顏色');

    record('graph.svg_rendered',
      await waitFor(`document.querySelector('[data-graph-canvas] svg') !== null`));

    const nodes = await evaluate(
      `return document.querySelectorAll('[data-graph-canvas] .graph-node').length;`);
    record('graph.nodes_drawn', nodes >= 4, `畫出 ${nodes} 個節點`);

    // 這是重點：retry 迴圈必須看得見
    const backEdges = await evaluate(
      `return document.querySelectorAll('[data-graph-canvas] .graph-edge--back').length;`);
    record('graph.back_edge_drawn', backEdges >= 1,
      `回頭的邊 ${backEdges} 條 —— 這條沒過的話，流程圖沒有比時間軸多給任何東西`);

    const repeated = await evaluate(
      `return document.querySelectorAll('[data-graph-canvas] .graph-node-count').length;`);
    record('graph.repeat_badge_shown', repeated >= 1, `${repeated} 個節點標了 ×N`);

    // 版面方向：節點必須橫向排開，而不是疊成一欄
    record('graph.lays_out_left_to_right', await evaluate(`
      const boxes = [...document.querySelectorAll('[data-graph-canvas] .graph-node-box')];
      if (boxes.length < 2) return false;
      const xs = boxes.map(b => Number(b.getAttribute('x')));
      const ys = boxes.map(b => Number(b.getAttribute('y')));
      const increasing = xs.every((x, i) => i === 0 || x > xs[i - 1]);
      const sameRow = ys.every(y => y === ys[0]);
      return increasing && sameRow;
    `), '節點要由左而右、同一列');

    // 長流程要能捲，而不是被縮到看不清
    record('graph.scrolls_instead_of_shrinking', await evaluate(`
      const svg = document.querySelector('[data-graph-canvas] svg');
      const canvas = document.querySelector('[data-graph-canvas]');
      const widthAttr = svg.getAttribute('width');
      if (widthAttr.includes('%')) return false;          // 100% 會縮到看不清
      // 內容比容器寬時，容器必須可捲
      if (svg.scrollWidth <= canvas.clientWidth) return true;   // 這次不夠長，不算失敗
      return getComputedStyle(canvas).overflowX === 'auto';
    `), 'SVG 要用自然寬度，容器負責捲動');

    record('graph.legend_mentions_retry', await evaluate(`
      return (document.querySelector('[data-graph-legend]').textContent || '').includes('retry');
    `), '圖例要說明上方弧線是什麼');

    record('graph.no_library_loaded', await evaluate(`
      const srcs = [...document.querySelectorAll('script[src]')].map(s => s.src);
      return srcs.every(s => s.includes('/console/static/'));
    `), 'ADR-0006：不得載入任何外部繪圖函式庫');

    // 切回去要還原
    await evaluate(`document.querySelector('[data-view="timeline"]').click(); return true;`);
    await sleep(300);
    record('toggle.switches_back', await evaluate(`
      return document.querySelector('[data-graph-card]').hidden === true
        && document.querySelector('[data-timeline]').hidden === false;
    `));
  } catch (error) {
    record('fatal', false, String(error));
  } finally {
    child.kill('SIGKILL');
    // Chrome 還在收尾時刪 profile 會 ENOTEMPTY。清不掉不是測試失敗，
    // 只是留下一個暫存目錄 —— 不要讓它蓋掉真正的結果。
    await sleep(200);
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }); }
    catch { /* 暫存目錄清不掉，忽略 */ }
  }

  console.log(JSON.stringify({ checks }, null, 2));
  process.exit(checks.every((c) => c.ok) ? 0 : 1);
}

main();
