// run-graph.js —— 事件時間軸的第二種版面：節點流程圖（ui-contract §5.7）。
//
// ## 畫的是執行軌跡，不是 graph 定義
//
// 平台**不知道**你的 graph 長什麼樣 —— `entrypoints` 只描述輸入輸出，
// 沒有節點與邊。我們手上只有 `node.start` / `node.end` 的先後順序。
//
// 所以這裡畫的是「這次 Run 實際走過的路」：
//
//   · 節點依**第一次出現**的順序由上而下排
//   · 跑過多次的節點標 ×N，不重複畫一個框
//   · 往回走的邊（回到更早出現過的節點）畫成右側的弧線 ← **那就是 retry 迴圈**
//
// 對 Run 觀測來說這比靜態拓撲有用：靜態圖每次都長一樣，
// 而「這次到底繞了幾圈」只有軌跡看得出來。
//
// ## 為什麼是手寫 SVG
//
// ADR-0006 禁止任何執行期函式庫 —— d3 / cytoscape / mermaid 一律不行。
// 版面因此刻意選了單欄直排：座標只是 index 乘上間距，不需要佈局演算法。
//
// 所有文字一律 textContent（節點名稱來自使用者的程式碼，是不可信字串）。

import { EVENT_TYPE } from '../api.js';
import { el } from '../dom.js';

const SVG_NS = 'http://www.w3.org/2000/svg';

// 版面常數。單欄直排，所以座標就是 index 乘間距 —— 沒有佈局演算法。
const BOX_W = 260;
const BOX_H = 56;
const GAP = 40;
const PAD_X = 28;          // 左側留白：回頭的弧線畫在這裡
const PAD_Y = 20;
const LOOP_OUT = 22;       // 弧線往左凸出多少

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
  const width = BOX_W + PAD_X * 2;
  const height = PAD_Y * 2 + order.length * BOX_H + Math.max(0, order.length - 1) * GAP;

  const root = svg('svg', {
    viewBox: `0 0 ${width} ${height}`,
    width: '100%',
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

  const topOf = (index) => PAD_Y + index * (BOX_H + GAP);

  // 先畫邊，節點蓋在上面
  for (const edge of edges) {
    const fromTop = topOf(edge.fromIndex);
    const toTop = topOf(edge.toIndex);
    if (!edge.back) {
      root.appendChild(svg('line', {
        x1: PAD_X + BOX_W / 2, y1: fromTop + BOX_H,
        x2: PAD_X + BOX_W / 2, y2: toTop,
        class: 'graph-edge', 'marker-end': 'url(#gx-arrow)',
      }));
      continue;
    }
    // 回頭的邊：從左側繞出去再接回上面的節點。這是 retry 迴圈的視覺形狀。
    const startY = fromTop + BOX_H / 2;
    const endY = toTop + BOX_H / 2;
    const outX = PAD_X - LOOP_OUT;
    root.appendChild(svg('path', {
      d: `M ${PAD_X} ${startY} C ${outX} ${startY}, ${outX} ${endY}, ${PAD_X} ${endY}`,
      class: 'graph-edge graph-edge--back',
      fill: 'none', 'marker-end': 'url(#gx-arrow-back)',
    }));
  }

  order.forEach((name, index) => {
    const top = topOf(index);
    const count = visits.get(name) || 0;
    const isDone = done.has(name);
    const isRunning = !isDone && !runFinished;

    const group = svg('g', { class: 'graph-node' });
    group.appendChild(svg('rect', {
      x: PAD_X, y: top, width: BOX_W, height: BOX_H, rx: 8,
      class: 'graph-node-box'
        + (isRunning ? ' graph-node-box--running' : '')
        + (isDone ? ' graph-node-box--done' : ''),
    }));
    group.appendChild(svg('circle', {
      cx: PAD_X + 18, cy: top + BOX_H / 2, r: 4,
      class: 'graph-node-dot'
        + (isRunning ? ' graph-node-dot--running' : ' graph-node-dot--done'),
    }));

    // 節點名稱來自使用者的程式碼 → textContent，且長名字截斷避免撐破框
    const label = svg('text', {
      x: PAD_X + 34, y: top + BOX_H / 2 + 5, class: 'graph-node-label',
    });
    label.textContent = name.length > 26 ? name.slice(0, 25) + '…' : name;
    const title = svg('title');
    title.textContent = name;            // 完整名稱放 tooltip
    label.appendChild(title);
    group.appendChild(label);

    if (count > 1) {
      const badge = svg('text', {
        x: PAD_X + BOX_W - 16, y: top + BOX_H / 2 + 5,
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
      legend.append('。左側弧線是回頭的邊 —— 那是 retry 迴圈。');
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
