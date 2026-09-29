// 色彩模式切換的瀏覽器驗證（ui-contract v0.7.0 §1.1 / §5.1）。
//
//   node tests/zen006a/cdp_theme_e2e.mjs --base http://127.0.0.1:8000
//   node tests/zen006a/cdp_theme_e2e.mjs --base http://127.0.0.1:8000 --shots <dir>
//
// 印出一份 JSON 檢查表。它自己不決定通過與否。
//
// 最重要的一條是 `nothing_stays_dark`：淺色模式下，畫面上不該還有任何元素
// 保持深色背景。少覆寫一個 token 不會壞掉，它只是留在深色 ——
// 而那在淺色版面上看起來像一塊莫名其妙的暗色，不像少了設定。

import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const args = process.argv.slice(2);
const argOf = (n, d) => { const i = args.indexOf('--' + n); return i === -1 ? d : args[i + 1]; };
const BASE = argOf('base', 'http://127.0.0.1:8000');
const SHOTS = argOf('shots', '');

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
  const profile = mkdtempSync(join(tmpdir(), 'theme-chrome-'));
  const port = 9300 + Math.floor(Math.random() * 600);
  const child = spawn(CHROME, [
    '--headless=new', `--remote-debugging-port=${port}`, `--user-data-dir=${profile}`,
    '--no-first-run', '--no-default-browser-check', '--disable-gpu',
    '--window-size=1280,900', 'about:blank',
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
      expression: `(async () => { ${expression} })()`, returnByValue: true, awaitPromise: true,
    }, sessionId);
    if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 300));
    return r.result.value;
  };
  const shoot = async (name) => {
    if (!SHOTS) return;
    const { data } = await send(ws, 'Page.captureScreenshot', { format: 'png' }, sessionId);
    writeFileSync(join(SHOTS, name + '.png'), Buffer.from(data, 'base64'));
  };

  try {
    await send(ws, 'Page.enable', {}, sessionId);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents` }, sessionId);
    await sleep(1500);

    record('toggle.exists', await evaluate(
      `return document.querySelector('[data-shell-theme]') !== null;`));

    // 沒存過偏好 → 跟隨系統。headless Chrome 預設是 light，所以只斷言「有值」
    const initial = await evaluate(`return document.documentElement.getAttribute('data-theme');`);
    record('theme.applied_on_load', initial === 'dark' || initial === 'light', initial);

    // 先確定停在深色，才有共同的起點
    if (initial !== 'dark') {
      await evaluate(`document.querySelector('[data-shell-theme]').click(); return true;`);
      await sleep(200);
    }
    const darkBg = await evaluate(
      `return getComputedStyle(document.body).backgroundColor;`);
    record('dark.body_is_dark', darkBg === 'rgb(13, 13, 13)', darkBg);
    record('dark.button_offers_light', await evaluate(`
      return (document.querySelector('[data-shell-theme]').textContent || '').trim() === '淺色';
    `), '按鈕說的是按下去會變成什麼');
    await shoot('01-dark');

    // 切到淺色
    await evaluate(`document.querySelector('[data-shell-theme]').click(); return true;`);
    await sleep(300);
    record('light.attribute_set', await evaluate(
      `return document.documentElement.getAttribute('data-theme') === 'light';`));
    const lightBg = await evaluate(`return getComputedStyle(document.body).backgroundColor;`);
    record('light.body_is_light', lightBg === 'rgb(251, 251, 251)', lightBg);
    record('light.text_flipped', await evaluate(`
      return getComputedStyle(document.body).color === 'rgb(31, 31, 31)';
    `), await evaluate(`return getComputedStyle(document.body).color;`));
    record('light.button_offers_dark', await evaluate(`
      return (document.querySelector('[data-shell-theme]').textContent || '').trim() === '深色';
    `));
    record('light.color_scheme_is_set', await evaluate(`
      return getComputedStyle(document.documentElement).colorScheme === 'light';
    `), '原生元件（select / checkbox / 捲軸）要跟著翻');
    await shoot('02-light');

    // **不是「不准有深色」。** `.btn-primary` 的底就是 --accent，淺色模式下它本來
    // 就該是深的（字用 --bg-base，對比很高）。真正的故障是**漏掉覆寫**：那個元素
    // 會留著深底卻配上已經翻成深色的字，變成深底深字 —— 看得到，讀不出來。
    // 所以這裡量的是可讀性，不是顏色。
    record('light.no_unreadable_surface', await evaluate(`
      const lum = (c) => {
        const m = c.match(/^rgba?\\((\\d+), (\\d+), (\\d+)(?:, ([\\d.]+))?\\)$/);
        if (!m) return null;
        if (m[4] !== undefined && Number(m[4]) < 0.5) return null;   // 半透明疊加不算
        return (Number(m[1]) + Number(m[2]) + Number(m[3])) / 3;
      };
      const bad = [];
      for (const node of document.querySelectorAll('*')) {
        const style = getComputedStyle(node);
        const bg = lum(style.backgroundColor);
        const fg = lum(style.color);
        if (bg === null || fg === null) continue;
        // 沒有文字的元素（狀態圓點那種純色塊）拿繼承來的 color 比對沒有意義
        if (!(node.textContent || '').trim()) continue;
        if (Math.abs(bg - fg) < 60) bad.push((node.className || node.tagName) + ' ' + style.backgroundColor + ' / ' + style.color);
      }
      return JSON.stringify([...new Set(bad)].slice(0, 5));
    `) === '[]', '漏掉覆寫的元素會變成深底深字');

    // 重新整理後要記得
    await send(ws, 'Page.navigate', { url: `${BASE}/console/runs` }, sessionId);
    await sleep(1500);
    record('light.persists_across_pages', await evaluate(
      `return document.documentElement.getAttribute('data-theme') === 'light';`),
      '偏好要跨頁記得，不是只在這一頁');
    await shoot('03-light-runs');

    // 切回深色也要記得
    await evaluate(`document.querySelector('[data-shell-theme]').click(); return true;`);
    await sleep(200);
    await send(ws, 'Page.navigate', { url: `${BASE}/console/agents` }, sessionId);
    await sleep(1500);
    record('dark.persists_after_switching_back', await evaluate(
      `return document.documentElement.getAttribute('data-theme') === 'dark';`));
  } catch (error) {
    record('fatal', false, String(error));
  } finally {
    child.kill('SIGKILL');
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 }); }
    catch { /* Chrome 還在寫 profile，刪不掉不是測試失敗 */ }
  }

  console.log(JSON.stringify({ checks }, null, 2));
  process.exit(checks.every((c) => c.ok) ? 0 : 1);
}

main();
