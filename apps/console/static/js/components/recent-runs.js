import { ApiError, listRuns } from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';

const STATUS_LABEL = {
  QUEUED: '排隊中',
  SCHEDULING: '配置中',
  RUNNING: '執行中',
  SUCCEEDED: '已完成',
  FAILED: '失敗',
  CANCELLED: '已取消',
};

export function mount(root, props) {
  let destroyed = false;
  let requestSerial = 0;

  const renderLoading = () => {
    root.replaceChildren();
    for (let index = 0; index < 3; index += 1) {
      const row = el('div', 'data-row');
      row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
      row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
      root.appendChild(row);
    }
  };

  const load = async () => {
    const serial = ++requestSerial;
    renderLoading();
    try {
      const page = await listRuns({ agentId: props.agentId, limit: 10 });
      if (destroyed || serial !== requestSerial) return;
      root.replaceChildren();
      if (page.items.length === 0) {
        const empty = el('div', 'empty');
        empty.appendChild(el('p', 'empty-text', '此 Agent 還沒有執行紀錄。'));
        if (props.hasPublished) {
          const button = el('button', 'btn-secondary', '執行 Agent');
          button.type = 'button';
          button.addEventListener('click', () => {
            if (typeof props.onCompose === 'function') props.onCompose();
          }, { once: true });
          empty.appendChild(button);
        } else {
          const link = el('a', 'btn-secondary', '註冊版本');
          link.href = '/console/agents/' + encodeURIComponent(props.agentId) + '/versions/new';
          empty.appendChild(link);
        }
        root.appendChild(empty);
        return;
      }
      const list = el('div', 'data-list');
      const header = el('div', 'data-row data-row--runs data-row--header');
      header.appendChild(el('span', null, '狀態'));
      header.appendChild(el('span', null, '版本'));
      header.appendChild(el('span', null, '入口'));
      header.appendChild(el('span', null, '建立時間'));
      list.appendChild(header);
      for (const run of page.items) {
        const link = el('a', 'data-row data-row--runs');
        link.href = '/console/runs/' + encodeURIComponent(run.runId);
        const status = el('span', 'status status--' + run.status);
        const dot = el('span', 'status-dot status-dot--' + run.status);
        dot.setAttribute('aria-hidden', 'true');
        status.appendChild(dot);
        status.appendChild(el('span', 'status-label', STATUS_LABEL[run.status] || run.status));
        link.appendChild(status);
        link.appendChild(el('span', 'mono text-xs', run.agentVersion || '—'));
        link.appendChild(el('span', 'mono text-xs', run.trigger || '—'));
        link.appendChild(el('span', 'mono text-2xs', formatDateTime(run.createdAt)));
        list.appendChild(link);
      }
      root.appendChild(list);
      if (page.nextCursor) {
        const footer = el('div', 'list-footer');
        const link = el('a', 'btn-secondary', '查看全部 Runs');
        link.href = '/console/runs?agent=' + encodeURIComponent(props.agentId);
        footer.appendChild(link);
        root.appendChild(footer);
      }
    } catch (error) {
      if (destroyed || serial !== requestSerial) return;
      if (!(error instanceof ApiError)) throw error;
      const box = createErrorBox(error, { hint: '版本與呼叫區仍可使用；可單獨重新載入最近 Runs。' });
      const retry = el('button', 'btn-secondary', '重新載入');
      retry.type = 'button';
      retry.addEventListener('click', load, { once: true });
      box.appendChild(retry);
      root.replaceChildren(box);
    }
  };

  load();
  return {
    reload: load,
    update(next = {}) {
      Object.assign(props, next);
      if (next.hasPublished !== undefined) load();
    },
    destroy() {
      destroyed = true;
      requestSerial += 1;
      root.replaceChildren();
    },
  };
}
