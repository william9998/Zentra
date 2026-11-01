// mcp-keys-card.js —— 平台 MCP 接口的 key 與勾選（specs/mcp.md、api.md §MCP key）。
//
// 三件事決定了它長什麼樣：
//
// 1. **接口只有一個。** 網址置頂可複製；每把 key 各自勾選要開放哪些 Agent，
//    那組 Agent 的 entrypoint 就是對端 LLM 看到的 tools。
// 2. **明文只出現一次**，同 api-keys-card：發完之後必須自己關掉那個區塊。
// 3. **勾選立刻生效**（下一次 tools/list 就是新的）；撤銷是軟的，已撤銷的 key 灰掉、不能再改勾選。
//
// 屬性名一律 `data-mcp-*`，id 一律 `mcp-*`。Agent 名稱來自 API，全部走 textContent。

import {
  ApiError, issueMcpKey, listAgents, listMcpKeys, revokeMcpKey, setMcpKeyAgents,
} from '../api.js';
import { createCopyButton, createErrorBox, el, formatDateTime } from '../dom.js';

const FRAME = `
  <div class="keys-card" data-mcp-root>
    <div class="endpoint-list" data-mcp-endpoint></div>
    <p class="field-hint">
      對端平台在 MCP 設定填這個網址，並帶
      <code class="mono">Authorization: Bearer &lt;key&gt;</code>。
      沒有 key 就連不上；每把 key 只看得到自己勾選的 Agent。沒有已發布版本的 Agent 即使勾選也不會出現在 tools 清單。
    </p>

    <form class="keys-form" data-mcp-form>
      <div class="field">
        <label for="mcp-name">用途名稱</label>
        <input type="text" id="mcp-name" autocomplete="off" maxlength="128"
               placeholder="例如：Dify 正式環境" aria-describedby="mcp-name-hint">
        <p class="field-hint" id="mcp-name-hint">給人看的，撤銷時靠它認出是哪一把。</p>
      </div>
      <div class="field" role="group" aria-labelledby="mcp-pick-label">
        <span id="mcp-pick-label">開放的 Agent</span>
        <div data-mcp-pick></div>
      </div>
      <div class="form-actions">
        <button type="submit" class="btn-primary" data-mcp-issue disabled>發一把新的 key</button>
      </div>
    </form>

    <div class="keys-reveal" data-mcp-reveal hidden></div>
    <div data-mcp-error hidden></div>
    <div data-mcp-list></div>

    <dialog data-mcp-confirm aria-labelledby="mcp-confirm-title">
      <form class="dialog-form" method="dialog">
        <h2 class="dialog-title" id="mcp-confirm-title">撤銷這把 key？</h2>
        <p class="field-hint" data-mcp-confirm-body></p>
        <div class="dialog-actions">
          <button type="button" class="btn-ghost" data-mcp-confirm-cancel>取消</button>
          <button type="button" class="btn-danger" data-mcp-confirm-ok>確定撤銷</button>
        </div>
      </form>
    </dialog>

    <p class="visually-hidden" data-mcp-live aria-live="polite"></p>
  </div>
`;

/** 一組 checkbox。回傳 { node, selected() }；節點內全是 textContent。 */
function createAgentPicker(agents, initial, onChange, disabled = false) {
  const chosen = new Set(initial);
  const node = el('div', 'mcp-agent-pick');
  if (agents.length === 0) {
    node.append(el('p', 'field-hint', '還沒有 Agent。先到 Agents 頁建立。'));
  }
  agents.forEach((agent) => {
    const label = el('label', 'mcp-agent-option');
    const box = document.createElement('input');
    box.type = 'checkbox';
    box.checked = chosen.has(agent.agentId);
    box.disabled = disabled;
    box.addEventListener('change', () => {
      if (box.checked) chosen.add(agent.agentId); else chosen.delete(agent.agentId);
      if (onChange) onChange();
    });
    label.append(box);
    label.append(el('span', 'mono', agent.name));
    if (!agent.latestPublishedVersion) {
      label.append(el('span', 'text-xs text-secondary', '（尚無已發布版本）'));
    }
    node.append(label);
  });
  return { node, selected: () => agents.map((a) => a.agentId).filter((id) => chosen.has(id)) };
}

async function loadAllAgents() {
  const all = [];
  let cursor = null;
  do {
    const page = await listAgents({ limit: 100, cursor });
    all.push(...page.items);
    cursor = page.nextCursor;
  } while (cursor);
  return all;
}

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const endpointSlot = root.querySelector('[data-mcp-endpoint]');
  const form = root.querySelector('[data-mcp-form]');
  const nameInput = root.querySelector('#mcp-name');
  const pickSlot = root.querySelector('[data-mcp-pick]');
  const issueButton = root.querySelector('[data-mcp-issue]');
  const reveal = root.querySelector('[data-mcp-reveal]');
  const errorSlot = root.querySelector('[data-mcp-error]');
  const list = root.querySelector('[data-mcp-list]');
  const confirm = root.querySelector('[data-mcp-confirm]');
  const confirmBody = root.querySelector('[data-mcp-confirm-body]');
  const confirmOk = root.querySelector('[data-mcp-confirm-ok]');
  const confirmCancel = root.querySelector('[data-mcp-confirm-cancel]');
  const live = root.querySelector('[data-mcp-live]');
  const timers = props.timers;

  let endpoint = '';
  let agents = [];
  let rows = [];
  let busy = false;
  let pending = null;
  let editingId = null;
  let issuePicker = null;

  const say = (message) => { live.textContent = message; };
  const clearError = () => { errorSlot.hidden = true; errorSlot.replaceChildren(); };
  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };
  const guard = (error, hint) => {
    if (!(error instanceof ApiError)) throw error;
    showError(error, hint);
  };

  const nameOf = (agentId) => {
    const found = agents.find((agent) => agent.agentId === agentId);
    return found ? found.name : agentId;
  };

  const syncForm = () => {
    issueButton.disabled = busy || nameInput.value.trim().length === 0;
    issueButton.textContent = busy ? '產生中…' : '發一把新的 key';
    nameInput.disabled = busy;
  };

  const renderEndpoint = () => {
    endpointSlot.replaceChildren();
    if (!endpoint) return;
    const row = el('div', 'endpoint-row');
    row.append(el('span', 'endpoint-label', 'MCP'));
    row.append(el('span', 'endpoint-value', endpoint));
    if (timers) row.append(createCopyButton(() => endpoint, timers, 'Copy'));
    endpointSlot.append(row);
  };

  const renderIssuePicker = () => {
    issuePicker = createAgentPicker(agents, issuePicker ? issuePicker.selected() : [], null);
    pickSlot.replaceChildren(issuePicker.node);
  };

  const showPlaintext = (issued) => {
    reveal.replaceChildren();
    const title = el('p', 'keys-reveal-title');
    title.append(el('span', 'status-dot status-dot--PUBLISHED'));
    title.append(' 這是「' + issued.name + '」的 key，');
    title.append(el('strong', null, '只會出現這一次'));
    title.append('。現在複製走，關掉之後平台也拿不回來（存的是雜湊）。');
    reveal.append(title);

    const valueRow = el('div', 'keys-reveal-value');
    valueRow.append(el('code', 'mono keys-plaintext', issued.plaintext));
    if (timers) valueRow.append(createCopyButton(() => issued.plaintext, timers, '複製'));
    reveal.append(valueRow);

    const dismiss = el('button', 'btn-ghost btn-sm', '我已經複製好了');
    dismiss.type = 'button';
    dismiss.addEventListener('click', () => {
      reveal.replaceChildren();
      reveal.hidden = true;
      nameInput.focus();
    });
    reveal.append(dismiss);
    reveal.hidden = false;
  };

  const replaceRow = (updated) => {
    rows = rows.map((item) => (item.keyId === updated.keyId ? updated : item));
  };

  const saveAgents = async (row, picker) => {
    clearError();
    try {
      replaceRow(await setMcpKeyAgents(row.keyId, picker.selected()));
      editingId = null;
      render();
      say('「' + row.name + '」的勾選已更新。');
    } catch (error) {
      guard(error);
    }
  };

  const rowNode = (row) => {
    const revoked = Boolean(row.revokedAt);
    const node = el('div', 'mcp-key-row' + (revoked ? ' data-row--muted' : ''));

    const head = el('div', 'mcp-key-head');
    const identity = el('div', 'list-identity');
    identity.append(el('span', 'list-title', row.name));
    identity.append(el('span', 'list-description mono', row.keyPrefix + '…'));
    head.append(identity);
    head.append(el('span', 'text-xs text-secondary',
      row.createdAt ? formatDateTime(row.createdAt) : '—'));
    head.append(el('span', 'text-xs text-secondary',
      row.lastUsedAt ? formatDateTime(row.lastUsedAt) : '沒用過'));
    const state = el('span', 'status');
    state.append(el('span', 'status-dot status-dot--' + (revoked ? 'CANCELLED' : 'SUCCEEDED')));
    state.append(el('span', 'status-label', revoked ? '已撤銷' : '可用'));
    head.append(state);

    const actions = el('div', 'data-row-actions');
    if (!revoked) {
      const edit = el('button', 'btn-ghost btn-sm', editingId === row.keyId ? '取消編輯' : '編輯勾選');
      edit.type = 'button';
      edit.addEventListener('click', () => {
        editingId = editingId === row.keyId ? null : row.keyId;
        render();
      });
      actions.append(edit);
      const revoke = el('button', 'btn-ghost btn-sm', '撤銷');
      revoke.type = 'button';
      revoke.addEventListener('click', () => openConfirm(row));
      actions.append(revoke);
    }
    head.append(actions);
    node.append(head);

    if (editingId === row.keyId && !revoked) {
      const picker = createAgentPicker(agents, row.agentIds, null);
      node.append(picker.node);
      const save = el('button', 'btn-primary btn-sm', '儲存勾選');
      save.type = 'button';
      save.addEventListener('click', () => saveAgents(row, picker));
      node.append(save);
    } else {
      node.append(el('p', 'mcp-key-agents', row.agentIds.length === 0
        ? '沒有勾選任何 Agent（tools 清單是空的）'
        : '開放 ' + row.agentIds.length + ' 個 Agent：' + row.agentIds.map(nameOf).join('、')));
    }
    return node;
  };

  const render = () => {
    list.replaceChildren();
    if (rows.length === 0) {
      const empty = el('div', 'empty');
      empty.append(el('p', 'empty-text', '還沒有 MCP key。發一把並勾選 Agent，對端才連得上。'));
      list.append(empty);
      return;
    }
    rows.forEach((row) => list.append(rowNode(row)));
  };

  const openConfirm = (row) => {
    pending = row;
    confirmBody.textContent = '「' + row.name + '」撤銷後立刻失效，帶著它的呼叫會被擋下來。'
      + '這一列會留在清單裡（灰掉），它過去建立的 Run 還掛在上面。';
    confirm.showModal();
  };

  const doRevoke = async (row) => {
    clearError();
    try {
      replaceRow(await revokeMcpKey(row.keyId));
      editingId = null;
      render();
      say('「' + row.name + '」已撤銷。');
    } catch (error) {
      guard(error);
    }
  };

  const onInput = () => syncForm();

  const onSubmit = async (event) => {
    event.preventDefault();
    if (issueButton.disabled) return;
    clearError();
    busy = true;
    syncForm();
    try {
      const issued = await issueMcpKey(nameInput.value.trim(), issuePicker.selected());
      nameInput.value = '';
      busy = false;
      syncForm();
      rows = [issued].concat(rows);
      render();
      showPlaintext(issued);
      say('已發出新的 key，記得現在複製走。');
    } catch (error) {
      busy = false;
      syncForm();
      guard(error);
    }
  };

  const onConfirmOk = () => {
    const row = pending;
    pending = null;
    confirm.close();
    if (row) doRevoke(row);
  };
  const onConfirmCancel = () => { pending = null; confirm.close(); };

  nameInput.addEventListener('input', onInput);
  form.addEventListener('submit', onSubmit);
  confirmOk.addEventListener('click', onConfirmOk);
  confirmCancel.addEventListener('click', onConfirmCancel);

  const load = async () => {
    try {
      const [keys, allAgents] = await Promise.all([listMcpKeys(), loadAllAgents()]);
      endpoint = keys.endpoint;
      rows = keys.items;
      agents = allAgents;
      renderEndpoint();
      renderIssuePicker();
      render();
    } catch (error) {
      guard(error, '無法讀取 MCP 設定。重新整理頁面再試一次。');
    }
  };

  syncForm();
  load();

  return {
    destroy() {
      nameInput.removeEventListener('input', onInput);
      form.removeEventListener('submit', onSubmit);
      confirmOk.removeEventListener('click', onConfirmOk);
      confirmCancel.removeEventListener('click', onConfirmCancel);
      if (confirm.open) confirm.close();
      root.replaceChildren();
    },
  };
}
