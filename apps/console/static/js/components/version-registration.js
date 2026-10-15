import {
  ApiError, ERROR_CODE, VERSION_STATUS, getAgentVersion, registerAgentVersion,
} from '../api.js';
import {
  createCopyButton, createErrorBox, createTimers, el, formatBytes, formatDuration, toPrettyJson,
} from '../dom.js';

const FRAME = `
  <form class="registration-form" data-form>
    <div class="field">
      <label for="version-semver">Semver</label>
      <input type="text" id="version-semver" required autocomplete="off"
             placeholder="1.2.0" aria-describedby="semver-hint semver-error">
      <p class="field-hint" id="semver-hint">用已存在的版號會取代它：新 image 驗證通過才換，失敗的話舊的照常服務。</p>
      <p class="field-error" id="semver-error" data-semver-error hidden></p>
    </div>
    <div class="field">
      <label for="version-image-ref">Canonical image reference</label>
      <textarea id="version-image-ref" class="mono" rows="4" required autocomplete="off"
                placeholder="ghcr.io/acme/agent@sha256:&lt;64 hex&gt;"
                aria-describedby="image-ref-hint image-ref-error"></textarea>
      <p class="field-hint" id="image-ref-hint">請先 push 到 VM 可存取的 Registry；Console 不接收 Registry 密碼。</p>
      <p class="field-error" id="image-ref-error" data-image-error hidden></p>
    </div>
    <div data-submit-error hidden></div>
    <div class="form-actions">
      <button type="submit" class="btn-primary btn-lg" data-submit disabled>註冊並驗證</button>
    </div>
  </form>
  <p class="visually-hidden" data-live aria-live="polite"></p>
  <section class="registration-status" data-status hidden></section>
  <dialog aria-labelledby="registration-confirm-title" data-confirm-dialog>
    <div class="dialog-form">
      <h2 class="dialog-title" id="registration-confirm-title">確認註冊版本</h2>
      <p class="field-hint">如果這個版號已經存在，驗證通過後會取代現有的 image，舊 image 會被刪除。</p>
      <div class="meta-grid" data-confirm-values></div>
      <div class="dialog-actions">
        <button type="button" class="btn-ghost" data-confirm-cancel>返回修改</button>
        <button type="button" class="btn-secondary" data-confirm>確認註冊</button>
      </div>
    </div>
  </dialog>
`;

const CANONICAL_IMAGE = /^\S+@sha256:[0-9a-f]{64}$/;

export function mount(root, props) {
  root.innerHTML = FRAME;
  const timers = createTimers();
  const form = root.querySelector('[data-form]');
  const semver = root.querySelector('#version-semver');
  const imageRef = root.querySelector('#version-image-ref');
  const semverError = root.querySelector('[data-semver-error]');
  const imageError = root.querySelector('[data-image-error]');
  const submitError = root.querySelector('[data-submit-error]');
  const submit = root.querySelector('[data-submit]');
  const liveSlot = root.querySelector('[data-live]');
  const statusSlot = root.querySelector('[data-status]');
  const confirmDialog = root.querySelector('[data-confirm-dialog]');
  const confirmValues = root.querySelector('[data-confirm-values]');
  const confirm = root.querySelector('[data-confirm]');
  const confirmCancel = root.querySelector('[data-confirm-cancel]');
  let busy = false;
  let destroyed = false;
  let pollTimer = null;
  let elapsedTimer = null;
  let startedAt = null;
  let current = null;
  let dialogOpener = null;
  let announcedStatus = null;

  const clearErrors = () => {
    semverError.hidden = true;
    imageError.hidden = true;
    semver.removeAttribute('aria-invalid');
    imageRef.removeAttribute('aria-invalid');
    submitError.hidden = true;
    submitError.replaceChildren();
  };

  const validInput = () => semver.value.trim() !== '' && imageRef.value.trim() !== '';
  const syncSubmit = () => {
    submit.disabled = busy || !validInput();
    submit.textContent = busy ? '已送出驗證' : '註冊並驗證';
  };

  const setBusy = (next) => {
    busy = next;
    semver.disabled = next;
    imageRef.disabled = next;
    syncSubmit();
  };

  const stopPolling = () => {
    if (pollTimer !== null) timers.cancel(pollTimer);
    if (elapsedTimer !== null) timers.cancel(elapsedTimer);
    pollTimer = null;
    elapsedTimer = null;
  };

  const metaItem = (label, value, copyable) => {
    const item = el('div', 'meta-item');
    item.appendChild(el('span', 'meta-label', label));
    const row = el('div', 'inline-row');
    row.appendChild(el('span', 'meta-value', value || '—'));
    if (copyable && value) row.appendChild(createCopyButton(() => value, timers, 'Copy'));
    item.appendChild(row);
    return item;
  };

  const failureHint = (error) => {
    const stage = error && error.details ? error.details.stage : null;
    if (stage === 'PULL') return '平台無法按此 digest 拉取 image。請確認 Registry 可達與 VM credential。';
    if (stage === 'INSPECT') return 'image platform、label 或 runtime executable 不符合契約。請檢查 image metadata。';
    if (stage === 'VALIDATE') return 'image 無法在 non-root、唯讀環境通過 runtime 驗證。請查看完整錯誤。';
    return '請查看完整錯誤，修正 image 後可以用同一個版號重新註冊。';
  };

  const renderStatus = () => {
    statusSlot.hidden = false;
    statusSlot.replaceChildren();
    if (!current || current.status === VERSION_STATUS.BUILDING) {
      if (announcedStatus !== VERSION_STATUS.BUILDING) {
        liveSlot.textContent = '版本驗證已開始。平台正在拉取、檢查並驗證 image。';
        announcedStatus = VERSION_STATUS.BUILDING;
      }
      const status = el('div', 'status status--BUILDING');
      const dot = el('span', 'status-dot status-dot--BUILDING');
      dot.setAttribute('aria-hidden', 'true');
      status.appendChild(dot);
      status.appendChild(el('span', 'status-label', '驗證中'));
      if (startedAt) status.appendChild(el('span', 'status-detail', '· 已耗時 ' + formatDuration(Date.now() - startedAt)));
      statusSlot.appendChild(status);
      statusSlot.appendChild(el('p', 'empty-text', startedAt && Date.now() - startedAt >= 3000
        ? '平台正在拉取、檢查並驗證 image。流程：pull exact digest → inspect runtime → isolated validate。'
        : '平台正在建立 image 驗證工作。'));
      if (current) {
        const buildingMeta = el('div', 'meta-grid');
        buildingMeta.appendChild(metaItem('Version ID', current.agentVersionId, true));
        buildingMeta.appendChild(metaItem('Image reference', current.artifact.imageRef, true));
        statusSlot.appendChild(buildingMeta);
      }
      return;
    }

    const status = el('div', 'status status--' + current.status);
    if (announcedStatus !== current.status) {
      liveSlot.textContent = current.status === VERSION_STATUS.PUBLISHED
        ? '版本已發布，可以執行 Agent。' : '版本驗證失敗，請查看錯誤內容。';
      announcedStatus = current.status;
    }
    const dot = el('span', 'status-dot status-dot--' + current.status);
    dot.setAttribute('aria-hidden', 'true');
    status.appendChild(dot);
    status.appendChild(el('span', 'status-label', current.status === VERSION_STATUS.PUBLISHED ? '已發布' : '驗證失敗'));
    statusSlot.appendChild(status);

    if (current.status === VERSION_STATUS.PUBLISHED) {
      const grid = el('div', 'meta-grid');
      grid.appendChild(metaItem('Semver', current.semver));
      grid.appendChild(metaItem('Image reference', current.artifact.imageRef, true));
      grid.appendChild(metaItem('Digest', current.artifact.digest, true));
      grid.appendChild(metaItem('Platform', current.artifact.platform));
      grid.appendChild(metaItem('Size', formatBytes(current.artifact.sizeBytes)));
      grid.appendChild(metaItem('Runtime contract', current.artifact.runtimeContract));
      statusSlot.appendChild(grid);
      const entries = el('details');
      entries.appendChild(el('summary', 'text-secondary text-xs', 'Entrypoints'));
      entries.appendChild(el('pre', 'code', toPrettyJson(current.entrypoints || [])));
      statusSlot.appendChild(entries);
      form.hidden = true;
      const link = el('a', 'btn-primary btn-lg', '前往 Agent 詳情');
      link.href = '/console/agents/' + encodeURIComponent(props.agentId);
      statusSlot.appendChild(link);
      return;
    }

    const failure = current.error || {
      code: ERROR_CODE.BUILD_FAILED,
      message: '版本驗證失敗，但 API 沒有提供錯誤內容。',
      details: {},
    };
    form.hidden = true;
    const failedMeta = el('div', 'meta-grid');
    failedMeta.appendChild(metaItem('Version ID', current.agentVersionId, true));
    failedMeta.appendChild(metaItem('Image reference', current.artifact.imageRef, true));
    statusSlot.appendChild(failedMeta);
    statusSlot.appendChild(createErrorBox(failure, { hint: failureHint(failure) }));
    statusSlot.appendChild(createCopyButton(
      () => failure.code + '\n' + failure.message + '\n' + toPrettyJson(failure.details),
      timers, '複製完整錯誤',
    ));
    const actions = el('div', 'form-actions');
    const back = el('a', 'btn-ghost', '返回 Agent 詳情');
    back.href = '/console/agents/' + encodeURIComponent(props.agentId);
    const retry = el('a', 'btn-secondary', '修正後重新註冊');
    retry.href = '/console/agents/' + encodeURIComponent(props.agentId) + '/versions/new';
    actions.appendChild(back);
    actions.appendChild(retry);
    statusSlot.appendChild(actions);
  };

  const poll = async () => {
    if (destroyed || !current || current.status !== VERSION_STATUS.BUILDING) return;
    try {
      current = await getAgentVersion(props.agentId, current.semver);
      if (destroyed) return;
      submitError.hidden = true;
      submitError.replaceChildren();
      renderStatus();
      if (current.status !== VERSION_STATUS.BUILDING) stopPolling();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      submitError.replaceChildren(createErrorBox(error, {
        hint: '驗證仍可能在後端進行。可回 Agent 詳情查看版本狀態。',
      }));
      submitError.hidden = false;
    }
  };

  const startPolling = () => {
    startedAt = Date.now();
    renderStatus();
    elapsedTimer = timers.every(1000, renderStatus);
    pollTimer = timers.every(2000, poll);
  };

  const validate = () => {
    clearErrors();
    let valid = true;
    if (semver.value.trim() === '') {
      semverError.textContent = '請輸入 semver。';
      semverError.hidden = false;
      semver.setAttribute('aria-invalid', 'true');
      valid = false;
    }
    if (!CANONICAL_IMAGE.test(imageRef.value.trim())) {
      imageError.textContent = '請使用 repository@sha256:<64 個小寫 hex>；tag-only reference 不接受。';
      imageError.hidden = false;
      imageRef.setAttribute('aria-invalid', 'true');
      valid = false;
    }
    if (!valid) (semver.getAttribute('aria-invalid') ? semver : imageRef).focus();
    return valid;
  };

  const showConfirm = (event) => {
    event.preventDefault();
    if (!validate()) return;
    confirmValues.replaceChildren(
      metaItem('Agent', props.agentName || props.agentId),
      metaItem('Semver', semver.value.trim()),
      metaItem('Image reference', imageRef.value.trim()),
    );
    dialogOpener = submit;
    confirmDialog.showModal();
    confirm.focus();
  };

  const onConfirm = async () => {
    confirm.disabled = true;
    confirmCancel.disabled = true;
    try {
      current = await registerAgentVersion(props.agentId, {
        semver: semver.value.trim(), imageRef: imageRef.value.trim(),
      });
      confirmDialog.close();
      setBusy(true);
      statusSlot.hidden = false;
      startPolling();
      if (current.status !== VERSION_STATUS.BUILDING) {
        stopPolling();
        renderStatus();
      }
    } catch (error) {
      confirmDialog.close();
      if (!(error instanceof ApiError)) throw error;
      const hint = error.code === ERROR_CODE.VERSION_CONFLICT
        ? '這個版號正在驗證中，請等它完成後再送。'
        : '請修正欄位後再試一次。';
      submitError.replaceChildren(createErrorBox(error, { hint }));
      submitError.hidden = false;
      const target = error.code === ERROR_CODE.VERSION_CONFLICT || error.field === 'semver'
        ? semver : imageRef;
      target.setAttribute('aria-invalid', 'true');
      target.focus();
    } finally {
      confirm.disabled = false;
      confirmCancel.disabled = false;
    }
  };

  const onInput = () => {
    clearErrors();
    syncSubmit();
  };
  const onCancel = () => confirmDialog.close();
  const onDialogClose = () => {
    if (!busy && dialogOpener && dialogOpener.isConnected) dialogOpener.focus();
    dialogOpener = null;
  };
  semver.addEventListener('input', onInput);
  imageRef.addEventListener('input', onInput);
  form.addEventListener('submit', showConfirm);
  confirm.addEventListener('click', onConfirm);
  confirmCancel.addEventListener('click', onCancel);
  confirmDialog.addEventListener('close', onDialogClose);
  syncSubmit();

  return {
    update(next = {}) { Object.assign(props, next); },
    destroy() {
      destroyed = true;
      stopPolling();
      timers.clearAll();
      semver.removeEventListener('input', onInput);
      imageRef.removeEventListener('input', onInput);
      form.removeEventListener('submit', showConfirm);
      confirm.removeEventListener('click', onConfirm);
      confirmCancel.removeEventListener('click', onCancel);
      confirmDialog.removeEventListener('close', onDialogClose);
      if (confirmDialog.open) confirmDialog.close();
      root.replaceChildren();
    },
  };
}
