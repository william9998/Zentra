# ADR-0001：沙盒隔離策略

| | |
|---|---|
| 狀態 | **已採納 (Accepted)** |
| 日期 | 2026-09-20 |
| 決策者 | @architect |
| 相關立場 | P1（使用者程式碼不在平台行程執行）、P4（沙盒無長期憑證） |

> ⚠️ **部分被 [ADR-0005](ADR-0005-mvp-single-host-scope.md) 取代（2026-09-20）。**
> 本 ADR 的**威脅模型與五層縱深防禦原則仍然有效**，但它假設的是「公開多租戶、執行不可信程式碼」。
> MVP 的落地條件是**內部工具 / 單租戶 / 只跑信任的程式碼**，因此 gVisor 專用節點池、三個 Proxy、
> Vault 與 Warm Pool 在 MVP 階段**不實作**，L2 核心隔離降級為 Docker 原生機制（無 gVisor）。
> **要跑任何不是自己寫的程式碼之前，回來讀這份 ADR。**

## 背景

平台允許使用者**上傳任意 Python 程式碼**並執行。這等同於在我們的基礎設施上執行不可信程式碼。
威脅模型必須假設：**上傳者是惡意的，且他知道我們的架構**。他的目標可能是讀取其他租戶資料、竊取平台憑證、橫向移動到內網、或挖礦。

同時我們有效能需求：冷啟動 P95 ≤ 3s，有 Warm Pool 時 ≤ 800ms。

## 考慮過的選項

### 選項 A：同行程執行 + Python 層沙箱（RestrictedPython / AST 白名單）
- ✅ 啟動幾乎零成本
- ❌ **CPython 沒有安全的行程內沙箱**。歷史上所有 Python 層沙箱都被逃逸過（`__subclasses__` 走訪、C extension、frame 操作）。
- ❌ 使用者需要 numpy/pandas 等 C extension，一旦允許就沒有邊界可言
- ❌ 無法限制 CPU / 記憶體，一個無窮迴圈拖垮整個 worker

### 選項 B：普通容器（runc）
- ✅ 生態成熟、啟動快（數百毫秒）
- ⚠️ **共用宿主核心**。容器逃逸 CVE 每年都有（runc、cgroups、核心 LPE）。
- ⚠️ 對「執行任意不可信程式碼」這個場景，單層容器隔離不足

### 選項 C：microVM（Firecracker / Kata）
- ✅ 隔離最強（獨立核心）
- ❌ 冷啟動較慢（約 1–3s），且需要巢狀虛擬化支援，在部分雲端託管 K8s 上不可用
- ❌ 記憶體開銷大，密度低

### 選項 D：gVisor（runsc）+ 多層縱深防禦 ← **選這個**
- ✅ 使用者態核心攔截 syscall，攻擊面遠小於直接接觸宿主核心
- ✅ 啟動約 100–300ms，可做 Warm Pool
- ✅ 以 RuntimeClass 整合進 K8s，不需巢狀虛擬化
- ⚠️ 部分 syscall 不支援（少數 C extension 可能受影響）
- ⚠️ syscall 密集的工作負載有 10–30% 效能損失

## 決定

**採用選項 D：gVisor + 五層縱深防禦。**

單靠 gVisor 不夠 —— 我們假設**每一層都可能被突破**，所以五層都必須存在：

| 層 | 措施 |
|---|---|
| L1 網路 | NetworkPolicy 預設 deny；封鎖 metadata 端點與內網 CIDR；唯一出口 Egress Proxy |
| L2 核心 | RuntimeClass=gvisor；seccomp 白名單；drop ALL caps；no-new-privileges；non-root |
| L3 資源 | cgroups v2 限 CPU/Mem/pids；無 swap；wall-clock timeout；stdout 上限 |
| L4 檔案 | `/app` 唯讀；`/workspace` tmpfs 有配額；無 hostPath、無 docker.sock |
| L5 應用 | SDK 攔截層；import 黑名單；禁自建 LLM client |

**兩條附帶硬規則**：
1. **沙盒專用 node pool**（taint/toleration），不與平台元件同機。即使逃逸到宿主，鄰居也只有其他沙盒。
2. **沙盒絕不重用**。一 Run 一盒，結束即銷毀。重用等於接受跨租戶狀態殘留風險，省下的啟動時間不值得。

**憑證邊界（P4）**：長期憑證只存在於 Tool / LLM / Egress Proxy。沙盒只拿 TTL ≤ 60s 的短期 token，且該 token 只能對 Proxy 使用。
**因此，即使沙盒被完全攻陷，攻擊者拿到的也只是一個 60 秒後失效、且只能呼叫已授權工具的 token。**

## 後果

### 正面
- 逃逸需要同時突破多層，成本極高
- 資源限制讓惡意程式碼無法影響鄰居
- 憑證邊界讓「沙盒被攻陷」的損失可控且可稽核

### 負面（誠實列出）
- **效能損失 10–30%**（syscall 密集場景更明顯）。接受，因為安全優先於效能。
- **部分 Python 套件可能不相容** gVisor。需要維護相容性清單，Build 階段就要測。
- **維運複雜度上升**：需要專用 node pool、RuntimeClass、NetworkPolicy、三個 Proxy。這是必要成本。
- **Warm Pool 增加閒置成本**。以命中率與冷啟 SLO 為依據調整池子大小。

### 何時該重新檢討
- gVisor 的相容性問題影響到超過 5% 的使用者 Agent
- 出現 gVisor 本身的嚴重逃逸漏洞
- 雲端環境提供了啟動足夠快的 microVM（屆時可考慮升級到選項 C）

## 驗收

每一層都必須有自動化的「攻擊必須失敗」測試，納入 CI 回歸。
清單見 `.claude/commands/verify.md` 的隔離清單。**測試沒綠，沙盒不准上線。**
