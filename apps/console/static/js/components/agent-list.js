// agent-list.js —— Agent 總覽（卡片格子）與批次刪除的選取模式（ui-contract v0.11.0 §5.7，ADR-0013）。
//
// 一般模式：每張卡唯一的互動元素是名稱 <a>，靠 a::after 把點擊範圍撐到整張卡。
// 選取模式：名稱換成 checkbox 的 <label>，同樣撐到整張卡 —— 點卡片 = 原生 label 切換勾選，
// 卡片內不存在任何連結，也不另寫 click handler。

import { ApiError, deleteAgent, listAgents } from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';

const SKELETON_CARDS = 6;

const FRAME = `
  <div class="list-toolbar agent-toolbar">
    <label class="visually-hidden" for="agent-search">搜尋 Agent</label>
    <input type="search" id="agent-search" placeholder="搜尋名稱或描述" autocomplete="off" disabled>
    <div class="agent-toolbar-actions">
      <label class="sel-all" data-sel-all-wrap hidden>
        <input type="checkbox" class="sel-box" data-sel-all>
        <span>全選目前顯示的</span>
      </label>
      <span class="text-xs text-secondary" data-sel-count aria-live="polite"></span>
      <button type="button" class="btn-danger" data-sel-delete disabled hidden>刪除所選</button>
      <button type="button" class="btn-secondary" data-sel-mode disabled>選取</button>
      <button type="button" class="btn-ghost" data-sel-cancel hidden>取消</button>
    </div>
  </div>
  <div data-sel-error hidden></div>
  <div data-state></div>
  <div class="agent-grid" data-grid></div>
  <div class="list-footer" data-footer hidden>
    <button type="button" class="btn-secondary" data-more>載入更多</button>
  </div>

  <dialog data-sel-confirm aria-labelledby="sel-confirm-title">
    <form class="dialog-form" method="dialog">
      <h2 class="dialog-title" id="sel-confirm-title">刪除這些 Agent？</h2>
      <p class="field-hint" data-sel-confirm-body></p>
      <ul class="sel-list sel-list--tall" data-sel-confirm-names></ul>
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

/**
 * 名稱首字：第一個字母或數字（以 code point 計），拉丁字母轉大寫；沒有就是「—」。
 * 轉大寫後若變成多個字（例如 ß → SS）就保留原字，方塊裡只放一個字。
 */
export function monogramOf(name) {
  const match = String(name || '').match(/[\p{L}\p{N}]/u);
  if (!match) return '—';
  const upper = match[0].toUpperCase();
  return [...upper].length === 1 ? upper : match[0];
}

export function mount(root, props = {}) {
  root.innerHTML = FRAME;
  const search = root.querySelector('#agent-search');
  const selectAllWrap = root.querySelector('[data-sel-all-wrap]');
  const selectAll = root.querySelector('[data-sel-all]');
  const selectionCount = root.querySelector('[data-sel-count]');
  const deleteButton = root.querySelector('[data-sel-delete]');
  const modeButton = root.querySelector('[data-sel-mode]');
  const cancelButton = root.querySelector('[data-sel-cancel]');
  const selectionError = root.querySelector('[data-sel-error]');
  const confirmBox = root.querySelector('[data-sel-confirm]');
  const confirmBody = root.querySelector('[data-sel-confirm-body]');
  const confirmNames = root.querySelector('[data-sel-confirm-names]');
  const confirmOk = root.querySelector('[data-sel-confirm-ok]');
  const confirmCancel = root.querySelector('[data-sel-confirm-cancel]');
  const stateSlot = root.querySelector('[data-state]');
  const grid = root.querySelector('[data-grid]');
  const footer = root.querySelector('[data-footer]');
  const more = root.querySelector('[data-more]');

  //: 勾選的 agentId。**存 id 不存索引或位置** —— 搜尋會換掉可見的卡，欄數也會隨寬度變，
  //  存位置的話濾一下就會刪到別人。被搜尋藏起來的仍算已選。
  const selected = new Set();
  let selecting = false;
  let busy = false;
  let items = [];
  let nextCursor = null;
  let loading = false;
  let destroyed = false;

  const visibleItems = () => {
    const needle = search.value.trim().toLocaleLowerCase();
    if (!needle) return items;
    return items.filter((item) => (item.name + ' ' + (item.description || ''))
      .toLocaleLowerCase().includes(needle));
  };

  // ---------------------------------------------------------------- 卡片

  const skeletonCard = () => {
    const card = el('div', 'agent-card agent-card--skeleton');
    card.setAttribute('aria-hidden', 'true');
    const head = el('div', 'agent-card-head');
    head.appendChild(el('span', 'skeleton agent-card-monogram'));
    card.appendChild(head);
    const name = el('div', 'agent-card-name');
    name.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
    card.appendChild(name);
    const desc = el('div', 'agent-card-desc');
    desc.appendChild(el('div', 'skeleton skeleton-line skeleton-line--wide'));
    desc.appendChild(el('div', 'skeleton skeleton-line skeleton-line--third'));
    card.appendChild(desc);
    const foot = el('div', 'agent-card-foot');
    foot.appendChild(el('span', 'skeleton skeleton-line skeleton-line--wide'));
    card.appendChild(foot);
    return card;
  };

  const renderCard = (agent) => {
    const isChosen = selected.has(agent.agentId);
    const card = el('article', 'agent-card' + (selecting ? ' agent-card--selecting' : '')
      + (selecting && isChosen ? ' is-selected' : ''));
    card.dataset.agentId = agent.agentId;

    const head = el('div', 'agent-card-head');
    const boxId = 'agent-sel-' + agent.agentId;
    if (selecting) {
      const box = el('input', 'sel-box');
      box.type = 'checkbox';
      box.id = boxId;
      box.checked = isChosen;
      box.dataset.selBox = agent.agentId;
      head.appendChild(box);
    }
    const monogram = el('span', 'agent-card-monogram', monogramOf(agent.name));
    monogram.setAttribute('aria-hidden', 'true');
    head.appendChild(monogram);
    card.appendChild(head);

    const title = el('h3', 'agent-card-name');
    // 名稱來自 API，一律 textContent（el() 內部就是 textContent）
    const hit = selecting ? el('label', 'agent-card-hit', agent.name) : el('a', 'agent-card-hit', agent.name);
    if (selecting) hit.htmlFor = boxId;
    else hit.href = '/console/agents/' + encodeURIComponent(agent.agentId);
    hit.title = agent.name;
    title.appendChild(hit);
    card.appendChild(title);

    const description = agent.description || '';
    card.appendChild(el('p', 'agent-card-desc', description || '沒有描述'));

    const foot = el('div', 'agent-card-foot');
    foot.appendChild(agent.latestPublishedVersion
      ? el('span', 'mono text-xs', agent.latestPublishedVersion)
      : el('span', 'text-xs text-secondary', '尚無已發布版本'));
    const when = agent.updatedAt || agent.createdAt;
    const time = el('time', 'text-xs text-secondary', formatDateTime(when));
    if (when instanceof Date && !Number.isNaN(when.getTime())) time.dateTime = when.toISOString();
    foot.appendChild(time);
    card.appendChild(foot);
    return card;
  };

  // ---------------------------------------------------------------- 工具列

  /** 只清掉「已經不在清單裡」的選取，不要在每次 render 都清空。 */
  const pruneSelection = () => {
    const alive = new Set(items.map((item) => item.agentId));
    for (const id of [...selected]) if (!alive.has(id)) selected.delete(id);
  };

  const syncToolbar = () => {
    search.disabled = loading && items.length === 0;
    modeButton.hidden = selecting;
    modeButton.disabled = loading || items.length === 0;
    cancelButton.hidden = !selecting;
    cancelButton.disabled = busy;
    selectAllWrap.hidden = !selecting;
    deleteButton.hidden = !selecting;
    selectionCount.textContent = selecting ? '已選 ' + selected.size + ' 個' : '';
    deleteButton.disabled = !selecting || selected.size === 0 || busy || loading;

    // 全選只作用在**目前看得到的**那些；部分選取時用 indeterminate。
    const shownIds = visibleItems().map((item) => item.agentId);
    const chosenShown = shownIds.filter((id) => selected.has(id)).length;
    selectAll.checked = shownIds.length > 0 && chosenShown === shownIds.length;
    selectAll.indeterminate = chosenShown > 0 && chosenShown < shownIds.length;
    selectAll.disabled = shownIds.length === 0 || busy || loading;
  };

  // ---------------------------------------------------------------- 五態

  const renderLoading = () => {
    stateSlot.replaceChildren();
    footer.hidden = true;
    grid.hidden = false;
    grid.setAttribute('aria-busy', 'true');
    const cards = [];
    for (let index = 0; index < SKELETON_CARDS; index += 1) cards.push(skeletonCard());
    grid.replaceChildren(...cards);
    syncToolbar();
  };

  const render = () => {
    stateSlot.replaceChildren();
    grid.replaceChildren();
    grid.removeAttribute('aria-busy');
    const shown = visibleItems();

    if (items.length === 0) {
      grid.hidden = true;
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '建立第一個 Agent，接著註冊 OCI image 版本。'));
      const button = el('button', 'btn-secondary', '建立 Agent');
      button.type = 'button';
      button.addEventListener('click', () => {
        if (typeof props.onCreate === 'function') props.onCreate(button);
      });
      empty.appendChild(button);
      stateSlot.appendChild(empty);
    } else if (shown.length === 0) {
      grid.hidden = true;
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '沒有符合搜尋條件的 Agent。'));
      stateSlot.appendChild(empty);
    } else {
      grid.hidden = false;
      grid.replaceChildren(...shown.map(renderCard));
    }
    footer.hidden = !nextCursor || items.length === 0;
    more.disabled = loading;
    more.textContent = loading ? '載入中…' : '載入更多';
    syncToolbar();
  };

  /** Partial：保留現有卡片，末端追加一排骨架卡（數量 = 目前欄數，至少 1）。 */
  const appendSkeletonRow = () => {
    if (grid.hidden) return;
    const tracks = getComputedStyle(grid).gridTemplateColumns.split(' ').filter(Boolean).length;
    const count = Math.max(1, tracks);
    for (let index = 0; index < count; index += 1) grid.appendChild(skeletonCard());
    grid.setAttribute('aria-busy', 'true');
  };

  const renderError = (error) => {
    // 不留下看似可點的舊卡片
    grid.replaceChildren();
    grid.hidden = true;
    grid.removeAttribute('aria-busy');
    footer.hidden = true;
    const box = createErrorBox(error, { hint: '請重新載入 Agent 總覽。' });
    const retry = el('button', 'btn-secondary', '重新載入');
    retry.type = 'button';
    retry.addEventListener('click', () => load({ append: false }), { once: true });
    box.appendChild(retry);
    stateSlot.replaceChildren(box);
    syncToolbar();
  };

  // ---------------------------------------------------------------- 載入

  /**
   * append：載入下一個 cursor。keepSelection：刪除後的重新整理 —— 只移除刪掉的，
   * 失敗的保留勾選；其他「重新載入」一律清空選取並離開選取模式。
   */
  const load = async ({ append = false, keepSelection = false } = {}) => {
    if (loading) return;
    loading = true;
    if (!append && !keepSelection) {
      selecting = false;
      selected.clear();
      clearSelectionError();
    }
    if (append) {
      render();
      appendSkeletonRow();
    } else {
      renderLoading();
    }
    try {
      const page = await listAgents({ limit: 50, cursor: append ? nextCursor : null });
      if (destroyed) return;
      items = append ? items.concat(page.items) : page.items;
      nextCursor = page.nextCursor;
      loading = false;
      pruneSelection();
      if (selecting && selected.size === 0 && keepSelection) selecting = false;
      render();
    } catch (error) {
      loading = false;
      if (destroyed) return;
      if (!(error instanceof ApiError)) throw error;
      items = [];
      nextCursor = null;
      selecting = false;
      selected.clear();
      renderError(error);
    }
  };

  // ---------------------------------------------------------------- 選取與刪除

  const clearSelectionError = () => {
    selectionError.hidden = true;
    selectionError.replaceChildren();
  };

  const enterSelecting = () => {
    selecting = true;
    render();
    selectAll.focus();
  };

  const leaveSelecting = () => {
    selecting = false;
    selected.clear();
    clearSelectionError();
    render();
    modeButton.focus();
  };

  /** 單張卡勾選：就地更新，不重繪格子 —— 重繪會把鍵盤 focus 丟掉。 */
  const onGridChange = (event) => {
    const box = event.target;
    if (!(box instanceof HTMLInputElement) || !box.dataset.selBox) return;
    const agentId = box.dataset.selBox;
    if (box.checked) selected.add(agentId);
    else selected.delete(agentId);
    const card = box.closest('.agent-card');
    if (card) card.classList.toggle('is-selected', box.checked);
    syncToolbar();
  };

  const onSelectAll = () => {
    const shownIds = visibleItems().map((item) => item.agentId);
    const pick = selectAll.checked;
    shownIds.forEach((id) => (pick ? selected.add(id) : selected.delete(id)));
    render();
  };

  const openConfirm = () => {
    // 列出**全部**已選的名字，包含被搜尋藏起來的（ADR-0013 第 2、3 條）。
    const chosen = items.filter((item) => selected.has(item.agentId));
    if (chosen.length === 0) return;
    const shownIds = new Set(visibleItems().map((item) => item.agentId));
    const hiddenCount = chosen.filter((item) => !shownIds.has(item.agentId)).length;
    confirmBody.textContent = '將刪除 ' + chosen.length + ' 個 Agent'
      + (hiddenCount ? '（其中 ' + hiddenCount + ' 個目前被搜尋條件藏起來）' : '') + '：';
    // 名稱來自 API，一律 textContent
    confirmNames.replaceChildren(...chosen.map((item) => el('li', null, item.name)));
    confirmBox.showModal();
  };

  /**
   * 逐一刪除。**一個失敗不要停下來** —— 停在中間會留下「刪了一半」的狀態，
   * 而使用者無從得知刪到哪裡。全部試完再一起回報哪些沒刪掉。
   */
  const doDelete = async () => {
    busy = true;
    deleteButton.textContent = '刪除中…';
    syncToolbar();
    clearSelectionError();
    const names = new Map(items.map((item) => [item.agentId, item.name]));
    const failed = [];
    try {
      for (const agentId of [...selected]) {
        try {
          await deleteAgent(agentId);
          selected.delete(agentId);
        } catch (error) {
          if (!(error instanceof ApiError)) throw error;
          failed.push({ agentId, error });
        }
      }
    } finally {
      busy = false;
      deleteButton.textContent = '刪除所選';
    }
    if (destroyed) return;
    if (failed.length) {
      const box = createErrorBox(failed[0].error, {
        hint: failed.length === 1
          ? '這一個沒有刪掉，其餘的已經刪了。'
          : failed.length + ' 個沒有刪掉，其餘的已經刪了。',
      });
      const list = el('ul', 'sel-list');
      for (const item of failed) {
        // 名稱與 code 都走 text node（append 字串不會被當 HTML 解析）
        const li = el('li');
        li.append(names.get(item.agentId) || item.agentId, ' ',
          el('span', 'error-code', item.error.code || 'INTERNAL_ERROR'));
        list.appendChild(li);
      }
      box.appendChild(list);
      selectionError.replaceChildren(box);
      selectionError.hidden = false;
    }
    nextCursor = null;
    await load({ append: false, keepSelection: true });
  };

  const onSearch = () => render();
  const onMore = () => load({ append: true });
  const onDeleteClick = () => openConfirm();
  const onConfirmOk = () => { confirmBox.close(); doDelete(); };
  const onConfirmCancel = () => confirmBox.close();

  search.addEventListener('input', onSearch);
  more.addEventListener('click', onMore);
  modeButton.addEventListener('click', enterSelecting);
  cancelButton.addEventListener('click', leaveSelecting);
  selectAll.addEventListener('change', onSelectAll);
  grid.addEventListener('change', onGridChange);
  deleteButton.addEventListener('click', onDeleteClick);
  confirmOk.addEventListener('click', onConfirmOk);
  confirmCancel.addEventListener('click', onConfirmCancel);
  load({ append: false });

  return {
    reload() { return load({ append: false }); },
    update() {},
    destroy() {
      destroyed = true;
      search.removeEventListener('input', onSearch);
      more.removeEventListener('click', onMore);
      modeButton.removeEventListener('click', enterSelecting);
      cancelButton.removeEventListener('click', leaveSelecting);
      selectAll.removeEventListener('change', onSelectAll);
      grid.removeEventListener('change', onGridChange);
      deleteButton.removeEventListener('click', onDeleteClick);
      confirmOk.removeEventListener('click', onConfirmOk);
      confirmCancel.removeEventListener('click', onConfirmCancel);
      if (confirmBox.open) confirmBox.close();
      root.replaceChildren();
    },
  };
}
