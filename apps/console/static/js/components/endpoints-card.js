import { ERROR_CODE } from '../api.js';
import { createCopyButton, createErrorBox, createTimers, el } from '../dom.js';

export function mount(root, props = {}) {
  const timers = createTimers();
  let state = props;

  const render = () => {
    root.replaceChildren();
    if (state.loading) {
      for (let index = 0; index < 3; index += 1) {
        root.appendChild(el('div', 'skeleton skeleton-line skeleton-line--wide'));
      }
      return;
    }
    if (state.error) {
      root.appendChild(createErrorBox(state.error, { hint: '重新載入頁面以取得 endpoints。' }));
      return;
    }
    if (!state.published && !state.building) {
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '尚無已發布版本。發布版本後即可呼叫 REST 與 OpenAPI；要開給 MCP 客戶端，到 MCP 頁勾選。'));
      const link = el('a', 'btn-secondary', '註冊版本');
      link.href = '/console/agents/' + encodeURIComponent(state.agentId) + '/versions/new';
      empty.appendChild(link);
      root.appendChild(empty);
      return;
    }
    const endpoints = state.endpoints || {};
    const rows = [
      ['REST', endpoints.rest],
      ['OpenAPI', endpoints.openapi],
    ];
    if (rows.some((entry) => !entry[1])) {
      root.appendChild(createErrorBox({
        code: ERROR_CODE.INTERNAL_ERROR,
        message: 'Agent 詳情缺少 REST 或 OpenAPI endpoint。',
        details: {},
      }, { hint: '請重新載入；問題持續時請回報 Agent ID。' }));
      return;
    }
    const note = el('p', 'field-hint', state.published
      ? 'REST 與 OpenAPI 使用同一份已發布 entrypoint schema。MCP 是平台單一接口，到 MCP 頁勾選這個 Agent。'
      : '版本尚未發布，網址可先複製，但目前不可呼叫。');
    root.appendChild(note);
    const list = el('div', 'endpoint-list');
    for (const [label, value] of rows) {
      const row = el('div', 'endpoint-row');
      row.appendChild(el('span', 'endpoint-label', label));
      row.appendChild(el('span', 'endpoint-value', value));
      row.appendChild(createCopyButton(() => value, timers, 'Copy'));
      list.appendChild(row);
    }
    root.appendChild(list);
  };

  render();
  return {
    update(next = {}) {
      state = { ...state, ...next };
      render();
    },
    destroy() {
      timers.clearAll();
      root.replaceChildren();
    },
  };
}
