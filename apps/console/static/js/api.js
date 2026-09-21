// api.js —— specs/api-contract.md v0.2.2 的唯一出口（ui-contract §6.2）。
//
// 規則：契約的欄位名（snake_case）**只准出現在這個檔案裡**。
// 這一層把回應正規化成 Console 自己的 camelCase view model，頁面拿到的是那個物件。
// 沒有型別檢查（ADR-0006 的已知代價），所以這層的紀律是唯一的防線。
//
// ZEN-006A 只用得到三個真的存在的端點：
//   POST /v1/agents/{agent_id}/runs · GET /v1/runs/{run_id} · GET /v1/runs/{run_id}/events
// 其餘（GET /v1/runs 列表、cancel、agents、版本）尚未實作，這裡刻意不提供，
// 免得頁面不小心打出 404。

const BASE = '/v1';

/** SPEC §9 的 Run 狀態機（唯一定義）。不得自創，不得加回 WAITING_INPUT。 */
export const RUN_STATUS = Object.freeze({
  QUEUED: 'QUEUED',
  SCHEDULING: 'SCHEDULING',
  RUNNING: 'RUNNING',
  SUCCEEDED: 'SUCCEEDED',
  FAILED: 'FAILED',
  CANCELLED: 'CANCELLED',
});

const TERMINAL_STATUSES = Object.freeze([
  RUN_STATUS.SUCCEEDED, RUN_STATUS.FAILED, RUN_STATUS.CANCELLED,
]);

export const isTerminalStatus = (status) => TERMINAL_STATUSES.indexOf(status) !== -1;

/** api-contract §1 的 SSE 事件型別枚舉（7 種）。 */
export const EVENT_TYPE = Object.freeze({
  RUN_STATUS: 'run.status',
  NODE_START: 'node.start',
  NODE_END: 'node.end',
  TOKEN: 'token',
  LOG: 'log',
  CUSTOM: 'custom',
  ERROR: 'error',
});

/** api-contract §0 的錯誤 code 枚舉。不得自創，要加先改契約。 */
export const ERROR_CODE = Object.freeze({
  UNAUTHENTICATED: 'UNAUTHENTICATED',
  PERMISSION_DENIED: 'PERMISSION_DENIED',
  NOT_FOUND: 'NOT_FOUND',
  VALIDATION_ERROR: 'VALIDATION_ERROR',
  QUOTA_EXCEEDED: 'QUOTA_EXCEEDED',
  AGENT_NOT_PUBLISHED: 'AGENT_NOT_PUBLISHED',
  BUILD_FAILED: 'BUILD_FAILED',
  SANDBOX_TIMEOUT: 'SANDBOX_TIMEOUT',
  SANDBOX_ERROR: 'SANDBOX_ERROR',
  LLM_ERROR: 'LLM_ERROR',
  EGRESS_BLOCKED: 'EGRESS_BLOCKED',
  USER_CODE_ERROR: 'USER_CODE_ERROR',
  INTERNAL_ERROR: 'INTERNAL_ERROR',
});

/**
 * ZEN-000 的內建 agent fixture id（apps/api/enums.py BUILTIN_AGENT_ID）。
 * 還沒有 Agent Registry，`GET /v1/agents` 也不存在，所以 Console 先寫在契約層這一個地方。
 * ZEN-001 / ZEN-006 有真正的 Agent API 之後刪掉。
 */
export const BUILTIN_AGENT_ID = '01930000-0000-7000-8000-000000000001';

/** 統一錯誤物件：code / message / runId / retryable / details 都保證存在。 */
export class ApiError extends Error {
  constructor(error) {
    const safe = error && typeof error === 'object' ? error : {};
    super(typeof safe.message === 'string' && safe.message ? safe.message : '未知錯誤');
    this.name = 'ApiError';
    this.code = typeof safe.code === 'string' ? safe.code : ERROR_CODE.INTERNAL_ERROR;
    this.runId = typeof safe.run_id === 'string' ? safe.run_id : null;
    this.retryable = safe.retryable === true;
    this.details = safe.details && typeof safe.details === 'object' ? safe.details : {};
  }
}

/** 契約：所有時間都是 RFC 3339 UTC。少了時區標記就補 Z，避免被當成本地時間解析。 */
function toDate(value) {
  if (typeof value !== 'string' || value === '') return null;
  const normalized = /(Z|[+-]\d{2}:?\d{2})$/.test(value) ? value : value + 'Z';
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function toApiError(raw) {
  return new ApiError(raw);
}

/**
 * 前端自己產生的錯誤（例如網址沒有 run_id）也走同一個形狀，
 * 頁面才不需要手寫契約的 snake_case 欄位名。
 */
export function clientError(code, message, runId, retryable) {
  return toApiError({
    code, message, run_id: runId || null, retryable: retryable === true, details: {},
  });
}

async function req(path, options = {}) {
  let response;
  try {
    response = await fetch(BASE + path, options);
  } catch (cause) {
    throw toApiError({
      code: ERROR_CODE.INTERNAL_ERROR,
      message: '無法連線到 API：' + (cause && cause.message ? cause.message : '網路錯誤'),
      retryable: true,
    });
  }
  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch (_) {
      body = null;
    }
    if (body && body.error) throw toApiError(body.error);
    throw toApiError({
      code: ERROR_CODE.INTERNAL_ERROR,
      message: 'API 回應 HTTP ' + response.status + '，且不是契約定義的錯誤格式',
      retryable: response.status >= 500,
    });
  }
  return response.status === 204 ? null : response.json();
}

// ---- 正規化：契約欄位名到此為止 ----

function viewError(raw) {
  if (!raw || typeof raw !== 'object') return null;
  return {
    code: typeof raw.code === 'string' ? raw.code : ERROR_CODE.INTERNAL_ERROR,
    message: typeof raw.message === 'string' ? raw.message : '',
    runId: typeof raw.run_id === 'string' ? raw.run_id : null,
    retryable: raw.retryable === true,
    details: raw.details && typeof raw.details === 'object' ? raw.details : {},
  };
}

function viewUsage(raw) {
  if (!raw || typeof raw !== 'object') return null;
  return {
    llmCalls: raw.llm_calls,
    inputTokens: raw.input_tokens,
    outputTokens: raw.output_tokens,
    // decimal 字串，**不要**做浮點運算，直接印。
    costUsd: raw.cost_usd,
  };
}

function viewRun(raw) {
  return {
    runId: raw.run_id,
    agentId: raw.agent_id,
    agentVersion: raw.agent_version,
    status: raw.status,
    trigger: raw.trigger,
    input: raw.input,
    output: raw.output,
    error: viewError(raw.error),
    usage: viewUsage(raw.usage),
    createdAt: toDate(raw.created_at),
    startedAt: toDate(raw.started_at),
    finishedAt: toDate(raw.finished_at),
  };
}

function viewCreatedRun(raw) {
  return {
    runId: raw.run_id,
    agentId: raw.agent_id,
    agentVersion: raw.agent_version,
    status: raw.status,
    createdAt: toDate(raw.created_at),
    eventsUrl: raw.events_url,
  };
}

/**
 * SSE 事件 payload 的正規化。
 * 契約 §1 定義 7 種型別，ZEN-000 目前只會送出 run.status / node.start / node.end；
 * 其餘型別（token / log / custom / error）的欄位先照契約範例對應，收到也不會壞。
 * `payload` 保留原始物件供時間軸展開顯示。
 */
function viewEvent(type, seq, data) {
  const payload = data && typeof data === 'object' ? data : {};
  const str = (value) => (typeof value === 'string' ? value : null);
  return {
    seq,
    type,
    time: toDate(payload.ts),
    node: str(payload.node),
    status: str(payload.status),
    level: str(payload.level),
    message: str(payload.message),
    delta: str(payload.delta),
    name: str(payload.name),
    code: str(payload.code),
    payload,
  };
}

// ---- 端點 ----

/** GET /v1/runs/{run_id} —— 完整 Run 物件。 */
export async function getRun(runId) {
  return viewRun(await req('/runs/' + encodeURIComponent(runId)));
}

/**
 * POST /v1/agents/{agent_id}/runs —— 建立 Run（202）。
 *
 * ZEN-006A 限定版：內建 agent 只吃 entrypoint "default" 與字串 input.question，
 * 其他一律 422。ZEN-006 有 Agent / 版本 API 之後，這裡換成通用的 createRun(agentId, ...)。
 */
export async function createBuiltinRun(question) {
  const body = { entrypoint: 'default', input: { question } };
  const raw = await req('/agents/' + encodeURIComponent(BUILTIN_AGENT_ID) + '/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return viewCreatedRun(raw);
}

/**
 * GET /v1/runs/{run_id}/events —— SSE。
 *
 * handlers: { onEvent(event), onStatus(status), onOpen(), onReconnecting(), onDone(status), onFatal(apiError) }
 * 回傳 { close() }，呼叫端**必須**在 destroy 時呼叫 close()。
 *
 * 兩件必須做的事（ZEN-006A 後端現況）：
 *  1. 伺服器在終態事件後直接結束串流，而這個端點目前忽略 Last-Event-ID（ZEN-004 才做續傳）。
 *     收到終態 run.status 要立刻 close()，否則 EventSource 會自動重連並從 seq 0 重播一整串。
 *  2. 重連會重播 → 一律用 SSE 的 id（= DB seq，整數）去重。
 */
export function subscribeRunEvents(runId, handlers = {}) {
  const source = new EventSource(BASE + '/runs/' + encodeURIComponent(runId) + '/events');
  const seen = new Set();
  let closed = false;

  const call = (name, arg) => {
    if (typeof handlers[name] === 'function') handlers[name](arg);
  };

  const close = () => {
    if (closed) return;
    closed = true;
    source.close();
  };

  const onServerEvent = (type) => (nativeEvent) => {
    if (closed) return;
    // EventSource 自己的連線錯誤事件也叫 'error'，和契約的 error 事件型別撞名。
    // 真正的 SSE 訊息是 MessageEvent（有字串 data），連線錯誤沒有 —— 用這個區分，
    // 否則每次斷線都會在時間軸上多長出一列空的 error。
    if (typeof nativeEvent.data !== 'string') return;
    const seq = Number(nativeEvent.lastEventId);
    if (Number.isFinite(seq)) {
      if (seen.has(seq)) return;   // 重播去重
      seen.add(seq);
    }
    let data = {};
    try {
      data = JSON.parse(nativeEvent.data);
    } catch (_) {
      data = {};
    }
    const event = viewEvent(type, Number.isFinite(seq) ? seq : null, data);
    call('onEvent', event);
    if (type === EVENT_TYPE.RUN_STATUS && event.status) {
      call('onStatus', event.status);
      if (isTerminalStatus(event.status)) {
        close();                   // 先關連線，再通知，避免自動重連
        call('onDone', event.status);
      }
    }
  };

  for (const type of Object.values(EVENT_TYPE)) {
    source.addEventListener(type, onServerEvent(type));
  }

  source.onopen = () => {
    if (!closed) call('onOpen');
  };

  source.onerror = () => {
    if (closed) return;
    if (source.readyState === EventSource.CLOSED) {
      // 瀏覽器放棄重連（例如 404）—— 不要吞掉，交給頁面顯示。
      closed = true;
      call('onFatal', toApiError({
        code: ERROR_CODE.INTERNAL_ERROR,
        message: '事件串流中斷且無法重新連線',
        run_id: runId,
        retryable: true,
      }));
      return;
    }
    call('onReconnecting');
  };

  return { close };
}
