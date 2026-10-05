// agent-config-card.js —— 這個 Agent 的可變設定（ui-contract §5.16 / ZEN-019）。
//
// 三件事決定了這張卡長什麼樣：
//
// 1. **第一句話就要說「這裡不是放密碼的地方」。** 這是整個 ZEN-019 最容易出事的
//    誤用：使用者把 API key 貼進設定，它會明文顯示在畫面上、明文送進沙盒，
//    而且**平台不會掃描內容**（一個猜不準的警告比沒有警告更糟）。
//    所以這句文案就是唯一的防線，它必須在最上面。
// 2. **編輯器是一個 `<textarea>` 裝 JSON，不是動態鍵值表單。** 契約允許任何 JSON
//    值（巢狀物件、陣列、null），鍵值表單做不到那個自由度，做半套會讓使用者
//    以為不能用陣列。前端就驗 `JSON.parse` 並指出行號，不要送出去換一個 422。
// 3. **送出是整份取代。** 沒有 PATCH。使用者最容易以為是合併，所以 CTA 與確認
//    視窗都要用「取代 / 完整覆蓋」這組字。
//
// 屬性名一律 `data-config-*` 前綴（§5.16 第 6 點）：這一頁上還有別的卡片，
// `data-form` / `data-submit` 那些通用名字會撞。

import { ApiError, getAgentConfig, putAgentConfig } from '../api.js';
import { createErrorBox, el, formatDateTime, toPrettyJson } from '../dom.js';

const FRAME = `
  <div class="credential-card" data-config-root>
    <p class="field-hint">
      <strong>這裡不是放密碼的地方。</strong>
      設定是明文：任何打得開這個畫面的人都看得到，而且它會
      <strong>完整送進沙盒</strong>。API key、密碼、token 請放下面那張
      <a href="#agent-secrets" data-config-secrets-link>機密</a>卡片 ——
      平台<strong>不會</strong>掃描這份文件的內容，也不會猜哪個值像 secret。
    </p>
    <p class="field-hint">
      改了之後<strong>立刻對所有版本生效，下一筆 Run 就是新值</strong>；
      已經在跑的 Run 不受影響（它的設定在派工那一刻就定稿了）。
    </p>

    <p class="field-hint" data-config-summary>正在讀取設定…</p>
    <div data-config-error hidden></div>

    <form class="credential-form" data-config-form hidden>
      <div class="field">
        <label for="config-json">設定文件（JSON）</label>
        <textarea id="config-json" name="config" rows="14" spellcheck="false"
                  class="mono" aria-describedby="config-json-hint"></textarea>
        <p class="field-hint" id="config-json-hint">
          頂層必須是物件；鍵名 <code class="mono">^[A-Za-z_][A-Za-z0-9_]{0,63}$</code>，
          最多 100 個；值可以是任何 JSON（字串、數字、布林、null、陣列、巢狀物件）。
          整份 ≤ 64 KiB —— 它跟著<strong>每一筆 Run</strong> 走。
        </p>
        <p class="field-error" data-config-syntax hidden></p>
      </div>
      <div class="form-actions">
        <button type="button" class="btn-ghost" data-config-reset>還原</button>
        <button type="submit" class="btn-secondary" data-config-submit disabled>
          取代整份設定
        </button>
      </div>
    </form>

    <dialog data-config-confirm aria-labelledby="config-confirm-title">
      <form class="dialog-form" method="dialog">
        <h2 class="dialog-title" id="config-confirm-title">取代整份設定？</h2>
        <p class="field-hint">
          這份文件會<strong>完整覆蓋</strong>現有設定，不是合併 ——
          舊文件裡有、新文件裡沒有的鍵會消失。
          下一筆 Run 就會用新值，已經在跑的不受影響。
        </p>
        <div class="dialog-actions">
          <button type="button" class="btn-ghost" data-config-cancel>取消</button>
          <button type="button" class="btn-danger" data-config-ok>確定取代</button>
        </div>
      </form>
    </dialog>

    <p class="visually-hidden" data-config-live aria-live="polite"></p>
  </div>
`;

/** 從 JSON.parse 的錯誤訊息裡挖出位置，換算成行號。指不出來就回 null。 */
function lineOfSyntaxError(text, error) {
  const match = /position (\d+)/i.exec(String(error && error.message));
  if (!match) return null;
  const offset = Number(match[1]);
  if (!Number.isFinite(offset)) return null;
  return text.slice(0, offset).split('\n').length;
}

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const summary = root.querySelector('[data-config-summary]');
  const form = root.querySelector('[data-config-form]');
  const editor = root.querySelector('#config-json');
  const submit = root.querySelector('[data-config-submit]');
  const reset = root.querySelector('[data-config-reset]');
  const syntax = root.querySelector('[data-config-syntax]');
  const errorSlot = root.querySelector('[data-config-error]');
  const live = root.querySelector('[data-config-live]');
  const confirm = root.querySelector('[data-config-confirm]');
  const confirmOk = root.querySelector('[data-config-ok]');
  const confirmCancel = root.querySelector('[data-config-cancel]');

  let agentId = props.agentId || '';
  let current = { config: {}, updatedAt: null };
  let busy = false;

  const say = (message) => { live.textContent = message; };

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
  };

  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };

  /** 摘要那一行。**全部 textContent** —— 鍵名來自使用者輸入。 */
  const renderSummary = () => {
    summary.replaceChildren();
    const keys = Object.keys(current.config || {});
    if (keys.length === 0) {
      // 「沒設定」與「設定成空的」在契約裡是同一件事，畫面不該暗示有差別，
      // 所以這裡顯示 `{}` 而不是空白（§5.16 第 4 點）。
      summary.append(el('code', 'mono', '{}'));
      summary.append(' 還沒有任何設定。這支 agent 讀到的 ');
      summary.append(el('code', 'mono', 'ctx.config'));
      summary.append(' 會是空物件。');
      return;
    }
    summary.append(el('strong', null, String(keys.length)));
    summary.append(' 個頂層鍵：');
    summary.append(el('code', 'mono', keys.join(', ')));
    if (current.updatedAt) {
      summary.append('　更新於 ' + formatDateTime(current.updatedAt));
    }
  };

  const clearSyntax = () => {
    syntax.hidden = true;
    syntax.textContent = '';
    editor.removeAttribute('aria-invalid');
  };

  const showSyntax = (message) => {
    syntax.textContent = message;
    syntax.hidden = false;
    editor.setAttribute('aria-invalid', 'true');
  };

  /** 前端就驗，不要送出去換一個 422 回來（§5.16 第 2 點）。 */
  const parsed = () => {
    const text = editor.value.trim();
    if (text === '') return { ok: false, message: '設定不能是空白。沒有設定請填 {}。' };
    let value;
    try {
      value = JSON.parse(text);
    } catch (error) {
      const line = lineOfSyntaxError(text, error);
      return {
        ok: false,
        message: 'JSON 語法錯誤' + (line ? `（第 ${line} 行）` : '') + '：' + error.message,
      };
    }
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return { ok: false, message: '頂層必須是物件（{ … }），不能是陣列或單一值。' };
    }
    return { ok: true, value };
  };

  const syncSubmit = () => {
    submit.disabled = busy;
    submit.textContent = busy ? '儲存中…' : '取代整份設定';
    reset.disabled = busy;
    editor.disabled = busy;
  };

  const fill = () => {
    editor.value = toPrettyJson(current.config || {});
    clearSyntax();
  };

  const doSave = async (value) => {
    clearError();
    busy = true;
    syncSubmit();
    try {
      current = await putAgentConfig(agentId, value);
      busy = false;
      syncSubmit();
      fill();
      renderSummary();
      say('設定已取代，下一筆 Run 生效。');
    } catch (error) {
      busy = false;
      syncSubmit();
      if (!(error instanceof ApiError)) throw error;
      showError(error, '設定沒有被寫入 —— 畫面上的內容還是你剛才輸入的那份，'
        + '修正之後可以再送一次。');
    }
  };

  let pendingValue = null;

  const onSubmit = (event) => {
    event.preventDefault();
    if (busy) return;
    const result = parsed();
    if (!result.ok) {
      showSyntax(result.message);
      editor.focus();
      return;
    }
    clearSyntax();
    pendingValue = result.value;
    confirm.showModal();
  };

  const onInput = () => { if (!syntax.hidden) clearSyntax(); };
  const onReset = () => { fill(); clearError(); };
  const onConfirmOk = () => {
    const value = pendingValue;
    pendingValue = null;
    confirm.close();
    if (value) doSave(value);
  };
  const onConfirmCancel = () => { pendingValue = null; confirm.close(); };

  form.addEventListener('submit', onSubmit);
  editor.addEventListener('input', onInput);
  reset.addEventListener('click', onReset);
  confirmOk.addEventListener('click', onConfirmOk);
  confirmCancel.addEventListener('click', onConfirmCancel);

  const load = async () => {
    try {
      current = await getAgentConfig(agentId);
      form.hidden = false;
      fill();
      renderSummary();
      syncSubmit();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      summary.textContent = '';
      showError(error, '無法讀取設定。重新整理頁面再試一次。');
    }
  };

  if (agentId) load();

  return {
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        load();
      }
    },
    destroy() {
      form.removeEventListener('submit', onSubmit);
      editor.removeEventListener('input', onInput);
      reset.removeEventListener('click', onReset);
      confirmOk.removeEventListener('click', onConfirmOk);
      confirmCancel.removeEventListener('click', onConfirmCancel);
      if (confirm.open) confirm.close();
      root.replaceChildren();
    },
  };
}
