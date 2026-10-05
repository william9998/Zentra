"""諧波型態 —— XABCD 五點 Fibonacci 比例驗證。

## ⚠️ 先講清楚這東西的可信度

諧波型態（Gartley / Bat / Butterfly / Crab / Shark / Cypher）在交易圈很流行，
但**學術上的樣本外證據很弱**。沒有可靠的研究顯示它的預測力贏得過基準，
而且它有結構性的問題：型態要等 D 點成立才算數，而 D 點成立的定義本身
就依賴你選的 ZigZag 參數 —— 換個閾值，同一段行情會長出不同的型態。

所以這支 agent 把它當成**合成分數裡的一票**，不讓它單獨觸發訊號。
這不是保守，是照它實際的證據強度給權重。

## 型態怎麼定義

五個交替的轉折點 X→A→B→C→D，看四段的長度比例：

    多頭型態（D 是低點，預期在 D 反轉向上）：
        X低 → A高 → B低 → C高 → D低
    空頭型態是完全鏡像。

驗的比例：

| 型態 | B 相對 XA | C 相對 AB | D 相對 BC | D 的終點 |
|---|---|---|---|---|
| Gartley   | 0.618       | 0.382–0.886 | 1.13–1.618  | 0.786 × XA |
| Bat       | 0.382–0.50  | 0.382–0.886 | 1.618–2.618 | 0.886 × XA |
| Butterfly | 0.786       | 0.382–0.886 | 1.618–2.24  | 1.27–1.618 × XA |
| Crab      | 0.382–0.618 | 0.382–0.886 | 2.618–3.618 | 1.618 × XA |
| Deep Crab | 0.886       | 0.382–0.886 | 2.0–3.618   | 1.618 × XA |
| Shark     | 0.382–0.618 | 1.13–1.618  | 1.618–2.24  | 0.886–1.13 × **XC** |
| Cypher    | 0.382–0.618 | 1.272–1.414 | —           | 0.786 × **XC** |

⚠️ **Shark 與 Cypher 的 D 是對 XC 量的，不是對 XA。** 而且它們的 C 會**超過 A**
（所以 C 相對 AB 的比例大於 1）。很多實作把這兩個也拿 XA 去量，結果是永遠配不出來，
或配出一堆假的。這是這個檔案裡最容易寫錯的地方。
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ── 型態定義 ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PatternSpec:
    name: str
    b_xa: tuple[float, float]      # B 點回撤 XA 的容許區間
    c_ab: tuple[float, float]      # C 點回撤（或延伸）AB 的容許區間
    d_bc: tuple[float, float] | None   # D 點延伸 BC 的容許區間；Cypher 不驗這段
    d_ratio: tuple[float, float]   # D 的終點比例
    d_against: str                 # "XA" 或 "XC" —— D 拿哪一段量


#: 容差。點目標（例如 Gartley 的 0.618）左右各放這麼多。
#: 0.03 是業界常見值：太緊配不到，太鬆會把任意三角形都認成 Gartley。
TOLERANCE = 0.03


def _band(target: float, tol: float = TOLERANCE) -> tuple[float, float]:
    return (target - tol, target + tol)


PATTERNS: tuple[PatternSpec, ...] = (
    PatternSpec("Gartley",   _band(0.618),   (0.382, 0.886), (1.13, 1.618),  _band(0.786),   "XA"),
    PatternSpec("Bat",       (0.382, 0.50),  (0.382, 0.886), (1.618, 2.618), _band(0.886),   "XA"),
    PatternSpec("Butterfly", _band(0.786),   (0.382, 0.886), (1.618, 2.24),  (1.27, 1.618),  "XA"),
    PatternSpec("Crab",      (0.382, 0.618), (0.382, 0.886), (2.618, 3.618), _band(1.618),   "XA"),
    PatternSpec("DeepCrab",  _band(0.886),   (0.382, 0.886), (2.0, 3.618),   _band(1.618),   "XA"),
    PatternSpec("Shark",     (0.382, 0.618), (1.13, 1.618),  (1.618, 2.24),  (0.886, 1.13),  "XC"),
    PatternSpec("Cypher",    (0.382, 0.618), (1.272, 1.414), None,           _band(0.786),   "XC"),
)


# ── ZigZag 轉折點 ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str                 # "H" 或 "L"
    confirmed: bool = True    # False = 暫定，價格還沒反向走夠遠


def find_pivots(
    highs: list[float], lows: list[float], atr_series: list[float | None],
    *, atr_mult: float = 3.0, min_pct: float = 0.005, min_bars: int = 2,
    include_provisional: bool = True,
) -> list[Pivot]:
    """ATR 自適應的 ZigZag 轉折點，保證高低交替。

    ## 為什麼閾值要跟著 ATR 走

    固定百分比（例如「反轉 3% 算一個轉折」）在加密貨幣上會壞掉兩次：
    行情平靜時 3% 半年不動一次，一個轉折都抓不到；行情劇烈時一天來回
    好幾次 3%，抓出一堆雜訊。用 `atr_mult × ATR` 讓閾值自己跟著當下的
    波動縮放，同一組參數在 BTC 與冷門幣、在牛市與盤整都還能用。

    `min_pct` 是地板，防止 ATR 極小時閾值趨近 0 而把每根 K 棒都當轉折。
    `min_bars` 要求兩個轉折至少隔幾根 —— 少了它，一根長黑 K 可以同時
    確認一個高點與一個低點（**同一個 index 出現兩次**），後面算比例時
    就會出現長度為 0 的線段。實測在 BTC 1h 上真的會發生。

    ## 最後那個轉折是「暫定」的

    ZigZag 的本質是：一個轉折要等價格反向走夠遠才算數，所以**最新的極值
    永遠還沒被確認**。任何宣稱能即時確認轉折的實作，都是偷看了未來。

    但諧波型態要的 D 點，剛好就是那個還沒確認的極值 —— 等它確認完，
    行情也走完了。所以 `include_provisional=True` 會把「自最後一個確認轉折
    以來的當前極值」也放進來，標記 `confirmed=False`。
    **這正是「潛在反轉區（PRZ）」這個詞的由來**：你不是在事後認出型態，
    而是在推測 D 應該落在哪，然後看價格現在是不是到了那裡。
    """
    n = len(highs)
    if n < 3:
        return []
    if min_bars < 1:
        # 0 會讓上面「排除轉折點那一根」的範圍變成空的。
        raise ValueError("min_bars must be at least 1")

    def threshold(i: int) -> float:
        a = atr_series[i] if i < len(atr_series) else None
        floor = lows[i] * min_pct
        return max(a * atr_mult, floor) if a is not None else floor

    pivots: list[Pivot] = []
    direction = 0            # 0=未定, 1=正在往上找高點, -1=正在往下找低點
    hi_i, hi_p = 0, highs[0]
    lo_i, lo_p = 0, lows[0]

    # 確認一個轉折後，要回頭在「那個轉折之後」重掃反向的極值。
    #
    # 兩個細節都踩過：
    #   1. 不能從現在這根 `i` 開始掃 —— 轉折點與現在之間的真正極值會被跳過，
    #      下一個轉折的價格與位置都會偏掉。
    #   2. 範圍**必須排除轉折點那一根**（`+ 1`）。含進去的話，一根長黑 K 的
    #      high 當高點、同一根的 low 當低點，**同一個 index 會出現兩次**，
    #      後面算比例就冒出長度為 0 的線段。實測 BTC 1h 上真的會發生。
    # 呼叫點保證 `i - 轉折 index >= min_bars >= 1`，所以範圍不會是空的。

    def confirm_high(i: int) -> None:
        nonlocal direction, lo_i, lo_p
        pivots.append(Pivot(hi_i, hi_p, "H"))
        direction = -1
        lo_i = min(range(hi_i + 1, i + 1), key=lambda j: lows[j])
        lo_p = lows[lo_i]

    def confirm_low(i: int) -> None:
        nonlocal direction, hi_i, hi_p
        pivots.append(Pivot(lo_i, lo_p, "L"))
        direction = 1
        hi_i = max(range(lo_i + 1, i + 1), key=lambda j: highs[j])
        hi_p = highs[hi_i]

    for i in range(1, n):
        thr = threshold(i)
        if direction >= 0 and highs[i] >= hi_p:
            hi_i, hi_p = i, highs[i]
        if direction <= 0 and lows[i] <= lo_p:
            lo_i, lo_p = i, lows[i]

        if direction >= 0 and hi_p - lows[i] > thr and i - hi_i >= min_bars:
            confirm_high(i)
        elif direction <= 0 and highs[i] - lo_p > thr and i - lo_i >= min_bars:
            confirm_low(i)

    if include_provisional and pivots:
        last = pivots[-1]
        # 暫定轉折的方向永遠與最後一個確認轉折相反。
        if last.kind == "H" and lo_i > last.index:
            pivots.append(Pivot(lo_i, lo_p, "L", confirmed=False))
        elif last.kind == "L" and hi_i > last.index:
            pivots.append(Pivot(hi_i, hi_p, "H", confirmed=False))

    return pivots


# ── 型態比對 ────────────────────────────────────────────────────────

@dataclass
class HarmonicMatch:
    pattern: str
    direction: str                    # "BULLISH"（D 是低點）或 "BEARISH"
    points: dict[str, dict]           # X/A/B/C/D 各自的 index 與 price
    ratios: dict[str, float]
    prz: tuple[float, float]          # 潛在反轉區
    confidence: float                 # 0–1，比例落在容差帶中央的程度
    bars_since_d: int
    #: D 點是否已被 ZigZag 確認。**多數有交易價值的型態都是 False** ——
    #: 等 D 確認完行情通常也走完了。False 不代表比較差，只代表還可能延伸。
    d_confirmed: bool = True
    notes: list[str] = field(default_factory=list)


def _score(value: float, band: tuple[float, float]) -> float | None:
    """比例落在區間內得分，越靠中央越高；落在區間外回 None。

    只用「在不在區間內」當布林值會丟掉很多資訊：剛好壓線的 Gartley
    跟正中紅心的 Gartley 在畫面上是同一件事，但可信度差很多。
    """
    lo, hi = band
    if not (lo <= value <= hi):
        return None
    if hi == lo:
        return 1.0
    centre = (lo + hi) / 2
    half = (hi - lo) / 2
    return 1.0 - abs(value - centre) / half


def _match_one(pivots: list[Pivot], spec: PatternSpec) -> HarmonicMatch | None:
    """拿最後五個轉折點對一個型態。配不上回 None。"""
    x, a, b, c, d = pivots
    xa = abs(a.price - x.price)
    ab = abs(b.price - a.price)
    bc = abs(c.price - b.price)
    cd = abs(d.price - c.price)
    xc = abs(c.price - x.price)
    if min(xa, ab, bc, cd) == 0:
        return None

    r_b = ab / xa
    r_c = bc / ab
    r_d = cd / bc

    # ⚠️ D 的比例量的是「從 anchor 往回走了 XA（或 XC）的幾成」，
    # **不是** D 離 X 多遠。Gartley 的 0.786 意思是 D 回撤掉 XA 的 78.6%，
    # 也就是 |A−D| / |A−X| = 0.786 —— 此時 |D−X|/|XA| 是 0.214。
    # 寫成後者的話：Gartley 永遠配不出來，而 Butterfly（D 要穿過 X）
    # 這種延伸型態會配出完全錯誤的點。這是整個檔案最容易錯的一行。
    if spec.d_against == "XC":
        anchor, base = c.price, xc
    else:
        anchor, base = a.price, xa
    if base == 0:
        return None
    r_ad = abs(anchor - d.price) / base

    scores = [_score(r_b, spec.b_xa), _score(r_c, spec.c_ab), _score(r_ad, spec.d_ratio)]
    if spec.d_bc is not None:
        scores.append(_score(r_d, spec.d_bc))
    if any(s is None for s in scores):
        return None

    # 潛在反轉區：從 anchor 朝 D 的方向，投影出比例區間的兩端。
    # 多頭型態的 D 是低點，所以是從 A（高點）往下量。
    sign = -1 if d.kind == "L" else 1
    lo_r, hi_r = spec.d_ratio
    prz = tuple(sorted((anchor + sign * lo_r * base, anchor + sign * hi_r * base)))

    direction = "BULLISH" if d.kind == "L" else "BEARISH"
    return HarmonicMatch(
        pattern=spec.name,
        direction=direction,
        points={
            k: {"index": p.index, "price": round(p.price, 8)}
            for k, p in (("X", x), ("A", a), ("B", b), ("C", c), ("D", d))
        },
        ratios={
            "AB_XA": round(r_b, 4), "BC_AB": round(r_c, 4), "CD_BC": round(r_d, 4),
            # 從 anchor（XA 型態是 A、XC 型態是 C）往回撤掉基準段的幾成
            f"D_retrace_{spec.d_against}": round(r_ad, 4),
        },
        prz=(round(prz[0], 8), round(prz[1], 8)),
        confidence=round(sum(scores) / len(scores), 4),
        bars_since_d=0,
    )


def detect(
    highs: list[float], lows: list[float], atr_series: list[float | None],
    *, max_bars_since_d: int = 10, atr_mult: float = 3.0,
) -> list[HarmonicMatch]:
    """偵測最近完成的諧波型態，信心由高到低排序。

    `max_bars_since_d` 是**保鮮期**：D 點已經過去二十根 K 棒的型態，
    就算比例完美也沒有交易價值了 —— 該反轉的早就反轉完或失敗了。
    不設這個上限的話，掃到的多半是歷史型態，而它們看起來跟剛完成的
    一模一樣。這是諧波實作最常見的誤用。
    """
    pivots = find_pivots(highs, lows, atr_series, atr_mult=atr_mult)
    if len(pivots) < 5:
        return []

    last_index = len(highs) - 1
    results: list[HarmonicMatch] = []

    # 只看最後幾組五點窗口。往回掃太多組會配出一堆早就失效的歷史型態。
    for start in range(max(0, len(pivots) - 8), len(pivots) - 4):
        window = pivots[start:start + 5]
        if len({p.kind for p in window[::2]}) != 1:
            continue                      # X/B/D 必須同型（都高或都低）
        bars_since_d = last_index - window[-1].index
        if bars_since_d > max_bars_since_d:
            continue
        for spec in PATTERNS:
            match = _match_one(window, spec)
            if match is None:
                continue
            match.bars_since_d = bars_since_d
            match.d_confirmed = window[-1].confirmed
            if not window[-1].confirmed:
                match.notes.append(
                    "D 點尚未被 ZigZag 確認 —— 價格還可能繼續往同方向延伸，"
                    "型態會因此失效或變成另一種型態"
                )
            results.append(match)

    results.sort(key=lambda m: m.confidence, reverse=True)
    return results
