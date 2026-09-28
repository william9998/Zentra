// api-keys-card.js —— 這個 Agent 發給下游應用的 key（ZEN-015 / api-contract §2.3）。
//
// 三件事決定了它長什麼樣：
//
// 1. **明文只出現一次。** 平台存的是雜湊，沒有「再看一次」的端點可打，
//    所以發完之後那串字只在畫面上活著。這裡用一個必須自己關掉的區塊顯示它，
//    而不是一行小字 —— 使用者關掉它之前必須先看見「關掉就沒了」。
// 2. **撤銷是軟的。** 撤銷過的 key 仍留在列表裡（灰掉），因為它過去的用量還掛著。
//    把它藏起來的話，用量報表會出現一把查不到名字的 key。
// 3. **key 現在買到的是歸戶，不是門禁。** 契約 §2.3 寫明沒帶 key 一樣放行，
//    所以畫面上要講清楚，否則使用者會以為發了 key 就擋住了別人。
//
// 屬性名一律 `data-keys-*` 前綴：同一頁還有憑證卡（`data-cred-*`）與兩個對話框
// （`data-form` / `data-submit` 那些通用名），撞名在 document.querySelector 才會發作。

import {
  ApiError, issueAgentApiKey, listAgentApiKeys, revokeAgentApiKey,
} from '../api.js';
import { createCopyButton, createErrorBox, el, formatDateTime } from '../dom.js';

const FRAME = `
  <div class="keys-card" data-keys-root>
    <p class="field-hint">
      每個下游應用發一把，之後才分得出<strong>哪一筆請求是誰打的</strong>。
      怎麼帶在請求上見 api-contract §2.3 —— Console 是同源呼叫，自己不組認證標頭。
      目前<strong>沒帶 key 也能呼叫</strong>（同一節），所以 key 買到的是歸戶，不是門禁。
    </p>

    <form class="keys-form" data-keys-form>
      <div class="field">
        <label for="keys-name">用途名稱</label>
        <input type="text" id="keys-name" autocomplete="off" maxlength="128"
               placeholder="例如：行銷站、內部工具" aria-describedby="keys-name-hint">
        <p class="field-hint" id="keys-name-hint">
          給人看的，撤銷時靠它認出是哪一把。
        </p>
      </div>
      <div class="form-actions">
        <button type="submit" class="btn-primary" data-keys-issue disabled>發一把新的 key</button>
      </div>
    </form>

    <div class="keys-reveal" data-keys-reveal hidden></div>
    <div data-keys-error hidden></div>
    <div class="keys-list" data-keys-list></div>

    <dialog data-keys-confirm aria-labelledby="keys-confirm-title">
      <form class="dialog-form" method="dialog">
        <h2 class="dialog-title" id="keys-confirm-title">撤銷這把 key？</h2>
        <p class="field-hint" data-keys-confirm-body></p>
        <div class="dialog-actions">
          <button type="button" class="btn-ghost" data-keys-confirm-cancel>取消</button>
          <button type="button" class="btn-danger" data-keys-confirm-ok>確定撤銷</button>
        </div>
      </form>
    </dialog>

    <p class="visually-hidden" data-keys-live aria-live="polite"></p>
  </div>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const form = root.querySelector('[data-keys-form]');
  const nameInput = root.querySelector('#keys-name');
  const issueButton = root.querySelector('[data-keys-issue]');
  const reveal = root.querySelector('[data-keys-reveal]');
  const errorSlot = root.querySelector('[data-keys-error]');
  const list = root.querySelector('[data-keys-list]');
  const confirm = root.querySelector('[data-keys-confirm]');
  const confirmBody = root.querySelector('[data-keys-confirm-body]');
  const confirmOk = root.querySelector('[data-keys-confirm-ok]');
  const confirmCancel = root.querySelector('[data-keys-confirm-cancel]');
  const live = root.querySelector('[data-keys-live]');

  let agentId = props.agentId || '';
  let rows = [];
  let busy = false;
  let pending = null;
  const timers = props.timers;

  const say = (message) => { live.textContent = message; };
  const notifyChange = () => { if (typeof props.onChange === 'function') props.onChange(rows); };

  const clearError = () => { errorSlot.hidden = true; errorSlot.replaceChildren(); };
  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };

  const syncForm = () => {
    issueButton.disabled = busy || nameInput.value.trim().length === 0;
    issueButton.textContent = busy ? '產生中…' : '發一把新的 key';
    nameInput.disabled = busy;
  };

  /**
   * 明文那一次。**全部 textContent** —— 雖然這串是平台自己產的，
   * 但旁邊的名稱是使用者輸入的，同一個區塊不該有兩種規矩。
   */
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

  const rowNode = (row) => {
    const revoked = Boolean(row.revokedAt);
    const node = el('div', 'data-row data-row--keys' + (revoked ? ' data-row--muted' : ''));

    const identity = el('div', 'list-identity');
    identity.append(el('span', 'list-title', row.name));
    identity.append(el('span', 'list-description mono', row.keyPrefix + '…'));
    node.append(identity);

    node.append(el('span', 'text-xs text-secondary',
      row.createdAt ? formatDateTime(row.createdAt) : '—'));
    node.append(el('span', 'text-xs text-secondary',
      row.lastUsedAt ? formatDateTime(row.lastUsedAt) : '沒用過'));

    const state = el('span', 'status');
    state.append(el('span', 'status-dot status-dot--' + (revoked ? 'CANCELLED' : 'SUCCEEDED')));
    state.append(el('span', 'status-label', revoked ? '已撤銷' : '可用'));
    node.append(state);

    const actions = el('div', 'data-row-actions');
    if (!revoked) {
      const button = el('button', 'btn-ghost btn-sm', '撤銷');
      button.type = 'button';
      button.addEventListener('click', () => openConfirm(row));
      actions.append(button);
    }
    node.append(actions);
    return node;
  };

  const render = () => {
    list.replaceChildren();
    if (rows.length === 0) {
      const empty = el('div', 'empty');
      empty.append(el('p', 'empty-text',
        '還沒有發過 key。現在所有呼叫都記成「未歸戶」，用量分不出是誰打的。'));
      list.append(empty);
      return;
    }
    const header = el('div', 'data-row data-row--header data-row--keys');
    ['名稱', '建立於', '最後使用', '狀態', ''].forEach((text) => {
      header.append(el('span', null, text));
    });
    list.append(header);
    rows.forEach((row) => list.append(rowNode(row)));
  };

  const openConfirm = (row) => {
    pending = row;
    confirmBody.textContent = '「' + row.name + '」撤銷後立刻失效，帶著它的呼叫會被擋下來。'
      + '這一列會留在清單裡（灰掉）—— 它過去的用量還掛在上面，刪掉那些帳就沒有主人了。';
    confirm.showModal();
  };

  const doRevoke = async (row) => {
    clearError();
    try {
      const updated = await revokeAgentApiKey(agentId, row.keyId);
      rows = rows.map((item) => (item.keyId === updated.keyId ? updated : item));
      render();
      notifyChange();
      say('「' + row.name + '」已撤銷。');
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error);
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
      const issued = await issueAgentApiKey(agentId, nameInput.value.trim());
      nameInput.value = '';
      busy = false;
      syncForm();
      rows = [issued].concat(rows);
      render();
      showPlaintext(issued);
      notifyChange();
      say('已發出新的 key，記得現在複製走。');
    } catch (error) {
      busy = false;
      syncForm();
      if (!(error instanceof ApiError)) throw error;
      showError(error);
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
      rows = await listAgentApiKeys(agentId);
      render();
      notifyChange();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      showError(error, '無法讀取 key 清單。重新整理頁面再試一次。');
    }
  };

  syncForm();
  if (agentId) load();

  return {
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        load();
      }
    },
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
