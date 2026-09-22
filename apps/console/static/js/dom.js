// dom.js —— 共用的小工具：安全建立節點、圖示、計時器登記、複製、時間格式化。
//
// 註：ui-contract §6.1 的檔案清單沒有這一個模組。加它的理由只有一個 ——
// run.js 與 run-timeline.js 都需要「不用 innerHTML 就能長出節點」與「Copy 按鈕」，
// 重複實作反而更容易寫出 innerHTML 版本（§6.3 規則 3 最常被退回的地方）。
// 這裡不含任何契約欄位名，也沒有相依任何函式庫。

const SVG_NS = 'http://www.w3.org/2000/svg';
const ICON_SPRITE = '/console/static/icons.svg';

/** 建立元素；text 一律走 textContent，永遠不會把字串當 HTML 解析。 */
export function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

/** §3 的 sprite 圖示。顏色靠 currentColor，尺寸靠 CSS class。 */
export function icon(symbolId, extraClass) {
  const svg = document.createElementNS(SVG_NS, 'svg');
  svg.setAttribute('class', extraClass ? 'icon ' + extraClass : 'icon');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('focusable', 'false');
  const use = document.createElementNS(SVG_NS, 'use');
  use.setAttribute('href', ICON_SPRITE + '#' + symbolId);
  svg.appendChild(use);
  return svg;
}

/**
 * 計時器登記簿 —— destroy() 時一次清乾淨。
 * ui-contract §6.3 規則 6：mount 回傳的 destroy 必須真的 clearInterval。
 */
export function createTimers() {
  const pending = new Map();   // id -> 'timeout' | 'interval'

  return {
    after(ms, fn) {
      const id = setTimeout(() => {
        pending.delete(id);
        fn();
      }, ms);
      pending.set(id, 'timeout');
      return id;
    },
    every(ms, fn) {
      const id = setInterval(fn, ms);
      pending.set(id, 'interval');
      return id;
    },
    cancel(id) {
      const kind = pending.get(id);
      if (kind === 'interval') clearInterval(id);
      else if (kind === 'timeout') clearTimeout(id);
      pending.delete(id);
    },
    clearAll() {
      for (const [id, kind] of pending) {
        if (kind === 'interval') clearInterval(id);
        else clearTimeout(id);
      }
      pending.clear();
    },
  };
}

/**
 * 複製文字。clipboard API 在非安全來源（http + 非 localhost）不存在，
 * 所以保留 textarea + execCommand 的退路；兩條路都失敗就回 false，由呼叫端顯示失敗。
 */
export async function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch (_) {
      // 落到下面的退路，不要吞成功。
    }
  }
  const textarea = el('textarea');
  textarea.value = text;
  textarea.setAttribute('readonly', 'readonly');
  textarea.className = 'visually-hidden';
  document.body.appendChild(textarea);
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand('copy');
  } catch (_) {
    ok = false;
  }
  document.body.removeChild(textarea);
  return ok;
}

/**
 * Copy 按鈕（§5.5 的互動：成功後文字暫時轉「已複製」1.5s）。
 * timers 必須由呼叫端提供，destroy 時才有人清掉那個 1.5s 的 timeout。
 */
export function createCopyButton(getText, timers, label) {
  const text = label || 'Copy';
  const button = el('button', 'btn-ghost btn-sm');
  button.type = 'button';
  const labelNode = el('span', null, text);
  button.appendChild(icon('i-copy', 'icon-sm'));
  button.appendChild(labelNode);

  let resetTimer = null;
  const flash = (message) => {
    labelNode.textContent = message;
    if (resetTimer !== null) timers.cancel(resetTimer);
    resetTimer = timers.after(1500, () => {
      labelNode.textContent = text;
      resetTimer = null;
    });
  };

  button.addEventListener('click', async () => {
    const ok = await copyText(getText());
    flash(ok ? '已複製' : '複製失敗');
  });

  return button;
}

const pad = (value, width) => String(value).padStart(width, '0');

/** 事件時間欄：本地時間 HH:MM:SS.mmm（mono）。 */
export function formatClock(date) {
  if (!date) return '--:--:--';
  return pad(date.getHours(), 2) + ':' + pad(date.getMinutes(), 2) + ':'
    + pad(date.getSeconds(), 2) + '.' + pad(date.getMilliseconds(), 3);
}

/** meta 欄位：本地時間 YYYY-MM-DD HH:MM:SS。 */
export function formatDateTime(date) {
  if (!date) return '—';
  return date.getFullYear() + '-' + pad(date.getMonth() + 1, 2) + '-' + pad(date.getDate(), 2)
    + ' ' + pad(date.getHours(), 2) + ':' + pad(date.getMinutes(), 2) + ':' + pad(date.getSeconds(), 2);
}

/** 耗時：§2 的「8.4s」樣式。 */
export function formatDuration(ms) {
  if (typeof ms !== 'number' || !Number.isFinite(ms) || ms < 0) return '—';
  if (ms < 1000) return ms.toFixed(0) + 'ms';
  const seconds = ms / 1000;
  if (seconds < 60) return seconds.toFixed(1) + 's';
  const minutes = Math.floor(seconds / 60);
  return minutes + 'm ' + pad(Math.floor(seconds % 60), 2) + 's';
}

export function formatBytes(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return value + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let amount = value;
  let unit = -1;
  do {
    amount /= 1024;
    unit += 1;
  } while (amount >= 1024 && unit < units.length - 1);
  return amount.toFixed(amount >= 10 ? 0 : 1) + ' ' + units[unit];
}

/** 統一 Error 五態：保留 API message，並顯示 code / run id / details。 */
export function createErrorBox(error, options = {}) {
  const safe = error || {};
  const box = el('div', 'error-box');
  const head = el('div', 'inline-row');
  head.appendChild(el('span', 'error-code', safe.code || 'INTERNAL_ERROR'));
  if (safe.retryable) head.appendChild(el('span', 'error-hint', '可以重試'));
  box.appendChild(head);
  box.appendChild(el('p', 'error-message', safe.message || '發生未預期錯誤'));
  if (safe.runId || options.runId) {
    box.appendChild(el('p', 'error-hint mono', 'Run ID ' + (safe.runId || options.runId)));
  }
  if (options.hint) box.appendChild(el('p', 'error-hint', options.hint));
  if (safe.details && Object.keys(safe.details).length > 0) {
    const details = el('details');
    details.appendChild(el('summary', 'error-hint', '錯誤細節'));
    details.appendChild(el('pre', 'code', toPrettyJson(safe.details)));
    box.appendChild(details);
  }
  return box;
}

export function truncate(text, max) {
  const value = String(text);
  return value.length > max ? value.slice(0, max) + '…' : value;
}

/** JSON 顯示用；循環參照或不可序列化時退回 String()，不要讓畫面掛掉。 */
export function toPrettyJson(value) {
  try {
    return JSON.stringify(value, null, 2);
  } catch (_) {
    return String(value);
  }
}
