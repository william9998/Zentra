import {
  ApiError, ERROR_CODE, VERSION_STATUS, getAgentVersion, listAgentVersions,
} from '../api.js';
import {
  createCopyButton, createErrorBox, createTimers, el, formatBytes, formatDateTime, toPrettyJson,
} from '../dom.js';

const STATUS_LABEL = {
  [VERSION_STATUS.BUILDING]: '驗證中',
  [VERSION_STATUS.PUBLISHED]: '已發布',
  [VERSION_STATUS.FAILED]: '驗證失敗',
};

export function mount(root, props) {
  const timers = createTimers();
  let versions = [];
  let phase = 'loading';
  let error = null;
  let pollError = null;
  let pollTimer = null;
  let polling = false;
  let destroyed = false;

  const notify = () => {
    if (typeof props.onChange === 'function') props.onChange(versions.slice());
  };

  const artifactRow = (container, label, value, copyable) => {
    const item = el('div', 'meta-item');
    item.appendChild(el('span', 'meta-label', label));
    const row = el('div', 'inline-row');
    row.appendChild(el('span', 'meta-value', value || '—'));
    if (copyable && value) row.appendChild(createCopyButton(() => value, timers, 'Copy'));
    item.appendChild(row);
    container.appendChild(item);
  };

  const versionRow = (version) => {
    const details = el('details', 'version-row');
    details.dataset.semver = version.semver;
    const summary = el('summary', 'version-summary');
    summary.appendChild(el('span', 'version-semver', version.semver));
    const status = el('span', 'status status--' + version.status);
    const dot = el('span', 'status-dot status-dot--' + version.status);
    dot.setAttribute('aria-hidden', 'true');
    status.appendChild(dot);
    status.appendChild(el('span', 'status-label', STATUS_LABEL[version.status] || version.status));
    summary.appendChild(status);
    summary.appendChild(el('span', 'mono text-xs', version.versionId || '—'));
    summary.appendChild(el('span', 'text-xs', version.artifact.platform || '—'));
    summary.appendChild(el('span', 'mono text-xs', formatBytes(version.artifact.sizeBytes)));
    summary.appendChild(el('span', 'mono text-2xs', formatDateTime(version.createdAt)));
    details.appendChild(summary);

    const body = el('div', 'version-body');
    const grid = el('div', 'meta-grid');
    artifactRow(grid, 'Image reference', version.artifact.imageRef, true);
    artifactRow(grid, 'Digest', version.artifact.digest, true);
    artifactRow(grid, 'Platform', version.artifact.platform);
    artifactRow(grid, 'Size', formatBytes(version.artifact.sizeBytes));
    artifactRow(grid, 'Runtime contract', version.artifact.runtimeContract);
    artifactRow(grid, 'Version ID', version.agentVersionId, true);
    body.appendChild(grid);

    if (version.status === VERSION_STATUS.BUILDING) {
      const live = el('p', 'stream-status', '平台正在拉取、檢查並驗證 image。');
      body.appendChild(live);
    }
    if (version.status === VERSION_STATUS.PUBLISHED) {
      const entrypoints = el('details');
      entrypoints.appendChild(el('summary', 'text-secondary text-xs', 'Entrypoints'));
      entrypoints.appendChild(el('pre', 'code', toPrettyJson(version.entrypoints || [])));
      body.appendChild(entrypoints);
      const action = el('button', 'btn-secondary', '用這個版本執行');
      action.type = 'button';
      action.addEventListener('click', () => {
        if (typeof props.onSelect === 'function') props.onSelect(version.semver);
      });
      body.appendChild(action);
    }
    if (version.status === VERSION_STATUS.FAILED) {
      const failure = version.error || {
        code: ERROR_CODE.BUILD_FAILED, message: '版本驗證失敗，但 API 沒有提供錯誤內容。', details: {},
      };
      body.appendChild(createErrorBox(failure, {
        hint: '此 semver 不可覆寫。修正 image 後請用新的 semver 註冊。',
      }));
      body.appendChild(createCopyButton(
        () => failure.code + '\n' + failure.message + '\n' + toPrettyJson(failure.details),
        timers, '複製完整錯誤',
      ));
    }
    details.appendChild(body);
    return details;
  };

  const render = () => {
    const openVersions = new Set(Array.from(root.querySelectorAll('.version-row[open]'))
      .map((row) => row.dataset.semver));
    root.replaceChildren();
    if (phase === 'loading') {
      for (let index = 0; index < 3; index += 1) {
        const row = el('div', 'data-row');
        row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
        row.appendChild(el('span', 'skeleton skeleton-line skeleton-line--half'));
        root.appendChild(row);
      }
      return;
    }
    if (phase === 'error') {
      const box = createErrorBox(error, { hint: '請重新載入版本列表。' });
      const retry = el('button', 'btn-secondary', '重新載入');
      retry.type = 'button';
      retry.addEventListener('click', () => load(), { once: true });
      box.appendChild(retry);
      root.appendChild(box);
      return;
    }
    if (pollError) {
      root.appendChild(createErrorBox(pollError, {
        hint: '既有版本仍可查看；Console 會繼續嘗試更新驗證中的版本。',
      }));
    }
    if (versions.length === 0) {
      const empty = el('div', 'empty');
      empty.appendChild(el('p', 'empty-text', '尚未註冊版本。先註冊一個 canonical OCI image digest。'));
      const link = el('a', 'btn-secondary', '註冊第一個版本');
      link.href = '/console/agents/' + encodeURIComponent(props.agentId) + '/versions/new';
      empty.appendChild(link);
      root.appendChild(empty);
      return;
    }
    const list = el('div', 'version-list');
    for (const version of versions) {
      const row = versionRow(version);
      row.open = openVersions.has(version.semver);
      list.appendChild(row);
    }
    root.appendChild(list);
  };

  const stopPolling = () => {
    if (pollTimer !== null) {
      timers.cancel(pollTimer);
      pollTimer = null;
    }
  };

  const syncPolling = () => {
    const building = versions.some((version) => version.status === VERSION_STATUS.BUILDING);
    if (!building) {
      stopPolling();
      return;
    }
    if (pollTimer === null) pollTimer = timers.every(2000, refreshBuilding);
  };

  const refreshBuilding = async () => {
    if (polling || destroyed) return;
    const indexes = versions
      .map((version, index) => ({ version, index }))
      .filter((entry) => entry.version.status === VERSION_STATUS.BUILDING);
    if (indexes.length === 0) {
      stopPolling();
      return;
    }
    polling = true;
    const results = await Promise.allSettled(indexes.map((entry) =>
      getAgentVersion(props.agentId, entry.version.semver)));
    if (!destroyed) {
      const rejected = results.find((result) => result.status === 'rejected');
      pollError = rejected ? rejected.reason : null;
      results.forEach((result, resultIndex) => {
        if (result.status === 'fulfilled') versions[indexes[resultIndex].index] = result.value;
      });
      render();
      notify();
      syncPolling();
    }
    polling = false;
  };

  const load = async () => {
    phase = 'loading';
    error = null;
    render();
    try {
      const page = await listAgentVersions(props.agentId);
      if (destroyed) return;
      versions = page.items.slice().sort((left, right) =>
        (right.createdAt ? right.createdAt.getTime() : 0) - (left.createdAt ? left.createdAt.getTime() : 0));
      phase = 'ready';
      pollError = null;
      render();
      notify();
      syncPolling();
    } catch (caught) {
      if (!(caught instanceof ApiError)) throw caught;
      error = caught;
      phase = 'error';
      render();
    }
  };

  load();
  return {
    update() {},
    reload: load,
    destroy() {
      destroyed = true;
      stopPolling();
      timers.clearAll();
      root.replaceChildren();
    },
  };
}
