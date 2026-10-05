import { ApiError, ERROR_CODE, VERSION_STATUS, getAgent } from '../api.js';
import { mount as mountConfigCard } from '../components/agent-config-card.js';
import { mount as mountSecretsCard } from '../components/agent-secrets-card.js';
import { mount as mountEgressCard } from '../components/egress-card.js';
import { mount as mountEgressCalls } from '../components/egress-calls-panel.js';
import { mount as mountKeysCard } from '../components/api-keys-card.js';
import { mount as mountContainers } from '../components/containers-card.js';
import { mount as mountVersionList } from '../components/agent-version-list.js';
import { mount as mountDeleteDialog } from '../components/delete-agent-dialog.js';
import { mount as mountEditDialog } from '../components/edit-agent-dialog.js';
import { mount as mountEndpoints } from '../components/endpoints-card.js';
import { mount as mountCredential } from '../components/llm-credential-card.js';
import { mount as mountRecentRuns } from '../components/recent-runs.js';
import { mount as mountComposer } from '../components/run-composer.js';
import { mount as mountUsagePanel } from '../components/usage-panel.js';
import { createCopyButton, createErrorBox, createTimers, el, formatDateTime } from '../dom.js';
import { mount as mountShell } from '../shell.js';

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1 data-title>Agent</h1>
        <p class="page-subtitle" data-description>正在載入 Agent metadata…</p>
      </div>
      <div class="page-actions">
        <button type="button" class="btn-ghost btn-lg" data-edit disabled>編輯</button>
        <button type="button" class="btn-danger btn-lg" data-delete disabled>刪除</button>
        <a class="btn-secondary btn-lg" data-register>註冊版本</a>
      </div>
    </div>
    <div data-page-error hidden></div>
    <div data-edit-dialog></div>
    <div data-delete-dialog></div>
    <section class="card">
      <div class="card-header"><h2 class="card-title">基本資料</h2></div>
      <div class="meta-grid" data-meta></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">版本</h2></div>
      <div data-versions></div>
    </section>
    <section class="card composer-card" id="run-composer">
      <div class="card-header"><h2 class="card-title">執行 Agent</h2></div>
      <div data-composer></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">Endpoints</h2></div>
      <div data-endpoints></div>
    </section>
    <section class="card">
      <div class="card-header">
        <h2 class="card-title">LLM 憑證</h2>
      </div>
      <p class="field-hint">
        憑證屬於這個 Agent，<strong>不屬於某個版本</strong> ——
        發新版本不用重填，更換則所有版本立刻一起變。沒設定就用平台預設金鑰。
      </p>
      <div data-credential></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">設定</h2></div>
      <div data-config></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">機密</h2></div>
      <div data-secrets></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">對外出口與寄信</h2></div>
      <div data-egress></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">出口稽核</h2></div>
      <div data-egress-calls></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">常駐容器</h2></div>
      <div data-containers></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">下游 API key</h2></div>
      <div data-keys></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">用量與除錯</h2></div>
      <p class="field-hint">
        用量按 key 歸戶。沒帶 key 的呼叫記成<strong>未歸戶</strong> ——
        那不是錯誤，是 Console 自己發起的。
      </p>
      <div data-usage></div>
    </section>
    <section class="card">
      <div class="card-header"><h2 class="card-title">最近 Runs</h2></div>
      <div data-runs></div>
    </section>
  </div>
`;

function readAgentId() {
  const parts = location.pathname.split('/').filter(Boolean);
  if (parts.length >= 3 && parts[0] === 'console' && parts[1] === 'agents') {
    return decodeURIComponent(parts[2]);
  }
  return '';
}

function main() {
  const app = document.getElementById('app');
  const agentId = readAgentId();
  const timers = createTimers();
  const shell = mountShell(document.body, {
    breadcrumb: [{ text: 'Agents', href: '/console/agents' }, { text: agentId || '(缺少 Agent ID)' }],
  });
  app.innerHTML = PAGE;
  const title = app.querySelector('[data-title]');
  const description = app.querySelector('[data-description]');
  const register = app.querySelector('[data-register]');
  const meta = app.querySelector('[data-meta]');
  const pageError = app.querySelector('[data-page-error]');
  const editButton = app.querySelector('[data-edit]');
  const deleteButton = app.querySelector('[data-delete]');
  register.href = '/console/agents/' + encodeURIComponent(agentId) + '/versions/new';
  for (let index = 0; index < 4; index += 1) {
    const item = el('div', 'meta-item');
    item.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
    item.appendChild(el('span', 'skeleton skeleton-line skeleton-line--wide'));
    meta.appendChild(item);
  }

  const endpoints = mountEndpoints(app.querySelector('[data-endpoints]'), { loading: true, agentId });
  const credential = mountCredential(app.querySelector('[data-credential]'), { agentId });
  const composer = mountComposer(app.querySelector('[data-composer]'), {
    agentId, versions: [], versionsReady: false, latestPublishedVersion: null,
  });
  const recentRuns = mountRecentRuns(app.querySelector('[data-runs]'), {
    agentId,
    hasPublished: false,
    onCompose() {
      app.querySelector('#run-composer').scrollIntoView({ block: 'start' });
    },
  });
  // key 卡片與用量面板共用同一份 key 清單：卡片拿到之後推給面板，
  // 面板不必自己再抓一次（也就不會有兩份可能不同步的下拉選單）。
  const containers = mountContainers(app.querySelector('[data-containers]'), {
    agentId, timers,
  });
  // ZEN-019 的三層。機密卡是**唯一**抓機密清單的地方 ——
  // 出口卡的兩個下拉直接吃它推過來的名字，不自己再抓一次，
  // 不然兩份清單會不同步（而不同步時畫面上看不出來）。
  const configCard = mountConfigCard(app.querySelector('[data-config]'), { agentId });
  const egressCard = mountEgressCard(app.querySelector('[data-egress]'), { agentId });
  const secretsCard = mountSecretsCard(app.querySelector('[data-secrets]'), {
    agentId,
    onChange(secretRows) {
      egressCard.setSecretNames(secretRows.map((row) => row.name));
    },
  });
  const egressCalls = mountEgressCalls(app.querySelector('[data-egress-calls]'), {
    agentId,
  });
  const usagePanel = mountUsagePanel(app.querySelector('[data-usage]'), { agentId });
  const keysCard = mountKeysCard(app.querySelector('[data-keys]'), {
    agentId,
    timers,
    onChange(rows) { usagePanel.setKeys(rows); },
  });
  let knownPublished = false;
  let knownPublishedKey = null;
  let destroyed = false;
  const versions = mountVersionList(app.querySelector('[data-versions]'), {
    agentId,
    onChange(nextVersions) {
      const hasPublished = nextVersions.some((version) => version.status === VERSION_STATUS.PUBLISHED);
      const hasBuilding = nextVersions.some((version) => version.status === VERSION_STATUS.BUILDING);
      const publishedKey = nextVersions
        .filter((version) => version.status === VERSION_STATUS.PUBLISHED)
        .map((version) => version.semver).join('|');
      if (publishedKey !== knownPublishedKey) {
        knownPublishedKey = publishedKey;
        composer.update({ versions: nextVersions, versionsReady: true });
      }
      if (hasPublished !== knownPublished) {
        knownPublished = hasPublished;
        recentRuns.update({ hasPublished });
      }
      endpoints.update({ published: hasPublished, building: hasBuilding });
    },
    onSelect(semver) { composer.selectVersion(semver); },
  });

  // metadata 編輯／刪除。兩顆按鈕在 metadata 載進來之前保持 disabled ——
  // 沒有 agent 物件就不知道要改什麼，也不該讓人按下去才發現。
  const editDialog = mountEditDialog(app.querySelector('[data-edit-dialog]'), {
    onSaved(updated) { applyMetadata(updated); },
  });
  const deleteDialog = mountDeleteDialog(app.querySelector('[data-delete-dialog]'), {
    onDeleted() { location.href = '/console/agents'; },
  });
  const onEdit = () => editDialog.open(editButton);
  const onDelete = () => deleteDialog.open(deleteButton);
  editButton.addEventListener('click', onEdit);
  deleteButton.addEventListener('click', onDelete);

  const renderMeta = (agent) => {
    meta.replaceChildren();
    const add = (label, value, copyable) => {
      const item = el('div', 'meta-item');
      item.appendChild(el('span', 'meta-label', label));
      const row = el('div', 'inline-row');
      row.appendChild(el('span', 'meta-value', value || '—'));
      if (copyable && value) row.appendChild(createCopyButton(() => value, timers, 'Copy'));
      item.appendChild(row);
      meta.appendChild(item);
    };
    add('Agent ID', agent.agentId, true);
    add('Latest published', agent.latestPublishedVersion || '尚無已發布版本');
    add('Created', formatDateTime(agent.createdAt));
    add('Updated', formatDateTime(agent.updatedAt));
  };

  /** 首次載入與編輯成功後走同一條路徑，標題、麵包屑與 meta 不會各更新各的。 */
  function applyMetadata(agent) {
    title.textContent = agent.name;
    description.textContent = agent.description || '沒有描述';
    shell.setBreadcrumb([{ text: 'Agents', href: '/console/agents' }, { text: agent.name }]);
    renderMeta(agent);
    editDialog.update({ agent });
    deleteDialog.update({ agent });
    editButton.disabled = false;
    deleteButton.disabled = false;
  }

  const load = async () => {
    if (!agentId) {
      pageError.hidden = false;
      pageError.replaceChildren(createErrorBox({
        code: ERROR_CODE.NOT_FOUND, message: '網址裡沒有 Agent ID。', details: {},
      }, { hint: '請回 Agents 列表重新選擇。' }));
      return;
    }
    try {
      const agent = await getAgent(agentId);
      if (destroyed) return;
      applyMetadata(agent);
      endpoints.update({ loading: false, endpoints: agent.endpoints,
        published: Boolean(agent.latestPublishedVersion) || knownPublished });
      composer.update({ latestPublishedVersion: agent.latestPublishedVersion });
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      meta.replaceChildren(el('p', 'empty-text', '無法載入 Agent metadata。'));
      pageError.hidden = false;
      pageError.replaceChildren(createErrorBox(error, { hint: '請回 Agents 列表重新選擇或重新載入。' }));
      endpoints.update({ loading: false, error });
    }
  };

  load();
  const destroy = () => {
    destroyed = true;
    editButton.removeEventListener('click', onEdit);
    deleteButton.removeEventListener('click', onDelete);
    editDialog.destroy();
    deleteDialog.destroy();
    versions.destroy();
    composer.destroy();
    endpoints.destroy();
    credential.destroy();
    containers.destroy();
    configCard.destroy();
    secretsCard.destroy();
    egressCard.destroy();
    egressCalls.destroy();
    keysCard.destroy();
    usagePanel.destroy();
    recentRuns.destroy();
    timers.clearAll();
    shell.destroy();
  };
  window.addEventListener('beforeunload', destroy, { once: true });
  window.addEventListener('pagehide', destroy, { once: true });
  return { destroy };
}

main();
