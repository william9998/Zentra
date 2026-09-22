import { ApiError, ERROR_CODE, VERSION_STATUS, getAgent } from '../api.js';
import { mount as mountVersionList } from '../components/agent-version-list.js';
import { mount as mountEndpoints } from '../components/endpoints-card.js';
import { mount as mountRecentRuns } from '../components/recent-runs.js';
import { mount as mountComposer } from '../components/run-composer.js';
import { createCopyButton, createErrorBox, createTimers, el, formatDateTime } from '../dom.js';
import { mount as mountShell } from '../shell.js';

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1 data-title>Agent</h1>
        <p class="page-subtitle" data-description>正在載入 Agent metadata…</p>
      </div>
      <a class="btn-secondary btn-lg" data-register>註冊版本</a>
    </div>
    <div data-page-error hidden></div>
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
  register.href = '/console/agents/' + encodeURIComponent(agentId) + '/versions/new';
  for (let index = 0; index < 4; index += 1) {
    const item = el('div', 'meta-item');
    item.appendChild(el('span', 'skeleton skeleton-line skeleton-line--third'));
    item.appendChild(el('span', 'skeleton skeleton-line skeleton-line--wide'));
    meta.appendChild(item);
  }

  const endpoints = mountEndpoints(app.querySelector('[data-endpoints]'), { loading: true, agentId });
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
      title.textContent = agent.name;
      description.textContent = agent.description || '沒有描述';
      shell.setBreadcrumb([{ text: 'Agents', href: '/console/agents' }, { text: agent.name }]);
      renderMeta(agent);
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
    versions.destroy();
    composer.destroy();
    endpoints.destroy();
    recentRuns.destroy();
    timers.clearAll();
    shell.destroy();
  };
  window.addEventListener('beforeunload', destroy, { once: true });
  window.addEventListener('pagehide', destroy, { once: true });
  return { destroy };
}

main();
