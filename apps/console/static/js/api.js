// api.js —— specs/api-contract.md v0.8.0 的唯一出口（ui-contract §6.2）。
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
  EGRESS_FAILED: 'EGRESS_FAILED',
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

const jsonBody = (method, body) => ({
  method,
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
});

const jsonPost = (body) => jsonBody('POST', body);
const jsonPatch = (body) => jsonBody('PATCH', body);
const jsonPut = (body) => jsonBody('PUT', body);

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
    agentName: raw.agent_name,
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

/**
 * PATCH /v1/agents/{id} —— 只改 metadata（name / description）。
 *
 * 契約是 partial update：**沒送的欄位維持原值**，所以呼叫端只放真的改了的鍵，
 * 不要補一份完整物件。空 body 後端回 422（至少要一個欄位），由呼叫端先擋。
 * 版本、image digest 與已發布的行為都不在這裡 —— 那些發布後不可變（P5）。
 */
export async function updateAgent(agentId, changes) {
  const body = {};
  if (changes.name !== undefined) body.name = changes.name;
  if (changes.description !== undefined) body.description = changes.description || null;
  return viewAgent(await req('/agents/' + encodeURIComponent(agentId), jsonPatch(body)));
}

/** DELETE /v1/agents/{id} —— 軟刪除，回 204（沒有 body）。 */
export async function deleteAgent(agentId) {
  await req('/agents/' + encodeURIComponent(agentId), { method: 'DELETE' });
}

/** LLM 憑證的 metadata。**契約保證這裡不會有金鑰**，只有尾四碼（api-contract §2.1）。 */
function viewLlmCredential(raw) {
  if (!raw || typeof raw !== 'object') return { configured: false };
  return {
    configured: Boolean(raw.configured),
    lastFour: raw.last_four || null,
    baseUrl: raw.base_url || null,
    defaultModel: raw.default_model || null,
    updatedAt: toDate(raw.updated_at),
  };
}

/** GET /v1/agents/{id}/llm-credential —— 只回 metadata，讀不到金鑰。 */
export async function getAgentLlmCredential(agentId) {
  const path = '/agents/' + encodeURIComponent(agentId) + '/llm-credential';
  return viewLlmCredential(await req(path));
}

/**
 * PUT /v1/agents/{id}/llm-credential —— 註冊或覆寫。
 *
 * **覆寫立刻對該 Agent 的所有版本生效**（ADR-0011）。憑證綁 Agent 不綁版本，
 * 所以路徑裡沒有 semver。
 */
export async function setAgentLlmCredential(agentId, values) {
  const body = { api_key: values.apiKey };
  if (values.baseUrl) body.base_url = values.baseUrl;
  if (values.defaultModel) body.default_model = values.defaultModel;
  const path = '/agents/' + encodeURIComponent(agentId) + '/llm-credential';
  return viewLlmCredential(await req(path, jsonPut(body)));
}

/** DELETE /v1/agents/{id}/llm-credential —— 移除後退回用平台預設金鑰。 */
export async function deleteAgentLlmCredential(agentId) {
  await req('/agents/' + encodeURIComponent(agentId) + '/llm-credential', { method: 'DELETE' });
}

/** 常駐容器的狀態（api-contract §2.2）。 */
function viewContainer(raw) {
  return {
    containerId: raw.container_id,
    agentId: raw.agent_id,
    agentVersion: raw.agent_version,
    status: raw.status,
    inflightRuns: raw.inflight_runs,
    maxConcurrentRuns: raw.max_concurrent_runs,
    // 一直重啟的容器是壞掉了，不是需要再重啟一次 —— 沒有這個數字看不出差別
    restartCount: raw.restart_count,
    startedAt: toDate(raw.started_at),
    lastHealthAt: toDate(raw.last_health_at),
    error: raw.error || null,
  };
}

export async function listAgentContainers(agentId) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/containers');
  return (raw && raw.items ? raw.items : []).map(viewContainer);
}

/** POST /v1/agents/{id}/containers —— 回 202，狀態先是 STARTING。 */
export async function startAgentContainer(agentId, version) {
  const body = version ? { version } : {};
  return viewContainer(await req(
    '/agents/' + encodeURIComponent(agentId) + '/containers', jsonPost(body)));
}

export async function restartAgentContainer(agentId, containerId) {
  return viewContainer(await req('/agents/' + encodeURIComponent(agentId)
    + '/containers/' + encodeURIComponent(containerId) + '/restart', { method: 'POST' }));
}

export async function stopAgentContainer(agentId, containerId) {
  return viewContainer(await req('/agents/' + encodeURIComponent(agentId)
    + '/containers/' + encodeURIComponent(containerId), { method: 'DELETE' }));
}

/**
 * 下游 API key 的 metadata（api-contract §2.3）。
 *
 * **契約保證這裡沒有 key 本身** —— 只有 `key_prefix` 這幾碼辨識用。
 * 唯一有明文的地方是 `issueAgentApiKey()` 的回傳，而且只有那一次。
 */
function viewAgentKey(raw) {
  return {
    keyId: raw.key_id,
    name: raw.name,
    keyPrefix: raw.key_prefix,
    createdAt: toDate(raw.created_at),
    lastUsedAt: toDate(raw.last_used_at),
    revokedAt: toDate(raw.revoked_at),
  };
}

/** GET /v1/agents/{id}/api-keys —— **含已撤銷的**，歷史用量還掛在它們身上。 */
export async function listAgentApiKeys(agentId) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/api-keys');
  return (raw && raw.items ? raw.items : []).map(viewAgentKey);
}

/**
 * POST /v1/agents/{id}/api-keys —— 發一把新的。
 *
 * `plaintext` **只有這一次拿得到**。平台只存雜湊，所以沒有「再看一次」的端點，
 * 呼叫端沒抄下來就只能重發一把。
 */
export async function issueAgentApiKey(agentId, name) {
  const raw = await req(
    '/agents/' + encodeURIComponent(agentId) + '/api-keys', jsonPost({ name }));
  return Object.assign(viewAgentKey(raw), { plaintext: raw.key });
}

/** DELETE /v1/agents/{id}/api-keys/{key_id} —— 軟撤銷，回 200 帶 revoked_at。 */
export async function revokeAgentApiKey(agentId, keyId) {
  return viewAgentKey(await req('/agents/' + encodeURIComponent(agentId)
    + '/api-keys/' + encodeURIComponent(keyId), { method: 'DELETE' }));
}

/**
 * MCP key 的 metadata（api.md §MCP key）。**沒有 key 本身** —— 明文只在
 * `issueMcpKey()` 的回傳裡出現一次。
 */
function viewMcpKey(raw) {
  return {
    keyId: raw.key_id,
    name: raw.name,
    keyPrefix: raw.key_prefix,
    agentIds: Array.isArray(raw.agent_ids) ? raw.agent_ids : [],
    createdAt: toDate(raw.created_at),
    lastUsedAt: toDate(raw.last_used_at),
    revokedAt: toDate(raw.revoked_at),
  };
}

/** GET /v1/mcp-keys —— `endpoint` 是對端要連的網址；items 含已撤銷的。 */
export async function listMcpKeys() {
  const raw = await req('/mcp-keys');
  return {
    endpoint: typeof raw.endpoint === 'string' ? raw.endpoint : '',
    items: (raw.items || []).map(viewMcpKey),
  };
}

/** POST /v1/mcp-keys —— `plaintext` 只有這一次拿得到。 */
export async function issueMcpKey(name, agentIds) {
  const raw = await req('/mcp-keys', jsonPost({ name, agent_ids: agentIds }));
  return Object.assign(viewMcpKey(raw), { plaintext: raw.key });
}

/** PUT /v1/mcp-keys/{id}/agents —— 整份取代勾選。 */
export async function setMcpKeyAgents(keyId, agentIds) {
  return viewMcpKey(await req('/mcp-keys/' + encodeURIComponent(keyId) + '/agents',
    jsonPut({ agent_ids: agentIds })));
}

/** DELETE /v1/mcp-keys/{id} —— 軟撤銷，回 200 帶 revoked_at。 */
export async function revokeMcpKey(keyId) {
  return viewMcpKey(await req('/mcp-keys/' + encodeURIComponent(keyId), { method: 'DELETE' }));
}

/** §3.3 的篩選條件。空值一律不送，免得 `?model=` 被當成「model 是空字串」。 */
function usageParams(query = {}) {
  const params = new URLSearchParams();
  const set = (key, value) => {
    if (value !== null && value !== undefined && value !== '') params.set(key, value);
  };
  set('api_key_id', query.keyId);
  set('since', query.since);
  set('until', query.until);
  set('model', query.model);
  set('credential_source', query.credentialSource);
  set('status_code', query.statusCode);
  if (query.errorsOnly) params.set('errors_only', 'true');
  set('limit', query.limit);
  set('cursor', query.cursor);
  return params.toString();
}

/** GET /v1/agents/{id}/usage —— 總計、依 key、依 model 三個切面一次回。 */
export async function getAgentUsage(agentId, query = {}) {
  const suffix = usageParams(query);
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/usage'
    + (suffix ? '?' + suffix : ''));
  return {
    totals: viewUsage(raw.totals),
    // key_id 為 null 的那一列是 Console 發起、沒歸戶的用量。**不要濾掉它** ——
    // 那通常正是在找的東西。
    byKey: (raw.by_key || []).map((row) => Object.assign(viewUsage(row), {
      keyId: row.key_id, name: row.name, keyPrefix: row.key_prefix,
    })),
    byModel: (raw.by_model || []).map((row) => Object.assign(viewUsage(row), {
      model: row.model,
    })),
  };
}

/** 一次 LLM 呼叫。帶著 runId，所以查到哪筆錯就能直接跳過去。 */
function viewLlmCall(raw) {
  return {
    callId: raw.call_id,
    runId: raw.run_id,
    runStatus: raw.run_status,
    keyId: raw.api_key_id,
    model: raw.model,
    inputTokens: raw.input_tokens,
    outputTokens: raw.output_tokens,
    costUsd: raw.cost_usd,
    latencyMs: raw.latency_ms,
    statusCode: raw.status_code,
    error: raw.error,
    credentialSource: raw.credential_source,
    createdAt: toDate(raw.created_at),
  };
}

/** GET /v1/agents/{id}/llm-calls —— 逐筆，最新的在前。 */
export async function listAgentLlmCalls(agentId, query = {}) {
  const suffix = usageParams(query);
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/llm-calls'
    + (suffix ? '?' + suffix : ''));
  return {
    items: (raw.items || []).map(viewLlmCall),
    nextCursor: raw.next_cursor || null,
  };
}

// ── ZEN-019：設定・機密・對外能力・稽核（api-contract §2.6–§2.10）────────
//
// 三層的界線在畫面上要看得出來，所以這裡刻意不共用 view model：
//   設定  明文，會完整進沙盒
//   機密  只回名字與尾四碼，值永遠讀不回來
//   能力  平台願意替這個 Agent 送到哪裡去

/** api-contract §4.3 的 `details.reason`。**這張表只服務 EGRESS_BLOCKED。** */
export const EGRESS_REASON = Object.freeze({
  NO_RULES: 'NO_RULES',
  HOST_NOT_ALLOWED: 'HOST_NOT_ALLOWED',
  METHOD_NOT_ALLOWED: 'METHOD_NOT_ALLOWED',
  PATH_NOT_ALLOWED: 'PATH_NOT_ALLOWED',
  SCHEME_NOT_ALLOWED: 'SCHEME_NOT_ALLOWED',
  REDIRECT: 'REDIRECT',
  REQUEST_TOO_LARGE: 'REQUEST_TOO_LARGE',
  RESPONSE_TOO_LARGE: 'RESPONSE_TOO_LARGE',
  RUN_CALL_LIMIT: 'RUN_CALL_LIMIT',
  SENDER_NOT_CONFIGURED: 'SENDER_NOT_CONFIGURED',
  RECIPIENT_NOT_ALLOWED: 'RECIPIENT_NOT_ALLOWED',
  BODY_TOO_LARGE: 'BODY_TOO_LARGE',
  RUN_EMAIL_LIMIT: 'RUN_EMAIL_LIMIT',
  HOURLY_EMAIL_LIMIT: 'HOURLY_EMAIL_LIMIT',
  SMTP_AUTH: 'SMTP_AUTH',
  TLS_REQUIRED: 'TLS_REQUIRED',
});

/**
 * `409 VERSION_CONFLICT` 的 `details.referenced_by`（api-contract §2.7 第 6 點）。
 *
 * **錯誤的 details 也是契約的一部分**，所以它的欄位名也只能在這個檔案裡出現 ——
 * 不然「刪不掉的機密被誰引用」那段畫面就繞過了這一層。
 */
export function viewSecretReferences(error) {
  const raw = error && error.details && error.details.referenced_by;
  if (!Array.isArray(raw)) return [];
  return raw.map((item) => ({
    kind: item && item.kind,
    ruleId: item && item.rule_id,
    // 出口規則顯示主機、寄件者顯示 SMTP 主機 —— 對使用者來說都是「哪一個」。
    label: (item && (item.host || item.smtp_host)) || null,
  }));
}

/** GET /v1/agents/{id}/config —— 未設定過時 `{}` 與 `null`（key 一定在）。 */
export async function getAgentConfig(agentId) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/config');
  return {
    config: raw && raw.config ? raw.config : {},
    updatedAt: toDate(raw && raw.updated_at),
  };
}

/**
 * PUT /v1/agents/{id}/config —— **整份取代**，沒有 PATCH。
 *
 * body 是 `{"config": {...}}`，不是裸物件。
 */
export async function putAgentConfig(agentId, config) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/config',
    jsonPut({ config }));
  return { config: raw.config || {}, updatedAt: toDate(raw.updated_at) };
}

/** 機密的 metadata。**契約保證這裡沒有值，也沒有密文。** */
function viewSecret(raw) {
  return {
    name: raw.name,
    // 唯一看得到的線索。值長度 < 4 時是等長的 `*` 遮罩（§2.7 第 7 點）。
    lastFour: raw.last_four,
    createdAt: toDate(raw.created_at),
    updatedAt: toDate(raw.updated_at),
  };
}

export async function listAgentSecrets(agentId) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/secrets');
  return (raw && raw.items ? raw.items : []).map(viewSecret);
}

/** PUT /v1/agents/{id}/secrets/{name} —— 新建與覆寫都回 200。 */
export async function putAgentSecret(agentId, name, value) {
  return viewSecret(await req('/agents/' + encodeURIComponent(agentId)
    + '/secrets/' + encodeURIComponent(name), jsonPut({ value })));
}

/** DELETE —— 還被引用時後端回 409，`details.referenced_by` 說得出是誰。 */
export async function deleteAgentSecret(agentId, name) {
  await req('/agents/' + encodeURIComponent(agentId)
    + '/secrets/' + encodeURIComponent(name), { method: 'DELETE' });
}

function viewEgressRule(raw) {
  return {
    ruleId: raw.rule_id,
    agentId: raw.agent_id,
    host: raw.host,
    methods: raw.methods || [],
    pathPrefix: raw.path_prefix,
    // 帶認證時顯示的是**名字**，值不存在於任何回應裡。
    authSecretName: raw.auth_secret_name,
    authHeader: raw.auth_header,
    description: raw.description,
    createdAt: toDate(raw.created_at),
    updatedAt: toDate(raw.updated_at),
  };
}

function egressRuleBody(values) {
  return {
    host: values.host,
    methods: values.methods,
    path_prefix: values.pathPrefix || null,
    auth_secret_name: values.authSecretName || null,
    auth_header: values.authHeader || null,
    description: values.description || null,
  };
}

/** GET /v1/agents/{id}/egress-rules —— **空清單表示一個外部主機都連不到。** */
export async function listEgressRules(agentId) {
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/egress-rules');
  return (raw && raw.items ? raw.items : []).map(viewEgressRule);
}

export async function createEgressRule(agentId, values) {
  return viewEgressRule(await req('/agents/' + encodeURIComponent(agentId)
    + '/egress-rules', jsonPost(egressRuleBody(values))));
}

export async function updateEgressRule(agentId, ruleId, values) {
  return viewEgressRule(await req('/agents/' + encodeURIComponent(agentId)
    + '/egress-rules/' + encodeURIComponent(ruleId), jsonPut(egressRuleBody(values))));
}

export async function deleteEgressRule(agentId, ruleId) {
  await req('/agents/' + encodeURIComponent(agentId)
    + '/egress-rules/' + encodeURIComponent(ruleId), { method: 'DELETE' });
}

/** 寄件者。**沒有密碼欄位** —— 只有指向機密的名字。 */
function viewEmailSender(raw) {
  if (!raw || typeof raw !== 'object') return { configured: false };
  return {
    configured: Boolean(raw.configured),
    smtpHost: raw.smtp_host,
    smtpPort: raw.smtp_port,
    username: raw.username,
    fromAddress: raw.from_address,
    passwordSecretName: raw.password_secret_name,
    allowedRecipients: raw.allowed_recipients || [],
    updatedAt: toDate(raw.updated_at),
  };
}

export async function getEmailSender(agentId) {
  return viewEmailSender(await req('/agents/' + encodeURIComponent(agentId)
    + '/email-sender'));
}

export async function putEmailSender(agentId, values) {
  return viewEmailSender(await req('/agents/' + encodeURIComponent(agentId)
    + '/email-sender', jsonPut({
      smtp_host: values.smtpHost,
      smtp_port: Number(values.smtpPort),
      username: values.username,
      from_address: values.fromAddress,
      password_secret_name: values.passwordSecretName,
      allowed_recipients: values.allowedRecipients,
    })));
}

export async function deleteEmailSender(agentId) {
  await req('/agents/' + encodeURIComponent(agentId) + '/email-sender',
    { method: 'DELETE' });
}

/** 一列出口稽核（§2.10）。HTTP 與寄信共用，用 `kind` 分。 */
function viewEgressCall(raw) {
  return {
    callId: raw.call_id,
    runId: raw.run_id,
    kind: raw.kind,
    ruleId: raw.rule_id,
    method: raw.method,
    host: raw.host,
    // **不含 query string。** kind=EMAIL 時這裡是收件人。
    path: raw.path,
    statusCode: raw.status_code,
    requestBytes: raw.request_bytes,
    responseBytes: raw.response_bytes,
    latencyMs: raw.latency_ms,
    // 形狀是 `EGRESS_BLOCKED: <reason>` / `VALIDATION_ERROR: …` / `EGRESS_FAILED: …`，
    // 送出去了則是 null（即使上游回 4xx）。
    error: raw.error,
    createdAt: toDate(raw.created_at),
  };
}

export async function listEgressCalls(agentId, query = {}) {
  const params = new URLSearchParams();
  const set = (key, value) => {
    if (value !== null && value !== undefined && value !== '') params.set(key, value);
  };
  set('kind', query.kind);
  set('run_id', query.runId);
  set('since', query.since);
  set('until', query.until);
  set('host', query.host);
  set('status_code', query.statusCode);
  if (query.errorsOnly) params.set('errors_only', 'true');
  set('limit', query.limit);
  set('cursor', query.cursor);
  const suffix = params.toString();
  const raw = await req('/agents/' + encodeURIComponent(agentId) + '/egress-calls'
    + (suffix ? '?' + suffix : ''));
  return {
    items: (raw.items || []).map(viewEgressCall),
    nextCursor: raw.next_cursor || null,
  };
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
