import { ApiError, RUN_STATUS, listRuns } from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';
import { mount as mountShell } from '../shell.js';

const STATUS_LABEL = {
  [RUN_STATUS.QUEUED]: '排隊中', [RUN_STATUS.SCHEDULING]: '配置中',
  [RUN_STATUS.RUNNING]: '執行中', [RUN_STATUS.SUCCEEDED]: '已完成',
  [RUN_STATUS.FAILED]: '失敗', [RUN_STATUS.CANCELLED]: '已取消',
};

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1>Runs</h1>
        <p class="page-subtitle">跨 Agent 查找執行紀錄；點選一列進入 SSE Run 觀測頁。</p>
      </div>
    </div>
    <section class="card">
      <form class="filters" data-filters>
        <div class="field">
          <label for="run-agent-filter">Agent ID</label>
          <input type="text" id="run-agent-filter" autocomplete="off" placeholder="全部 Agent">
        </div>
        <div class="field">
          <label for="run-status-filter">狀態</label>
          <select id="run-status-filter">
            <option value="">全部狀態</option><option value="QUEUED">排隊中</option>
            <option value="SCHEDULING">配置中</option><option value="RUNNING">執行中</option>
            <option value="SUCCEEDED">已完成</option><option value="FAILED">失敗</option>
            <option value="CANCELLED">已取消</option>
          </select>
        </div>
        <div class="field">
          <label for="run-trigger-filter">入口</label>
          <select id="run-trigger-filter">
            <option value="">全部入口</option><option value="API">API</option><option value="MCP">MCP</option>
          </select>
        </div>
        <button type="submit" class="btn-secondary">套用篩選</button>
      </form>
    </section>
    <section class="card">
      <div data-state></div><div data-list></div>
      <div class="list-footer" data-footer hidden>
        <button type="button" class="btn-secondary" data-more>載入更多</button>
      </div>
    </section>
  </div>
`;

function main() {
  const app = document.getElementById('app');
  const shell = mountShell(document.body, { breadcrumb: [{ text: 'Runs' }] });
  app.innerHTML = PAGE;
  const filters = app.querySelector('[data-filters]');
  const agent = app.querySelector('#run-agent-filter');
  const status = app.querySelector('#run-status-filter');
  const trigger = app.querySelector('#run-trigger-filter');
  const stateSlot = app.querySelector('[data-state]');
  const listSlot = app.querySelector('[data-list]');
  const footer = app.querySelector('[data-footer]');
  const more = app.querySelector('[data-more]');
  const initialAgent = new URLSearchParams(location.search).get('agent');
  if (initialAgent) agent.value = initialAgent;
  let items = [];
  let nextCursor = null;
  let loading = false;
  let destroyed = false;

  const renderLoading = (append) => {
    if (!append) listSlot.replaceChildren();
    stateSlot.replaceChildren();
    const box = el('div');
    for (let index = 0; index < (append ? 1 : 5); index += 1) {
      const row = el('div', 'data-row');
      row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
      row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
      box.appendChild(row);
    }
    stateSlot.appendChild(box);
  };

  const render = () => {
    stateSlot.replaceChildren();
    listSlot.replaceChildren();
    if (items.length === 0) {
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '目前沒有符合篩選條件的 Run。'));
      const link = el('a', 'btn-secondary', '前往 Agents');
      link.href = '/console/agents';
      empty.appendChild(link);
      listSlot.appendChild(empty);
    } else {
      const list = el('div', 'data-list');
      const header = el('div', 'data-row data-row--run-list data-row--header');
      for (const label of ['狀態', 'Agent', '版本', '入口', '建立時間']) header.appendChild(el('span', null, label));
      list.appendChild(header);
      for (const run of items) {
        const link = el('a', 'data-row data-row--run-list');
        link.href = '/console/runs/' + encodeURIComponent(run.runId);
        const state = el('span', 'status status--' + run.status);
        const dot = el('span', 'status-dot status-dot--' + run.status);
        dot.setAttribute('aria-hidden', 'true');
        state.appendChild(dot);
        state.appendChild(el('span', 'status-label', STATUS_LABEL[run.status] || run.status));
        link.appendChild(state);
        link.appendChild(el('span', 'mono text-xs', run.agentId || '—'));
        link.appendChild(el('span', 'mono text-xs', run.agentVersion || '—'));
        link.appendChild(el('span', 'mono text-xs', run.trigger || '—'));
        link.appendChild(el('span', 'mono text-2xs', formatDateTime(run.createdAt)));
        list.appendChild(link);
      }
      listSlot.appendChild(list);
    }
    footer.hidden = !nextCursor;
    more.disabled = loading;
    more.textContent = loading ? '載入中…' : '載入更多';
  };

  const load = async (append) => {
    if (loading) return;
    loading = true;
    renderLoading(append);
    try {
      const page = await listRuns({ agentId: agent.value.trim(), status: status.value,
        trigger: trigger.value, limit: 50, cursor: append ? nextCursor : null });
      if (destroyed) return;
      items = append ? items.concat(page.items) : page.items;
      nextCursor = page.nextCursor;
      loading = false;
      render();
    } catch (error) {
      loading = false;
      if (!(error instanceof ApiError)) throw error;
      const box = createErrorBox(error, { hint: '請調整篩選或重新載入 Run 列表。' });
      const retry = el('button', 'btn-secondary', '重新載入');
      retry.type = 'button';
      retry.addEventListener('click', () => load(false), { once: true });
      box.appendChild(retry);
      stateSlot.replaceChildren(box);
      footer.hidden = true;
    }
  };

  const onFilter = (event) => { event.preventDefault(); nextCursor = null; load(false); };
  const onMore = () => load(true);
  filters.addEventListener('submit', onFilter);
  more.addEventListener('click', onMore);
  load(false);

  const destroy = () => {
    destroyed = true;
    filters.removeEventListener('submit', onFilter);
    more.removeEventListener('click', onMore);
    shell.destroy();
  };
  window.addEventListener('beforeunload', destroy, { once: true });
  window.addEventListener('pagehide', destroy, { once: true });
  return { destroy };
}

main();
