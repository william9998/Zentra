import { ApiError, deleteAgent, listAgents } from '../api.js';
import {
  createCopyButton, createErrorBox, createTimers, el, formatDateTime, truncate,
} from '../dom.js';

const FRAME = `
  <div class="list-toolbar">
    <label class="visually-hidden" for="agent-search">搜尋 Agent</label>
    <input type="search" id="agent-search" placeholder="搜尋名稱或描述" autocomplete="off">
    <span class="text-xs text-secondary" data-sel-count></span>
    <button type="button" class="btn-danger btn-sm" data-sel-delete disabled>刪除所選</button>
  </div>
  <div data-state></div>
  <div data-list></div>
  <div data-sel-error hidden></div>
  <div class="list-footer" data-footer hidden>
    <button type="button" class="btn-secondary" data-more>載入更多</button>
  </div>

  <dialog data-sel-confirm aria-labelledby="sel-confirm-title">
    <form class="dialog-form" method="dialog">
      <h2 class="dialog-title" id="sel-confirm-title">刪除這些 Agent？</h2>
      <p class="field-hint" data-sel-confirm-body></p>
      <ul class="sel-list" data-sel-confirm-names></ul>
      <p class="field-hint"><strong>這個動作不可復原。</strong>會一併刪掉：
        所有版本、常駐容器、Run 與事件紀錄、LLM 用量、下游 API key，
        以及 registry 裡的 image。</p>
      <div class="dialog-actions">
        <button type="button" class="btn-ghost" data-sel-confirm-cancel>取消</button>
        <button type="button" class="btn-danger" data-sel-confirm-ok>確定刪除</button>
      </div>
    </form>
  </dialog>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;
  const search = root.querySelector('#agent-search');
  const deleteButton = root.querySelector('[data-sel-delete]');
  const selectionCount = root.querySelector('[data-sel-count]');
  const selectionError = root.querySelector('[data-sel-error]');
  const confirmBox = root.querySelector('[data-sel-confirm]');
  const confirmBody = root.querySelector('[data-sel-confirm-body]');
  const confirmNames = root.querySelector('[data-sel-confirm-names]');
  const confirmOk = root.querySelector('[data-sel-confirm-ok]');
  const confirmCancel = root.querySelector('[data-sel-confirm-cancel]');
  //: 勾選的 agentId。**存 id 不存索引** —— 搜尋會換掉可見的列，
  //  存索引的話濾一下就會刪到別人。
  const selected = new Set();
  let busy = false;
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
      const header = el('div', 'data-row data-row--agents data-row--header');
      const all = el('input', 'sel-box');
      all.type = 'checkbox';
      all.setAttribute('aria-label', '全選目前顯示的 Agent');
      // 全選只作用在**目前看得到的**那些。搜尋過濾之後還去勾看不見的，
      // 是那種按下去才發現刪多了的錯。
      const shownIds = shown.map((item) => item.agentId);
      all.checked = shownIds.length > 0 && shownIds.every((id) => selected.has(id));
      all.indeterminate = !all.checked && shownIds.some((id) => selected.has(id));
      all.addEventListener('change', () => {
        shownIds.forEach((id) => (all.checked ? selected.add(id) : selected.delete(id)));
        render();
      });
      header.appendChild(all);
      header.appendChild(el('span', null, '名稱'));
      header.appendChild(el('span', null, '最新已發布版本'));
      header.appendChild(el('span', null, '更新時間'));
      table.appendChild(header);
      for (const agent of shown) {
        const row = el('div', 'data-row data-row--agents');
        const box = el('input', 'sel-box');
        box.type = 'checkbox';
        box.checked = selected.has(agent.agentId);
        box.setAttribute('aria-label', '選取 ' + agent.name);
        box.addEventListener('change', () => {
          if (box.checked) selected.add(agent.agentId);
          else selected.delete(agent.agentId);
          render();
        });
        row.appendChild(box);
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
    syncSelection();
  };

  /** 只清掉「已經不在清單裡」的選取，不要在每次 render 都清空。 */
  const syncSelection = () => {
    const alive = new Set(items.map((item) => item.agentId));
    for (const id of [...selected]) if (!alive.has(id)) selected.delete(id);
    selectionCount.textContent = selected.size ? '已選 ' + selected.size + ' 個' : '';
    deleteButton.disabled = selected.size === 0 || busy;
  };

  const clearSelectionError = () => {
    selectionError.hidden = true;
    selectionError.replaceChildren();
  };

  const openConfirm = () => {
    const chosen = items.filter((item) => selected.has(item.agentId));
    if (chosen.length === 0) return;
    confirmBody.textContent = '將刪除 ' + chosen.length + ' 個 Agent：';
    confirmNames.replaceChildren();
    // 名稱來自 API，一律 textContent
    chosen.slice(0, 12).forEach((item) => confirmNames.appendChild(el('li', null, item.name)));
    if (chosen.length > 12) {
      confirmNames.appendChild(el('li', 'text-secondary', '…以及另外 ' + (chosen.length - 12) + ' 個'));
    }
    confirmBox.showModal();
  };

  /**
   * 逐一刪除。**一個失敗不要停下來** —— 停在中間會留下「刪了一半」的狀態，
   * 而使用者無從得知刪到哪裡。全部試完再一起回報哪些沒刪掉。
   */
  const doDelete = async () => {
    busy = true;
    deleteButton.disabled = true;
    deleteButton.textContent = '刪除中…';
    clearSelectionError();
    const failed = [];
    for (const agentId of [...selected]) {
      try {
        await deleteAgent(agentId);
        selected.delete(agentId);
      } catch (error) {
        if (!(error instanceof ApiError)) throw error;
        failed.push({ agentId, error });
      }
    }
    busy = false;
    deleteButton.textContent = '刪除所選';
    if (failed.length) {
      selectionError.replaceChildren(createErrorBox(failed[0].error, {
        hint: failed.length === 1
          ? '這一個沒有刪掉，其餘的已經刪了。'
          : failed.length + ' 個沒有刪掉，其餘的已經刪了。',
      }));
      selectionError.hidden = false;
    }
    nextCursor = null;
    await load(false);
  };

  const onDeleteClick = () => openConfirm();
  const onConfirmOk = () => { confirmBox.close(); doDelete(); };
  const onConfirmCancel = () => confirmBox.close();
  deleteButton.addEventListener('click', onDeleteClick);
  confirmOk.addEventListener('click', onConfirmOk);
  confirmCancel.addEventListener('click', onConfirmCancel);

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
      deleteButton.removeEventListener('click', onDeleteClick);
      confirmOk.removeEventListener('click', onConfirmOk);
      confirmCancel.removeEventListener('click', onConfirmCancel);
      if (confirmBox.open) confirmBox.close();
      timers.clearAll();
      root.replaceChildren();
    },
  };
}
