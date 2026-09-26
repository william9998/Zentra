// llm-credential-card.js —— 這個 Agent 的 LLM 憑證（ZEN-012 / ADR-0011）。
//
// 三件事決定了這個元件長什麼樣：
//
// 1. **憑證綁 Agent 不綁版本。** 所以它出現在 Agent 詳情頁，不是版本列表裡，
//    而且畫面上要明說「改了所有版本一起變」—— 那是使用者最容易誤會的地方。
// 2. **金鑰寫得進去讀不出來。** 契約保證回應只有尾四碼（api-contract §2.1），
//    所以這裡沒有「顯示金鑰」的功能可做，也不該假裝有。輸入框是 password 型別，
//    送出後立刻清空，不留在 DOM 裡。
// 屬性名一律用 `data-cred-*` 前綴：這張卡與編輯／刪除對話框在同一頁，
//    而 `data-form` / `data-submit` / `data-summary` 那些通用名字它們已經用了。
//    元件內部用 root.querySelector 不會撞，但測試與除錯用 document.querySelector 會。
//
// 3. **移除不是停用。** 刪掉之後這個 Agent 退回用平台預設金鑰，不是不能呼叫 LLM。
//    按鈕文字與二次確認都要講清楚這件事。

import {
  ApiError, deleteAgentLlmCredential, getAgentLlmCredential, setAgentLlmCredential,
} from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';

const FRAME = `
  <div class="credential-card" data-cred-root>
    <p class="field-hint" data-cred-summary>正在讀取憑證狀態…</p>

    <form class="credential-form" data-cred-form hidden>
      <div class="field">
        <label for="cred-api-key">供應商金鑰</label>
        <input type="password" id="cred-api-key" name="api_key" autocomplete="off"
               spellcheck="false" placeholder="sk-…" aria-describedby="cred-api-key-hint">
        <p class="field-hint" id="cred-api-key-hint">
          存進平台時會加密。<strong>寫進去之後就讀不出來了</strong>，畫面上只看得到尾四碼。
        </p>
      </div>
      <div class="field">
        <label for="cred-base-url">Base URL<span class="field-optional">選填</span></label>
        <input type="url" id="cred-base-url" name="base_url" autocomplete="off"
               placeholder="https://api.openai.com/v1" aria-describedby="cred-base-url-hint">
        <p class="field-hint" id="cred-base-url-hint">
          OpenAI 相容端點。留空用平台預設。金鑰與端點通常是一組的 ——
          換供應商時兩個要一起改。
        </p>
      </div>
      <div class="field">
        <label for="cred-model">預設 model<span class="field-optional">選填</span></label>
        <input type="text" id="cred-model" name="default_model" autocomplete="off"
               placeholder="gpt-4o-mini" aria-describedby="cred-model-hint">
        <p class="field-hint" id="cred-model-hint">
          Agent 沒指定 model 時用哪個。留空用平台預設。
        </p>
      </div>
      <div data-cred-error hidden></div>
      <div class="form-actions">
        <button type="button" class="btn-ghost" data-cred-remove hidden>移除憑證</button>
        <button type="submit" class="btn-primary" data-cred-submit disabled>儲存憑證</button>
      </div>
    </form>

    <dialog data-confirm aria-labelledby="cred-confirm-title">
      <form class="dialog-form" method="dialog">
        <h2 class="dialog-title" id="cred-confirm-title" data-confirm-title></h2>
        <p class="field-hint" data-confirm-body></p>
        <div class="dialog-actions">
          <button type="button" class="btn-ghost" data-confirm-cancel>取消</button>
          <button type="button" class="btn-danger" data-confirm-ok></button>
        </div>
      </form>
    </dialog>

    <p class="visually-hidden" data-cred-live aria-live="polite"></p>
  </div>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const summary = root.querySelector('[data-cred-summary]');
  const form = root.querySelector('[data-cred-form]');
  const apiKey = root.querySelector('#cred-api-key');
  const baseUrl = root.querySelector('#cred-base-url');
  const model = root.querySelector('#cred-model');
  const submit = root.querySelector('[data-cred-submit]');
  const remove = root.querySelector('[data-cred-remove]');
  const errorSlot = root.querySelector('[data-cred-error]');
  const live = root.querySelector('[data-cred-live]');
  const confirm = root.querySelector('[data-confirm]');
  const confirmTitle = root.querySelector('[data-confirm-title]');
  const confirmBody = root.querySelector('[data-confirm-body]');
  const confirmOk = root.querySelector('[data-confirm-ok]');
  const confirmCancel = root.querySelector('[data-confirm-cancel]');

  let agentId = props.agentId || '';
  let current = null;
  let busy = false;
  let pending = null;          // 等待二次確認的動作

  const say = (message) => { live.textContent = message; };

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
    apiKey.removeAttribute('aria-invalid');
  };

  const showError = (error, hint) => {
    errorSlot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    errorSlot.hidden = false;
  };

  /** 摘要那一行。**全部走 textContent** —— 這裡的字串來自 API。 */
  const renderSummary = () => {
    summary.replaceChildren();
    if (!current || !current.configured) {
      // 未設定是**剛建立 Agent 時的預設狀態**（建立對話框刻意不收金鑰，
      // 見 BOARD/ZEN-012），所以這裡要說得夠清楚可以直接動手，
      // 而不只是陳述「沒有設定」。
      summary.append(el('span', 'status-dot'));
      summary.append(' 尚未設定。這個 Agent 呼叫 LLM 時會用');
      summary.append(el('strong', null, '平台預設的金鑰'));
      summary.append('。要讓它用自己的帳號，在下面填入金鑰即可 —— ');
      summary.append('設定一次之後，這個 Agent 的');
      summary.append(el('strong', null, '所有版本'));
      summary.append('都會用它，發新版本不用重填。');
      return;
    }
    // 沿用既有的狀態圓點命名，不自創 class（ui-contract §2 狀態語言）
    summary.append(el('span', 'status-dot status-dot--PUBLISHED'));
    summary.append(' 已設定，尾碼 ');
    summary.append(el('code', 'mono', '…' + (current.lastFour || '')));
    if (current.baseUrl) {
      summary.append('　端點 ');
      summary.append(el('code', 'mono', current.baseUrl));
    }
    if (current.defaultModel) {
      summary.append('　預設 model ');
      summary.append(el('code', 'mono', current.defaultModel));
    }
    if (current.updatedAt) {
      summary.append('　更新於 ' + formatDateTime(current.updatedAt));
    }
  };

  const syncSubmit = () => {
    submit.disabled = busy || apiKey.value.trim().length < 8;
    submit.textContent = busy ? '儲存中…' : (current && current.configured ? '更換憑證' : '儲存憑證');
    remove.hidden = !(current && current.configured);
    remove.disabled = busy;
  };

  const setBusy = (next) => {
    busy = next;
    apiKey.disabled = next;
    baseUrl.disabled = next;
    model.disabled = next;
    syncSubmit();
  };

  const openConfirm = (action) => {
    pending = action;
    confirmTitle.textContent = action.title;
    confirmBody.textContent = action.body;
    confirmOk.textContent = action.ok;
    confirm.showModal();
  };

  const doSave = async () => {
    clearError();
    setBusy(true);
    try {
      current = await setAgentLlmCredential(agentId, {
        apiKey: apiKey.value.trim(),
        baseUrl: baseUrl.value.trim(),
        defaultModel: model.value.trim(),
      });
      apiKey.value = '';            // 金鑰不留在 DOM 裡
      setBusy(false);
      renderSummary();
      syncSubmit();
      say('憑證已儲存，這個 Agent 的所有版本都會用它。');
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) throw error;
      showError(error, '平台若沒設定 ZENTRA_SECRET_KEY 就不會接受憑證 —— 那是刻意的，'
        + '它不會退而把金鑰存成明文。');
      apiKey.setAttribute('aria-invalid', 'true');
      apiKey.focus();
    }
  };

  const doRemove = async () => {
    clearError();
    setBusy(true);
    try {
      await deleteAgentLlmCredential(agentId);
      current = { configured: false };
      apiKey.value = '';
      setBusy(false);
      renderSummary();
      syncSubmit();
      say('憑證已移除，這個 Agent 會改用平台預設金鑰。');
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) throw error;
      showError(error);
    }
  };

  const onInput = () => syncSubmit();

  const onSubmit = (event) => {
    event.preventDefault();
    if (submit.disabled) return;
    if (current && current.configured) {
      openConfirm({
        title: '更換這個 Agent 的憑證？',
        body: '新金鑰會立刻對這個 Agent 的所有版本生效，包含已發布的舊版本。'
          + '這個動作沒有灰度也沒有回滾。',
        ok: '確定更換',
        run: doSave,
      });
      return;
    }
    doSave();
  };

  const onRemove = () => {
    openConfirm({
      title: '移除這個 Agent 的憑證？',
      body: '移除之後這個 Agent 會改用平台預設金鑰，不是停止呼叫 LLM。'
        + '金鑰本身無法復原，要用回原本那把得重新輸入一次。',
      ok: '確定移除',
      run: doRemove,
    });
  };

  const onConfirmOk = () => {
    const action = pending;
    pending = null;
    confirm.close();
    if (action) action.run();
  };
  const onConfirmCancel = () => { pending = null; confirm.close(); };

  apiKey.addEventListener('input', onInput);
  form.addEventListener('submit', onSubmit);
  remove.addEventListener('click', onRemove);
  confirmOk.addEventListener('click', onConfirmOk);
  confirmCancel.addEventListener('click', onConfirmCancel);

  const load = async () => {
    try {
      current = await getAgentLlmCredential(agentId);
      form.hidden = false;
      renderSummary();
      syncSubmit();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      summary.textContent = '';
      showError(error, '無法讀取憑證狀態。重新整理頁面再試一次。');
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
      apiKey.removeEventListener('input', onInput);
      form.removeEventListener('submit', onSubmit);
      remove.removeEventListener('click', onRemove);
      confirmOk.removeEventListener('click', onConfirmOk);
      confirmCancel.removeEventListener('click', onConfirmCancel);
      if (confirm.open) confirm.close();
      apiKey.value = '';
      root.replaceChildren();
    },
  };
}
