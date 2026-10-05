// agent-secrets-card.js —— 這個 Agent 的機密（ui-contract §5.17 / ZEN-019）。
//
// 它緊鄰設定卡，**兩張放在一起是為了讓「哪個放哪裡」一眼看得出來。**
//
// 1. **每一列只有名字、尾四碼、更新時間。沒有「顯示」按鈕，也不該假裝有** ——
//    契約保證回應不含值（api-contract §2.7 第 1 點）。與 LLM 憑證卡同一條規則。
// 2. **值不會進到沙盒。** agent 拿得到的只有名字（`ctx.secret_names`）。
//    用法是在出口白名單或寄件者設定裡**以名字引用**它，平台在代打／寄信時
//    才解密注入。不講這件事，使用者會以為存了就能在程式碼裡讀到。
// 3. **刪除被引用的機密時後端回 409**，`details.referenced_by` 說得出是誰。
//    只說「刪不掉」等於要使用者自己去猜。
//
// 屬性名一律 `data-secret-*` 前綴。

import {
  ApiError, deleteAgentSecret, listAgentSecrets, putAgentSecret,
} from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';

const FRAME = `
  <div class="credential-card" data-secret-root id="agent-secrets">
    <p class="field-hint">
      <strong>寫得進去，讀不出來。</strong> 存進來之後沒有任何端點回得出值 ——
      畫面上只看得到尾四碼。
    </p>
    <p class="field-hint">
      <strong>這些值不會進到沙盒。</strong> agent 拿得到的只有名字清單
      （<code class="mono">ctx.secret_names</code>），用來在第一個節點就說出
      「缺 SMTP_PASSWORD」。要真的用到值，在下面的
      <a href="#agent-egress" data-secret-egress-link>對外出口</a>
      規則或寄件者設定裡<strong>以名字引用</strong>它，平台會在送出的那一刻解密注入。
    </p>

    <div data-secret-error hidden></div>
    <div class="data-list" data-secret-list></div>

    <form class="keys-form" data-secret-form>
      <div class="field">
        <label for="secret-name">名字</label>
        <input type="text" id="secret-name" name="name" autocomplete="off"
               spellcheck="false" placeholder="SMTP_PASSWORD"
               aria-describedby="secret-name-hint">
        <p class="field-hint" id="secret-name-hint">
          <code class="mono">^[A-Za-z_][A-Za-z0-9_]{0,63}$</code>，最多 50 個。
          名字長得像環境變數，但它<strong>永遠不會變成環境變數</strong>。
        </p>
      </div>
      <div class="field">
        <label for="secret-value">值</label>
        <input type="password" id="secret-value" name="value" autocomplete="new-password"
               spellcheck="false" aria-describedby="secret-value-hint">
        <p class="field-hint" id="secret-value-hint">
          1–4096 字元。同名再存一次就是覆寫。
        </p>
      </div>
      <div class="form-actions">
        <button type="submit" class="btn-secondary" data-secret-submit disabled>
          儲存機密
        </button>
      </div>
    </form>

    <p class="visually-hidden" data-secret-live aria-live="polite"></p>
  </div>
`;

const REFERENCE_LABEL = {
  egress_rule: '出口規則',
  email_sender: '寄件者設定',
};

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const list = root.querySelector('[data-secret-list]');
  const form = root.querySelector('[data-secret-form]');
  const nameInput = root.querySelector('#secret-name');
  const valueInput = root.querySelector('#secret-value');
  const submit = root.querySelector('[data-secret-submit]');
  const errorSlot = root.querySelector('[data-secret-error]');
  const live = root.querySelector('[data-secret-live]');

  let agentId = props.agentId || '';
  let rows = [];
  let busy = false;
  let loaded = false;

  const say = (message) => { live.textContent = message; };

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
  };

  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };

  /** 409 的那一列：把「被誰引用」直接列出來，不要只說刪不掉（§5.17 第 4 點）。 */
  const showReferenceConflict = (error) => {
    const box = createErrorBox(error, {
      hint: '要刪它，先在「對外出口」把引用解除（改掉規則的認證機密，'
        + '或換掉寄件者的密碼機密），再回來刪。',
    });
    const users = error.details && error.details.referenced_by;
    if (Array.isArray(users) && users.length > 0) {
      const heading = el('p', 'error-hint', '正在引用它的是：');
      box.appendChild(heading);
      const items = el('ul', 'sel-list');
      for (const user of users) {
        const item = el('li');
        const label = REFERENCE_LABEL[user && user.kind] || String(user && user.kind || '未知');
        item.appendChild(el('span', null, label));
        if (user && user.host) {
          item.appendChild(el('span', null, ' · '));
          item.appendChild(el('code', 'mono', user.host));
        }
        if (user && user.smtp_host) {
          item.appendChild(el('span', null, ' · '));
          item.appendChild(el('code', 'mono', user.smtp_host));
        }
        items.appendChild(item);
      }
      box.appendChild(items);
      const jump = el('a', 'btn-ghost btn-sm', '跳到對外出口');
      jump.href = '#agent-egress';
      box.appendChild(jump);
    }
    errorSlot.replaceChildren(box);
    errorSlot.hidden = false;
  };

  const render = () => {
    list.replaceChildren();
    if (!loaded) {
      const skeleton = el('div', 'data-row');
      skeleton.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
      list.appendChild(skeleton);
      return;
    }
    if (rows.length === 0) {
      list.appendChild(el('p', 'empty-text',
        '還沒有任何機密。需要密碼或 API key 的能力（寄信、帶認證的出口）都要先在這裡存。'));
      return;
    }
    const header = el('div', 'data-row data-row--header data-row--keys');
    header.appendChild(el('span', null, '名字'));
    header.appendChild(el('span', null, '尾四碼'));
    header.appendChild(el('span', null, '更新時間'));
    header.appendChild(el('span', null, ''));
    list.appendChild(header);

    for (const row of rows) {
      const line = el('div', 'data-row data-row--keys');
      // **一律 textContent** —— 名字完全是使用者輸入。
      line.appendChild(el('code', 'mono', row.name));
      line.appendChild(el('code', 'mono', '…' + (row.lastFour || '')));
      line.appendChild(el('span', 'text-secondary', formatDateTime(row.updatedAt)));
      const actions = el('div', 'data-row-actions');
      const remove = el('button', 'btn-danger btn-sm', '刪除');
      remove.type = 'button';
      remove.dataset.secretDelete = row.name;
      remove.disabled = busy;
      actions.appendChild(remove);
      line.appendChild(actions);
      list.appendChild(line);
    }
  };

  const syncSubmit = () => {
    submit.disabled = busy
      || nameInput.value.trim() === '' || valueInput.value === '';
    submit.textContent = busy ? '儲存中…' : '儲存機密';
  };

  const load = async () => {
    try {
      rows = await listAgentSecrets(agentId);
      loaded = true;
      render();
      if (typeof props.onChange === 'function') props.onChange(rows);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      loaded = true;
      rows = [];
      render();
      showError(error, '無法讀取機密清單。重新整理頁面再試一次。');
    }
  };

  const onSubmit = async (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    clearError();
    busy = true;
    syncSubmit();
    try {
      await putAgentSecret(agentId, nameInput.value.trim(), valueInput.value);
      // 值不留在 DOM 裡。
      valueInput.value = '';
      nameInput.value = '';
      busy = false;
      syncSubmit();
      say('機密已儲存。它的值不會進到沙盒，agent 只看得到名字。');
      await load();
    } catch (error) {
      busy = false;
      syncSubmit();
      if (!(error instanceof ApiError)) throw error;
      showError(error, '平台若沒設定 ZENTRA_SECRET_KEY 就不接受機密 —— 那是刻意的，'
        + '它不會退而把值存成明文。');
      valueInput.setAttribute('aria-invalid', 'true');
      valueInput.focus();
    }
  };

  const onListClick = async (event) => {
    const button = event.target.closest('[data-secret-delete]');
    if (!button || busy) return;
    const name = button.dataset.secretDelete;
    clearError();
    busy = true;
    render();
    try {
      await deleteAgentSecret(agentId, name);
      busy = false;
      say('機密已刪除。');
      await load();
    } catch (error) {
      busy = false;
      render();
      if (!(error instanceof ApiError)) throw error;
      if (error.code === 'VERSION_CONFLICT') showReferenceConflict(error);
      else showError(error);
    }
  };

  const onInput = () => syncSubmit();

  form.addEventListener('submit', onSubmit);
  list.addEventListener('click', onListClick);
  nameInput.addEventListener('input', onInput);
  valueInput.addEventListener('input', onInput);

  render();
  if (agentId) load();

  return {
    /** 讓別的卡片（出口規則的下拉）拿到同一份清單，不必自己再抓一次。 */
    names() { return rows.map((row) => row.name); },
    refresh() { return load(); },
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        load();
      }
    },
    destroy() {
      form.removeEventListener('submit', onSubmit);
      list.removeEventListener('click', onListClick);
      nameInput.removeEventListener('input', onInput);
      valueInput.removeEventListener('input', onInput);
      valueInput.value = '';
      root.replaceChildren();
    },
  };
}
