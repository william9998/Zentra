"""訊號合成 —— 把一堆指標變成一個「要不要動、動多少」的決定。

## 三條讓它像業界做法、而不像網路教學的規則

**1. 只用已收盤的 K 棒。**
形成中的那根會一路變，用它算出來的訊號會在盤中閃爍然後消失。
回測時你拿到的是那根的最終值（未來資訊），實盤拿到的是中間值 ——
這就是「回測漂亮、實盤失效」最常見的單一原因。
`prepare()` 會直接砍掉未收盤的那根，不給呼叫端選。

**2. 先判狀態，再判方向。**
順勢指標（EMA 排列、MACD）在盤整裡會一直來回假訊號；均值回歸指標
（RSI 超買超賣、布林軌道）在單邊趨勢裡會一路逆勢挨打。
所以先用 ADX 判斷「現在是哪種行情」，再決定**聽誰的**。
沒有這道過濾的順勢策略，在盤整期會連續停損到懷疑人生。

**3. 風險先於報酬。**
停損距離由 ATR 決定（不是拍腦袋的百分比），部位大小由停損距離反推，
讓每筆交易虧損的金額固定。這樣一來波動大的幣自動買少一點。

## 分數怎麼讀

`score` 是 −1 到 +1：正的看多、負的看空、絕對值是強度。
它是各因子的**加權平均**，權重隨狀態切換。
`score` 不是機率，也不是預期報酬 —— 它只是「幾個指標同不同意」的量化，
別把 0.8 讀成「八成會漲」。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import harmonics as hm
import indicators as ind


# ── 行情狀態 ────────────────────────────────────────────────────────

TRENDING = "TRENDING"
RANGING = "RANGING"
TRANSITION = "TRANSITION"


@dataclass
class Candles:
    """已收盤的 K 棒。欄位都是等長的 list，index 對齊。"""
    open_time: list[int]
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]

    def __len__(self) -> int:
        return len(self.close)


def prepare(rows: list[list], *, drop_unclosed: bool = True) -> Candles:
    """把正規化過的 OHLCV 列轉成 `Candles`，**並砍掉未收盤的那根**。

    `rows` 的每一列是 `[open_time_ms, open, high, low, close, volume, is_closed]`。
    `is_closed` 由資料源那一層判斷（不同交易所講法不同），這裡只負責執行。

    砍掉最後一根不是可選的保守設定，是正確性要求 —— 理由見模組開頭第 1 條。
    """
    if drop_unclosed:
        while rows and not rows[-1][6]:
            rows = rows[:-1]
    return Candles(
        open_time=[int(r[0]) for r in rows],
        open=[float(r[1]) for r in rows],
        high=[float(r[2]) for r in rows],
        low=[float(r[3]) for r in rows],
        close=[float(r[4]) for r in rows],
        volume=[float(r[5]) for r in rows],
    )


# ── 算出全部指標 ────────────────────────────────────────────────────

@dataclass
class Snapshot:
    """最後一根已收 K 棒上的所有指標值。"""
    close: float
    open_time: int
    rsi: float | None
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    macd_hist_prev: float | None
    ema_fast: float | None
    ema_slow: float | None
    ema_long: float | None
    atr: float | None
    atr_pct: float | None
    adx: float | None
    plus_di: float | None
    minus_di: float | None
    bb_upper: float | None
    bb_lower: float | None
    bb_mid: float | None
    bb_pctb: float | None          # 收盤價在通道裡的位置，0=下軌 1=上軌
    squeeze_pct: float | None      # 帶寬百分位，低=擠壓
    stoch_k: float | None
    stoch_d: float | None
    vol_z: float | None
    obv_slope: float | None        # 近 n 根 OBV 的線性斜率（已除以量的尺度）
    regime: str
    harmonics: list[hm.HarmonicMatch] = field(default_factory=list)


def _slope(values: list[float], lookback: int) -> float | None:
    """最小平方法斜率，再除以序列尺度做正規化。

    不正規化的話 OBV 斜率的量級是「成交量/根」，BTC 跟小幣差好幾個數量級，
    沒辦法用同一個門檻判斷。
    """
    pts = values[-lookback:]
    n = len(pts)
    if n < 3:
        return None
    mean_x = (n - 1) / 2
    mean_y = sum(pts) / n
    denom = sum((i - mean_x) ** 2 for i in range(n))
    if denom == 0:
        return None
    slope = sum((i - mean_x) * (p - mean_y) for i, p in enumerate(pts)) / denom
    scale = max(abs(max(pts)), abs(min(pts)), 1e-9)
    return slope * n / scale


def analyse(c: Candles, cfg: dict) -> Snapshot:
    """算完所有指標，回最後一根已收 K 棒的快照。"""
    rsi_v = ind.rsi(c.close, cfg["rsi_period"])
    macd_l, macd_s, macd_h = ind.macd(
        c.close, cfg["macd_fast"], cfg["macd_slow"], cfg["macd_signal"])
    ema_f = ind.ema(c.close, cfg["ema_fast"])
    ema_s = ind.ema(c.close, cfg["ema_slow"])
    ema_l = ind.ema(c.close, cfg["ema_long"])
    atr_v = ind.atr(c.high, c.low, c.close, cfg["atr_period"])
    adx_v, pdi, mdi = ind.adx(c.high, c.low, c.close, cfg["adx_period"])
    bb_m, bb_u, bb_l, bb_w = ind.bollinger(c.close, cfg["bb_period"], cfg["bb_mult"])
    sq = ind.squeeze_percentile(bb_w, cfg["squeeze_lookback"])
    k, d = ind.stochastic(c.high, c.low, c.close, cfg["stoch_period"])
    vz = ind.volume_zscore(c.volume, cfg["volume_period"])
    obv_v = ind.obv(c.close, c.volume)

    i = len(c) - 1
    close = c.close[i]
    atr_now = atr_v[i]
    adx_now = adx_v[i]

    if adx_now is None:
        regime = TRANSITION
    elif adx_now >= cfg["adx_trend_min"]:
        regime = TRENDING
    elif adx_now <= cfg["adx_range_max"]:
        regime = RANGING
    else:
        regime = TRANSITION

    pctb = None
    if bb_u[i] is not None and bb_l[i] is not None and bb_u[i] != bb_l[i]:
        pctb = (close - bb_l[i]) / (bb_u[i] - bb_l[i])

    harms = hm.detect(
        c.high, c.low, atr_v,
        max_bars_since_d=cfg["harmonic_max_age"],
        atr_mult=cfg["harmonic_atr_mult"],
    ) if cfg["harmonic_enabled"] else []

    return Snapshot(
        close=close, open_time=c.open_time[i],
        rsi=rsi_v[i],
        macd=macd_l[i], macd_signal=macd_s[i], macd_hist=macd_h[i],
        macd_hist_prev=macd_h[i - 1] if i > 0 else None,
        ema_fast=ema_f[i], ema_slow=ema_s[i], ema_long=ema_l[i],
        atr=atr_now, atr_pct=(atr_now / close * 100) if atr_now and close else None,
        adx=adx_now, plus_di=pdi[i], minus_di=mdi[i],
        bb_upper=bb_u[i], bb_lower=bb_l[i], bb_mid=bb_m[i], bb_pctb=pctb,
        squeeze_pct=sq[i], stoch_k=k[i], stoch_d=d[i], vol_z=vz[i],
        obv_slope=_slope(obv_v, cfg["obv_lookback"]),
        regime=regime, harmonics=harms,
    )


# ── 因子評分 ────────────────────────────────────────────────────────

@dataclass
class Factor:
    name: str
    score: float          # −1 到 +1
    weight: float
    detail: str

    @property
    def contribution(self) -> float:
        return self.score * self.weight


def _clamp(v: float) -> float:
    return max(-1.0, min(1.0, v))


def score_factors(s: Snapshot, cfg: dict) -> list[Factor]:
    """算出每個因子的方向分數。權重與**解讀方式**都依行情狀態切換。

    ## 這是整支 agent 最關鍵的設計，而且不只是調權重

    同樣的 RSI 30，在盤整裡是買點，在下跌趨勢裡只是「還在跌」。
    只調權重不夠 —— 第一版就是只調權重，結果**一段完美的線性上漲只拿 0.31 分，
    打不到 0.35 的門檻**：EMA 排列與 DMI 都給滿分 +1.0，
    但 RSI(100 超買)、布林(%B 0.91)、隨機(%K 86) 三個一起投反對票，
    硬生生扣掉 0.70。最強的趨勢反而不觸發，這顯然是錯的。

    修法照業界實際做法：**趨勢裡的均值回歸指標是進場時機，不是反對票。**

      * 順勢時，「與趨勢同向的極值」（上漲趨勢裡的超買）記 0 —— 它只表示
        「漲多了」，不表示要轉空。強趨勢本來就會長期超買，那是趨勢的特徵。
      * 「與趨勢反向的極值」（上漲趨勢裡的超賣）**保留完整分數** ——
        這就是「回檔買點」，是順勢策略真正想等的東西。
      * 盤整時沒有趨勢可依附，兩邊都照原本的均值回歸邏輯算。
    """
    trending = s.regime == TRENDING
    ranging = s.regime == RANGING
    w = cfg["weights"]
    # TRANSITION 兩邊都打折：說不清楚是哪種行情時，本來就不該重倉下注。
    trend_w = w["trend"] if trending else (w["trend"] * 0.3 if ranging else w["trend"] * 0.6)
    rev_w = (w["reversion"] if ranging
             else (w["reversion"] * cfg["reversion_mute_in_trend"] if trending
                   else w["reversion"] * 0.6))

    trend_factors: list[Factor] = []

    # —— 順勢：EMA 排列 ——
    if None not in (s.ema_fast, s.ema_slow, s.ema_long):
        if s.ema_fast > s.ema_slow > s.ema_long:
            v, why = 1.0, "多頭排列（快>慢>長）"
        elif s.ema_fast < s.ema_slow < s.ema_long:
            v, why = -1.0, "空頭排列（快<慢<長）"
        elif s.ema_fast > s.ema_slow:
            v, why = 0.4, "短均線在上，但長均線未跟上"
        else:
            v, why = -0.4, "短均線在下，但長均線未跟上"
        trend_factors.append(Factor("ema_alignment", v, trend_w, why))

    # —— 順勢：MACD 柱狀體的符號與變化方向 ——
    if s.macd_hist is not None and s.atr:
        # 用 ATR 正規化，不用價格。柱狀體的單位是價格，除以 ATR 之後無因次，
        # 而且自動跟著當下波動縮放 —— 同一組門檻在 BTC 與冷門幣都成立。
        # 除以價格的版本在低波動期會把微小的柱狀體放大成滿分。
        v = _clamp(s.macd_hist / (s.atr * cfg["macd_hist_atr_mult"]))
        why = f"柱狀體 {s.macd_hist:+.2f}（{s.macd_hist / s.atr:+.2f} ATR）"
        if s.macd_hist_prev is not None:
            if abs(s.macd_hist) > abs(s.macd_hist_prev):
                why += "，力道增強"
            else:
                # 動能在縮，把分數往中性拉 —— 方向對但力道在消退。
                v *= 0.5
                why += "，力道縮減"
        trend_factors.append(Factor("macd", v, trend_w, why))

    # —— 順勢：DI 方向 ——
    if None not in (s.plus_di, s.minus_di) and (s.plus_di + s.minus_di) > 0:
        v = _clamp((s.plus_di - s.minus_di) / (s.plus_di + s.minus_di))
        trend_factors.append(Factor("dmi", v, trend_w,
                                    f"+DI {s.plus_di:.1f} / −DI {s.minus_di:.1f}"))

    # 先定出趨勢方向，均值回歸因子才知道自己是「同向」還是「反向」。
    trend_bias = sum(f.contribution for f in trend_factors)
    trend_dir = 1 if trend_bias > 0 else (-1 if trend_bias < 0 else 0)

    def reversion(name: str, v: float, why: str) -> Factor:
        """順勢狀態下，把「與趨勢同向的極值」靜音成 0。"""
        if trending and trend_dir != 0 and v * trend_dir < 0:
            return Factor(name, 0.0, rev_w, f"{why}（順勢中，不視為反轉訊號）")
        if trending and trend_dir != 0 and v * trend_dir > 0:
            return Factor(name, v, rev_w, f"{why}（逆勢極值 → 回檔進場時機）")
        return Factor(name, v, rev_w, why)

    out = list(trend_factors)

    # —— 均值回歸：RSI ——
    if s.rsi is not None:
        ob, os_ = cfg["rsi_overbought"], cfg["rsi_oversold"]
        if s.rsi >= ob:
            v, why = -_clamp((s.rsi - ob) / (100 - ob)), f"RSI {s.rsi:.1f} 超買"
        elif s.rsi <= os_:
            v, why = _clamp((os_ - s.rsi) / os_), f"RSI {s.rsi:.1f} 超賣"
        else:
            v, why = 0.0, f"RSI {s.rsi:.1f} 中性"
        out.append(reversion("rsi", v, why))

    # —— 均值回歸：布林位置 ——
    if s.bb_pctb is not None:
        # %B > 1 表示收在上軌外。用 0.5 當中心線性映射到 ±1。
        v = _clamp((0.5 - s.bb_pctb) * 2)
        out.append(reversion("bollinger", v, f"%B {s.bb_pctb:.2f}"))

    # —— 均值回歸：隨機指標 ——
    if None not in (s.stoch_k, s.stoch_d):
        v = _clamp((50 - s.stoch_k) / 50) * 0.7
        cross = "K 在 D 上" if s.stoch_k > s.stoch_d else "K 在 D 下"
        out.append(reversion("stochastic", v, f"%K {s.stoch_k:.1f} {cross}"))

    # —— 量能確認：不指方向，只放大或縮小既有方向 ——
    if s.obv_slope is not None:
        out.append(Factor("obv", _clamp(s.obv_slope * cfg["obv_scale"]),
                          w["volume"], f"OBV 斜率 {s.obv_slope:+.4f}"))

    # —— 諧波 ——
    if s.harmonics:
        best = s.harmonics[0]
        v = best.confidence * (1 if best.direction == "BULLISH" else -1)
        # 未確認的 D 打折 —— 型態還可能延伸成別的東西。
        if not best.d_confirmed:
            v *= cfg["harmonic_unconfirmed_factor"]
        note = "（D 未確認）" if not best.d_confirmed else ""
        out.append(Factor("harmonic", v, w["harmonic"],
                          f"{best.pattern} {best.direction} 信心 {best.confidence:.2f}{note}"))

    return out


# ── 最終決定 ────────────────────────────────────────────────────────

@dataclass
class Decision:
    symbol: str
    interval: str
    direction: str           # "LONG" | "SHORT" | "FLAT"
    score: float
    triggered: bool
    regime: str
    price: float
    candle_time: int
    factors: list[Factor]
    entry: float | None = None
    stop: float | None = None
    targets: list[float] = field(default_factory=list)
    risk_pct: float | None = None       # 停損距離佔價格的百分比
    position_size: float | None = None  # 依風險預算算出的部位（計價幣單位）
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def decide(symbol: str, interval: str, s: Snapshot, cfg: dict) -> Decision:
    """把因子分數合成最終決定，並算出停損、目標與部位大小。"""
    factors = score_factors(s, cfg)
    total_w = sum(f.weight for f in factors)
    score = sum(f.contribution for f in factors) / total_w if total_w else 0.0

    threshold = cfg["signal_threshold"]
    direction = "LONG" if score >= threshold else ("SHORT" if score <= -threshold else "FLAT")

    d = Decision(
        symbol=symbol, interval=interval, direction=direction,
        score=round(score, 4), triggered=False, regime=s.regime,
        price=s.close, candle_time=s.open_time, factors=factors,
    )

    # 幾道否決：方向對了也可能不該動。
    if direction == "FLAT":
        d.reasons.append(f"合成分數 {score:+.3f} 未達門檻 ±{threshold}")
        return d

    if s.regime == TRANSITION and cfg["require_clear_regime"]:
        # ⚠️ `s.adx` 在暖機不足時是 `None`，而**那時 regime 也是 TRANSITION**，
        # 所以這一行是 None 唯一會漏出來的地方。直接格式化會 TypeError，
        # 而且只在「K 棒剛好不夠」時才炸 —— 使用者把 limit 設小、或盯一個
        # 剛上市的幣就會撞到。實測 30 根時炸過。
        if s.adx is None:
            d.warnings.append("K 棒數不足以算出 ADX，無法判斷行情狀態")
        else:
            d.warnings.append(f"ADX {s.adx:.1f} 落在趨勢與盤整之間，方向不明確")
        d.reasons.append("行情狀態不明確，不觸發")
        return d

    if s.atr_pct is not None and s.atr_pct > cfg["max_atr_pct"]:
        d.warnings.append(f"ATR 佔價 {s.atr_pct:.2f}% 超過上限 {cfg['max_atr_pct']}%")
        d.reasons.append("波動過大，停損距離會大到不合理，不觸發")
        return d

    if cfg["require_volume_confirm"] and (s.vol_z is None or s.vol_z < cfg["min_volume_z"]):
        got = f"{s.vol_z:.2f}" if s.vol_z is not None else "無"
        d.reasons.append(f"量能未確認（z={got} < {cfg['min_volume_z']}），不觸發")
        return d

    # —— 風險：停損由 ATR 決定，部位由停損反推 ——
    if s.atr is None:
        d.reasons.append("ATR 尚未暖機完成，無法計算風險，不觸發")
        return d

    k = cfg["atr_stop_mult"]
    entry = s.close
    stop = entry - k * s.atr if direction == "LONG" else entry + k * s.atr
    risk_per_unit = abs(entry - stop)
    d.entry = round(entry, 8)
    d.stop = round(stop, 8)
    d.risk_pct = round(risk_per_unit / entry * 100, 3)
    d.targets = [
        round(entry + (1 if direction == "LONG" else -1) * r * risk_per_unit, 8)
        for r in cfg["target_r_multiples"]
    ]
    if risk_per_unit > 0:
        # 固定金額風險 → 波動大的幣自動買少一點。
        d.position_size = round(cfg["risk_budget_quote"] / risk_per_unit * entry, 2)

    d.triggered = True
    top = sorted(factors, key=lambda f: abs(f.contribution), reverse=True)[:3]
    d.reasons = [f"{f.name}: {f.detail}" for f in top]
    if s.squeeze_pct is not None and s.squeeze_pct <= cfg["squeeze_alert_pct"]:
        d.warnings.append(
            f"布林帶寬處於近期低點（{s.squeeze_pct:.0f} 百分位）—— 波動擴張在即，"
            "突破方向未定"
        )
    return d


# ── 預設設定 ────────────────────────────────────────────────────────

#: 每一個值都可以用平台的 per-agent 設定覆寫（`ctx.config`）。
#: 放在這裡的是「沒設定時用什麼」，不是「唯一能用的值」。
DEFAULTS: dict = {
    "rsi_period": 14, "rsi_overbought": 70, "rsi_oversold": 30,
    "macd_fast": 12, "macd_slow": 26, "macd_signal": 9,
    #: 柱狀體達到幾倍 ATR 算滿分。0.5 表示「半個 ATR 的柱狀體」= 滿分順勢。
    "macd_hist_atr_mult": 0.5,
    "ema_fast": 12, "ema_slow": 26, "ema_long": 200,
    "atr_period": 14, "adx_period": 14,
    "adx_trend_min": 25, "adx_range_max": 20,
    "bb_period": 20, "bb_mult": 2.0, "squeeze_lookback": 120, "squeeze_alert_pct": 15,
    "stoch_period": 14, "volume_period": 20,
    "obv_lookback": 20, "obv_scale": 3.0,
    "harmonic_enabled": True, "harmonic_max_age": 5,
    "harmonic_atr_mult": 3.0, "harmonic_unconfirmed_factor": 0.6,
    "weights": {"trend": 1.0, "reversion": 1.0, "volume": 0.5, "harmonic": 0.6},
    #: 順勢狀態下均值回歸因子的權重折扣。它們此時的角色是進場時機，不是方向判斷。
    "reversion_mute_in_trend": 0.25,
    "signal_threshold": 0.35,
    "require_clear_regime": True,
    "require_volume_confirm": False, "min_volume_z": 0.5,
    "max_atr_pct": 8.0,
    "atr_stop_mult": 2.0, "target_r_multiples": [1.0, 2.0, 3.0],
    "risk_budget_quote": 100.0,
}


def merge_config(overrides: dict) -> dict:
    """把 `ctx.config` 疊到預設值上。只認得的 key 才收。

    不認得的 key **要留下來讓呼叫端回報**，不要安靜吃掉 ——
    使用者在 Console 上打錯一個字（`rsi_perio`），如果沒有任何提示，
    他會以為設定生效了，然後對著沒變的訊號困惑很久。
    """
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    unknown = []
    for key, value in (overrides or {}).items():
        if key not in cfg:
            unknown.append(key)
            continue
        if isinstance(cfg[key], dict) and isinstance(value, dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    cfg["_unknown_config_keys"] = unknown
    return cfg
