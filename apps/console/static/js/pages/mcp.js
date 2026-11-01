import { mount as mountMcpKeys } from '../components/mcp-keys-card.js';
import { mount as mountShell } from '../shell.js';
import { createTimers } from '../dom.js';

const PAGE = `
  <div class="page">
    <div class="page-header">
      <div class="page-title-group">
        <h1>MCP</h1>
        <p class="page-subtitle">平台只有一個 MCP 接口。發一把 key、勾選要開放的 Agent，對端 AI 平台連上後會在 tools 清單裡看到它們。</p>
      </div>
    </div>
    <section class="card">
      <div data-keys></div>
    </section>
  </div>
`;

function main() {
  const app = document.getElementById('app');
  const shell = mountShell(document.body, { breadcrumb: [{ text: 'MCP' }] });
  app.innerHTML = PAGE;
  const timers = createTimers();
  const keys = mountMcpKeys(app.querySelector('[data-keys]'), { timers });

  const destroy = () => {
    keys.destroy();
    timers.clearAll();
    shell.destroy();
  };
  window.addEventListener('beforeunload', destroy, { once: true });
  window.addEventListener('pagehide', destroy, { once: true });
  return { destroy };
}

main();
