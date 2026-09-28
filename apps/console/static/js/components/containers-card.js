// containers-card.js —— 這個 Agent 的常駐容器（ZEN-015 / ADR-0012）。
//
// 四件事決定了它長什麼樣：
//
// 1. **容器現在會自己起來。** 第一筆 Run 進來時平台會拉一個並等它 ——
//    這張卡上的「啟動」是**預熱**，把冷啟動的成本挪到使用者方便的時候，
//    不是「不按就不能用」。文案要說清楚，否則使用者會以為忘了按就壞了。
// 2. **閒置會自己收掉。** 所以「容器不見了」通常不是故障。空狀態要講這件事，
//    不然每次回來看到空的都會以為出事。
// 3. **`restart_count` 一定要顯示**（ui-contract §2.2）。一直重啟的容器是壞掉了，
//    不是需要再重啟一次 —— 少了這個數字，那件事看起來跟「剛啟動」一模一樣。
// 4. **容器綁單一版本**（P5）。發新版會出現第二個容器，切換期間兩個並存，
//    所以這裡是列表不是單筆。
//
// 屬性名一律 `data-ctr-*` 前綴：同一頁還有 `data-cred-*`、`data-keys-*`、
// `data-usage-*` 與兩個對話框的通用名。

import {
  ApiError, listAgentContainers, restartAgentContainer, startAgentContainer,
  stopAgentContainer,
} from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';

//: 有容器在 STARTING 時才輪詢。沒有的話這張卡完全不發請求。
const POLL_MS = 2000;

//: 這些狀態表示還在變化中，值得再看一次。
const TRANSIENT = ['STARTING'];

const STATUS_LABEL = {
  STARTING: '啟動中',
  READY: '服務中',
  UNHEALTHY: '不健康',
  STOPPED: '已停止',
  FAILED: '啟動失敗',
};

//: 借用既有的狀態圓點命名，不自創 class（ui-contract §2 的狀態語言）。
const STATUS_DOT = {
  STARTING: 'SCHEDULING',
  READY: 'SUCCEEDED',
  UNHEALTHY: 'FAILED',
  STOPPED: 'CANCELLED',
  FAILED: 'FAILED',
};

const FRAME = `
  <div class="containers-card" data-ctr-root>
    <p class="field-hint">
      容器會在<strong>第一筆 Run 進來時自己啟動</strong>，閒置一段時間後自己停掉。
      下面的「預熱」只是把冷啟動的成本提前付掉 —— 不按也能用，第一筆會多等一下。
    </p>
    <div class="form-actions">
      <button type="button" class="btn-secondary btn-sm" data-ctr-start>預熱容器</button>
    </div>
    <div data-ctr-error hidden></div>
    <div class="containers-list" data-ctr-list></div>
    <p class="visually-hidden" data-ctr-live aria-live="polite"></p>
  </div>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const startButton = root.querySelector('[data-ctr-start]');
  const errorSlot = root.querySelector('[data-ctr-error]');
  const list = root.querySelector('[data-ctr-list]');
  const live = root.querySelector('[data-ctr-live]');

  let agentId = props.agentId || '';
  const timers = props.timers;
  let rows = [];
  let pollTimer = null;
  let busy = false;
  let disposed = false;

  const clearError = () => { errorSlot.hidden = true; errorSlot.replaceChildren(); };
  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };

  const syncPolling = () => {
    const changing = rows.some((row) => TRANSIENT.indexOf(row.status) !== -1);
    if (changing && pollTimer === null && timers) {
      pollTimer = timers.every(POLL_MS, () => { load(); });
    } else if (!changing && pollTimer !== null && timers) {
      timers.cancel(pollTimer);
      pollTimer = null;
    }
  };

  const action = async (label, work) => {
    if (busy) return;
    busy = true;
    startButton.disabled = true;
    clearError();
    try {
      await work();
      live.textContent = label;
      await load();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error);
    } finally {
      busy = false;
      if (!disposed) startButton.disabled = false;
    }
  };

  const rowNode = (row) => {
    const node = el('div', 'data-row data-row--containers');

    const identity = el('div', 'list-identity');
    identity.append(el('span', 'list-title mono', row.agentVersion));
    identity.append(el('span', 'list-description',
      row.startedAt ? '啟動於 ' + formatDateTime(row.startedAt) : '尚未啟動'));
    node.append(identity);

    const state = el('span', 'status');
    state.append(el('span', 'status-dot status-dot--' + (STATUS_DOT[row.status] || 'QUEUED')));
    state.append(el('span', 'status-label', STATUS_LABEL[row.status] || row.status));
    node.append(state);

    node.append(el('span', 'mono text-xs',
      row.inflightRuns + ' / ' + row.maxConcurrentRuns));

    // 重啟次數只有在不是 0 的時候才值得佔版面，但它一旦不是 0 就很重要
    node.append(el('span', 'text-xs text-secondary',
      row.restartCount > 0 ? '重啟過 ' + row.restartCount + ' 次' : ''));

    const actions = el('div', 'data-row-actions');
    if (row.status !== 'STOPPED') {
      const restart = el('button', 'btn-ghost btn-sm', '重啟');
      restart.type = 'button';
      restart.addEventListener('click', () => action('容器重啟中。',
        () => restartAgentContainer(agentId, row.containerId)));
      actions.append(restart);

      const stop = el('button', 'btn-ghost btn-sm', '停止');
      stop.type = 'button';
      stop.addEventListener('click', () => action('容器停止中。',
        () => stopAgentContainer(agentId, row.containerId)));
      actions.append(stop);
    }
    node.append(actions);

    if (row.error) {
      // 錯誤字串來自 Docker 或使用者的 image，一律 textContent
      const detail = el('p', 'field-hint mono text-2xs', row.error);
      const wrapper = el('div', 'containers-error');
      wrapper.append(detail);
      node.append(wrapper);
    }
    return node;
  };

  const render = () => {
    list.replaceChildren();
    if (rows.length === 0) {
      const empty = el('div', 'empty');
      empty.append(el('p', 'empty-text',
        '目前沒有容器。這通常不是故障 —— 沒人呼叫的 Agent 不會佔著記憶體，'
        + '下一筆 Run 進來時會自己起一個。'));
      list.append(empty);
      return;
    }
    const header = el('div', 'data-row data-row--header data-row--containers');
    ['版本', '狀態', '併發', '', ''].forEach((text) => header.append(el('span', null, text)));
    list.append(header);
    rows.forEach((row) => list.append(rowNode(row)));
  };

  const load = async () => {
    if (!agentId) return;
    try {
      rows = await listAgentContainers(agentId);
      if (disposed) return;
      render();
      syncPolling();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error, '無法讀取容器狀態。重新整理頁面再試一次。');
    }
  };

  const onStart = () => action('容器啟動中。', () => startAgentContainer(agentId));
  startButton.addEventListener('click', onStart);

  load();

  return {
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        load();
      }
    },
    destroy() {
      disposed = true;
      startButton.removeEventListener('click', onStart);
      if (pollTimer !== null && timers) timers.cancel(pollTimer);
      pollTimer = null;
      root.replaceChildren();
    },
  };
}
