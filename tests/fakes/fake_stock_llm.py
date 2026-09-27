"""會演戲的假供應商 —— 專門驗 stock-agent 的 retry 迴圈。

真的模型每次回什麼不固定，那驗不了「不夠就回頭再搜」這條路徑。
這支照 prompt 內容決定回什麼，並且**刻意讓第一輪的 assess 判定不足**，
這樣才看得到 research → assess → research 真的回頭了。

它也拒絕 Responses API（回 400），逼 agent 退回 chat/completions ——
那條退化路徑同樣需要被驗過。

    python -m tests.fakes.fake_stock_llm        # :9100
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

EXPECTED_KEY = os.environ.get("FAKE_EXPECT_KEY", "sk-test-real-key")

#: research 被呼叫幾次。第一次給不足的內容，之後給足夠的。
_research_calls = {"n": 0}


def _reply_for(prompt: str) -> str:
    if "判斷下面這個問題問的是" in prompt:
        return json.dumps({
            "subject_type": "STOCK", "subject": "TSMC 台積電",
            "focus": "近期新聞與股價方向",
        }, ensure_ascii=False)

    if "用網路搜尋找最新公開資訊" in prompt:
        # 每次 Run 的第一輪都要不足，才驗得到 retry 回頭。
        # 靠 prompt 裡有沒有「上一輪缺的是」來分辨第幾輪 —— 用全域計數器的話，
        # 跨 Run 會累加，第二次 Run 就直接拿到足夠的資料，測試等於沒測到。
        is_retry = "上一輪缺的是" in prompt
        _research_calls["n"] += 1
        if not is_retry:
            # 故意給沒有內容的東西，逼 assess 判定不足
            return json.dumps({"findings": "查無相關資訊。", "sources": []},
                              ensure_ascii=False)
        return json.dumps({
            "findings": "台積電公布最新月營收年增 25%，3nm 產能滿載；"
                        "外資上調目標價。近期地緣政治風險仍在。",
            "sources": ["https://example.com/tsmc-revenue",
                        "https://example.com/tsmc-analyst"],
        }, ensure_ascii=False)

    if "夠不夠回答使用者的問題" in prompt:
        # 第一輪的 findings 是「查無相關資訊」→ 不足
        enough = "查無相關資訊" not in prompt
        return json.dumps({
            "sufficient": enough,
            "missing": "" if enough else "缺具體營收數據與法人動向",
        }, ensure_ascii=False)

    if "給出判斷" in prompt:
        return json.dumps({
            "summary": "月營收年增 25%，3nm 滿載，外資上調目標價；地緣政治為主要風險。",
            "bullish_percent": 70, "bearish_percent": 30,
            "recommendation": "BUY",
            "rationale": "基本面成長明確，風險屬外部且已部分反映。",
            "data_quality": "GOOD",
        }, ensure_ascii=False)

    return json.dumps({"note": "unrecognised prompt"}, ensure_ascii=False)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:                                    # noqa: N802
        # 印出實際收到的上游路徑。Gateway 到底轉發到哪裡，看這個最直接。
        print(f"[fake] POST {self.path}", flush=True)
        if self.headers.get("authorization") != f"Bearer {EXPECTED_KEY}":
            self._send(401, {"error": {"message": "fake provider: wrong key"}})
            return

        # Responses API：預設拒絕（驗 agent 的退化路徑）。
        # 設 FAKE_ACCEPT_RESPONSES=1 則接受，用來證明 Gateway 真的轉發得到 /responses。
        if self.path.rstrip("/").endswith("/responses"):
            if os.environ.get("FAKE_ACCEPT_RESPONSES") != "1":
                self._send(400, {"error": {
                    "message": "fake provider does not implement the Responses API",
                    "type": "invalid_request_error",
                }})
                return
            length = int(self.headers.get("content-length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            prompt = ""
            raw_input = body.get("input")
            if isinstance(raw_input, str):
                prompt = raw_input
            elif isinstance(raw_input, list):
                for item in raw_input:
                    content = item.get("content") if isinstance(item, dict) else None
                    if isinstance(content, str):
                        prompt = content
                    elif isinstance(content, list):
                        for part in content:
                            if isinstance(part, dict) and part.get("text"):
                                prompt = str(part["text"])
            self._send(200, {
                "id": "resp-fake", "object": "response",
                "model": body.get("model", "fake-model"),
                "status": "completed",
                "output": [{
                    "type": "message", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": _reply_for(prompt)}],
                }],
                # ← Responses API 的 usage 欄位名跟 chat/completions 不同
                "usage": {"input_tokens": 33, "output_tokens": 44, "total_tokens": 77},
            })
            return

        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        prompt = ""
        for message in body.get("messages") or []:
            if message.get("role") == "user":
                prompt = str(message.get("content", ""))

        content = _reply_for(prompt)
        model = body.get("model", "fake-model")
        if body.get("stream"):
            self._stream(model, content)
            return
        self._send(200, {
            "id": "chatcmpl-fake", "object": "chat.completion", "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 40, "total_tokens": 60},
        })

    def _stream(self, model: str, content: str) -> None:
        def chunk(payload: dict) -> bytes:
            return b"data: " + json.dumps(payload).encode() + b"\n\n"

        parts = [chunk({"id": "c", "object": "chat.completion.chunk", "model": model,
                        "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                     "finish_reason": None}], "usage": None})]
        parts.append(chunk({"id": "c", "object": "chat.completion.chunk", "model": model,
                            "choices": [{"index": 0, "delta": {"content": content},
                                         "finish_reason": None}], "usage": None}))
        parts.append(chunk({"id": "c", "object": "chat.completion.chunk", "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                            "usage": None}))
        parts.append(chunk({"id": "c", "object": "chat.completion.chunk", "model": model,
                            "choices": [],
                            "usage": {"prompt_tokens": 20, "completion_tokens": 40}}))
        parts.append(b"data: [DONE]\n\n")
        raw = b"".join(parts)
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9100), Handler).serve_forever()
