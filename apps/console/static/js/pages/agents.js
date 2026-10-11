import { mount as mountAgentList } from '../components/agent-list.js';
import { mount as mountCreateDialog } from '../components/create-agent-dialog.js';
import { mount as mountShell } from '../shell.js';

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1>Agents</h1>
        <p class="page-subtitle">建立 Agent metadata、註冊 immutable OCI image 版本，再從同一個平台呼叫。</p>
      </div>
      <button type="button" class="btn-primary btn-lg" data-create>建立 Agent</button>
    </div>
    <section class="agent-overview" data-list aria-label="Agent 總覽"></section>
    <div data-dialog></div>
  </div>
`;

function main() {
  const app = document.getElementById('app');
  const shell = mountShell(document.body, { breadcrumb: [{ text: 'Agents' }] });
  app.innerHTML = PAGE;
  const createButton = app.querySelector('[data-create]');
  const dialog = mountCreateDialog(app.querySelector('[data-dialog]'));
  const list = mountAgentList(app.querySelector('[data-list]'), {
    onCreate(trigger) { dialog.open(trigger); },
  });
  const onCreate = () => dialog.open(createButton);
  createButton.addEventListener('click', onCreate);

  const destroy = () => {
    createButton.removeEventListener('click', onCreate);
    list.destroy();
    dialog.destroy();
    shell.destroy();
  };
  window.addEventListener('beforeunload', destroy, { once: true });
  window.addEventListener('pagehide', destroy, { once: true });
  return { destroy };
}

main();
