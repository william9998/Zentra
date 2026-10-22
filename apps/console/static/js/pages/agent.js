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

// 固定結構的樣板（標題與說明都是這個檔案裡的常數，不是 API 字串），所以可以用樣板字串。
const CHEVRON = '<svg class="icon icon-md fold-chevron" aria-hidden="true">'
  + '<use href="/console/static/icons.svg#i-chevron-down"/></svg>';

function fold(title, hint, body, open = false) {
  return `
    <details class="card fold"${open ? ' open' : ''}>
      <summary class="fold-summary">
        <span class="fold-head"><h2 class="card-title">${title}</h2>
        ${hint ? `<span class="fold-hint">${hint}</span>` : ''}</span>${CHEVRON}
      </summary>
      <div class="fold-body">${body}</div>
    </details>`;
}

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
    ${fold('基本資料', '', '<div class="meta-grid" data-meta></div>', true)}
    ${fold('版本', '已註冊的 image 版本。同版號重新註冊會在驗證通過後取代舊的。',
      '<div data-versions></div>', true)}
    <details class="card fold composer-card" id="run-composer" open>
      <summary class="fold-summary">
        <span class="fold-head"><h2 class="card-title">執行 Agent</h2>
        <span class="fold-hint">選版本和入口，填輸入資料，直接跑一次。</span></span>${CHEVRON}
      </summary>
      <div class="fold-body"><div data-composer></div></div>
    </details>
    ${fold('Endpoints', 'REST 與 OpenAPI 網址。要開給外部 AI 平台用，到左邊的 MCP 頁勾選這個 Agent。',
      '<div data-endpoints></div>')}
    ${fold('最近 Runs', '這個 Agent 最近的執行紀錄。', '<div data-runs></div>')}
    ${fold('LLM 憑證',
      '只用於「打 LLM」。屬於這個 Agent、不屬於某個版本：發新版不用重填，更換則所有版本立刻一起變。沒設定就用平台預設金鑰。',
      '<div data-credential></div>')}
    ${fold('設定',
      'Agent 的參數，<strong>不是機密</strong>。例如搜尋輪數、預設語言。改了不用重新發版，下一筆 Run 就生效。'
      + '以<strong>明文</strong>儲存、完整傳進 Agent；密碼和 key 請放「對外連線 → 機密」。',
      '<div data-config></div>')}
    <details class="card fold">
      <summary class="fold-summary">
        <span class="fold-head"><h2 class="card-title">對外連線</h2>
        <span class="fold-hint">Agent 要連外面的網站、或寄信，都要經過這裡。沒設定就一律擋下。</span></span>${CHEVRON}
      </summary>
      <div class="fold-body">
        <section class="fold-sub">
          <h3 class="fold-sub-title">出口白名單與寄件者</h3>
          <p class="field-hint">只有列在白名單上的網址能被連到；寄信則只能用這裡設定的寄件者、寄給指定的收件人。</p>
          <div data-egress></div>
        </section>
        <section class="fold-sub">
          <h3 class="fold-sub-title">機密</h3>
          <p class="field-hint">
            Agent 去連<strong>別的服務</strong>要用的密碼或 key，例如交易所 API key、SMTP 密碼。
            存進來後 Agent 程式讀不到值，只有平台代為連線時才會用：在上面的出口規則或寄件者裡<strong>引用它的名字</strong>。
            打 LLM 用的金鑰不是放這裡，請到「LLM 憑證」。
          </p>
          <div data-secrets></div>
        </section>
        <section class="fold-sub">
          <h3 class="fold-sub-title">出口稽核</h3>
          <p class="field-hint">每一次對外請求（含被擋下的）都記在這裡。Agent 連不出去時，先看這裡找原因。</p>
          <div data-egress-calls></div>
        </section>
      </div>
    </details>
    ${fold('常駐容器',
      'Agent 的執行容器狀態。通常不用管：第一筆 Run 會自動啟動，閒置會自動回收。',
      '<div data-containers></div>')}
    <details class="card fold">
      <summary class="fold-summary">
        <span class="fold-head"><h2 class="card-title">API key 與用量</h2>
        <span class="fold-hint">給呼叫這個 Agent 的下游應用發 key，用來記帳：知道每筆是誰打的。</span></span>${CHEVRON}
      </summary>
      <div class="fold-body">
        <section class="fold-sub">
          <h3 class="fold-sub-title">下游 API key</h3>
          <div data-keys></div>
        </section>
        <section class="fold-sub">
          <h3 class="fold-sub-title">用量與除錯</h3>
          <p class="field-hint">
            用量按 key 歸戶。沒帶 key 的呼叫記成<strong>未歸戶</strong> —— 那不是錯誤，是 Console 自己發起的。
          </p>
          <div data-usage></div>
        </section>
      </div>
    </details>
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
      const composerCard = app.querySelector('#run-composer');
      composerCard.open = true;
      composerCard.scrollIntoView({ block: 'start' });
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
