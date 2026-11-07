// egress-card.js —— 對外出口與寄信（ui-contract §5.18 / ZEN-019）。
//
// 一張卡，上下兩塊：上面是主機白名單（api-contract §2.8），下面是寄件者（§2.9）。
// **合成一張是因為它們回答同一個問題：這個 Agent 能把東西送到哪裡去。**
//
// 1. **空狀態要說出後果，不是說「沒有資料」。** 預設拒絕是這個功能最重要的性質，
//    而一個空清單看起來跟「還沒載入」一模一樣。
// 2. `POST` 要用 `--warning` 標出來 —— 它是「能把東西寫出去」的那一個。
// 3. **`auth_secret_name` 是下拉，不讓使用者手打**：打錯名字只會在下一次 Run
//    才失敗，而那時畫面上什麼線索都沒有。
// 4. 寄件者區塊**沒有密碼欄位**，只有指向機密的下拉。
// 5. `allowed_recipients` 空的時候 CTA 維持 disabled，並說明理由。
//
// 屬性名一律 `data-egress-*` 前綴。

import {
  ApiError, createEgressRule, deleteEgressRule, deleteEmailSender,
  getEmailSender, listEgressRules, putEmailSender,
} from '../api.js';
import { createErrorBox, el, formatDateTime } from '../dom.js';

const FRAME = `
  <div class="credential-card" data-egress-root id="agent-egress">
    <p class="field-hint">
      沙盒網段<strong>沒有對外路由</strong>。這裡定義的是「平台願意替這個 Agent
      代打哪些主機」—— 白名單擋得住<strong>送到哪</strong>，擋不住<strong>送了什麼</strong>。
    </p>

    <h3 class="usage-heading">主機白名單</h3>
    <div data-egress-rules-error hidden></div>
    <div class="data-list" data-egress-rules></div>

    <form class="keys-form" data-egress-rule-form>
      <div class="form-grid">
        <div class="field">
          <label for="egress-host">主機</label>
          <input type="text" id="egress-host" name="host" autocomplete="off"
                 spellcheck="false" placeholder="api.binance.com"
                 aria-describedby="egress-host-hint">
          <p class="field-hint" id="egress-host-hint">
            小寫主機名或 IP（<code class="mono">localhost</code> 是平台自己，連本機服務用
            <code class="mono">host.docker.internal</code>）。<strong>沒有萬用字元</strong>，
            不能帶 scheme、port 或路徑，一條規則放行該主機所有 port。每個 Agent 最多 20 條。
          </p>
        </div>
        <div class="field">
          <label for="egress-path-prefix">路徑前綴<span class="field-optional">選填</span></label>
          <input type="text" id="egress-path-prefix" name="path_prefix" autocomplete="off"
                 spellcheck="false" placeholder="/api/v3/">
        </div>
      </div>
      <fieldset class="field">
        <legend>允許的 method</legend>
        <label class="field--inline">
          <input type="checkbox" data-egress-method="GET" checked> GET
        </label>
        <label class="field--inline">
          <input type="checkbox" data-egress-method="POST"> POST
        </label>
        <p class="field-hint" data-egress-post-warning hidden>
          <strong>POST 可以把資料送到外部網站</strong>，只在需要時開啟。
        </p>
      </fieldset>
      <div class="form-grid">
        <div class="field">
          <label for="egress-auth-secret">認證機密<span class="field-optional">選填</span></label>
          <select id="egress-auth-secret" name="auth_secret_name"
                  aria-describedby="egress-auth-hint">
            <option value="">不帶認證</option>
          </select>
          <p class="field-hint" id="egress-auth-hint" data-egress-auth-hint>
            選一個機密，平台會在送出時解密並放進下面那個 header。
            <strong>值不會進到沙盒。</strong>
          </p>
        </div>
        <div class="field">
          <label for="egress-auth-header">認證 header<span class="field-optional">與機密成對</span></label>
          <input type="text" id="egress-auth-header" name="auth_header" autocomplete="off"
                 spellcheck="false" placeholder="X-MBX-APIKEY">
        </div>
      </div>
      <div class="field">
        <label for="egress-description">說明<span class="field-optional">選填</span></label>
        <input type="text" id="egress-description" name="description" maxlength="200"
               autocomplete="off" placeholder="Binance 現貨行情">
      </div>
      <div class="form-actions">
        <button type="submit" class="btn-secondary" data-egress-rule-submit disabled>
          新增規則
        </button>
      </div>
    </form>

    <h3 class="usage-heading">寄件者</h3>
    <p class="field-hint">
      SMTP 不走上面的白名單，它是獨立的一份設定。
      <strong>這裡沒有密碼欄位</strong> —— 密碼存在機密裡，這裡只指名字。
      Gmail 要用<strong>應用程式密碼</strong>，不是登入密碼。
    </p>
    <p class="field-hint" data-egress-sender-summary>正在讀取寄件者設定…</p>
    <div data-egress-sender-error hidden></div>

    <form class="keys-form" data-egress-sender-form hidden>
      <div class="form-grid">
        <div class="field">
          <label for="egress-smtp-host">SMTP 主機</label>
          <input type="text" id="egress-smtp-host" name="smtp_host" autocomplete="off"
                 spellcheck="false" placeholder="smtp.gmail.com">
        </div>
        <div class="field">
          <label for="egress-smtp-port">Port</label>
          <select id="egress-smtp-port" name="smtp_port">
            <option value="587">587（STARTTLS）</option>
            <option value="465">465（隱式 TLS）</option>
            <option value="25">25（需可升級 TLS）</option>
          </select>
        </div>
      </div>
      <div class="form-grid">
        <div class="field">
          <label for="egress-username">帳號</label>
          <input type="text" id="egress-username" name="username" autocomplete="off"
                 spellcheck="false" placeholder="me@gmail.com">
        </div>
        <div class="field">
          <label for="egress-from">寄件地址</label>
          <input type="text" id="egress-from" name="from_address" autocomplete="off"
                 spellcheck="false" placeholder="me@gmail.com">
        </div>
      </div>
      <div class="field">
        <label for="egress-smtp-secret">密碼（以機密名字引用）</label>
        <select id="egress-smtp-secret" name="password_secret_name"
                aria-describedby="egress-smtp-hint">
          <option value="">選一個機密</option>
        </select>
        <p class="field-hint" id="egress-smtp-hint" data-egress-smtp-hint>
          先在上面的機密卡存一個 <code class="mono">SMTP_PASSWORD</code>，這裡才選得到。
        </p>
      </div>
      <div class="field">
        <label for="egress-recipients">收件人白名單</label>
        <input type="text" id="egress-recipients" name="allowed_recipients"
               autocomplete="off" spellcheck="false" placeholder="me@example.com, ops@example.com"
               aria-describedby="egress-recipients-hint">
        <p class="field-hint" id="egress-recipients-hint">
          以逗號分隔，1–10 個<strong>精確地址</strong>，不接受網域萬用字元。
          <strong>沒有收件人白名單，一個迴圈寫錯就會用你的帳號寄出幾百封信</strong>，
          而被鎖的是那個帳號，不是這支 agent。
        </p>
      </div>
      <div class="form-actions">
        <button type="button" class="btn-ghost" data-egress-sender-remove hidden>
          移除寄件者
        </button>
        <button type="submit" class="btn-secondary" data-egress-sender-submit disabled>
          儲存寄件者
        </button>
      </div>
    </form>

    <p class="field-hint">
      <a href="#agent-egress-calls" data-egress-audit-link>
        看看這個 Agent 實際送出去了什麼 →
      </a>
    </p>
    <p class="visually-hidden" data-egress-live aria-live="polite"></p>
  </div>
`;

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const rulesList = root.querySelector('[data-egress-rules]');
  const rulesError = root.querySelector('[data-egress-rules-error]');
  const ruleForm = root.querySelector('[data-egress-rule-form]');
  const hostInput = root.querySelector('#egress-host');
  const pathInput = root.querySelector('#egress-path-prefix');
  const authSelect = root.querySelector('#egress-auth-secret');
  const authHint = root.querySelector('[data-egress-auth-hint]');
  const authHeader = root.querySelector('#egress-auth-header');
  const descriptionInput = root.querySelector('#egress-description');
  const ruleSubmit = root.querySelector('[data-egress-rule-submit]');
  const postToggle = root.querySelector('[data-egress-method="POST"]');
  const getToggle = root.querySelector('[data-egress-method="GET"]');
  const postWarning = root.querySelector('[data-egress-post-warning]');

  const senderSummary = root.querySelector('[data-egress-sender-summary]');
  const senderError = root.querySelector('[data-egress-sender-error]');
  const senderForm = root.querySelector('[data-egress-sender-form]');
  const smtpHost = root.querySelector('#egress-smtp-host');
  const smtpPort = root.querySelector('#egress-smtp-port');
  const username = root.querySelector('#egress-username');
  const fromAddress = root.querySelector('#egress-from');
  const passwordSecret = root.querySelector('#egress-smtp-secret');
  const passwordHint = root.querySelector('[data-egress-smtp-hint]');
  const recipients = root.querySelector('#egress-recipients');
  const senderSubmit = root.querySelector('[data-egress-sender-submit]');
  const senderRemove = root.querySelector('[data-egress-sender-remove]');
  const live = root.querySelector('[data-egress-live]');

  let agentId = props.agentId || '';
  let rules = [];
  let rulesLoaded = false;
  let sender = { configured: false };
  let secretNames = Array.isArray(props.secretNames) ? props.secretNames.slice() : [];
  let busy = false;

  const say = (message) => { live.textContent = message; };

  const showIn = (slot, error, hint) => {
    slot.replaceChildren(createErrorBox(error, hint ? { hint } : {}));
    slot.hidden = false;
  };
  const clearIn = (slot) => { slot.hidden = true; slot.replaceChildren(); };

  const renderRules = () => {
    rulesList.replaceChildren();
    if (!rulesLoaded) {
      const skeleton = el('div', 'data-row');
      skeleton.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
      rulesList.appendChild(skeleton);
      return;
    }
    if (rules.length === 0) {
      // **說出後果**，不是「沒有資料」（§5.18 第 1 點）。
      rulesList.appendChild(el('p', 'empty-text',
        '還沒有任何規則，這個 Agent 連不到任何外部網站。'
        + '這是預設拒絕的正常行為，不是故障 —— 要讓它抓 Binance 的 K 棒，'
        + '在下面加一條 api.binance.com。'));
      return;
    }
    const header = el('div', 'data-row data-row--header data-row--keys');
    header.appendChild(el('span', null, '主機'));
    header.appendChild(el('span', null, 'Method'));
    header.appendChild(el('span', null, '路徑前綴 / 認證'));
    header.appendChild(el('span', null, ''));
    rulesList.appendChild(header);

    for (const rule of rules) {
      const line = el('div', 'data-row data-row--keys');
      const identity = el('div', 'list-identity');
      identity.appendChild(el('code', 'mono', rule.host));
      if (rule.description) {
        identity.appendChild(el('span', 'list-description', rule.description));
      }
      line.appendChild(identity);

      const methods = el('div', 'inline-row');
      for (const method of rule.methods) {
        // POST 用 --warning 標出來：它是「能把東西寫出去」的那一個。
        const tag = el('span',
          method === 'POST' ? 'status-label egress-method--write' : 'status-label',
          method);
        methods.appendChild(tag);
      }
      line.appendChild(methods);

      const detail = el('div', 'list-identity');
      detail.appendChild(el('code', 'mono', rule.pathPrefix || '（所有路徑）'));
      if (rule.authSecretName) {
        const auth = el('span', 'list-description');
        auth.append('認證 ');
        auth.appendChild(el('code', 'mono', rule.authHeader || ''));
        auth.append(' ← 機密 ');
        // **顯示的是 secret 的名字，不是值**（§5.18 第 2 點）。
        auth.appendChild(el('code', 'mono', rule.authSecretName));
        detail.appendChild(auth);
      } else {
        detail.appendChild(el('span', 'list-description', '不帶認證'));
      }
      line.appendChild(detail);

      const actions = el('div', 'data-row-actions');
      const remove = el('button', 'btn-danger btn-sm', '刪除');
      remove.type = 'button';
      remove.dataset.egressRuleDelete = rule.ruleId;
      remove.disabled = busy;
      actions.appendChild(remove);
      line.appendChild(actions);
      rulesList.appendChild(line);
    }
  };

  /** 機密下拉：選項來自機密卡，**不讓使用者手打名字**（§5.18 第 3 點）。 */
  const renderSecretOptions = () => {
    for (const [select, empty, hint, hintText] of [
      [authSelect, '不帶認證', authHint,
        '選一個機密，平台會在送出時解密並放進右邊那個 header。值不會進到沙盒。'],
      [passwordSecret, '選一個機密', passwordHint,
        '先在上面的機密卡存一個 SMTP_PASSWORD，這裡才選得到。'],
    ]) {
      const previous = select.value;
      select.replaceChildren();
      const blank = el('option', null, empty);
      blank.value = '';
      select.appendChild(blank);
      for (const name of secretNames) {
        const option = el('option', null, name);   // textContent，使用者輸入
        option.value = name;
        select.appendChild(option);
      }
      select.value = secretNames.indexOf(previous) === -1 ? '' : previous;
      select.disabled = secretNames.length === 0;
      if (secretNames.length === 0) {
        hint.textContent = '還沒有任何機密。先在上面的「機密」卡存一個，這裡才選得到。';
      } else {
        hint.textContent = hintText;
      }
    }
  };

  const renderSender = () => {
    senderSummary.replaceChildren();
    if (!sender.configured) {
      senderSummary.append('還沒有寄件者設定。');
      senderSummary.appendChild(el('code', 'mono', 'ctx 的 send_email()'));
      senderSummary.append(' 會被擋下並回 SENDER_NOT_CONFIGURED —— 不是靜默不寄。');
      senderRemove.hidden = true;
      return;
    }
    senderSummary.appendChild(el('span', 'status-dot status-dot--PUBLISHED'));
    senderSummary.append(' 已設定：');
    senderSummary.appendChild(el('code', 'mono',
      sender.username + ' → ' + sender.smtpHost + ':' + sender.smtpPort));
    senderSummary.append('　收件人白名單 ');
    senderSummary.appendChild(el('code', 'mono',
      (sender.allowedRecipients || []).join(', ')));
    if (sender.updatedAt) {
      senderSummary.append('　更新於 ' + formatDateTime(sender.updatedAt));
    }
    senderRemove.hidden = false;
  };

  const fillSender = () => {
    smtpHost.value = sender.smtpHost || '';
    smtpPort.value = String(sender.smtpPort || 587);
    username.value = sender.username || '';
    fromAddress.value = sender.fromAddress || '';
    passwordSecret.value = sender.passwordSecretName || '';
    recipients.value = (sender.allowedRecipients || []).join(', ');
    syncSender();
  };

  const parseRecipients = () => recipients.value
    .split(',').map((item) => item.trim()).filter((item) => item !== '');

  function syncRuleSubmit() {
    ruleSubmit.disabled = busy || hostInput.value.trim() === ''
      || (!getToggle.checked && !postToggle.checked);
    ruleSubmit.textContent = busy ? '處理中…' : '新增規則';
    postWarning.hidden = !postToggle.checked;
  }

  function syncSender() {
    const list = parseRecipients();
    senderSubmit.disabled = busy || smtpHost.value.trim() === ''
      || username.value.trim() === '' || fromAddress.value.trim() === ''
      || passwordSecret.value === '' || list.length === 0;
    senderSubmit.textContent = busy ? '處理中…' : (sender.configured ? '更新寄件者' : '儲存寄件者');
    senderRemove.disabled = busy;
  }

  const loadRules = async () => {
    try {
      rules = await listEgressRules(agentId);
      rulesLoaded = true;
      renderRules();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      rulesLoaded = true;
      rules = [];
      renderRules();
      showIn(rulesError, error, '無法讀取出口規則。重新整理頁面再試一次。');
    }
  };

  const loadSender = async () => {
    try {
      sender = await getEmailSender(agentId);
      senderForm.hidden = false;
      renderSender();
      fillSender();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      senderSummary.textContent = '';
      showIn(senderError, error, '無法讀取寄件者設定。');
    }
  };

  const onRuleSubmit = async (event) => {
    event.preventDefault();
    if (ruleSubmit.disabled) return;
    clearIn(rulesError);
    busy = true;
    syncRuleSubmit();
    const methods = [];
    if (getToggle.checked) methods.push('GET');
    if (postToggle.checked) methods.push('POST');
    try {
      await createEgressRule(agentId, {
        host: hostInput.value.trim(),
        methods,
        pathPrefix: pathInput.value.trim(),
        authSecretName: authSelect.value || null,
        authHeader: authSelect.value ? authHeader.value.trim() : null,
        description: descriptionInput.value.trim(),
      });
      hostInput.value = '';
      pathInput.value = '';
      authSelect.value = '';
      authHeader.value = '';
      descriptionInput.value = '';
      postToggle.checked = false;
      getToggle.checked = true;
      busy = false;
      syncRuleSubmit();
      say('規則已新增，立刻生效。');
      await loadRules();
    } catch (error) {
      busy = false;
      syncRuleSubmit();
      if (!(error instanceof ApiError)) throw error;
      showIn(rulesError, error);
      hostInput.focus();
    }
  };

  const onRulesClick = async (event) => {
    const button = event.target.closest('[data-egress-rule-delete]');
    if (!button || busy) return;
    clearIn(rulesError);
    busy = true;
    renderRules();
    try {
      await deleteEgressRule(agentId, button.dataset.egressRuleDelete);
      busy = false;
      say('規則已刪除。它過去的稽核紀錄還在。');
      await loadRules();
    } catch (error) {
      busy = false;
      renderRules();
      if (!(error instanceof ApiError)) throw error;
      showIn(rulesError, error);
    }
  };

  const onSenderSubmit = async (event) => {
    event.preventDefault();
    if (senderSubmit.disabled) return;
    clearIn(senderError);
    busy = true;
    syncSender();
    try {
      sender = await putEmailSender(agentId, {
        smtpHost: smtpHost.value.trim(),
        smtpPort: smtpPort.value,
        username: username.value.trim(),
        fromAddress: fromAddress.value.trim(),
        passwordSecretName: passwordSecret.value,
        allowedRecipients: parseRecipients(),
      });
      busy = false;
      renderSender();
      syncSender();
      say('寄件者已儲存。');
    } catch (error) {
      busy = false;
      syncSender();
      if (!(error instanceof ApiError)) throw error;
      showIn(senderError, error, '密碼不存在這張表裡 —— 它只能以機密的名字引用。');
    }
  };

  const onSenderRemove = async () => {
    if (busy) return;
    clearIn(senderError);
    busy = true;
    syncSender();
    try {
      await deleteEmailSender(agentId);
      sender = { configured: false };
      busy = false;
      renderSender();
      fillSender();
      say('寄件者已移除。之後寄信會被擋下並說明原因，不是靜默不寄。');
    } catch (error) {
      busy = false;
      syncSender();
      if (!(error instanceof ApiError)) throw error;
      showIn(senderError, error);
    }
  };

  const onRuleInput = () => syncRuleSubmit();
  const onSenderInput = () => syncSender();

  ruleForm.addEventListener('submit', onRuleSubmit);
  ruleForm.addEventListener('input', onRuleInput);
  ruleForm.addEventListener('change', onRuleInput);
  rulesList.addEventListener('click', onRulesClick);
  senderForm.addEventListener('submit', onSenderSubmit);
  senderForm.addEventListener('input', onSenderInput);
  senderForm.addEventListener('change', onSenderInput);
  senderRemove.addEventListener('click', onSenderRemove);

  renderRules();
  renderSecretOptions();
  syncRuleSubmit();
  if (agentId) {
    loadRules();
    loadSender();
  }

  return {
    /** 機密卡載完之後把名字推進來，下拉才有東西可選。 */
    setSecretNames(names) {
      secretNames = Array.isArray(names) ? names.slice() : [];
      renderSecretOptions();
      if (sender.configured) passwordSecret.value = sender.passwordSecretName || '';
      syncSender();
      syncRuleSubmit();
    },
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        loadRules();
        loadSender();
      }
    },
    destroy() {
      ruleForm.removeEventListener('submit', onRuleSubmit);
      ruleForm.removeEventListener('input', onRuleInput);
      ruleForm.removeEventListener('change', onRuleInput);
      rulesList.removeEventListener('click', onRulesClick);
      senderForm.removeEventListener('submit', onSenderSubmit);
      senderForm.removeEventListener('input', onSenderInput);
      senderForm.removeEventListener('change', onSenderInput);
      senderRemove.removeEventListener('click', onSenderRemove);
      root.replaceChildren();
    },
  };
}
