// pages/runs.js —— /console/runs。
//
// ############################################################################
// # ZEN-006A 臨時鷹架，ZEN-004 後刪除                                        #
// #                                                                          #
// # Run 列表 API 尚未實作，所以這個畫面**永遠是 ui-contract §4 的 Empty 狀態**。#
// # 為了讓人能在瀏覽器上真的操作骨架，這裡多做了兩件臨時的事：                 #
// #   1. 一個 <dialog> 表單，直接對內建 agent 發起測試 Run                     #
// #   2. 「最近（本機暫存）」—— 把發起過的 run_id 存在 localStorage（上限 20）  #
// #                                                                          #
// # ZEN-004 的 Run 列表端點上線後，第 2 項（STASH_* 與 renderStash 整段）      #
// # 連同 DOM 一併刪除，改成真正的列表 + 篩選 + cursor 分頁。                   #
// # localStorage 只放 run_id / question 摘要 / 時間，**永遠不放任何憑證**（§6.4）。#
// ############################################################################

import { ApiError, createBuiltinRun } from '../api.js';
import { el, formatDateTime, truncate } from '../dom.js';
import { mount as mountShell } from '../shell.js';

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1>Runs</h1>
        <p class="page-subtitle">Run 列表 API 尚未上線（ZEN-004），所以這頁還沒有列表可以顯示。</p>
      </div>
    </div>

    <section class="card">
      <div class="empty">
        <p class="empty-text">這裡之後會出現 Run 列表：狀態、Agent、建立時間，可篩選與分頁。</p>
        <button type="button" class="btn-secondary" data-new-run>發起測試 Run</button>
      </div>
    </section>

    <section class="card">
      <div class="card-header">
        <h2 class="card-title">最近（本機暫存）</h2>
      </div>
      <p class="page-subtitle">
        臨時鷹架：只存在這台瀏覽器，換一台機器就看不到，也不會同步到伺服器。
        Run 列表 API 上線後這個區塊會移除。
      </p>
      <div data-stash></div>
    </section>
  </div>

  <dialog data-dialog aria-labelledby="new-run-title">
    <form class="dialog-form" data-form>
      <h2 class="dialog-title" id="new-run-title">發起測試 Run</h2>
      <div class="field">
        <label for="question">question</label>
        <input type="text" id="question" name="question" required autocomplete="off"
               placeholder="例如：hello zentra">
        <p class="field-hint">
          ZEN-000 的內建 agent 只接受 entrypoint「default」與字串型別的 question，其他一律被拒絕。
        </p>
      </div>
      <div data-dialog-error hidden></div>
      <div class="dialog-actions">
        <button type="button" class="btn-ghost" data-cancel>取消</button>
        <button type="submit" class="btn-primary" data-submit>建立 Run</button>
      </div>
    </form>
  </dialog>
`;

// ---- ZEN-006A 臨時鷹架：localStorage 暫存（ZEN-004 後整段刪除）開始 ----

const STASH_KEY = 'zentra.console.recentRuns';
const STASH_MAX = 20;
const QUESTION_MAX = 120;

function readStash() {
  try {
    const parsed = JSON.parse(localStorage.getItem(STASH_KEY) || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((item) => item && typeof item.runId === 'string' && item.runId !== '')
      .slice(0, STASH_MAX);
  } catch (_) {
    return [];   // 壞掉的 JSON 或隱私模式：當作沒有紀錄，不要讓頁面掛掉
  }
}

function addToStash(runId, question) {
  const next = readStash().filter((item) => item.runId !== runId);
  next.unshift({
    runId,
    question: truncate(question, QUESTION_MAX),
    createdAt: new Date().toISOString(),
  });
  try {
    localStorage.setItem(STASH_KEY, JSON.stringify(next.slice(0, STASH_MAX)));
  } catch (_) {
    // 存不進去不影響導頁，使用者仍然會看到 Run 觀測頁。
  }
}

// ---- 臨時鷹架結束 ----

function main() {
  const app = document.getElementById('app');
  const shell = mountShell(document.body, { breadcrumb: [{ text: 'Runs' }] });

  app.innerHTML = PAGE;   // 固定結構，一次寫入
  const stashSlot = app.querySelector('[data-stash]');
  const dialog = app.querySelector('[data-dialog]');
  const form = app.querySelector('[data-form]');
  const input = app.querySelector('#question');
  const submit = app.querySelector('[data-submit]');
  const cancel = app.querySelector('[data-cancel]');
  const openButton = app.querySelector('[data-new-run]');
  const dialogError = app.querySelector('[data-dialog-error]');

  const renderStash = () => {
    stashSlot.replaceChildren();
    const items = readStash();
    if (items.length === 0) {
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '這台瀏覽器還沒有從 Console 發起過 Run。'));
      stashSlot.appendChild(empty);
      return;
    }
    const list = el('ul', 'stash-list');
    for (const item of items) {
      const row = el('li', 'stash-item');
      const link = el('a', 'stash-link');
      link.href = '/console/runs/' + encodeURIComponent(item.runId);
      const id = el('span', 'stash-id', truncate(item.runId, 12));   // textContent
      id.title = item.runId;
      link.appendChild(id);
      link.appendChild(el('span', 'stash-question', item.question || '(沒有記錄 question)'));
      link.appendChild(el('span', 'stash-time', formatDateTime(new Date(item.createdAt))));
      row.appendChild(link);
      list.appendChild(row);
    }
    stashSlot.appendChild(list);
  };

  const clearDialogError = () => {
    dialogError.hidden = true;
    dialogError.replaceChildren();
  };

  /** §4 Error：code（mono）+ 訊息 + 該怎麼辦。建立 Run 還沒有 run_id，所以不顯示。 */
  const showDialogError = (error) => {
    const box = el('div', 'error-box');
    const head = el('div', 'inline-row');
    head.appendChild(el('span', 'error-code', error.code));
    if (error.retryable) head.appendChild(el('span', 'error-hint', '可以直接再送一次'));
    head.appendChild(el('span', 'error-hint', '建立 Run 失敗'));
    box.appendChild(head);
    box.appendChild(el('p', 'error-message', error.message));
    box.appendChild(el('p', 'error-hint', '確認 question 是一段文字後再送一次；若持續失敗，請把上面的 code 回報。'));
    if (error.details && Object.keys(error.details).length > 0) {
      const details = el('details');
      details.appendChild(el('summary', 'error-hint', '錯誤細節'));
      details.appendChild(el('pre', 'code', JSON.stringify(error.details, null, 2)));
      box.appendChild(details);
    }
    dialogError.replaceChildren(box);
    dialogError.hidden = false;
  };

  const setBusy = (busy) => {
    submit.disabled = busy;
    input.disabled = busy;
    submit.textContent = busy ? '建立中…' : '建立 Run';
  };

  const onOpen = () => {
    clearDialogError();
    setBusy(false);
    dialog.showModal();     // 原生 <dialog>：ESC 關閉、焦點鎖定都不用自己寫
    input.focus();
  };

  const onCancel = () => dialog.close();

  const onSubmit = async (domEvent) => {
    domEvent.preventDefault();
    const question = input.value.trim();
    if (question === '') {
      input.reportValidity();
      return;
    }
    clearDialogError();
    setBusy(true);
    try {
      const created = await createBuiltinRun(question);
      addToStash(created.runId, question);
      // 建立 Run 是非同步的，導到觀測頁看真實進度（不做假的樂觀更新）。
      location.href = '/console/runs/' + encodeURIComponent(created.runId);
    } catch (error) {
      setBusy(false);
      if (error instanceof ApiError) {
        showDialogError(error);
        return;
      }
      throw error;          // 不吞錯誤
    }
  };

  openButton.addEventListener('click', onOpen);
  cancel.addEventListener('click', onCancel);
  form.addEventListener('submit', onSubmit);
  dialog.addEventListener('close', clearDialogError);

  renderStash();

  const destroy = () => {
    openButton.removeEventListener('click', onOpen);
    cancel.removeEventListener('click', onCancel);
    form.removeEventListener('submit', onSubmit);
    dialog.removeEventListener('close', clearDialogError);
    shell.destroy();
  };

  window.addEventListener('beforeunload', destroy);
  window.addEventListener('pagehide', destroy);

  return { destroy };
}

main();
