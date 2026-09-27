"""ZEN-002：LLM Gateway 的單元測試（不需要服務）。

每條擋的是一個具體的錯，不是「檢查一下設定」：

  L-1  run token 被轉發給供應商      → 內部憑證外流，而且供應商會看到我們的 token
  L-2  金鑰換不上去                  → 呼叫全部 401，功能等於沒有
  L-3  usage 從沙盒回報值來          → 使用者程式碼可以自己報 0，帳就錯了
  L-4  串流時漏記用量                → langchain-openai 預設就走串流，漏了等於全漏
  L-5  cost 用 float               → 累加會有浮點誤差（契約 §1 明訂用 decimal 字串）
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.api.services import llm


def test_run_token_is_never_forwarded_to_the_provider() -> None:
    """L-1：`authorization` 必須被剝掉，否則沙盒的 run token 會流到外部服務。"""
    incoming = {
        "authorization": "Bearer run-token-secret",
        "content-type": "application/json",
        "x-api-key": "another-secret",
        "user-agent": "langchain",
    }
    forwarded = llm._forwardable(incoming)

    assert "authorization" not in forwarded, "run token 被轉發給供應商了"
    assert "x-api-key" not in forwarded
    assert forwarded["user-agent"] == "langchain", "不相干的 header 不該被剝掉"


def test_provider_headers_that_leak_org_info_are_stripped() -> None:
    """供應商的組織資訊沒必要讓沙盒看到。"""
    assert "openai-organization" in llm._STRIP_RESPONSE_HEADERS
    assert "set-cookie" in llm._STRIP_RESPONSE_HEADERS


def test_usage_is_read_from_the_provider_response() -> None:
    """L-3：用量來自供應商的回應，不是沙盒說了算。"""
    assert llm._usage_from({"usage": {"prompt_tokens": 12, "completion_tokens": 34}}) == (12, 34)
    assert llm._usage_from({}) == (0, 0), "沒有 usage 欄位要記 0，不能猜"


def test_streaming_usage_comes_from_the_last_usage_chunk() -> None:
    """L-4：langchain-openai 預設走串流，用量在 `[DONE]` 之前那個 chunk。"""
    raw = (
        b'data: {"choices":[{"delta":{"content":"hi"}}],"usage":null}\n\n'
        b'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":9}}\n\n'
        b"data: [DONE]\n\n"
    )
    assert llm._usage_from_stream(raw) == (5, 9)


def test_streaming_without_usage_records_zero_rather_than_guessing() -> None:
    """找不到用量就記 0。寧可記少也不要記錯 —— 這張表是稽核用的。"""
    raw = b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n'
    assert llm._usage_from_stream(raw) == (0, 0)


def test_cost_is_zero_when_no_price_is_configured() -> None:
    """SPEC §11 把計費出帳列在範圍外；沒設價格不影響 token 數的正確性。"""
    assert llm._cost("gpt-4o-mini", 1000, 1000) == Decimal("0")


def test_cost_uses_decimal_not_float(monkeypatch: pytest.MonkeyPatch) -> None:
    """L-5：契約 §1 明訂 `cost_usd` 是 decimal 字串，避免浮點誤差。"""
    from apps.api import config

    settings = config.get_settings()
    monkeypatch.setattr(
        settings, "llm_prices",
        {"m": {"input": "0.15", "output": "0.60"}}, raising=False,
    )
    config.get_settings.cache_clear()
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    monkeypatch.setattr(llm, "get_settings", lambda: settings)

    cost = llm._cost("m", 1_000_000, 1_000_000)
    assert isinstance(cost, Decimal)
    assert cost == Decimal("0.750000")


def test_only_whitelisted_upstream_paths_are_forwarded() -> None:
    """L-6：代理握著真金鑰，能打哪裡由平台決定，不是沙盒決定。

    白名單外的路徑必須被擋 —— 否則沙盒可以用平台的金鑰打供應商的任何端點
    （檔案上傳、fine-tune、帳務…），而那些都不在計量與稽核裡。
    """
    assert llm.UPSTREAM_PATHS == {"chat/completions", "responses"}

    for evil in ("files", "fine_tuning/jobs", "../v1/organization", "chat/completions/../files"):
        assert evil not in llm.UPSTREAM_PATHS


def test_usage_is_read_from_the_responses_api_shape_too() -> None:
    """L-7：兩種 API 的 usage 欄位名不同，只認一種就會把另一種記成 0。

    `chat/completions` 用 prompt/completion_tokens，`responses` 用 input/output_tokens。
    記成 0 不會報錯 —— 帳只是安靜地錯了。
    """
    assert llm._usage_from({"usage": {"input_tokens": 31, "output_tokens": 12}}) == (31, 12)
    # 兩種都在時以 chat/completions 的為準（那是它自己的回應）
    assert llm._usage_from({
        "usage": {"prompt_tokens": 5, "completion_tokens": 6,
                  "input_tokens": 99, "output_tokens": 99},
    }) == (5, 6)


def test_stream_options_is_only_added_for_chat_completions() -> None:
    """L-8：`stream_options` 是 chat/completions 專屬的。

    實測踩過：把它塞給 Responses API，供應商回
    `400 Unknown parameter: 'stream_options.include_usage'`，整個呼叫失敗。
    **假供應商照單全收，所以這條只有真金鑰才發現得了** —— 這個測試補上那個缺口。

    這裡驗的是 `proxy()` 裡那段條件，用讀原始碼的方式擋回歸：
    加參數的那一行必須被路徑條件包住。
    """
    import inspect

    source = inspect.getsource(llm.proxy)
    assert 'streaming and upstream_path == "chat/completions"' in source, (
        "stream_options 必須只在 chat/completions 時加入"
    )


def test_streaming_usage_is_found_in_the_responses_api_nesting() -> None:
    """L-9：Responses API 把用量放在 `response.completed` 事件的 `response` 底下。

    只找頂層的話，那些呼叫全部記成 0 token —— 不會報錯，帳安靜地錯。
    """
    raw = (
        b'data: {"type":"response.output_text.delta","delta":"hi"}\n\n'
        b'data: {"type":"response.completed","response":'
        b'{"usage":{"input_tokens":41,"output_tokens":17}}}\n\n'
    )
    assert llm._usage_from_stream(raw) == (41, 17)
