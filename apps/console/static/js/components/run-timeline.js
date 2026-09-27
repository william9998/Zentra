// run-timeline.js —— ui-contract §5.3，產品最重要的元件。
//
// 規則：
//  · 只追加渲染（append-only），新事件 append 一列，不重畫整個列表
//  · 預設收合，用原生 <details>/<summary>，展開才看完整 payload
//  · 無虛擬捲動（ADR-0006 的已知降級）：只渲染最近 200 筆，頂端「載入更早的事件」再取 200 筆
//  · 超過 2000 筆提示改用 API 拉完整事件流
//  · 大 payload（> 2KB）再折一層，點擊才展開
//  · 任何來自 API 的字串一律 textContent

import { EVENT_TYPE } from '../api.js';
import {
  createTimers, copyText, el, formatClock, icon, toPrettyJson, truncate,
} from '../dom.js';

const WINDOW_STEP = 200;      // 一次渲染／載入的筆數
const LARGE_PAYLOAD_BYTES = 2048;
const HUGE_RUN_EVENTS = 2000;
const SUMMARY_MAX = 160;

// 固定結構，沒有動態字串。
const FRAME = `
  <section class="card timeline">
    <div class="card-header">
      <h2 class="card-title">事件時間軸</h2>
      <p class="stream-status" data-connection aria-live="polite"></p>
    </div>
    <p class="timeline-notice" data-notice hidden></p>
    <button type="button" class="btn-secondary btn-sm" data-load-earlier hidden>載入更早的事件</button>
    <div data-state></div>
    <ol class="timeline-list" data-list aria-live="polite" aria-label="Run 事件"></ol>
  </section>
`;

const byteLength = (text) => new TextEncoder().encode(text).length;

function formatBytes(bytes) {
  if (bytes < 1024) return bytes + ' B';
  return (bytes / 1024).toFixed(1) + ' KB';
}

/** 7 種事件型別各給一行摘要；未知型別也要能渲染而不崩。 */
function summarize(event) {
  switch (event.type) {
    case EVENT_TYPE.RUN_STATUS:
      return event.status ? '狀態 → ' + event.status : '狀態更新';
    case EVENT_TYPE.NODE_START:
      return event.node ? '節點開始 · ' + event.node : '節點開始';
    case EVENT_TYPE.NODE_END:
      return event.node ? '節點結束 · ' + event.node : '節點結束';
    case EVENT_TYPE.TOKEN:
      return event.delta !== null ? truncate(event.delta, SUMMARY_MAX) : 'token';
    case EVENT_TYPE.LOG:
      return (event.level ? '[' + event.level + '] ' : '')
        + truncate(event.message || '', SUMMARY_MAX);
    case EVENT_TYPE.CUSTOM:
      return event.name ? '自訂事件 · ' + event.name : '自訂事件';
    case EVENT_TYPE.ERROR:
      return (event.code ? event.code + ' · ' : '') + truncate(event.message || '', SUMMARY_MAX);
    default:
      return truncate(toPrettyJson(event.payload), SUMMARY_MAX);
  }
}

function buildPayloadBlock(event) {
  const text = toPrettyJson(event.payload);
  const size = byteLength(text);

  const pre = el('pre', 'code', text);          // textContent，永遠不用 innerHTML
  pre.setAttribute('data-payload', '');

  const header = el('div', 'payload-header');
  header.appendChild(el('span', 'payload-size', 'payload ' + formatBytes(size)));
  const copy = el('button', 'btn-ghost btn-sm');
  copy.type = 'button';
  copy.setAttribute('data-copy', '');
  copy.appendChild(icon('i-copy', 'icon-sm'));
  copy.appendChild(el('span', null, 'Copy'));
  header.appendChild(copy);

  const body = el('div', 'event-body');
  body.appendChild(header);

  if (size > LARGE_PAYLOAD_BYTES) {
    // 大 payload 再折一層：先只顯示大小，點擊才展開。
    const large = el('details', 'payload-large');
    large.appendChild(el('summary', null, '展開完整 payload（' + formatBytes(size) + '）'));
    large.appendChild(pre);
    body.appendChild(large);
  } else {
    body.appendChild(pre);
  }
  return body;
}

function buildRow(event) {
  const row = el('li');
  const details = el('details', 'event');
  const summary = el('summary', 'event-summary');
  summary.appendChild(icon('i-chevron-right', 'icon-sm event-chevron--closed'));
  summary.appendChild(icon('i-chevron-down', 'icon-sm event-chevron--open'));
  summary.appendChild(el('span', 'event-time', formatClock(event.time || event.receivedAt)));
  summary.appendChild(el('span', 'event-type', event.type));
  summary.appendChild(el('span', 'event-text', summarize(event)));
  details.appendChild(summary);
  details.appendChild(buildPayloadBlock(event));
  row.appendChild(details);
  return row;
}

export function mount(root, props = {}) {
  root.innerHTML = FRAME;                       // 固定結構，一次寫入
  const list = root.querySelector('[data-list]');
  const stateSlot = root.querySelector('[data-state]');
  const connection = root.querySelector('[data-connection]');
  const notice = root.querySelector('[data-notice]');
  const loadEarlier = root.querySelector('[data-load-earlier]');
  const timers = createTimers();

  const events = [];        // 全部收到的事件（view model）
  let renderedFrom = 0;     // 目前渲染視窗的起點（events 的索引）
  let windowSize = WINDOW_STEP;

  const refreshControls = () => {
    loadEarlier.hidden = renderedFrom === 0;
    const huge = events.length >= HUGE_RUN_EVENTS;
    notice.hidden = !huge;
    if (huge) {
      notice.textContent = '此 Run 的事件已超過 ' + HUGE_RUN_EVENTS
        + ' 筆。Console 不做虛擬捲動，建議直接用 API 取完整事件流。';
    }
  };

  const trim = () => {
    while (list.childElementCount > windowSize) {
      list.removeChild(list.firstElementChild);
      renderedFrom += 1;
    }
  };

  const clearState = () => stateSlot.replaceChildren();

  const setState = (state, info) => {
    clearState();
    if (state === 'loading') {
      const box = el('div');
      for (let i = 0; i < 3; i += 1) {
        box.appendChild(el('div', 'skeleton skeleton-line skeleton-line--wide'));
      }
      box.appendChild(el('p', 'text-secondary text-xs', '正在載入這個 Run 的事件…'));
      stateSlot.appendChild(box);
      return;
    }
    if (state === 'empty') {
      const box = el('div', 'empty');
      box.appendChild(el('p', 'empty-text', '這裡會出現這個 Run 的每一個事件：狀態變化、節點進出、log 與錯誤。'));
      const reload = el('button', 'btn-secondary', '重新整理');
      reload.type = 'button';
      reload.addEventListener('click', () => location.reload());
      box.appendChild(reload);
      stateSlot.appendChild(box);
      return;
    }
    if (state === 'error') {
      const error = info || {};
      const box = el('div', 'error-box');
      const head = el('div', 'inline-row');
      if (error.code) head.appendChild(el('span', 'error-code', error.code));
      box.appendChild(head);
      box.appendChild(el('p', 'error-message', error.message || '事件串流發生錯誤'));
      // §4：Error 一定要有 run_id。API 的錯誤物件可能沒帶，就退回頁面傳進來的那個。
      const shownRunId = error.runId || props.runId;
      if (shownRunId) box.appendChild(el('p', 'error-hint mono', 'run_id ' + shownRunId));
      box.appendChild(el('p', 'error-hint',
        '事件串流中斷。重新整理頁面可再接一次；若持續失敗，請把上面的 code 與 run_id 一併回報。'));
      stateSlot.appendChild(box);
      return;
    }
    // 'idle' / 'streaming' —— 有列表就不需要狀態區塊
    if (events.length === 0) setState('empty');
  };

  const setConnection = (text) => {
    connection.textContent = text || '';
  };

  const append = (event) => {
    events.push(event);
    clearState();
    list.appendChild(buildRow(event));
    trim();
    refreshControls();
  };

  const onLoadEarlier = () => {
    const target = Math.max(0, renderedFrom - WINDOW_STEP);
    if (target === renderedFrom) return;
    windowSize += WINDOW_STEP;
    const fragment = document.createDocumentFragment();
    for (let i = target; i < renderedFrom; i += 1) fragment.appendChild(buildRow(events[i]));
    list.insertBefore(fragment, list.firstElementChild);
    renderedFrom = target;
    refreshControls();
  };
  loadEarlier.addEventListener('click', onLoadEarlier);

  // Copy 用事件委派：一個 listener 管整個列表，不是每列綁一個。
  const onClick = async (domEvent) => {
    const button = domEvent.target.closest('[data-copy]');
    if (!button || !root.contains(button)) return;
    const pre = button.closest('.event-body').querySelector('[data-payload]');
    const label = button.querySelector('span');
    const ok = await copyText(pre.textContent);
    label.textContent = ok ? '已複製' : '複製失敗';
    timers.after(1500, () => { label.textContent = 'Copy'; });
  };
  root.addEventListener('click', onClick);

  setState(props.state || 'loading');
  setConnection(props.connection);

  return {
    append,
    setState,
    setConnection,
    get length() {
      return events.length;
    },
    /** 圖表版面要讀同一份事件 —— 兩個版面各存一份會漂移。 */
    get events() {
      return events;
    },
    update(next = {}) {
      if (next.state) setState(next.state, next.info);
      if (next.connection !== undefined) setConnection(next.connection);
    },
    destroy() {
      loadEarlier.removeEventListener('click', onLoadEarlier);
      root.removeEventListener('click', onClick);
      timers.clearAll();
      events.length = 0;
      root.replaceChildren();
    },
  };
}
