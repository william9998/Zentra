"""技術指標 —— 純函式，只吃 list[float]，不碰平台也不碰網路。

刻意做成沒有任何相依（連 numpy 都不用）：這個檔案可以直接
`python -c "import indicators"` 拿真實資料對答案，不需要起沙盒。
指標算錯是**不會報錯**的那種 bug，只會安靜地給你錯訊號，所以要能單獨驗。

## 這裡最重要的一件事：Wilder 平滑不是移動平均

RSI、ATR、ADX 三個都是 J. Welles Wilder 在 1978 年提出的，它們用的平滑
**不是** SMA 也不是一般的 EMA，而是 Wilder 自己的遞迴（又叫 RMA / SMMA）：

    rma[i] = (rma[i-1] * (n-1) + x[i]) / n

它等價於 alpha = 1/n 的 EMA，而一般 EMA 的 alpha = 2/(n+1)。
n=14 時是 0.0714 對 0.1333 —— **差了快一倍**。

拿 SMA 或標準 EMA 去算 RSI 是這類程式碼最常見的錯誤，而且特別難發現：
數值仍然落在 0–100、圖形看起來也很像，只是你的 70/30 跟 TradingView 上
所有人看到的 70/30 不是同一條線。訊號會早發或晚發幾根 K 棒。

## 回傳格式的約定

每個函式回傳的 list **長度都與輸入相同**，前面暖機不足的位置補 `None`。
不回傳「較短的 list」是刻意的：那會讓呼叫端必須自己記得位移多少，
而位移算錯同樣是安靜的錯 —— 你會拿到別根 K 棒的指標值。
索引對齊之後，`rsi[i]` 永遠就是第 i 根 K 棒的 RSI。
"""

from __future__ import annotations

import math

Series = list[float | None]


# ── 基礎平滑 ────────────────────────────────────────────────────────

def sma(values: list[float], period: int) -> Series:
    """簡單移動平均。"""
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    window = sum(values[:period])
    out[period - 1] = window / period
    for i in range(period, len(values)):
        window += values[i] - values[i - period]
        out[i] = window / period
    return out


def ema(values: list[float], period: int) -> Series:
    """指數移動平均，alpha = 2/(period+1)。

    用前 `period` 筆的 SMA 當種子，而不是直接拿 values[0]。
    拿單一值當種子的話，前幾十根的值會被那一根拉歪 ——
    period=200 時要一兩百根才收斂回來。
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    alpha = 2.0 / (period + 1)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * alpha + prev * (1 - alpha)
        out[i] = prev
    return out


def rma(values: list[float], period: int) -> Series:
    """Wilder 平滑（RMA / SMMA），alpha = 1/period。

    **RSI / ATR / ADX 一律用這個，不要用 `ema()`。** 理由見模組開頭。
    """
    if period <= 0:
        raise ValueError("period must be positive")
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = (prev * (period - 1) + values[i]) / period
        out[i] = prev
    return out


def stdev(values: list[float], period: int) -> Series:
    """滾動標準差（**母體**，分母 n）。

    布林通道用的是母體標準差。改用樣本標準差（分母 n-1）通道會稍微寬一點，
    在 period=20 時差約 2.6% —— 不足以讓圖看起來有問題，卻足以讓
    「碰到上軌」的判斷跟別人不一致。
    """
    out: Series = [None] * len(values)
    if len(values) < period:
        return out
    for i in range(period - 1, len(values)):
        window = values[i - period + 1: i + 1]
        mean = sum(window) / period
        var = sum((v - mean) ** 2 for v in window) / period
        out[i] = math.sqrt(var)
    return out


# ── 動能 ────────────────────────────────────────────────────────────

def rsi(closes: list[float], period: int = 14) -> Series:
    """相對強弱指標（Wilder）。

    avg_loss 為 0 時 RS 會除以零。回 100 是正確答案（純漲無跌），
    不是要防的例外 —— 低波動的穩定幣或剛上市的幣種真的會出現。
    """
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out

    gains = [0.0]
    losses = [0.0]
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    # 第 0 筆是補的 0（沒有前一根可比），平滑要從第 1 筆開始才對。
    avg_gain = rma(gains[1:], period)
    avg_loss = rma(losses[1:], period)

    for i, (g, loss) in enumerate(zip(avg_gain, avg_loss), start=1):
        if g is None or loss is None:
            continue
        if loss == 0:
            out[i] = 100.0
        else:
            rs = g / loss
            out[i] = 100.0 - (100.0 / (1.0 + rs))
    return out


def macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[Series, Series, Series]:
    """MACD，回 (macd_line, signal_line, histogram)。

    signal 是 macd_line 的 EMA，而 macd_line 前面有 None。
    直接把含 None 的 list 丟進 `ema()` 會炸，所以先切掉暖機段、
    算完再平移回原位 —— 平移這一步錯了，signal 會整條錯位。
    """
    fast_ema = ema(closes, fast)
    slow_ema = ema(closes, slow)

    macd_line: Series = [None] * len(closes)
    for i in range(len(closes)):
        if fast_ema[i] is not None and slow_ema[i] is not None:
            macd_line[i] = fast_ema[i] - slow_ema[i]

    first = next((i for i, v in enumerate(macd_line) if v is not None), None)
    signal_line: Series = [None] * len(closes)
    hist: Series = [None] * len(closes)
    if first is None:
        return macd_line, signal_line, hist

    dense = [v for v in macd_line[first:] if v is not None]
    sig_dense = ema(dense, signal)
    for offset, v in enumerate(sig_dense):
        signal_line[first + offset] = v

    for i in range(len(closes)):
        if macd_line[i] is not None and signal_line[i] is not None:
            hist[i] = macd_line[i] - signal_line[i]
    return macd_line, signal_line, hist


def stochastic(
    highs: list[float], lows: list[float], closes: list[float],
    period: int = 14, smooth_k: int = 3, smooth_d: int = 3,
) -> tuple[Series, Series]:
    """隨機指標，回 (%K, %D)。用的是慢速版（%K 已經平滑過）。

    最高等於最低（整段完全沒波動）時分母為 0，給 50 ——
    「不偏多也不偏空」是這種情況下唯一說得通的值。
    """
    raw: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        hh = max(highs[i - period + 1: i + 1])
        ll = min(lows[i - period + 1: i + 1])
        raw[i] = 50.0 if hh == ll else 100.0 * (closes[i] - ll) / (hh - ll)

    first = next((i for i, v in enumerate(raw) if v is not None), None)
    k: Series = [None] * len(closes)
    d: Series = [None] * len(closes)
    if first is None:
        return k, d

    dense = [v for v in raw[first:] if v is not None]
    k_dense = sma(dense, smooth_k)
    for offset, v in enumerate(k_dense):
        k[first + offset] = v

    k_first = next((i for i, v in enumerate(k) if v is not None), None)
    if k_first is None:
        return k, d
    d_dense = sma([v for v in k[k_first:] if v is not None], smooth_d)
    for offset, v in enumerate(d_dense):
        d[k_first + offset] = v
    return k, d


# ── 波動 ────────────────────────────────────────────────────────────

def true_range(highs: list[float], lows: list[float], closes: list[float]) -> list[float]:
    """真實波幅。

    第一根沒有前收，只能用 high-low。這一根的值略偏小是已知且無解的，
    暖機幾根之後影響就被平滑掉了。
    """
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        prev_close = closes[i - 1]
        tr.append(max(
            highs[i] - lows[i],
            abs(highs[i] - prev_close),
            abs(lows[i] - prev_close),
        ))
    return tr


def atr(highs: list[float], lows: list[float], closes: list[float],
        period: int = 14) -> Series:
    """平均真實波幅（Wilder）。停損與部位大小都靠它。"""
    return rma(true_range(highs, lows, closes), period)


def bollinger(
    closes: list[float], period: int = 20, mult: float = 2.0
) -> tuple[Series, Series, Series, Series]:
    """布林通道，回 (middle, upper, lower, bandwidth)。

    `bandwidth = (upper - lower) / middle` —— 這是**相對**寬度，
    所以 BTC 跟一個單價 0.001 的幣可以直接比。用絕對寬度就不行。
    """
    mid = sma(closes, period)
    sd = stdev(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    width: Series = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is None or sd[i] is None:
            continue
        upper[i] = mid[i] + mult * sd[i]
        lower[i] = mid[i] - mult * sd[i]
        width[i] = (upper[i] - lower[i]) / mid[i] if mid[i] else None
    return mid, upper, lower, width


def squeeze_percentile(bandwidth: Series, lookback: int = 120) -> Series:
    """布林帶寬在過去 `lookback` 根裡的百分位（0–100）。

    這是「擠壓」的量化定義：低百分位表示波動被壓縮到近期低點，
    通常在大行情之前。**它不指方向** —— 只說「要動了」，不說往哪動，
    所以只拿它當過濾器，不當訊號。
    """
    out: Series = [None] * len(bandwidth)
    for i in range(len(bandwidth)):
        if bandwidth[i] is None:
            continue
        window = [v for v in bandwidth[max(0, i - lookback + 1): i + 1] if v is not None]
        if len(window) < 20:          # 樣本太少的百分位沒有意義
            continue
        below = sum(1 for v in window if v < bandwidth[i])
        out[i] = 100.0 * below / len(window)
    return out


# ── 趨勢強度 ────────────────────────────────────────────────────────

def adx(
    highs: list[float], lows: list[float], closes: list[float], period: int = 14
) -> tuple[Series, Series, Series]:
    """ADX / DMI，回 (adx, plus_di, minus_di)。

    ADX 是**這支 agent 的狀態過濾器**：它不講方向，只講「現在有沒有趨勢」。
    業界慣例是 ADX > 25 才吃順勢訊號、< 20 才吃均值回歸訊號，
    中間那段兩種都不吃。順勢策略在盤整裡連續停損，多半就是少了這道過濾。

    ⚠️ +DM 與 -DM **不能同時為正**：哪邊的動作大就算哪邊，另一邊歸零。
    少了這個互斥判斷，內外包（inside / outside bar）會被同時記兩邊，
    ADX 會被灌水，於是盤整被誤判成有趨勢 —— 正好是這道過濾器要擋的情況。
    """
    n = len(closes)
    out_adx: Series = [None] * n
    out_pdi: Series = [None] * n
    out_mdi: Series = [None] * n
    if n < period * 2:
        return out_adx, out_pdi, out_mdi

    plus_dm = [0.0]
    minus_dm = [0.0]
    for i in range(1, n):
        up = highs[i] - highs[i - 1]
        down = lows[i - 1] - lows[i]
        plus_dm.append(up if (up > down and up > 0) else 0.0)
        minus_dm.append(down if (down > up and down > 0) else 0.0)

    tr = true_range(highs, lows, closes)
    # 與 rsi() 同樣的理由：第 0 筆是補的，平滑從第 1 筆開始。
    atr_s = rma(tr[1:], period)
    pdm_s = rma(plus_dm[1:], period)
    mdm_s = rma(minus_dm[1:], period)

    dx_dense: list[float] = []
    dx_index: list[int] = []
    for offset in range(len(atr_s)):
        i = offset + 1
        a, p, m = atr_s[offset], pdm_s[offset], mdm_s[offset]
        if a is None or p is None or m is None or a == 0:
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        out_pdi[i] = pdi
        out_mdi[i] = mdi
        total = pdi + mdi
        if total == 0:
            continue
        dx_dense.append(100.0 * abs(pdi - mdi) / total)
        dx_index.append(i)

    for offset, v in enumerate(rma(dx_dense, period)):
        if v is not None:
            out_adx[dx_index[offset]] = v
    return out_adx, out_pdi, out_mdi


# ── 量能 ────────────────────────────────────────────────────────────

def obv(closes: list[float], volumes: list[float]) -> list[float]:
    """能量潮。收漲加量、收跌減量、平盤不動。

    絕對值沒有意義（起點是任意的 0），**只看方向與背離**：
    價格創新高而 OBV 沒有，就是背離。
    """
    out = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            out.append(out[-1] - volumes[i])
        else:
            out.append(out[-1])
    return out


def rolling_vwap(
    highs: list[float], lows: list[float], closes: list[float],
    volumes: list[float], period: int = 20,
) -> Series:
    """滾動 VWAP（成交量加權均價）。

    ⚠️ 這**不是**交易所畫的那條 VWAP。標準 VWAP 從交易時段開盤累積，
    但加密貨幣 24 小時不收盤，「開盤」得自己定義（多半是 UTC 00:00）。
    滾動窗口沒有這個模糊地帶，代價是跟別人圖上那條對不起來。
    要跟別人對答案就得改成錨定式。
    """
    out: Series = [None] * len(closes)
    tp = [(highs[i] + lows[i] + closes[i]) / 3 for i in range(len(closes))]
    for i in range(period - 1, len(closes)):
        vol = sum(volumes[i - period + 1: i + 1])
        if vol == 0:
            continue
        out[i] = sum(tp[j] * volumes[j] for j in range(i - period + 1, i + 1)) / vol
    return out


def volume_zscore(volumes: list[float], period: int = 20) -> Series:
    """成交量的 z-score：這根的量比近期常態高几個標準差。

    突破配上 z > 2 的量才有說服力。沒量的突破多半是假突破。
    """
    out: Series = [None] * len(volumes)
    mean = sma(volumes, period)
    sd = stdev(volumes, period)
    for i in range(len(volumes)):
        if mean[i] is None or sd[i] is None or sd[i] == 0:
            continue
        out[i] = (volumes[i] - mean[i]) / sd[i]
    return out
