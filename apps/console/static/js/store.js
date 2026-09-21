// store.js —— ui-contract §6.1 的那個「約 40 行發布／訂閱」。
// 只有 run.html 需要：一份 Run 狀態要同時餵給狀態列、結果卡、用量卡。
// 沒有響應式、沒有 diff —— 訂閱者收到整份狀態，自己整塊重繪。

export function createStore(initialState) {
  let state = initialState;
  const subscribers = new Set();

  const notify = () => {
    for (const subscriber of Array.from(subscribers)) subscriber(state);
  };

  return {
    get() {
      return state;
    },

    /** patch 可以是物件（淺層合併）或 (prev) => next 函式。 */
    set(patch) {
      const next = typeof patch === 'function' ? patch(state) : patch;
      state = Object.assign({}, state, next);
      notify();
    },

    /** 立刻用目前狀態呼叫一次，回傳取消訂閱函式。 */
    subscribe(subscriber) {
      subscribers.add(subscriber);
      subscriber(state);
      return () => subscribers.delete(subscriber);
    },

    /** 頁面 destroy 時呼叫：切斷所有訂閱，不留下指向已移除 DOM 的 closure。 */
    clear() {
      subscribers.clear();
    },
  };
}
