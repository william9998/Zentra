import { ApiError, getAgent } from '../api.js';
import { mount as mountRegistration } from '../components/version-registration.js';
import { createErrorBox } from '../dom.js';
import { mount as mountShell } from '../shell.js';

const PAGE = `
  <div class="page page--reading">
    <div class="page-header">
      <div class="page-title-group">
        <h1>註冊版本</h1>
        <p class="page-subtitle" data-agent>正在載入 Agent…</p>
      </div>
    </div>
    <div data-page-error hidden></div>
    <section class="card">
      <div class="card-header"><h2 class="card-title">OCI image</h2></div>
      <p class="field-hint">平台只保存 metadata，並按 immutable digest 拉取、inspect 與隔離驗證 image。</p>
      <div data-registration></div>
    </section>
  </div>
`;

const LOADING = `
  <div class="skeleton skeleton-line skeleton-line--third"></div>
  <div class="skeleton skeleton-line skeleton-line--wide"></div>
  <div class="skeleton skeleton-line skeleton-line--wide"></div>
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
  const shell = mountShell(document.body, {
    breadcrumb: [
      { text: 'Agents', href: '/console/agents' },
      { text: agentId, href: '/console/agents/' + encodeURIComponent(agentId) },
      { text: '註冊版本' },
    ],
  });
  app.innerHTML = PAGE;
  const agentSlot = app.querySelector('[data-agent]');
  const errorSlot = app.querySelector('[data-page-error]');
  const registrationSlot = app.querySelector('[data-registration]');
  registrationSlot.innerHTML = LOADING;
  let registration = null;
  let destroyed = false;

  const load = async () => {
    try {
      const agent = await getAgent(agentId);
      if (destroyed) return;
      agentSlot.textContent = agent.name + ' · 只接受 repository@sha256:<64 hex>';
      registration = mountRegistration(registrationSlot, { agentId, agentName: agent.name });
      shell.setBreadcrumb([
        { text: 'Agents', href: '/console/agents' },
        { text: agent.name, href: '/console/agents/' + encodeURIComponent(agentId) },
        { text: '註冊版本' },
      ]);
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      registrationSlot.replaceChildren();
      errorSlot.hidden = false;
      errorSlot.replaceChildren(createErrorBox(error, { hint: '請回 Agents 列表重新選擇。' }));
    }
  };
  load();

  const destroy = () => {
    destroyed = true;
    if (registration) registration.destroy();
    shell.destroy();
  };
  window.addEventListener('beforeunload', destroy, { once: true });
  window.addEventListener('pagehide', destroy, { once: true });
  return { destroy };
}

main();
