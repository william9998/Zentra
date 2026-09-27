// run-graph.js —— 事件時間軸的第二種版面：節點流程圖（ui-contract §5.7）。
//
// ## 畫的是執行軌跡，不是 graph 定義
//
// 平台**不知道**你的 graph 長什麼樣 —— `entrypoints` 只描述輸入輸出，
// 沒有節點與邊。我們手上只有 `node.start` / `node.end` 的先後順序。
//
// 所以這裡畫的是「這次 Run 實際走過的路」：
//
//   · 節點依**第一次出現**的順序由左而右排
//   · 跑過多次的節點標 ×N，不重複畫一個框
//   · 往回走的邊（回到更早出現過的節點）畫成上方的弧線 ← **那就是 retry 迴圈**
//
// 對 Run 觀測來說這比靜態拓撲有用：靜態圖每次都長一樣，
// 而「這次到底繞了幾圈」只有軌跡看得出來。
//
// ## 為什麼是手寫 SVG
//
// ADR-0006 禁止任何執行期函式庫 —— d3 / cytoscape / mermaid 一律不行。
// 版面因此刻意選了單列橫排：座標只是 index 乘上間距，不需要佈局演算法。
//
// **SVG 用自然像素寬度，不是 100%。** 設成 100% 會把長流程縮到看不清字；
// 給它真實寬度、讓外層容器橫向捲動，節點大小才會固定（ui-contract §5.7）。
//
// 所有文字一律 textContent（節點名稱來自使用者的程式碼，是不可信字串）。

import { EVENT_TYPE } from '../api.js';
import { el } from '../dom.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

// 版面常數。單列橫排，所以座標就是 index 乘間距 —— 沒有佈局演算法。
const BOX_W = 190;
const BOX_H = 56;
const GAP = 56;            // 節點之間的水平間距，箭頭畫在這裡
const PAD_X = 20;
const PAD_TOP = 64;        // 上方留白：回頭的弧線畫在這裡
const PAD_BOTTOM = 20;
const ARC_BASE = 26;       // 弧線最低的高度
const ARC_PER_SPAN = 14;   // 每跨過一個節點再加高一點，避免多條弧線重疊

const FRAME = `
  <div class="run-graph" data-graph-root>
    <p class="empty-text" data-graph-empty>還沒有節點事件。Agent 開始執行後這裡會畫出走過的路徑。</p>
    <div class="run-graph-canvas" data-graph-canvas hidden></div>
    <p class="field-hint" data-graph-legend hidden></p>
  </div>
`;

function svg(tag, attrs = {}) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attrs)) {
    node.setAttribute(key, String(value));
  }
  return node;
}

/**
 * 從事件序列建出軌跡模型。
 *
 * 注意 LangGraph 的節點事件**會巢狀**（例如 route 函式在 assess 還沒結束時就開始），
 * 所以不能假設 start/end 成對出現且不重疊。這裡只看 `node.start` 的先後，
 * 用 `node.end` 標記完成 —— 對「走過哪些路」這個問題來說足夠，而且不會被巢狀弄亂。
 */
function buildTrace(events) {
  const order = [];                       // 第一次出現的順序
  const visits = new Map();               // name → 次數
  const done = new Set();
  const transitions = [];                 // [fromName, toName]
  let previous = null;

  for (const event of events) {
    // view model 的形狀見 api.js `viewEvent()`：節點名在頂層的 `node`，
    // 不是 `data.node`。契約欄位名只在 api.js 出現一次，這裡讀的是正規化後的結果。
    const name = event && event.node;
    if (!name) continue;

    if (event.type === EVENT_TYPE.NODE_START) {
      if (!visits.has(name)) {
        visits.set(name, 0);
        order.push(name);
      }
      visits.set(name, visits.get(name) + 1);
      if (previous && previous !== name) transitions.push([previous, name]);
      previous = name;
    } else if (event.type === EVENT_TYPE.NODE_END) {
      done.add(name);
    }
  }

  // 邊去重：同一對節點走過幾次都只畫一條
  const seen = new Set();
  const edges = [];
  for (const [from, to] of transitions) {
    const key = from + '\u0000' + to;
    if (seen.has(key)) continue;
    seen.add(key);
    const fromIndex = order.indexOf(from);
    const toIndex = order.indexOf(to);
    if (fromIndex === -1 || toIndex === -1) continue;
    edges.push({ fromIndex, toIndex, back: toIndex <= fromIndex });
  }

  return { order, visits, done, edges, running: previous };
}

function render(canvas, trace, runFinished) {
  const { order, visits, done, edges } = trace;
  const width = PAD_X * 2 + order.length * BOX_W + Math.max(0, order.length - 1) * GAP;
  const height = PAD_TOP + BOX_H + PAD_BOTTOM;

  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`,
    // 自然寬度（不是 100%）—— 長流程要能橫向捲動，而不是被縮到看不清
    width,
    height,
    role: 'img',
    'aria-label': `流程圖：依序經過 ${order.join('、')}`,
  });

  const defs = svg('defs');
  for (const [id, cls] of [['gx-arrow', 'graph-arrow'], ['gx-arrow-back', 'graph-arrow--back']]) {
    const marker = svg('marker', {
      id, viewBox: '0 0 8 8', refX: 7, refY: 4,
      markerWidth: 6, markerHeight: 6, orient: 'auto-start-reverse',
    });
    marker.appendChild(svg('path', { d: 'M0,0 L8,4 L0,8 z', class: cls }));
    defs.appendChild(marker);
  }
  root.appendChild(defs);

  const leftOf = (index) => PAD_X + index * (BOX_W + GAP);
  const midY = PAD_TOP + BOX_H / 2;

  // 先畫邊，節點蓋在上面
  for (const edge of edges) {
    const fromLeft = leftOf(edge.fromIndex);
    const toLeft = leftOf(edge.toIndex);
    if (!edge.back) {
      root.appendChild(svg('line', {
        x1: fromLeft + BOX_W, y1: midY,
        x2: toLeft, y2: midY,
        class: 'graph-edge', 'marker-end': 'url(#gx-arrow)',
      }));
      continue;
    }
    // 回頭的邊：從上方繞回去。這是 retry 迴圈的視覺形狀。
    // 跨得越遠拱得越高，多條弧線才不會疊在一起。
    const span = Math.max(1, edge.fromIndex - edge.toIndex);
    const lift = PAD_TOP - ARC_BASE - Math.min(span - 1, 3) * ARC_PER_SPAN;
    const startX = fromLeft + BOX_W / 2;
    const endX = toLeft + BOX_W / 2;
    root.appendChild(svg('path', {
      d: `M ${startX} ${PAD_TOP} C ${startX} ${lift}, ${endX} ${lift}, ${endX} ${PAD_TOP}`,
      class: 'graph-edge graph-edge--back',
      fill: 'none', 'marker-end': 'url(#gx-arrow-back)',
    }));
  }

  order.forEach((name, index) => {
    const left = leftOf(index);
    const top = PAD_TOP;
    const count = visits.get(name) || 0;
    const isDone = done.has(name);
    const isRunning = !isDone && !runFinished;

    const group = svg('g', { class: 'graph-node' });
    group.appendChild(svg('rect', {
      x: left, y: top, width: BOX_W, height: BOX_H, rx: 8,
      class: 'graph-node-box'
        + (isRunning ? ' graph-node-box--running' : '')
        + (isDone ? ' graph-node-box--done' : ''),
    }));
    group.appendChild(svg('circle', {
      cx: left + 16, cy: top + BOX_H / 2, r: 4,
      class: 'graph-node-dot'
        + (isRunning ? ' graph-node-dot--running' : ' graph-node-dot--done'),
    }));

    // 節點名稱來自使用者的程式碼 → textContent，且長名字截斷避免撐破框
    const label = svg('text', {
      x: left + 30, y: top + BOX_H / 2 + 5, class: 'graph-node-label',
    });
    label.textContent = name.length > 17 ? name.slice(0, 16) + '…' : name;
    const title = svg('title');
    title.textContent = name;            // 完整名稱放 tooltip
    label.appendChild(title);
    group.appendChild(label);

    if (count > 1) {
      const badge = svg('text', {
        x: left + BOX_W - 12, y: top + BOX_H / 2 + 5,
        'text-anchor': 'end', class: 'graph-node-count',
      });
      badge.textContent = '×' + count;
      group.appendChild(badge);
    }
    root.appendChild(group);
  });

  canvas.replaceChildren(root);
}

export function mount(root, props = {}) {
  root.innerHTML = FRAME;
  const canvas = root.querySelector('[data-graph-canvas]');
  const empty = root.querySelector('[data-graph-empty]');
  const legend = root.querySelector('[data-graph-legend]');

  let events = props.events || [];
  let finished = Boolean(props.finished);

  const draw = () => {
    const trace = buildTrace(events);
    const hasNodes = trace.order.length > 0;
    empty.hidden = hasNodes;
    canvas.hidden = !hasNodes;
    legend.hidden = !hasNodes;
    if (!hasNodes) {
      canvas.replaceChildren();
      return;
    }
    render(canvas, trace, finished);

    const loops = trace.edges.filter((edge) => edge.back).length;
    const repeated = [...trace.visits.entries()].filter(([, n]) => n > 1);
    legend.replaceChildren();
    legend.append(`${trace.order.length} 個節點`);
    if (repeated.length) {
      legend.append('，其中 ');
      legend.append(el('strong', null, String(repeated.length)));
      legend.append(' 個跑了不只一次');
    }
    if (loops) {
      legend.append('。上方弧線是回頭的邊 —— 那是 retry 迴圈。');
    } else {
      legend.append('。這次沒有回頭的邊。');
    }
  };

  draw();

  return {
    update(next = {}) {
      if (next.events) events = next.events;
      if (next.finished !== undefined) finished = Boolean(next.finished);
      draw();
    },
    destroy() {
      root.replaceChildren();
    },
  };
}
