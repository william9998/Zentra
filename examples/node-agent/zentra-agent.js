#!/usr/bin/env node
// zentra.image/v1 的 Node.js 實作（specs/runtime.md）。沒有任何套件依賴，只用 Node 內建的 fetch。
//
// 平台用 /opt/zentra/bin/zentra-agent <validate|serve|run> 啟動這支程式。
// 它讀 agent.js 的 manifest / entrypoints / handlers，負責跟平台溝通。

import { randomBytes } from "node:crypto";
import { manifest, entrypoints, handlers } from "/opt/zentra/app/agent.js";

const CONTRACT = "zentra.image/v1";
const env = (name) => {
  const value = process.env[name];
  if (!value) throw new Error(`missing env ${name}`);
  return value;
};
const gateway = () => env("ZENTRA_GATEWAY_URL").replace(/\/+$/, "");
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function uuid7() {
  const bytes = randomBytes(16);
  const ms = BigInt(Date.now());
  for (let i = 0; i < 6; i += 1) bytes[i] = Number((ms >> BigInt(8 * (5 - i))) & 0xffn);
  bytes[6] = (bytes[6] & 0x0f) | 0x70;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.toString("hex");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

// 4xx 直接丟錯（重送也不會變），5xx 與連線錯誤退避重試。
async function call(method, path, token, body, attempts = 5) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      const response = await fetch(`${gateway()}${path}`, {
        method,
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await response.text();
      if (response.status < 500 || attempt === attempts - 1) {
        if (response.status >= 400) {
          throw Object.assign(new Error(`${method} ${path} -> ${response.status}: ${text.slice(0, 500)}`), { final: true });
        }
        return { status: response.status, body: text ? JSON.parse(text) : null };
      }
    } catch (error) {
      if (error.final || attempt === attempts - 1) throw error;
    }
    await sleep(Math.min(200 * 2 ** attempt, 2000));
  }
}

function checkEntrypoints() {
  if (!Array.isArray(entrypoints) || entrypoints.length === 0) {
    throw new Error("agent.js must export a non-empty entrypoints list");
  }
  for (const item of entrypoints) {
    if (typeof handlers[item.name] !== "function") {
      throw new Error(`entrypoint ${item.name} has no handler in agent.js`);
    }
  }
}

async function validate() {
  const versionId = env("ZENTRA_AGENT_VERSION_ID");
  const token = env("ZENTRA_VALIDATION_TOKEN");
  const path = `/versions/${versionId}/validation`;
  try {
    checkEntrypoints();
  } catch (error) {
    await call("POST", path, token, {
      valid: false, runtime_contract: CONTRACT, manifest: null, entrypoints: null,
      error: { code: "BUILD_FAILED", message: String(error.message), run_id: null,
               retryable: false, details: { stage: "VALIDATE" } },
    });
    throw error;
  }
  // 平台會再驗一次入口格式；不合格回 422，這裡丟錯、exit 非 0，訊息進版本錯誤。
  await call("POST", path, token, {
    valid: true, runtime_contract: CONTRACT, manifest, entrypoints, error: null,
  });
}

function makeContext(item, token) {
  const runId = item.run_id;
  const send = (type, payload) =>
    call("POST", `/runs/${runId}/events`, token, [{ event_id: uuid7(), type, payload }]);
  return {
    runId,
    config: item.config ?? {},
    secretNames: item.secret_names ?? [],
    log: (level, message, fields = {}) => send("log", { level, message: String(message), ...fields }),
    emit: (name, payload = {}) => send("custom", { name, payload }),
    async step(node, fn) {
      await send("node.start", { node, ts: new Date().toISOString() });
      const value = await fn();
      await send("node.end", { node, ts: new Date().toISOString() });
      return value;
    },
    // OpenAI 相容的 chat/completions；金鑰在平台那側，這裡只帶 run token。
    async llm(body) {
      const response = await call("POST", "/llm/v1/chat/completions", token,
        { model: item.default_model || undefined, ...body }, 1);
      return response.body;
    },
    // 經 Gateway 代打外部 HTTP，要先在 Console 加白名單。
    async http(request) {
      const response = await call("POST", "/egress/request", token,
        { method: "GET", headers: {}, body: null, ...request }, 1);
      return response.body;
    },
  };
}

async function execute(item, token) {
  const runId = item.run_id;
  try {
    const handler = handlers[item.entrypoint];
    if (typeof handler !== "function") throw new Error(`unknown entrypoint ${item.entrypoint}`);
    const output = await handler(item.input ?? {}, makeContext(item, token));
    if (output === null || typeof output !== "object" || Array.isArray(output)) {
      throw new Error("handler must return an object");
    }
    await call("POST", `/runs/${runId}/result`, token, { output, error: null, usage: null });
  } catch (error) {
    console.error(`[zentra] run ${runId} failed:`, error);
    await call("POST", `/runs/${runId}/result`, token, {
      output: null, usage: null,
      error: { code: "USER_CODE_ERROR", message: `${error.name}: ${error.message}`.slice(0, 4000),
               run_id: runId, retryable: false, details: { stage: "RUN" } },
    });
  }
}

async function serve() {
  const agentId = env("ZENTRA_AGENT_ID");
  const containerToken = env("ZENTRA_CONTAINER_TOKEN");
  const max = Math.max(1, Number(process.env.ZENTRA_MAX_CONCURRENT_RUNS) || 1);
  let inflight = 0;
  for (;;) {
    if (inflight >= max) { await sleep(50); continue; }
    let response;
    try {
      response = await call("GET", `/agents/${agentId}/work`, containerToken, undefined, 1);
    } catch (error) {
      console.error("[zentra] taking work failed:", error.message);
      await sleep(2000);
      continue;
    }
    if (response.status === 204 || !response.body) continue;
    const item = response.body;
    console.log(`[zentra] picked up run ${item.run_id}`);
    inflight += 1;
    execute(item, item.run_token)
      .catch((error) => console.error(`[zentra] reporting run ${item.run_id} failed:`, error))
      .finally(() => { inflight -= 1; });
  }
}

async function run() {
  const runId = env("ZENTRA_RUN_ID");
  const token = env("ZENTRA_RUN_TOKEN");
  const { body } = await call("GET", `/runs/${runId}/bootstrap`, token);
  await execute({ ...body, run_id: runId }, token);
}

const modes = { validate, serve, run };
const mode = process.argv[2];
if (!modes[mode]) {
  console.error("usage: zentra-agent <validate|serve|run>");
  process.exit(2);
}
modes[mode]().then(
  () => process.exit(0),
  (error) => { console.error(error); process.exit(1); },
);
