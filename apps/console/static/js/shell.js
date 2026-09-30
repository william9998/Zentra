// shell.js —— ui-contract §5.1 的共用外框。
// 每個 .html 只寫 <main id="app">，側欄與頂部列由這裡注入，不要複製貼上五份。
//
// 選中狀態由 location.pathname 判斷（startsWith），**沒有前端路由器**：
// 網址由 api 的 FileResponse 路由給出（§6.3 規則 4）。

import { el } from './dom.js';

const SIDEBAR_KEY = 'zentra.console.sidebar';   // 只存 UI 偏好，永遠不存憑證（§6.4）
const THEME_KEY = 'zentra.console.theme';       // 同上：偏好而已

// 深色是產品的預設樣貌（ui-contract §1.1），`data-theme` 只在使用者明講時才出現。
const DARK = 'dark';
const LIGHT = 'light';

// 固定結構，沒有任何動態字串 —— 唯一允許使用樣板字串的情況（§6.3 規則 3）。
const FRAME = `
  <aside class="sidebar">
    <div class="brand">
      <svg class="icon icon-lg" aria-hidden="true"><use href="/console/static/icons.svg#i-bot"/></svg>
      <span>Zentra</span>
    </div>
    <nav class="nav" aria-label="主要導覽">
      <a class="nav-item" href="/console/agents" data-nav="/console/agents">
        <svg class="icon icon-lg" aria-hidden="true"><use href="/console/static/icons.svg#i-box"/></svg>
        <span>Agents</span>
      </a>
      <a class="nav-item" href="/console/runs" data-nav="/console/runs">
        <svg class="icon icon-lg" aria-hidden="true"><use href="/console/static/icons.svg#i-activity"/></svg>
        <span>Runs</span>
      </a>
    </nav>
  </aside>
  <div class="shell-main">
    <header class="topbar">
      <button type="button" class="btn-ghost btn-sm shell-toggle" aria-expanded="true">
        <svg class="icon icon-sm" aria-hidden="true"><use href="/console/static/icons.svg#i-chevron-right"/></svg>
        <span class="visually-hidden">切換側欄</span>
      </button>
      <nav class="breadcrumb" aria-label="麵包屑"></nav>
      <button type="button" class="btn-ghost btn-sm shell-theme" data-shell-theme>
        <svg class="icon icon-md" aria-hidden="true"><use data-shell-theme-icon href="/console/static/icons.svg#i-sun"/></svg>
      </button>
    </header>
  </div>
`;

/**
 * mount(document.body, { breadcrumb: [{ text, href? }, …] })
 * 最後一個 breadcrumb 項目視為目前頁面（mono、不可點）。
 */
export function mount(root, props = {}) {
  const app = root.querySelector('#app');
  const shell = el('div', 'shell');
  shell.innerHTML = FRAME;
  root.insertBefore(shell, app);
  shell.querySelector('.shell-main').appendChild(app);

  const breadcrumb = shell.querySelector('.breadcrumb');
  const toggle = shell.querySelector('.shell-toggle');
  const themeButton = shell.querySelector('[data-shell-theme]');
  const themeIcon = shell.querySelector('[data-shell-theme-icon]');

  const markActive = () => {
    for (const link of shell.querySelectorAll('[data-nav]')) {
      const target = link.getAttribute('data-nav');
      link.classList.toggle('nav-item--active', location.pathname.startsWith(target));
    }
  };

  const applyCollapsed = (collapsed) => {
    shell.classList.toggle('shell--collapsed', collapsed);
    toggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  };

  let collapsed = false;
  try {
    collapsed = localStorage.getItem(SIDEBAR_KEY) === 'collapsed';
  } catch (_) {
    collapsed = false;   // 隱私模式可能整個丟例外，側欄預設展開即可
  }
  applyCollapsed(collapsed);

  /**
   * 色彩模式（ui-contract §1.1 / §5.1）。
   *
   * **只有一個屬性在動**：`<html data-theme>`。所有顏色都是 token，所以切換不必碰
   * 任何元件 —— 反過來說，新增顏色 token 時兩邊都要加，不然它在淺色下會留在深色。
   *
   * 沒存過偏好就跟隨作業系統。這裡用 JS 判斷而不是 CSS 的 `prefers-color-scheme`，
   * 是因為那需要把整組淺色值再寫一遍（媒體查詢的選擇器不同，純 CSS 無法共用區塊），
   * 而零建置（ADR-0006）沒有前處理器可以幫忙去重。多一份會漂掉的值不划算。
   */
  const applyTheme = (theme) => {
    document.documentElement.setAttribute('data-theme', theme);
    // 圖示講的是**按下去會變成什麼**，不是現在是什麼 —— 後者畫面本身已經在說了。
    // 沒有可見文字，所以完整句放 aria-label（讀屏）與 title（滑鼠停留）。
    themeIcon.setAttribute('href',
      `/console/static/icons.svg#${theme === LIGHT ? 'i-moon' : 'i-sun'}`);
    const label = theme === LIGHT ? '切換為深色模式' : '切換為淺色模式';
    themeButton.setAttribute('aria-label', label);
    themeButton.setAttribute('title', label);
  };

  const preferredTheme = () => {
    try {
      const stored = localStorage.getItem(THEME_KEY);
      if (stored === LIGHT || stored === DARK) return stored;
    } catch (_) {
      // 隱私模式讀不到就當沒存過
    }
    return window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches
      ? LIGHT : DARK;
  };

  let theme = preferredTheme();
  applyTheme(theme);

  const onTheme = () => {
    theme = theme === LIGHT ? DARK : LIGHT;
    applyTheme(theme);
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch (_) {
      // 存不進去就算了，這一頁仍然是切好的
    }
  };
  themeButton.addEventListener('click', onTheme);

  const onToggle = () => {
    collapsed = !collapsed;
    applyCollapsed(collapsed);
    try {
      localStorage.setItem(SIDEBAR_KEY, collapsed ? 'collapsed' : 'expanded');
    } catch (_) {
      // 存不進去就算了，不影響操作。
    }
  };
  toggle.addEventListener('click', onToggle);

  const setBreadcrumb = (items) => {
    breadcrumb.replaceChildren();
    const list = Array.isArray(items) ? items : [];
    list.forEach((item, index) => {
      const isLast = index === list.length - 1;
      if (index > 0) breadcrumb.appendChild(el('span', 'breadcrumb-sep', '/'));
      if (item.href && !isLast) {
        const link = el('a', 'breadcrumb-link', item.text);   // textContent，不是 innerHTML
        link.href = item.href;
        breadcrumb.appendChild(link);
      } else {
        breadcrumb.appendChild(el('span', isLast ? 'breadcrumb-current' : null, item.text));
      }
    });
  };

  markActive();
  setBreadcrumb(props.breadcrumb);

  return {
    setBreadcrumb,
    update(next = {}) {
      setBreadcrumb(next.breadcrumb);
    },
    destroy() {
      toggle.removeEventListener('click', onToggle);
      themeButton.removeEventListener('click', onTheme);
      // #app 留在原地：Console 是多頁架構，沒有客戶端切頁，不需要把外框拆回去。
    },
  };
}
