// pages/run.js —— /console/runs/{run_id}：Run 觀測頁（ui-contract §5.3 的宿主）。
//
// 流程：從 location.pathname 讀 run_id（沒有前端路由器）→ GET 取初始狀態
//      → EventSource 接事件流 → 終態時後端會結束串流，api.js 會立刻 close()。
// 離開頁面時 destroy()：關 EventSource、clearInterval、切斷訂閱。

import {
  ApiError, ERROR_CODE, EVENT_TYPE, RUN_STATUS,
  clientError, getRun, isTerminalStatus, subscribeRunEvents,
} from '../api.js';
import {
  createCopyButton, createTimers, el, formatDateTime, formatDuration, toPrettyJson,
} from '../dom.js';
import { createStore } from '../store.js';
import { mount as mountShell } from '../shell.js';
import { mount as mountGraph } from '../components/run-graph.js';
import { mount as mountTimeline } from '../components/run-timeline.js';

// §2 Run 狀態的視覺語言：圓點 + 文字標籤，永遠不只靠顏色。
const STATUS_LABEL = {
  [RUN_STATUS.QUEUED]: '排隊中',
  [RUN_STATUS.SCHEDULING]: '配置中',
  [RUN_STATUS.RUNNING]: '執行中',
  [RUN_STATUS.SUCCEEDED]: '已完成',
  [RUN_STATUS.FAILED]: '失敗',
  [RUN_STATUS.CANCELLED]: '已取消',
};

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1>Run 觀測</h1>
        <p class="status" data-status aria-live="polite"></p>
      </div>
    </div>
    <div data-page-error hidden></div>
    <section class="card">
      <div class="card-header"><h2 class="card-title">基本資料</h2></div>
      <div class="meta-grid" data-meta></div>
    </section>
    <div class="view-switch" role="group" aria-label="事件檢視方式">
      <button type="button" class="btn-ghost btn-sm" data-view="timeline" aria-pressed="true">時間軸</button>
      <button type="button" class="btn-ghost btn-sm" data-view="graph" aria-pressed="false">流程圖</button>
    </div>
    <div data-timeline></div>
    <section class="card" data-graph-card hidden>
      <div class="card-header"><h2 class="card-title">流程圖</h2></div>
      <div data-graph></div>
    </section>
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">結果</h2>
        <span data-result-actions class="inline-row"></span>
      </div>
      <div data-result></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">用量</h2></div>
      <div class="meta-grid" data-usage></div>
    </section>
  </div>
`;

/** 網址由 api 的 FileResponse 路由給出：/console/runs/{run_id}。 */
function readRunId() {
  const parts = location.pathname.split('/').filter(Boolean);
  if (parts.length >= 3 && parts[0] === 'console' && parts[1] === 'runs') {
    return decodeURIComponent(parts[2]);
  }
  return '';
}

function metaItem(label, value, extra) {
  const item = el('div', 'meta-item');
  item.appendChild(el('span', 'meta-label', label));
  if (extra) {
    const row = el('div', 'inline-row');
    row.appendChild(el('span', 'meta-value', value));
    row.appendChild(extra);
    item.appendChild(row);
  } else {
    item.appendChild(el('span', 'meta-value', value));
  }
  return item;
}

function skeletonGrid(slot, count) {
  slot.replaceChildren();
  for (let i = 0; i < count; i += 1) {
    const item = el('div', 'meta-item');
    item.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
    item.appendChild(el('span', 'skeleton skeleton-line skeleton-line--wide'));
    slot.appendChild(item);
  }
}

/** §4 Error：必須有 error.code、run_id，以及「我現在該做什麼」。 */
function errorBox(error, runId, hint) {
  const box = el('div', 'error-box');
  const head = el('div', 'inline-row');
  head.appendChild(el('span', 'error-code', error.code));
  if (error.retryable) head.appendChild(el('span', 'error-hint', '這個錯誤標記為可重試'));
  box.appendChild(head);
  box.appendChild(el('p', 'error-message', error.message));
  box.appendChild(el('p', 'error-hint mono', 'run_id ' + (error.runId || runId || '—')));
  box.appendChild(el('p', 'error-hint', hint));
  if (error.details && Object.keys(error.details).length > 0) {
    const details = el('details');
    details.appendChild(el('summary', 'error-hint', '錯誤細節'));
    details.appendChild(el('pre', 'code', toPrettyJson(error.details)));
    box.appendChild(details);
  }
  return box;
}

function main() {
  const app = document.getElementById('app');
  const runId = readRunId();
  const timers = createTimers();
  const store = createStore({
    phase: 'loading',     // loading | ready | error
    run: null,
    error: null,
    currentNode: null,
  });

  const shell = mountShell(document.body, {
    breadcrumb: [{ text: 'Runs', href: '/console/runs' }, { text: runId || '(缺少 run_id)' }],
  });

  app.innerHTML = PAGE;   // 固定結構，一次寫入
  const statusSlot = app.querySelector('[data-status]');
  const metaSlot = app.querySelector('[data-meta]');
  const resultSlot = app.querySelector('[data-result]');
  const resultActions = app.querySelector('[data-result-actions]');
  const usageSlot = app.querySelector('[data-usage]');
  const pageErrorSlot = app.querySelector('[data-page-error]');
  const graphCard = app.querySelector('[data-graph-card]');
  const timelineSlot = app.querySelector('[data-timeline]');
  const viewButtons = [...app.querySelectorAll('[data-view]')];
  const graph = mountGraph(app.querySelector('[data-graph]'), { events: [], finished: false });

  // 兩個版面共用 timeline 收到的同一份事件；切換只是顯示哪一個。
  // 圖表不自己訂閱 SSE —— 兩份訂閱會各自漏事件，而且沒人分得出哪一份是對的。
  let view = 'timeline';
  const syncView = () => {
    timelineSlot.hidden = view !== 'timeline';
    graphCard.hidden = view !== 'graph';
    for (const button of viewButtons) {
      button.setAttribute('aria-pressed', String(button.dataset.view === view));
    }
    if (view === 'graph') graph.update({ events: timeline.events });
  };
  const onViewClick = (event) => {
    const button = event.target.closest('[data-view]');
    if (!button) return;
    view = button.dataset.view;
    syncView();
  };
  for (const button of viewButtons) button.addEventListener('click', onViewClick);

  const timeline = mountTimeline(timelineSlot, {
    state: 'loading',
    connection: '準備連線…',
    runId,
  });

  let subscription = null;
  let elapsedTimer = null;
  let refreshing = false;
  let refreshQueued = false;

  // ---- 渲染（狀態變了就整塊重繪；只有事件流是只追加）----

  const elapsedMs = (run) => {
    const start = run.startedAt || run.createdAt;
    if (!start) return null;
    const end = run.finishedAt || new Date();
    return end.getTime() - start.getTime();
  };

  const renderStatus = (state) => {
    statusSlot.replaceChildren();
    if (state.phase === 'loading') {
      statusSlot.className = 'status';
      statusSlot.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
      return;
    }
    if (state.phase === 'error' || !state.run) {
      statusSlot.className = 'status';
      statusSlot.appendChild(el('span', 'status-detail', '無法取得狀態'));
      return;
    }
    const run = state.run;
    statusSlot.className = 'status status--' + run.status;
    const dot = el('span', 'status-dot status-dot--' + run.status);
    dot.setAttribute('aria-hidden', 'true');
    statusSlot.appendChild(dot);
    statusSlot.appendChild(el('span', 'status-label', STATUS_LABEL[run.status] || run.status));

    const parts = [];
    // §2：RUNNING 必須同時顯示當前節點名稱與已耗時。
    if (run.status === RUN_STATUS.RUNNING) parts.push(state.currentNode || '尚未進入節點');
    if (run.status === RUN_STATUS.FAILED && run.error) parts.push(run.error.code);
    const elapsed = elapsedMs(run);
    if (elapsed !== null) parts.push(formatDuration(elapsed));
    if (parts.length > 0) {
      statusSlot.appendChild(el('span', 'status-detail', '· ' + parts.join(' · ')));
    }
  };

  const renderMeta = (state) => {
    if (state.phase === 'loading') {
      skeletonGrid(metaSlot, 4);
      return;
    }
    metaSlot.replaceChildren();
    if (!state.run) {
      metaSlot.appendChild(metaItem('run_id', runId || '—'));
      return;
    }
    const run = state.run;
    metaSlot.appendChild(metaItem(
      'run_id', run.runId,
      createCopyButton(() => run.runId, timers, '複製'),
    ));
    metaSlot.appendChild(metaItem('agent_id', run.agentId));
    metaSlot.appendChild(metaItem('agent_version', run.agentVersion));
    metaSlot.appendChild(metaItem('trigger', run.trigger));
    metaSlot.appendChild(metaItem('建立時間', formatDateTime(run.createdAt)));
    metaSlot.appendChild(metaItem('開始時間', formatDateTime(run.startedAt)));
    metaSlot.appendChild(metaItem('結束時間', formatDateTime(run.finishedAt)));
  };

  const renderResult = (state) => {
    resultActions.replaceChildren();
    resultSlot.replaceChildren();

    if (state.phase === 'loading') {
      resultSlot.appendChild(el('div', 'skeleton skeleton-line skeleton-line--wide'));
      resultSlot.appendChild(el('div', 'skeleton skeleton-line skeleton-line--half'));
      return;
    }
    if (state.phase === 'error' || !state.run) {
      resultSlot.appendChild(el('p', 'empty-text', '這個 Run 讀不到，沒有結果可以顯示。'));
      return;
    }

    const run = state.run;
    if (!isTerminalStatus(run.status)) {
      const box = el('div', 'empty');
      box.appendChild(el('p', 'empty-text', '這裡會出現 Run 的輸出或錯誤。Run 還在進行中，結果尚未產生。'));
      resultSlot.appendChild(box);
      return;
    }

    if (run.status === RUN_STATUS.FAILED) {
      const error = run.error;
      if (!error) {
        resultSlot.appendChild(el('p', 'empty-text', 'Run 失敗，但沒有拿到錯誤物件。請回報這個 run_id。'));
        return;
      }
      resultSlot.appendChild(errorBox(
        error, run.runId,
        // ZEN-006A：POST /v1/runs/{id}/cancel 與重試端點尚未存在，所以這裡不放重試按鈕。
        '請把 code 與 run_id 一併回報。要再跑一次，請回 Runs 頁重新發起一個 Run（重試端點尚未上線）。',
      ));
      resultActions.appendChild(createCopyButton(
        () => 'code: ' + error.code
          + '\nrun_id: ' + (error.runId || run.runId)
          + '\nmessage: ' + error.message,
        timers, '複製錯誤',
      ));
      return;
    }

    if (run.status === RUN_STATUS.CANCELLED) {
      resultSlot.appendChild(el('p', 'empty-text', '這個 Run 已被取消，沒有輸出。'));
      return;
    }

    if (run.output === null || run.output === undefined) {
      resultSlot.appendChild(el('p', 'empty-text', 'Run 已完成，但沒有輸出內容。'));
      return;
    }
    const text = toPrettyJson(run.output);
    resultSlot.appendChild(el('pre', 'code', text));        // textContent，不是 innerHTML
    resultActions.appendChild(createCopyButton(() => text, timers, '複製輸出'));
  };

  const renderUsage = (state) => {
    if (state.phase === 'loading') {
      skeletonGrid(usageSlot, 4);
      return;
    }
    usageSlot.replaceChildren();
    const usage = state.run ? state.run.usage : null;
    if (!usage) {
      usageSlot.appendChild(el('p', 'empty-text', '這裡會出現 LLM 呼叫次數、token 數與成本。Run 結束後才會有。'));
      return;
    }
    const show = (value) => (value === null || value === undefined ? '—' : String(value));
    usageSlot.appendChild(metaItem('llm_calls', show(usage.llmCalls)));
    usageSlot.appendChild(metaItem('input_tokens', show(usage.inputTokens)));
    usageSlot.appendChild(metaItem('output_tokens', show(usage.outputTokens)));
    // cost_usd 是 decimal 字串：直接印，不做浮點運算。
    usageSlot.appendChild(metaItem('cost_usd', show(usage.costUsd)));
  };

  const renderPageError = (state) => {
    if (state.phase !== 'error' || !state.error) {
      pageErrorSlot.hidden = true;
      pageErrorSlot.replaceChildren();
      return;
    }
    pageErrorSlot.hidden = false;
    pageErrorSlot.replaceChildren(errorBox(
      state.error, runId,
      '確認網址裡的 run_id 是否正確，或回 Runs 頁重新發起一個 Run。問題持續時請附上 code 與 run_id 回報。',
    ));
  };

  store.subscribe(renderStatus);
  store.subscribe(renderMeta);
  store.subscribe(renderResult);
  store.subscribe(renderUsage);
  store.subscribe(renderPageError);

  // ---- 資料 ----

  const stopElapsedTimer = () => {
    if (elapsedTimer !== null) {
      timers.cancel(elapsedTimer);
      elapsedTimer = null;
    }
  };

  const startElapsedTimer = () => {
    if (elapsedTimer !== null) return;
    elapsedTimer = timers.every(1000, () => {
      const state = store.get();
      if (!state.run || isTerminalStatus(state.run.status)) {
        stopElapsedTimer();
        return;
      }
      renderStatus(state);      // 只重畫狀態列，不動事件流
    });
  };

  const refreshRun = async () => {
    // SSE 的 run.status 只帶 status，output / error / usage / started_at 要回頭拿完整 Run。
    // 進行中就排隊再跑一次 —— 終態那一次絕對不能被丟掉。
    if (refreshing) {
      refreshQueued = true;
      return;
    }
    refreshing = true;
    try {
      const run = await getRun(runId);
      store.set({ run });
      if (isTerminalStatus(run.status)) stopElapsedTimer();
      else startElapsedTimer();
      // Run 已終結 → 圖表不要再把沒收到 node.end 的節點畫成「執行中」
      graph.update({ finished: isTerminalStatus(run.status), events: timeline.events });
    } catch (error) {
      if (error instanceof ApiError) {
        // 已經有畫面時不要把整頁換掉，只在串流區塊說明。
        timeline.setConnection('狀態更新失敗：' + error.code);
      } else {
        throw error;
      }
    } finally {
      refreshing = false;
      if (refreshQueued) {
        refreshQueued = false;
        refreshRun();
      }
    }
  };

  const openStream = () => {
    subscription = subscribeRunEvents(runId, {
      onOpen() {
        timeline.setConnection('已連線，接收事件中');
      },
      onReconnecting() {
        timeline.setConnection('重新連線中…');
      },
      onEvent(event) {
        event.receivedAt = new Date();
        timeline.append(event);
        if (view === 'graph') graph.update({ events: timeline.events });
        if (event.type === EVENT_TYPE.NODE_START || event.type === EVENT_TYPE.NODE_END) {
          store.set({ currentNode: event.node });
        }
      },
      onStatus() {
        refreshRun();
      },
      onDone() {
        timeline.setConnection('串流已結束');
        stopElapsedTimer();
        refreshRun();
      },
      onFatal(error) {
        timeline.setConnection('連線中斷');
        timeline.setState('error', error);
      },
    });
  };

  const load = async () => {
    if (!runId) {
      const error = clientError(ERROR_CODE.NOT_FOUND, '網址裡沒有 run_id，無法載入這個畫面。');
      store.set({ phase: 'error', error });
      timeline.setState('error', error);
      timeline.setConnection('未連線');
      return;
    }
    try {
      const run = await getRun(runId);
      store.set({ phase: 'ready', run, error: null });
      timeline.setState('empty');
      if (!isTerminalStatus(run.status)) startElapsedTimer();
      openStream();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      store.set({ phase: 'error', error, run: null });
      timeline.setState('error', error);
      timeline.setConnection('未連線');
    }
  };

  const destroy = () => {
    if (subscription) {
      subscription.close();     // EventSource 一定要關
      subscription = null;
    }
    timers.clearAll();          // 所有 setInterval / setTimeout
    for (const button of viewButtons) button.removeEventListener('click', onViewClick);
    graph.destroy();
    timeline.destroy();
    shell.destroy();
    store.clear();
  };

  window.addEventListener('beforeunload', destroy);
  window.addEventListener('pagehide', destroy);

  // 不吞錯誤：非 ApiError 的例外要讓它浮上來（console + 頁面錯誤區）。
  load().catch((error) => {
    store.set({
      phase: 'error',
      error: clientError(
        ERROR_CODE.INTERNAL_ERROR,
        'Console 發生未預期錯誤：' + (error && error.message ? error.message : String(error)),
        runId, true,
      ),
    });
    throw error;
  });

  return { destroy };
}

main();
