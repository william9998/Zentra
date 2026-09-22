import { ApiError, ERROR_CODE, VERSION_STATUS, createRun, getAgentVersion } from '../api.js';
import { createErrorBox, el, toPrettyJson } from '../dom.js';

const FRAME = `
  <div data-loading>
    <div class="skeleton skeleton-line skeleton-line--wide"></div>
    <div class="skeleton skeleton-line skeleton-line--half"></div>
  </div>
  <div class="empty" data-empty hidden>
    <p class="empty-text">沒有可執行的版本。先完成 image 驗證。</p>
    <a class="btn-secondary" data-register>註冊版本</a>
  </div>
  <form class="composer-form" data-form hidden>
    <div class="form-grid">
      <div class="field">
        <label for="run-version">版本</label>
        <select id="run-version" data-version></select>
      </div>
      <div class="field">
        <label for="run-entrypoint">Entrypoint</label>
        <select id="run-entrypoint" data-entrypoint></select>
      </div>
    </div>
    <div data-entrypoint-description class="field-hint"></div>
    <details data-schema>
      <summary class="text-secondary text-xs">查看 input schema</summary>
      <pre class="code" data-schema-code></pre>
    </details>
    <div class="field">
      <label for="run-input">Input JSON object</label>
      <textarea id="run-input" class="mono" rows="10" data-input
                aria-describedby="run-input-hint run-input-error">{}</textarea>
      <p class="field-hint" id="run-input-hint">送出前會先確認是有效的 JSON object；後端仍做權威 schema 驗證。</p>
      <p class="field-error" id="run-input-error" data-input-error hidden></p>
    </div>
    <div data-error hidden></div>
    <div class="form-actions">
      <button type="submit" class="btn-primary btn-lg" data-submit>執行 Agent</button>
    </div>
  </form>
`;

export function mount(root, props) {
  root.innerHTML = FRAME;
  const loadingSlot = root.querySelector('[data-loading]');
  const emptySlot = root.querySelector('[data-empty]');
  const form = root.querySelector('[data-form]');
  const register = root.querySelector('[data-register]');
  const versionSelect = root.querySelector('[data-version]');
  const entrypointSelect = root.querySelector('[data-entrypoint]');
  const description = root.querySelector('[data-entrypoint-description]');
  const schemaCode = root.querySelector('[data-schema-code]');
  const input = root.querySelector('[data-input]');
  const inputError = root.querySelector('[data-input-error]');
  const errorSlot = root.querySelector('[data-error]');
  const submit = root.querySelector('[data-submit]');
  register.href = '/console/agents/' + encodeURIComponent(props.agentId) + '/versions/new';
  let versions = Array.isArray(props.versions) ? props.versions : [];
  let versionsReady = props.versionsReady === true;
  let detail = null;
  let busy = false;
  let requestSerial = 0;
  let destroyed = false;

  const published = () => versions.filter((version) => version.status === VERSION_STATUS.PUBLISHED);

  const clearError = () => {
    errorSlot.hidden = true;
    errorSlot.replaceChildren();
    inputError.hidden = true;
    inputError.textContent = '';
    input.removeAttribute('aria-invalid');
  };

  const setBusy = (next) => {
    busy = next;
    versionSelect.disabled = next || !detail;
    entrypointSelect.disabled = next || !detail;
    input.disabled = next || !detail;
    submit.disabled = next || !detail;
    submit.textContent = next ? '建立 Run 中…' : '執行 Agent';
  };

  const renderEntrypoints = () => {
    entrypointSelect.replaceChildren();
    const entrypoints = detail && Array.isArray(detail.entrypoints) ? detail.entrypoints : [];
    for (const entrypoint of entrypoints) {
      const option = el('option', null,
        entrypoint.name + (entrypoint.description ? ' — ' + entrypoint.description : ''));
      option.value = entrypoint.name;
      entrypointSelect.appendChild(option);
    }
    if (entrypoints.length === 0) {
      description.textContent = '';
      schemaCode.textContent = '';
      errorSlot.replaceChildren(createErrorBox({
        code: ERROR_CODE.INTERNAL_ERROR,
        message: '已發布版本沒有 entrypoint，無法建立 Run。',
        details: {},
      }, { hint: '請回報這個版本，並確認 image validation 回傳 entrypoints。' }));
      errorSlot.hidden = false;
      setBusy(false);
      submit.disabled = true;
      return;
    }
    renderEntrypointDetail();
    setBusy(false);
  };

  const renderEntrypointDetail = () => {
    const entrypoints = detail && detail.entrypoints ? detail.entrypoints : [];
    const selected = entrypoints.find((item) => item.name === entrypointSelect.value) || entrypoints[0];
    description.textContent = selected ? selected.description : '';
    schemaCode.textContent = selected ? toPrettyJson(selected.inputSchema) : '';
  };

  const loadDetail = async (semver) => {
    const serial = ++requestSerial;
    detail = null;
    clearError();
    setBusy(false);
    versionSelect.disabled = true;
    entrypointSelect.disabled = true;
    input.disabled = true;
    submit.disabled = true;
    description.textContent = '正在載入 entrypoints…';
    try {
      const loaded = await getAgentVersion(props.agentId, semver);
      if (destroyed || serial !== requestSerial) return;
      detail = loaded;
      renderEntrypoints();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      if (destroyed || serial !== requestSerial) return;
      errorSlot.replaceChildren(createErrorBox(error, { hint: '請重新選擇版本或稍後再試。' }));
      errorSlot.hidden = false;
      description.textContent = '';
    }
  };

  const renderVersions = (preferred) => {
    loadingSlot.hidden = versionsReady;
    if (!versionsReady) {
      emptySlot.hidden = true;
      form.hidden = true;
      return;
    }
    const choices = published();
    emptySlot.hidden = choices.length !== 0;
    form.hidden = choices.length === 0;
    if (choices.length === 0) {
      detail = null;
      return;
    }
    const previous = preferred || versionSelect.value || props.latestPublishedVersion;
    versionSelect.replaceChildren();
    for (const version of choices) {
      const option = el('option', null, version.semver);
      option.value = version.semver;
      versionSelect.appendChild(option);
    }
    versionSelect.value = choices.some((version) => version.semver === previous)
      ? previous : choices[0].semver;
    loadDetail(versionSelect.value);
  };

  const onVersionChange = () => loadDetail(versionSelect.value);
  const onEntrypointChange = () => renderEntrypointDetail();
  const onSubmit = async (event) => {
    event.preventDefault();
    if (busy || !detail) return;
    clearError();
    let parsed;
    try {
      parsed = JSON.parse(input.value);
    } catch (error) {
      inputError.textContent = 'JSON 無法解析：' + error.message;
      inputError.hidden = false;
      input.setAttribute('aria-invalid', 'true');
      input.focus();
      return;
    }
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) {
      inputError.textContent = 'Input 必須是 JSON object。';
      inputError.hidden = false;
      input.setAttribute('aria-invalid', 'true');
      input.focus();
      return;
    }
    setBusy(true);
    try {
      const created = await createRun(props.agentId, {
        version: versionSelect.value,
        entrypoint: entrypointSelect.value,
        input: parsed,
      });
      location.href = '/console/runs/' + encodeURIComponent(created.runId);
    } catch (error) {
      setBusy(false);
      if (!(error instanceof ApiError)) throw error;
      const box = createErrorBox(error, { hint: '請依錯誤內容修正 input 或重新載入版本。' });
      if (error.runId) {
        const link = el('a', 'btn-secondary', '前往 Run 觀測');
        link.href = '/console/runs/' + encodeURIComponent(error.runId);
        box.appendChild(link);
      }
      errorSlot.replaceChildren(box);
      errorSlot.hidden = false;
    }
  };

  versionSelect.addEventListener('change', onVersionChange);
  entrypointSelect.addEventListener('change', onEntrypointChange);
  form.addEventListener('submit', onSubmit);
  renderVersions();

  return {
    selectVersion(semver) {
      renderVersions(semver);
      form.scrollIntoView({ block: 'start' });
      versionSelect.focus();
    },
    update(next = {}) {
      if (Array.isArray(next.versions)) versions = next.versions;
      if (next.versionsReady !== undefined) versionsReady = next.versionsReady;
      if (next.latestPublishedVersion !== undefined) props.latestPublishedVersion = next.latestPublishedVersion;
      renderVersions(next.preferredVersion);
    },
    destroy() {
      destroyed = true;
      requestSerial += 1;
      versionSelect.removeEventListener('change', onVersionChange);
      entrypointSelect.removeEventListener('change', onEntrypointChange);
      form.removeEventListener('submit', onSubmit);
      root.replaceChildren();
    },
  };
}
