// usage-panel.js —— per-key 的用量與逐筆除錯（ZEN-015 §3.3 / api-contract §2.4）。
//
// 這個面板回答兩個問題，所以它有兩張表：
//
//   「這把 key 花了多少、花在哪個模型上」   → 上半：總計 + 依 key + 依 model
//   「這把 key 的哪一筆 request 出錯了」     → 下半：逐筆 LLM 呼叫，可只看出錯的
//
// 兩個刻意的決定：
//
// 1. **沒歸戶的那一列要留著。** key 為空的是 Console 自己發起的呼叫。把它濾掉
//    會讓總計對不起來，而「這些用量是誰打的」通常正是在查的東西。
// 2. **金額不做運算。** `costUsd` 是十進位字串，乘除或加總都會掉進浮點誤差。
//    要合計就讓後端 SQL 算，前端只負責印。
//
// 屬性名一律 `data-usage-*` 前綴（同頁還有 `data-cred-*` 與 `data-keys-*`）。

import { ApiError, getAgentUsage, listAgentLlmCalls } from '../api.js';
import { createErrorBox, el, formatDateTime, truncate } from '../dom.js';

const PAGE_SIZE = 25;

const FRAME = `
  <div class="usage-panel" data-usage-root>
    <div class="usage-filters" data-usage-filters>
      <div class="field field--inline">
        <label for="usage-key">API key</label>
        <select id="usage-key" data-usage-key>
          <option value="">全部</option>
        </select>
      </div>
      <div class="field field--inline">
        <label for="usage-errors">只看出錯的</label>
        <input type="checkbox" id="usage-errors" data-usage-errors>
      </div>
      <button type="button" class="btn-secondary btn-sm" data-usage-reload>重新整理</button>
    </div>

    <div data-usage-error hidden></div>
    <div class="usage-totals" data-usage-totals></div>

    <div class="usage-split">
      <section class="usage-block">
        <h3 class="usage-heading">依 key</h3>
        <div data-usage-by-key></div>
      </section>
      <section class="usage-block">
        <h3 class="usage-heading">依 model</h3>
        <div data-usage-by-model></div>
      </section>
    </div>

    <section class="usage-block">
      <h3 class="usage-heading">逐筆呼叫</h3>
      <div data-usage-calls></div>
      <div class="list-footer" data-usage-footer hidden>
        <button type="button" class="btn-ghost btn-sm" data-usage-more>載入更多</button>
      </div>
    </section>

    <p class="visually-hidden" data-usage-live aria-live="polite"></p>
  </div>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const keySelect = root.querySelector('[data-usage-key]');
  const errorsOnly = root.querySelector('[data-usage-errors]');
  const reloadButton = root.querySelector('[data-usage-reload]');
  const errorSlot = root.querySelector('[data-usage-error]');
  const totalsSlot = root.querySelector('[data-usage-totals]');
  const byKeySlot = root.querySelector('[data-usage-by-key]');
  const byModelSlot = root.querySelector('[data-usage-by-model]');
  const callsSlot = root.querySelector('[data-usage-calls]');
  const footer = root.querySelector('[data-usage-footer]');
  const moreButton = root.querySelector('[data-usage-more]');
  const live = root.querySelector('[data-usage-live]');

  let agentId = props.agentId || '';
  let nextCursor = null;
  let loading = false;
  let disposed = false;

  const clearError = () => { errorSlot.hidden = true; errorSlot.replaceChildren(); };
  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };

  const filters = () => ({
    keyId: keySelect.value || null,
    errorsOnly: errorsOnly.checked,
  });

  const metric = (label, value) => {
    const item = el('div', 'usage-metric');
    item.append(el('span', 'meta-label', label));
    item.append(el('span', 'meta-value mono', value));
    return item;
  };

  const renderTotals = (totals) => {
    totalsSlot.replaceChildren();
    if (!totals) return;
    totalsSlot.append(metric('呼叫次數', String(totals.llmCalls)));
    totalsSlot.append(metric('輸入 tokens', String(totals.inputTokens)));
    totalsSlot.append(metric('輸出 tokens', String(totals.outputTokens)));
    // 字串直接印，不換算也不四捨五入 —— 這是帳。
    totalsSlot.append(metric('費用 (USD)', totals.costUsd));
  };

  const emptyInto = (slot, text) => {
    const empty = el('div', 'empty');
    empty.append(el('p', 'empty-text', text));
    slot.replaceChildren(empty);
  };

  const renderBreakdown = (slot, columns, rows, cells) => {
    slot.replaceChildren();
    if (rows.length === 0) {
      emptyInto(slot, '這個範圍內沒有 LLM 呼叫。');
      return;
    }
    const header = el('div', 'data-row data-row--header data-row--usage');
    columns.forEach((text) => header.append(el('span', null, text)));
    slot.append(header);
    rows.forEach((row) => {
      const node = el('div', 'data-row data-row--usage');
      cells(row).forEach((cell) => node.append(cell));
      slot.append(node);
    });
  };

  const renderByKey = (rows) => renderBreakdown(
    byKeySlot, ['key', '呼叫', 'tokens', '費用 (USD)'], rows,
    (row) => {
      const identity = el('div', 'list-identity');
      if (row.keyId) {
        identity.append(el('span', 'list-title', row.name || '(已刪除)'));
        identity.append(el('span', 'list-description mono', (row.keyPrefix || '') + '…'));
      } else {
        // 沒有 key 的呼叫不是錯誤，是 Console 自己發起的。講清楚，不要只留一個破折號。
        identity.append(el('span', 'list-title', '未歸戶'));
        identity.append(el('span', 'list-description', 'Console 發起，沒有帶 key'));
      }
      return [
        identity,
        el('span', 'mono', String(row.llmCalls)),
        el('span', 'mono', row.inputTokens + ' / ' + row.outputTokens),
        el('span', 'mono', row.costUsd),
      ];
    },
  );

  const renderByModel = (rows) => renderBreakdown(
    byModelSlot, ['model', '呼叫', 'tokens', '費用 (USD)'], rows,
    (row) => [
      el('span', 'mono', row.model),
      el('span', 'mono', String(row.llmCalls)),
      el('span', 'mono', row.inputTokens + ' / ' + row.outputTokens),
      el('span', 'mono', row.costUsd),
    ],
  );

  const callNode = (call) => {
    const failed = Boolean(call.error) || (call.statusCode !== null && call.statusCode >= 400);
    const node = el('div', 'data-row data-row--calls');

    node.append(el('span', 'text-xs text-secondary',
      call.createdAt ? formatDateTime(call.createdAt) : '—'));

    // Run 連結：查到哪一筆錯了之後，下一個動作一定是去看那個 Run。
    const link = el('a', 'mono text-xs', call.runId.slice(0, 8));
    link.href = '/console/runs/' + encodeURIComponent(call.runId);
    node.append(link);

    node.append(el('span', 'mono text-xs', call.model));
    node.append(el('span', 'mono text-xs',
      call.inputTokens + ' / ' + call.outputTokens));
    node.append(el('span', 'mono text-xs', call.costUsd));
    node.append(el('span', 'text-xs text-secondary',
      call.latencyMs === null ? '—' : call.latencyMs + ' ms'));

    const state = el('span', 'status');
    state.append(el('span', 'status-dot status-dot--' + (failed ? 'FAILED' : 'SUCCEEDED')));
    state.append(el('span', 'status-label',
      call.statusCode === null ? (failed ? '失敗' : '成功') : String(call.statusCode)));
    node.append(state);

    // 錯誤字串來自供應商，一律 textContent（el 的第三個參數就是 textContent）
    node.append(el('span', 'text-xs text-secondary',
      call.error ? truncate(call.error, 80) : ''));
    return node;
  };

  const renderCalls = (items, append) => {
    if (!append) callsSlot.replaceChildren();
    if (items.length === 0 && !append) {
      emptyInto(callsSlot, errorsOnly.checked
        ? '這個範圍內沒有出錯的呼叫。'
        : '這個範圍內沒有 LLM 呼叫。');
      return;
    }
    if (!append) {
      const header = el('div', 'data-row data-row--header data-row--calls');
      ['時間', 'Run', 'model', 'tokens', '費用', '延遲', '狀態', '錯誤'].forEach((text) => {
        header.append(el('span', null, text));
      });
      callsSlot.append(header);
    }
    items.forEach((call) => callsSlot.append(callNode(call)));
  };

  const loadCalls = async (append) => {
    const query = Object.assign(filters(), { limit: PAGE_SIZE });
    if (append && nextCursor) query.cursor = nextCursor;
    const page = await listAgentLlmCalls(agentId, query);
    if (disposed) return;
    nextCursor = page.nextCursor;
    renderCalls(page.items, append);
    footer.hidden = !nextCursor;
  };

  const load = async () => {
    if (!agentId || loading) return;
    loading = true;
    reloadButton.disabled = true;
    clearError();
    try {
      const usage = await getAgentUsage(agentId, filters());
      if (disposed) return;
      renderTotals(usage.totals);
      renderByKey(usage.byKey);
      renderByModel(usage.byModel);
      nextCursor = null;
      await loadCalls(false);
      if (!disposed) live.textContent = '用量已更新。';
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error, '無法讀取用量。重新整理頁面再試一次。');
    } finally {
      loading = false;
      if (!disposed) reloadButton.disabled = false;
    }
  };

  const onMore = async () => {
    moreButton.disabled = true;
    try {
      await loadCalls(true);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error);
    } finally {
      if (!disposed) moreButton.disabled = false;
    }
  };

  const onFilterChange = () => load();

  keySelect.addEventListener('change', onFilterChange);
  errorsOnly.addEventListener('change', onFilterChange);
  reloadButton.addEventListener('click', onFilterChange);
  moreButton.addEventListener('click', onMore);

  /** 讓 key 卡片把它的清單推過來，免得同一份資料抓兩次。 */
  const setKeyOptions = (rows) => {
    const chosen = keySelect.value;
    keySelect.replaceChildren();
    keySelect.append(el('option', null, '全部'));
    keySelect.firstChild.value = '';
    (rows || []).forEach((row) => {
      const option = el('option', null,
        row.name + (row.revokedAt ? '（已撤銷）' : ''));
      option.value = row.keyId;
      keySelect.append(option);
    });
    keySelect.value = chosen;
    if (keySelect.value !== chosen) keySelect.value = '';
  };

  if (agentId) load();

  return {
    setKeys(rows) { setKeyOptions(rows); },
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        load();
      }
    },
    destroy() {
      disposed = true;
      keySelect.removeEventListener('change', onFilterChange);
      errorsOnly.removeEventListener('change', onFilterChange);
      reloadButton.removeEventListener('click', onFilterChange);
      moreButton.removeEventListener('click', onMore);
      root.replaceChildren();
    },
  };
}
