"""壓測用的假 LLM：OpenAI 相容的 `/chat/completions`，不花錢、回應時間和長度可控。

讓 probe-agent 的 `mode=llm` 走**真的 LLM 路徑**（容器 → Gateway → LLM → Gateway → 容器），
只是最後一站換成這支程式。這樣量得到「大家都在等 LLM」時連線、回覆在各處佔的記憶體，
而不是用 `sleep` 假裝。

**跑在 Docker 網路裡**（只接 `zentra-net`，沙盒網段連不到它，只有 Gateway 連得到）：

    docker run -d --rm --name zentra-mock-llm --network zentra-net \
      -v "$PWD/mock_llm.py:/mock_llm.py:ro" python:3.12-slim \
      python /mock_llm.py --bind 0.0.0.0 --delay-ms 2000 --reply-kb 32

    ZENTRA_MOCK_LLM_URL=http://zentra-mock-llm:8090/v1

**不要跑在主機上再讓 Gateway 經 `host.docker.internal` 連回來。** 在 Colima 上實測：
同時 50 筆時有一成左右的呼叫卡在剛好 70 秒，量到的是那條轉送通道，不是平台。

locustfile 會在測試開始時把目標 Agent 的 LLM 憑證指過來，Run 都跑完後才撤掉。

只用標準函式庫：一個連線一條 thread，同時幾百條沒問題 —— 平台這端一個容器最多同時 50 筆。
"""

import argparse
import json
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_FILLER = "這是假 LLM 的回答，用來模擬真實模型回來的長文字。"


def _reply_text(kb: int) -> str:
    if kb <= 0:
        return "好的。"
    chars = kb * 1024 * len(_FILLER) // len(_FILLER.encode())
    return (_FILLER * (chars // len(_FILLER) + 1))[:chars]


class Handler(BaseHTTPRequestHandler):
    # HTTP/1.0：回應以關閉連線結束，串流不用自己做 chunked encoding。
    protocol_version = "HTTP/1.0"
    delay_ms = 0
    reply_kb = 0
    chunks = 20

    def do_POST(self):
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self.send_error(404, "only /chat/completions")
            return
        length = int(self.headers.get("content-length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        model = body.get("model") or "mock"
        prompt_chars = sum(len(str(m.get("content", ""))) for m in body.get("messages", []))
        text = _reply_text(self.reply_kb)
        # 中文約 1 字 1 token，夠壓測用；平台照這個數字計量。
        usage = {"prompt_tokens": prompt_chars, "completion_tokens": len(text),
                 "total_tokens": prompt_chars + len(text)}
        rid = f"chatcmpl-{uuid.uuid4().hex[:12]}"

        if body.get("stream"):
            self._stream(rid, model, text, usage)
        else:
            time.sleep(self.delay_ms / 1000)
            self._json({
                "id": rid, "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": text}}],
                "usage": usage,
            })

    def _json(self, payload: dict):
        data = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, rid: str, model: str, text: str, usage: dict):
        """像真的模型：回覆在等待時間內分段吐出來，不是等完一次給。"""
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        step = max(1, len(text) // self.chunks)
        pieces = [text[i:i + step] for i in range(0, len(text), step)]
        pause = self.delay_ms / 1000 / len(pieces)

        def send(chunk: dict):
            self.wfile.write(b"data: " + json.dumps(chunk, ensure_ascii=False).encode() + b"\n\n")
            self.wfile.flush()

        base = {"id": rid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model}
        for i, piece in enumerate(pieces):
            time.sleep(pause)
            delta = {"content": piece} if i else {"role": "assistant", "content": piece}
            send({**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]})
        send({**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
        send({**base, "choices": [], "usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *_):
        pass    # 壓測時每秒幾十筆，印出來只是噪音


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # **一定要寫在類別上。** listen() 在建構時就呼叫了，建好之後才改這個值沒有用，
    # 排隊上限會停在預設的 5，同時湧進幾十個連線時會被丟掉、等 TCP 重試。
    request_queue_size = 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--port", type=int, default=8090)
    # 預設只綁本機，直接在主機上跑也不會被同網段的機器連到。
    # 在容器裡跑（建議的方式）要 --bind 0.0.0.0，Gateway 才連得到。
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--delay-ms", type=int, default=2000, help="每次回答花多久")
    parser.add_argument("--reply-kb", type=int, default=2, help="每次回答多長")
    args = parser.parse_args()

    Handler.delay_ms, Handler.reply_kb = args.delay_ms, args.reply_kb
    server = Server((args.bind, args.port), Handler)
    print(f"mock LLM on {args.bind}:{args.port}  delay={args.delay_ms}ms  reply={args.reply_kb}KB", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
