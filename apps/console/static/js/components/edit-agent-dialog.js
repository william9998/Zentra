// edit-agent-dialog.js —— 改 Agent metadata（name / description）。
//
// 只碰 metadata。Agent「跑什麼」由版本決定，發布後不可變（P5），
// 要換行為是去註冊新的 image 版本，不是在這裡編輯。
//
// 送的是 PATCH，所以**只放真的改了的欄位**：沒改的鍵不送，後端就不動它。
// 兩欄都沒改時 primary CTA 維持 disabled —— 空 body 後端會回 422。

import { ApiError, updateAgent } from '../api.js';
import { createErrorBox } from '../dom.js';

const FRAME = `
  <dialog aria-labelledby="edit-agent-title">
    <form class="dialog-form" data-form>
      <h2 class="dialog-title" id="edit-agent-title">編輯 Agent</h2>
      <p class="field-hint">只改 metadata。已發布的版本與 image digest 不會被動到。</p>
      <div class="field">
        <label for="edit-agent-name">名稱</label>
        <input type="text" id="edit-agent-name" name="name" required autocomplete="off"
               aria-describedby="edit-agent-name-hint">
        <p class="field-hint" id="edit-agent-name-hint">必填；名稱不能與其他 Agent 重複。</p>
      </div>
      <div class="field">
        <label for="edit-agent-description">描述</label>
        <textarea id="edit-agent-description" name="description" rows="4"
                  aria-describedby="edit-agent-description-hint"></textarea>
        <p class="field-hint" id="edit-agent-description-hint">選填；清空即移除描述。</p>
      </div>
      <div data-error hidden></div>
      <div class="dialog-actions">
        <button type="button" class="btn-ghost" data-cancel>取消</button>
        <button type="submit" class="btn-secondary" data-submit disabled>儲存變更</button>
      </div>
    </form>
  </dialog>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;
  const dialog = root.querySelector('dialog');
  const form = root.querySelector('[data-form]');
  const name = root.querySelector('#edit-agent-name');
  const description = root.querySelector('#edit-agent-description');
  const submit = root.querySelector('[data-submit]');
  const cancel = root.querySelector('[data-cancel]');
  const errorSlot = root.querySelector('[data-error]');
  const onSaved = typeof props.onSaved === 'function' ? props.onSaved : () => {};
  let agent = props.agent || null;
  let opener = null;
  let busy = false;

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
    name.removeAttribute('aria-invalid');
    description.removeAttribute('aria-invalid');
  };

  /** 只回傳真的改了的欄位；兩欄都沒動時回 null。 */
  const changes = () => {
    if (!agent) return null;
    const patch = {};
    const nextName = name.value.trim();
    const nextDescription = description.value.trim();
    if (nextName !== '' && nextName !== agent.name) patch.name = nextName;
    if (nextDescription !== (agent.description || '')) patch.description = nextDescription;
    return Object.keys(patch).length > 0 ? patch : null;
  };

  const syncSubmit = () => {
    submit.disabled = busy || name.value.trim() === '' || changes() === null;
    submit.textContent = busy ? '儲存中…' : '儲存變更';
  };

  const setBusy = (next) => {
    busy = next;
    name.disabled = next;
    description.disabled = next;
    cancel.disabled = next;
    syncSubmit();
  };

  const open = (trigger) => {
    if (!agent) return;
    opener = trigger || document.activeElement;
    name.value = agent.name || '';
    description.value = agent.description || '';
    clearError();
    setBusy(false);
    dialog.showModal();
    name.focus();
    name.select();
  };

  const close = () => {
    if (!busy) dialog.close();
  };

  const onInput = () => syncSubmit();
  const onSubmit = async (event) => {
    event.preventDefault();
    const patch = changes();
    if (!agent || !patch) return;
    clearError();
    setBusy(true);
    try {
      const updated = await updateAgent(agent.agentId, patch);
      agent = updated;
      dialog.close();
      onSaved(updated);
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) throw error;
      errorSlot.replaceChildren(createErrorBox(error, {
        hint: '請修正欄位後再試一次。若名稱已存在，請改用另一個名稱。',
      }));
      errorSlot.hidden = false;
      const target = error.field === 'description' ? description : name;
      target.setAttribute('aria-invalid', 'true');
      target.focus();
    }
  };
  const onClose = () => {
    clearError();
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  };

  name.addEventListener('input', onInput);
  description.addEventListener('input', onInput);
  cancel.addEventListener('click', close);
  form.addEventListener('submit', onSubmit);
  dialog.addEventListener('close', onClose);

  return {
    open,
    update(next) {
      if (next && next.agent) agent = next.agent;
    },
    destroy() {
      name.removeEventListener('input', onInput);
      description.removeEventListener('input', onInput);
      cancel.removeEventListener('click', close);
      form.removeEventListener('submit', onSubmit);
      dialog.removeEventListener('close', onClose);
      if (dialog.open) dialog.close();
      root.replaceChildren();
    },
  };
}
