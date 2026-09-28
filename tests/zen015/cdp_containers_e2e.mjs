// ZEN-015 容器卡的瀏覽器驗證 —— headless Chrome over CDP，零相依（ADR-0006）。
//
//   node tests/zen015/cdp_containers_e2e.mjs --base http://127.0.0.1:8000 --agent <agent_id>
//
// **需要一個已發布版本的 Agent**（要能真的啟動容器）。沒有的話先照 README 推一個
// image 上去註冊。不自己建 Agent 是刻意的：建一個「有 image 的」Agent 需要
// docker push，那不是瀏覽器測試該做的事。
//
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
const AGENT = argOf('agent', '');
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
  const profile = mkdtempSync(join(tmpdir(), 'zen015c-chrome-'));
  const port = 9300 + Math.floor(Math.random() * 600);
  const child = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`, '--no-first-run', '--no-default-browser-check',
    '--disable-gpu', 'about:blank'], { stdio: 'ignore' });
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
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails));
    return r.result.value;
  };
  const waitFor = async (expr, ms = 30000) => {
    const deadline = Date.now() + ms;
    while (Date.now() < deadline) {
      if (await evaluate(`return (${expr});`)) return true;
      await sleep(200);
    }
    return false;
  };

  try {
    await send(ws, 'Page.enable', {}, sessionId);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents/${AGENT}` }, sessionId);
    await sleep(1500);

    record('card.renders', await waitFor(`document.querySelector('[data-ctr-root]') !== null`));
    record('card.explains_auto_start', await evaluate(`
      const t = document.querySelector('[data-ctr-root]').textContent || '';
      return t.includes('第一筆 Run') && t.includes('閒置');
    `), '要說清楚容器會自己起、也會自己收');
    record('card.empty_state_is_not_alarming', await evaluate(`
      const t = document.querySelector('[data-ctr-list]').textContent || '';
      return t.includes('沒有容器') ? t.includes('不是故障') : true;
    `));

    // 按預熱 → 應該出現一列，最後變成服務中
    await evaluate(`document.querySelector('[data-ctr-start]').click(); return true;`);
    record('card.row_appears_after_prewarm',
      await waitFor(`document.querySelectorAll('[data-ctr-list] .data-row').length > 1`));
    record('card.reaches_ready', await waitFor(`
      (document.querySelector('[data-ctr-list]').textContent || '').includes('服務中')
    `, 60000), '輪詢要自己把 STARTING 追到 READY');
    record('card.shows_concurrency', await evaluate(`
      return (document.querySelector('[data-ctr-list]').textContent || '').includes('/ 30');
    `), '要看得到 N');
    record('card.has_stop_and_restart', await evaluate(`
      const t = document.querySelector('[data-ctr-list]').textContent || '';
      return t.includes('重啟') && t.includes('停止');
    `));

    // 停止 → 狀態變成已停止
    await evaluate(`
      const rows = Array.from(document.querySelectorAll('[data-ctr-list] .data-row'));
      const row = rows.find((r) => (r.textContent || '').includes('服務中'));
      const b = row && Array.from(row.querySelectorAll('button')).find((x) => x.textContent === '停止');
      if (b) b.click();
      return true;
    `);
    record('card.stop_reflects', await waitFor(`
      (document.querySelector('[data-ctr-list]').textContent || '').includes('已停止')
    `, 40000));
  } catch (error) {
    record('fatal', false, String(error));
  } finally {
    child.kill('SIGKILL');
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); } catch {}
  }
  console.log(JSON.stringify({ checks }, null, 2));
  process.exit(checks.every((c) => c.ok) ? 0 : 1);
}
main();
