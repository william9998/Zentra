import { ApiError, createAgent } from '../api.js';
import { createErrorBox } from '../dom.js';

const FRAME = `
  <dialog aria-labelledby="create-agent-title">
    <form class="dialog-form" data-form>
      <h2 class="dialog-title" id="create-agent-title">建立 Agent</h2>
      <p class="field-hint">先建立 metadata，下一步再註冊 immutable OCI image 版本。</p>
      <div class="field">
        <label for="agent-name">名稱</label>
        <input type="text" id="agent-name" name="name" required autocomplete="off"
               aria-describedby="agent-name-hint">
        <p class="field-hint" id="agent-name-hint">必填；用來辨識 Agent。</p>
      </div>
      <div class="field">
        <label for="agent-description">描述</label>
        <textarea id="agent-description" name="description" rows="4"
                  aria-describedby="agent-description-hint"></textarea>
        <p class="field-hint" id="agent-description-hint">選填。建立後即可註冊第一個 image 版本。</p>
      </div>
      <div data-error hidden></div>
      <div class="dialog-actions">
        <button type="button" class="btn-ghost" data-cancel>取消</button>
        <button type="submit" class="btn-primary" data-submit disabled>建立 Agent</button>
      </div>
    </form>
  </dialog>
`;

export function mount(root) {
  root.innerHTML = FRAME;
  const dialog = root.querySelector('dialog');
  const form = root.querySelector('[data-form]');
  const name = root.querySelector('#agent-name');
  const description = root.querySelector('#agent-description');
  const submit = root.querySelector('[data-submit]');
  const cancel = root.querySelector('[data-cancel]');
  const errorSlot = root.querySelector('[data-error]');
  let opener = null;
  let busy = false;

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
    name.removeAttribute('aria-invalid');
    description.removeAttribute('aria-invalid');
  };

  const syncSubmit = () => {
    submit.disabled = busy || name.value.trim() === '';
    submit.textContent = busy ? '建立中…' : '建立 Agent';
  };

  const setBusy = (next) => {
    busy = next;
    name.disabled = next;
    description.disabled = next;
    cancel.disabled = next;
    syncSubmit();
  };

  const open = (trigger) => {
    opener = trigger || document.activeElement;
    form.reset();
    clearError();
    setBusy(false);
    dialog.showModal();
    name.focus();
  };

  const close = () => {
    if (!busy) dialog.close();
  };

  const onInput = () => syncSubmit();
  const onSubmit = async (event) => {
    event.preventDefault();
    const value = name.value.trim();
    if (!value) {
      const target = error.field === 'description' ? description : name;
      target.setAttribute('aria-invalid', 'true');
      target.focus();
      return;
    }
    clearError();
    setBusy(true);
    try {
      const agent = await createAgent({ name: value, description: description.value.trim() });
      dialog.close();
      location.href = '/console/agents/' + encodeURIComponent(agent.agentId);
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) throw error;
      errorSlot.replaceChildren(createErrorBox(error, {
        hint: '請修正欄位後再試一次。若名稱已存在，請改用另一個名稱。',
      }));
      errorSlot.hidden = false;
      name.setAttribute('aria-invalid', 'true');
      name.focus();
    }
  };
  const onClose = () => {
    clearError();
    if (opener && opener.isConnected) opener.focus();
    opener = null;
  };

  name.addEventListener('input', onInput);
  cancel.addEventListener('click', close);
  form.addEventListener('submit', onSubmit);
  dialog.addEventListener('close', onClose);

  return {
    open,
    update() {},
    destroy() {
      name.removeEventListener('input', onInput);
      cancel.removeEventListener('click', close);
      form.removeEventListener('submit', onSubmit);
      dialog.removeEventListener('close', onClose);
      if (dialog.open) dialog.close();
      root.replaceChildren();
    },
  };
}
