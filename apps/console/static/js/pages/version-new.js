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
      <details class="publish-help">
        <summary>怎麼把 image 推上來</summary>
        <p class="field-hint">
          <strong>只能在跑 Zentra 的那台主機上執行。</strong>
          <code class="mono">localhost:5000</code> 是平台內部的 Registry 位址，不是你的筆電。
        </p>
        <pre class="mono publish-help-steps"># 1. 打 tag 並推上去（建議 tag 用 semver）
docker tag my-agent:dev localhost:5000/my-agent:1.0.0
docker push localhost:5000/my-agent:1.0.0
#    最後一行會印出： 1.0.0: digest: sha256:&lt;64 hex&gt; size: &lt;n&gt;

# 2. 事後要再查 digest
docker inspect --format='{{index .RepoDigests 0}}' localhost:5000/my-agent:1.0.0</pre>
        <p class="field-hint">
          把 <code class="mono">repository@sha256:…</code> 整串貼進下面的欄位。
          外部 Registry（GHCR、Docker Hub…）一樣可以用，兩者在這裡是同一件事。
        </p>
      </details>
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
