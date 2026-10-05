// egress-calls-panel.js —— 出口稽核（ui-contract §5.19 / ZEN-019）。
//
// 形狀刻意做成跟用量面板（§5.14）一樣：篩選列 + 逐筆表 + 「載入更多」。
//
// 1. **HTTP 與寄信在同一張表**，用 `kind` 分。兩個分頁籤會讓「這次 Run 往外送了
//    什麼」要看兩個地方。
// 2. **被擋下的呼叫要顯示，而且要顯眼** —— 出口設定錯了，只有這裡看得出來。
//    `reason` 直接譯成人話並附「去加一條規則」的連結。
// 3. `path` **不含 query string**（契約不入庫），所以畫面上不要把它排成完整網址。
// 4. 預設最近 50 筆，**不自動輪詢**。

import { ApiError, listEgressCalls } from '../api.js';
import { createErrorBox, el, formatBytes, formatDateTime } from '../dom.js';

/** api-contract §4.3 的 reason → 人話。**不要在這裡自創沒有的 reason。** */
const REASON_TEXT = {
  NO_RULES: '這個 Agent 一條白名單規則都沒有',
  HOST_NOT_ALLOWED: '主機不在白名單',
  METHOD_NOT_ALLOWED: '那條規則沒開這個 method',
  PATH_NOT_ALLOWED: '不符合那條規則的路徑前綴',
  SCHEME_NOT_ALLOWED: '不是 https',
  PRIVATE_ADDRESS: '解析到內網／loopback／link-local 位址',
  REDIRECT: '上游回 3xx，平台不跟隨 redirect',
  REQUEST_TOO_LARGE: '送出的內容超過上限',
  RESPONSE_TOO_LARGE: '回應超過上限（不截斷，直接當錯誤）',
  RUN_CALL_LIMIT: '這筆 Run 的出口次數用完了',
  SENDER_NOT_CONFIGURED: '這個 Agent 沒有寄件者設定',
  RECIPIENT_NOT_ALLOWED: '收件人不在白名單，整封信都沒寄',
  BODY_TOO_LARGE: '信件內容超過上限',
  RUN_EMAIL_LIMIT: '這筆 Run 的寄信次數用完了',
  HOURLY_EMAIL_LIMIT: '這個 Agent 這一小時的寄信次數用完了',
  SMTP_AUTH: 'SMTP 認證失敗（Gmail 要用應用程式密碼）',
  TLS_REQUIRED: '連上了但升不上 TLS，平台不用明文送密碼',
};

const FRAME = `
  <div class="usage-panel" data-egress-calls-root id="agent-egress-calls">
    <p class="field-hint">
      這個 Agent 實際往外送了什麼。<strong>被擋下的也在裡面</strong> ——
      「為什麼我的 agent 連不出去」只有這裡看得出來。
      路徑欄<strong>不含 query string</strong>（那裡常常是 token，稽核表不存）。
    </p>

    <form class="usage-filters" data-egress-calls-filters>
      <div class="field field--inline">
        <label for="egress-calls-kind">類型</label>
        <select id="egress-calls-kind" name="kind">
          <option value="">全部</option>
          <option value="HTTP">HTTP</option>
          <option value="EMAIL">EMAIL</option>
        </select>
      </div>
      <div class="field field--inline">
        <label for="egress-calls-run">Run ID</label>
        <input type="text" id="egress-calls-run" name="run_id" autocomplete="off"
               spellcheck="false" placeholder="全部">
      </div>
      <div class="field field--inline">
        <label for="egress-calls-host">主機</label>
        <input type="text" id="egress-calls-host" name="host" autocomplete="off"
               spellcheck="false" placeholder="全部">
      </div>
      <div class="field field--inline">
        <label for="egress-calls-errors">
          <input type="checkbox" id="egress-calls-errors" name="errors_only">
          只看出錯的
        </label>
      </div>
      <button type="submit" class="btn-secondary btn-sm" data-egress-calls-apply>套用</button>
    </form>

    <div data-egress-calls-error hidden></div>
    <div class="data-list" data-egress-calls-list></div>
    <div class="list-footer">
      <button type="button" class="btn-ghost" data-egress-calls-more hidden>載入更多</button>
    </div>
  </div>
`;

/** `error` 欄位是 `EGRESS_BLOCKED: <reason>` 這種形狀，拆出 code 與 reason。 */
function splitError(text) {
  if (!text) return null;
  const index = String(text).indexOf(':');
  if (index === -1) return { code: String(text), reason: '' };
  return {
    code: String(text).slice(0, index).trim(),
    reason: String(text).slice(index + 1).trim(),
  };
}

export function mount(root, props = {}) {
  root.innerHTML = FRAME;

  const filters = root.querySelector('[data-egress-calls-filters]');
  const kind = root.querySelector('#egress-calls-kind');
  const runId = root.querySelector('#egress-calls-run');
  const host = root.querySelector('#egress-calls-host');
  const errorsOnly = root.querySelector('#egress-calls-errors');
  const list = root.querySelector('[data-egress-calls-list]');
  const errorSlot = root.querySelector('[data-egress-calls-error]');
  const more = root.querySelector('[data-egress-calls-more]');

  let agentId = props.agentId || '';
  let rows = [];
  let cursor = null;
  let loading = false;
  let loaded = false;

  const query = () => ({
    kind: kind.value,
    runId: runId.value.trim(),
    host: host.value.trim(),
    errorsOnly: errorsOnly.checked,
    limit: 50,
  });

  const clearError = () => { errorSlot.hidden = true; errorSlot.replaceChildren(); };

  const renderRow = (row) => {
    const line = el('div', 'data-row data-row--calls');
    const parsedError = splitError(row.error);
    if (parsedError) line.classList.add('egress-call--blocked');

    const when = el('div', 'list-identity');
    when.appendChild(el('span', 'text-secondary', formatDateTime(row.createdAt)));
    const runLink = el('a', 'stash-link mono', row.runId);
    runLink.href = '/console/runs/' + encodeURIComponent(row.runId);
    when.appendChild(runLink);
    line.appendChild(when);

    line.appendChild(el('span', 'status-label', row.kind));

    const target = el('div', 'list-identity');
    target.appendChild(el('code', 'mono',
      (row.method ? row.method + ' ' : '') + row.host));
    // `path` 不是完整網址（沒有 scheme、沒有 query），所以分開一行顯示。
    target.appendChild(el('span', 'list-description mono', row.path));
    line.appendChild(target);

    const outcome = el('div', 'list-identity');
    if (parsedError) {
      const code = el('span', 'error-code', parsedError.code);
      outcome.appendChild(code);
      const human = REASON_TEXT[parsedError.reason];
      outcome.appendChild(el('span', 'error-message',
        human ? human + '（' + parsedError.reason + '）' : parsedError.reason));
      if (human) {
        const fix = el('a', 'btn-ghost btn-sm', '去加一條規則');
        fix.href = '#agent-egress';
        outcome.appendChild(fix);
      }
    } else {
      outcome.appendChild(el('span', 'status-label',
        row.statusCode === null || row.statusCode === undefined
          ? '—' : String(row.statusCode)));
      outcome.appendChild(el('span', 'list-description',
        '↑ ' + formatBytes(row.requestBytes) + '　↓ ' + formatBytes(row.responseBytes)
        + (row.latencyMs === null || row.latencyMs === undefined
          ? '' : '　' + row.latencyMs + 'ms')));
    }
    line.appendChild(outcome);
    return line;
  };

  const render = () => {
    list.replaceChildren();
    if (!loaded) {
      const skeleton = el('div', 'data-row');
      skeleton.appendChild(el('span', 'skeleton skeleton-line skeleton-line--wide'));
      list.appendChild(skeleton);
      return;
    }
    if (rows.length === 0) {
      list.appendChild(el('p', 'empty-text',
        '這個 Agent 還沒有任何出口紀錄。跑一筆會用到 ctx.http 或 ctx.send_email() 的 Run 之後，'
        + '每一次嘗試（含被擋下的）都會出現在這裡。'));
      return;
    }
    const header = el('div', 'data-row data-row--header data-row--calls');
    header.appendChild(el('span', null, '時間 / Run'));
    header.appendChild(el('span', null, '類型'));
    header.appendChild(el('span', null, '目的地'));
    header.appendChild(el('span', null, '結果'));
    list.appendChild(header);
    for (const row of rows) list.appendChild(renderRow(row));
  };

  const load = async (append) => {
    if (loading) return;
    loading = true;
    more.disabled = true;
    try {
      const page = await listEgressCalls(agentId,
        append ? { ...query(), cursor } : query());
      rows = append ? rows.concat(page.items) : page.items;
      cursor = page.nextCursor;
      loaded = true;
      clearError();
      render();
      more.hidden = !cursor;
    } catch (error) {
      loaded = true;
      render();
      if (!(error instanceof ApiError)) throw error;
      errorSlot.replaceChildren(createErrorBox(error, {
        hint: '無法讀取出口稽核。檢查篩選條件（Run ID 要是完整的 UUID）後再試一次。',
      }));
      errorSlot.hidden = false;
    } finally {
      loading = false;
      more.disabled = false;
    }
  };

  const onApply = (event) => {
    event.preventDefault();
    cursor = null;
    loaded = false;
    render();
    load(false);
  };
  const onMore = () => load(true);

  filters.addEventListener('submit', onApply);
  more.addEventListener('click', onMore);

  render();
  if (agentId) load(false);

  return {
    refresh() { cursor = null; return load(false); },
    update(next) {
      if (next && next.agentId && next.agentId !== agentId) {
        agentId = next.agentId;
        cursor = null;
        load(false);
      }
    },
    destroy() {
      // **不自動輪詢**，所以這裡沒有 interval 要清；只要拆掉監聽就好。
      filters.removeEventListener('submit', onApply);
      more.removeEventListener('click', onMore);
      root.replaceChildren();
    },
  };
}
