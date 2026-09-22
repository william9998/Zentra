import { ApiError, listAgents } from '../api.js';
import {
  createCopyButton, createErrorBox, createTimers, el, formatDateTime, truncate,
} from '../dom.js';

const FRAME = `
  <div class="list-toolbar">
    <label class="visually-hidden" for="agent-search">搜尋 Agent</label>
    <input type="search" id="agent-search" placeholder="搜尋名稱或描述" autocomplete="off">
  </div>
  <div data-state></div>
  <div data-list></div>
  <div class="list-footer" data-footer hidden>
    <button type="button" class="btn-secondary" data-more>載入更多</button>
  </div>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;
  const search = root.querySelector('#agent-search');
  const stateSlot = root.querySelector('[data-state]');
  const listSlot = root.querySelector('[data-list]');
  const footer = root.querySelector('[data-footer]');
  const more = root.querySelector('[data-more]');
  const timers = createTimers();
  let items = [];
  let nextCursor = null;
  let loading = false;
  let destroyed = false;

  const renderSkeleton = () => {
    listSlot.replaceChildren();
    footer.hidden = true;
    const table = el('div', 'data-list');
    const header = el('div', 'data-row data-row--header');
    header.appendChild(el('span', null, '名稱'));
    header.appendChild(el('span', null, '最新已發布版本'));
    header.appendChild(el('span', null, '更新時間'));
    table.appendChild(header);
    for (let index = 0; index < 5; index += 1) {
      const row = el('div', 'data-row');
      row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
      row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
      table.appendChild(row);
    }
    stateSlot.replaceChildren(table);
  };

  const render = () => {
    stateSlot.replaceChildren();
    listSlot.replaceChildren();
    const needle = search.value.trim().toLocaleLowerCase();
    const shown = needle
      ? items.filter((item) => (item.name + ' ' + (item.description || '')).toLocaleLowerCase().includes(needle))
      : items;

    if (items.length === 0) {
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '建立第一個 Agent，接著註冊 OCI image 版本。'));
      const button = el('button', 'btn-secondary', '建立 Agent');
      button.type = 'button';
      button.addEventListener('click', () => {
        if (typeof props.onCreate === 'function') props.onCreate(button);
      }, { once: true });
      empty.appendChild(button);
      listSlot.appendChild(empty);
    } else if (shown.length === 0) {
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '沒有符合搜尋條件的 Agent。'));
      listSlot.appendChild(empty);
    } else {
      const table = el('div', 'data-list');
      const header = el('div', 'data-row data-row--header');
      header.appendChild(el('span', null, '名稱'));
      header.appendChild(el('span', null, '最新已發布版本'));
      header.appendChild(el('span', null, '更新時間'));
      table.appendChild(header);
      for (const agent of shown) {
        const row = el('div', 'data-row');
        const identity = el('div', 'list-identity');
        const link = el('a', 'list-title', agent.name);
        link.href = '/console/agents/' + encodeURIComponent(agent.agentId);
        identity.appendChild(link);
        identity.appendChild(el('p', 'list-description', truncate(agent.description || '沒有描述', 140)));
        const details = el('details', 'row-details');
        details.appendChild(el('summary', 'text-xs text-secondary', 'Agent ID'));
        const idRow = el('div', 'inline-row');
        idRow.appendChild(el('span', 'meta-value', agent.agentId));
        idRow.appendChild(createCopyButton(() => agent.agentId, timers, '複製'));
        details.appendChild(idRow);
        identity.appendChild(details);
        row.appendChild(identity);
        row.appendChild(el('span', 'mono text-xs', agent.latestPublishedVersion || '尚無已發布版本'));
        row.appendChild(el('span', 'mono text-2xs', formatDateTime(agent.updatedAt || agent.createdAt)));
        table.appendChild(row);
      }
      listSlot.appendChild(table);
    }
    footer.hidden = !nextCursor;
    more.disabled = loading;
    more.textContent = loading ? '載入中…' : '載入更多';
  };

  const load = async (append) => {
    if (loading) return;
    loading = true;
    if (!append) renderSkeleton();
    else {
      render();
      const table = listSlot.querySelector('.data-list');
      if (table) {
        const row = el('div', 'data-row');
        row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
        row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
        table.appendChild(row);
      }
    }
    try {
      const page = await listAgents({ limit: 50, cursor: append ? nextCursor : null });
      if (destroyed) return;
      items = append ? items.concat(page.items) : page.items;
      nextCursor = page.nextCursor;
      loading = false;
      render();
    } catch (error) {
      loading = false;
      if (!(error instanceof ApiError)) throw error;
      if (append) render();
      stateSlot.replaceChildren(createErrorBox(error, { hint: '請重新載入 Agent 列表。' }));
      const retry = el('button', 'btn-secondary', '重新載入');
      retry.type = 'button';
      retry.addEventListener('click', () => load(false), { once: true });
      stateSlot.firstElementChild.appendChild(retry);
      footer.hidden = true;
    }
  };

  const onSearch = () => render();
  const onMore = () => load(true);
  search.addEventListener('input', onSearch);
  more.addEventListener('click', onMore);
  load(false);

  return {
    reload() { return load(false); },
    update() {},
    destroy() {
      destroyed = true;
      search.removeEventListener('input', onSearch);
      more.removeEventListener('click', onMore);
      timers.clearAll();
      root.replaceChildren();
    },
  };
}
