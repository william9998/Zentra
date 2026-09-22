// delete-agent-dialog.js —— 刪除 Agent 的確認步驟。
//
// 後端是軟刪除（寫 deleted_at）：Agent 從列表消失，詳情、REST 與 MCP 門面一律回 404，
// 但既有的 Run 紀錄與版本列仍在資料庫裡，Runs 頁還查得到。
// Console 這一側沒有「復原」入口，所以這顆按鈕一定要先經過確認。

import { ApiError, deleteAgent } from '../api.js';
import { createErrorBox } from '../dom.js';

const FRAME = `
  <dialog aria-labelledby="delete-agent-title">
    <div class="dialog-form">
      <h2 class="dialog-title" id="delete-agent-title">刪除 Agent</h2>
      <p class="field-hint" data-summary></p>
      <p class="field-hint">刪除後它會從 Agents 列表消失，REST 與 MCP 門面改回 404，無法再建立新的 Run。
        已經跑過的 Run 紀錄保留在 Runs 列表。Console 沒有復原入口。</p>
      <div data-error hidden></div>
      <div class="dialog-actions">
        <button type="button" class="btn-ghost" data-cancel>取消</button>
        <button type="button" class="btn-danger" data-confirm>刪除 Agent</button>
      </div>
    </div>
  </dialog>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;
  const dialog = root.querySelector('dialog');
  const summary = root.querySelector('[data-summary]');
  const confirm = root.querySelector('[data-confirm]');
  const cancel = root.querySelector('[data-cancel]');
  const errorSlot = root.querySelector('[data-error]');
  const onDeleted = typeof props.onDeleted === 'function' ? props.onDeleted : () => {};
  let agent = props.agent || null;
  let opener = null;
  let busy = false;

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
  };

  const setBusy = (next) => {
    busy = next;
    cancel.disabled = next;
    confirm.disabled = next;
    confirm.textContent = next ? '刪除中…' : '刪除 Agent';
  };

  const open = (trigger) => {
    if (!agent) return;
    opener = trigger || document.activeElement;
    // 名稱來自 API，只准走 textContent。
    summary.textContent = '確定要刪除「' + (agent.name || '') + '」嗎？';
    clearError();
    setBusy(false);
    dialog.showModal();
    cancel.focus();
  };

  const close = () => {
    if (!busy) dialog.close();
  };

  const onConfirm = async () => {
    if (!agent || busy) return;
    clearError();
    setBusy(true);
    try {
      await deleteAgent(agent.agentId);
      dialog.close();
      onDeleted(agent);
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) throw error;
      errorSlot.replaceChildren(createErrorBox(error, {
        hint: 'Agent 沒有被刪除。可以重新載入頁面再試一次。',
      }));
      errorSlot.hidden = false;
      confirm.focus();
    }
  };

  const onCancelKey = (event) => {
    // 刪除進行中不准用 Esc 關掉 dialog，否則畫面會停在一個看不出結果的狀態。
    if (busy) event.preventDefault();
  };
  const onClose = () => {
    clearError();
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  };

  cancel.addEventListener('click', close);
  confirm.addEventListener('click', onConfirm);
  dialog.addEventListener('cancel', onCancelKey);
  dialog.addEventListener('close', onClose);

  return {
    open,
    update(next) {
      if (next && next.agent) agent = next.agent;
    },
    destroy() {
      cancel.removeEventListener('click', close);
      confirm.removeEventListener('click', onConfirm);
      dialog.removeEventListener('cancel', onCancelKey);
      dialog.removeEventListener('close', onClose);
      if (dialog.open) dialog.close();
      root.replaceChildren();
    },
  };
}
