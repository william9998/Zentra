// api.js —— specs/api-contract.md v0.3.1 的唯一出口（ui-contract §6.2）。
//
// 規則：契約的欄位名（snake_case）**只准出現在這個檔案裡**。
// 這一層把回應正規化成 Console 自己的 camelCase view model，頁面拿到的是那個物件。
// 沒有型別檢查（ADR-0006 的已知代價），所以這層的紀律是唯一的防線。
//
// Agent / Version / Run 的原始欄位只在這一層出現。元件只拿 camelCase view model，
// 也不能在這一層偷偷補 trigger、image 或任何 container runtime 控制欄位。

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

export const VERSION_STATUS = Object.freeze({
  BUILDING: 'BUILDING',
  PUBLISHED: 'PUBLISHED',
  FAILED: 'FAILED',
});

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
  VERSION_CONFLICT: 'VERSION_CONFLICT',
  BUILD_FAILED: 'BUILD_FAILED',
  SANDBOX_TIMEOUT: 'SANDBOX_TIMEOUT',
  SANDBOX_ERROR: 'SANDBOX_ERROR',
  LLM_ERROR: 'LLM_ERROR',
  EGRESS_BLOCKED: 'EGRESS_BLOCKED',
  USER_CODE_ERROR: 'USER_CODE_ERROR',
  INTERNAL_ERROR: 'INTERNAL_ERROR',
});

function errorField(details) {
  if (!details || !Array.isArray(details.fields) || details.fields.length === 0) return null;
  const location = details.fields[0] && details.fields[0].loc;
  if (!Array.isArray(location) || location.length === 0) return null;
  const field = location[location.length - 1];
  if (field === 'image_ref') return 'imageRef';
  if (field === 'agent_id') return 'agentId';
  return typeof field === 'string' ? field : null;
}

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
    this.field = errorField(this.details);
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

const jsonPost = (body) => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

// ---- 正規化：契約欄位名到此為止 ----

function viewError(raw) {
  if (!raw || typeof raw !== 'object') return null;
  return {
    code: typeof raw.code === 'string' ? raw.code : ERROR_CODE.INTERNAL_ERROR,
    message: typeof raw.message === 'string' ? raw.message : '',
    runId: typeof raw.run_id === 'string' ? raw.run_id : null,
    retryable: raw.retryable === true,
    details: raw.details && typeof raw.details === 'object' ? raw.details : {},
    field: errorField(raw.details),
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

function viewRunSummary(raw) {
  return {
    runId: raw.run_id,
    agentId: raw.agent_id,
    agentVersion: raw.agent_version,
    status: raw.status,
    trigger: raw.trigger,
    createdAt: toDate(raw.created_at),
    startedAt: toDate(raw.started_at),
    finishedAt: toDate(raw.finished_at),
  };
}

function viewEndpointSet(raw) {
  const safe = raw && typeof raw === 'object' ? raw : {};
  return {
    rest: typeof safe.rest === 'string' ? safe.rest : '',
    mcp: typeof safe.mcp === 'string' ? safe.mcp : '',
    openapi: typeof safe.openapi === 'string' ? safe.openapi : '',
  };
}

function viewAgent(raw) {
  return {
    agentId: raw.agent_id,
    name: raw.name,
    description: raw.description,
    latestPublishedVersion: raw.latest_published_version,
    endpoints: viewEndpointSet(raw.endpoints),
    createdAt: toDate(raw.created_at),
    updatedAt: toDate(raw.updated_at),
  };
}

function viewArtifact(raw) {
  const safe = raw && typeof raw === 'object' ? raw : {};
  return {
    imageRef: typeof safe.image_ref === 'string' ? safe.image_ref : '',
    digest: typeof safe.digest === 'string' ? safe.digest : '',
    platform: typeof safe.platform === 'string' ? safe.platform : null,
    sizeBytes: typeof safe.size_bytes === 'number' ? safe.size_bytes : null,
    runtimeContract: typeof safe.runtime_contract === 'string' ? safe.runtime_contract : null,
  };
}

function viewEntrypoint(raw) {
  const safe = raw && typeof raw === 'object' ? raw : {};
  return {
    name: typeof safe.name === 'string' ? safe.name : '',
    description: typeof safe.description === 'string' ? safe.description : '',
    inputSchema: safe.input_schema && typeof safe.input_schema === 'object' ? safe.input_schema : {},
    outputSchema: safe.output_schema && typeof safe.output_schema === 'object' ? safe.output_schema : {},
  };
}

function viewVersion(raw) {
  return {
    agentVersionId: raw.agent_version_id,
    semver: raw.semver,
    versionId: raw.version_id,
    sourceType: raw.source_type,
    artifact: viewArtifact(raw.artifact),
    status: raw.status,
    error: viewError(raw.error),
    manifest: raw.manifest && typeof raw.manifest === 'object' ? raw.manifest : null,
    entrypoints: Array.isArray(raw.entrypoints) ? raw.entrypoints.map(viewEntrypoint) : null,
    createdAt: toDate(raw.created_at),
    publishedAt: toDate(raw.published_at),
  };
}

function viewPage(raw, itemView) {
  return {
    items: Array.isArray(raw.items) ? raw.items.map(itemView) : [],
    nextCursor: typeof raw.next_cursor === 'string' ? raw.next_cursor : null,
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

export async function listRuns(query = {}) {
  const params = new URLSearchParams();
  const set = (key, value) => {
    if (value !== null && value !== undefined && value !== '') params.set(key, value);
  };
  set('agent_id', query.agentId);
  set('status', query.status);
  set('trigger', query.trigger);
  set('created_after', query.createdAfter);
  set('created_before', query.createdBefore);
  set('limit', query.limit);
  set('cursor', query.cursor);
  const suffix = params.toString();
  return viewPage(await req('/runs' + (suffix ? '?' + suffix : '')), viewRunSummary);
}

export async function cancelRun(runId) {
  return viewRun(await req('/runs/' + encodeURIComponent(runId) + '/cancel', { method: 'POST' }));
}

export async function listAgents(query = {}) {
  const params = new URLSearchParams();
  if (query.limit !== null && query.limit !== undefined) params.set('limit', query.limit);
  if (query.cursor) params.set('cursor', query.cursor);
  const suffix = params.toString();
  return viewPage(await req('/agents' + (suffix ? '?' + suffix : '')), viewAgent);
}

export async function createAgent(body) {
  return viewAgent(await req('/agents', jsonPost({
    name: body.name,
    description: body.description || null,
  })));
}

export async function getAgent(agentId) {
  return viewAgent(await req('/agents/' + encodeURIComponent(agentId)));
}

export async function listAgentVersions(agentId) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/versions');
  if (Array.isArray(raw)) return { items: raw.map(viewVersion), nextCursor: null };
  return viewPage(raw, viewVersion);
}

export async function getAgentVersion(agentId, semver) {
  return viewVersion(await req('/agents/' + encodeURIComponent(agentId)
    + '/versions/' + encodeURIComponent(semver)));
}

export async function registerAgentVersion(agentId, body) {
  return viewVersion(await req('/agents/' + encodeURIComponent(agentId) + '/versions', jsonPost({
    semver: body.semver,
    image_ref: body.imageRef,
  })));
}

/** The body is deliberately closed over these three keys. No trigger/header discriminator is sent. */
export async function createRun(agentId, body) {
  return viewCreatedRun(await req('/agents/' + encodeURIComponent(agentId) + '/runs', jsonPost({
    version: body.version,
    entrypoint: body.entrypoint,
    input: body.input,
  })));
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
