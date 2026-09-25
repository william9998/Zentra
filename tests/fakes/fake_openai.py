"""最小的 OpenAI 相容供應商，給 ZEN-002 的驗證用。

**為什麼要這個而不是真金鑰**：要驗的是「金鑰有沒有留在 app 這一側、用量有沒有記對」，
那跟模型講了什麼無關。假供應商讓測試變成確定性的、不花錢、不依賴外網，
而且可以斷言**它收到的 Authorization 是真金鑰，不是 run token** —— 那正是 P4 的重點。

    python -m tests.fakes.fake_openai        # 監聽 :9000
"""

import json
import os
from http.server import BaseHTTPRequestHandler, HTTPServer

EXPECTED_KEY = os.environ.get("FAKE_EXPECT_KEY", "sk-test-real-key")


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:                                   # noqa: N802
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        auth = self.headers.get("authorization", "")

        # 這條斷言就是 P4 的證據：Gateway 必須換上真金鑰，
        # 沙盒的 run token 絕對不能被轉發到這裡。
        if auth != f"Bearer {EXPECTED_KEY}":
            self._send(401, {"error": {
                "message": f"fake provider expected the real key, got: {auth[:24]}…",
                "type": "invalid_request_error",
            }})
            return

        prompt = ""
        for message in body.get("messages") or []:
            if message.get("role") == "user":
                prompt = str(message.get("content", ""))

        model = body.get("model", "fake-model")
        content = f"[fake] 你說的是：{prompt}"

        if body.get("stream"):
            # langchain-openai 預設就走串流，所以這條才是真實路徑。
            # 依 stream_options.include_usage 在 [DONE] 之前送一個帶 usage 的 chunk ——
            # Gateway 的 _usage_from_stream 就是靠它計量的。
            self._send_stream(model, content)
            return

        self._send(200, {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
        })

    def _send_stream(self, model: str, content: str) -> None:
        def chunk(delta: dict, *, usage=None, finish=None) -> bytes:
            payload = {
                "id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                "usage": usage,
            }
            return b"data: " + json.dumps(payload).encode() + b"\n\n"

        parts = [chunk({"role": "assistant", "content": ""})]
        parts += [chunk({"content": piece}) for piece in (content[:8], content[8:])]
        parts.append(chunk({}, finish="stop"))
        # 最後這個 chunk 沒有 choices delta，只帶 usage —— OpenAI 的實際行為
        parts.append(
            b"data: " + json.dumps({
                "id": "chatcmpl-fake", "object": "chat.completion.chunk", "model": model,
                "choices": [],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            }).encode() + b"\n\n"
        )
        parts.append(b"data: [DONE]\n\n")
        raw = b"".join(parts)

        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send(self, status: int, payload: dict) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args) -> None:                        # 靜音
        pass


if __name__ == "__main__":
    HTTPServer(("0.0.0.0", 9000), Handler).serve_forever()
