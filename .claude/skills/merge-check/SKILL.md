---
name: merge-check
description: 檢查 GitHub 上（和本機）還有哪些分支沒合併進 main，列出每個分支的狀態與下一步；指定分支時照 SOP 帶著把它合併進 main
argument-hint: [不給 = 只檢查；給分支名 = 照 SOP 合併那個分支；「清理」= 列出可刪的已合併分支]
disable-model-invocation: true
---

檢查分支合併狀態，需要時照 SOP 合併進 main。

使用者補充說明：$ARGUMENTS

## 鐵則
- 預設**只讀**：`git fetch` 和查詢指令可以直接跑。
- 以下動作每一次都要先問使用者，同意才做：`git push`、開 PR、合併 PR、刪除分支（本機或遠端）、`rebase`、`reset`、任何 `--force`。
- 不要直接 `git push origin main`。進 main 一律走 PR（這個 repo 的歷史是 `Merge pull request #N`）。
- 工作目錄有未提交的修改時，不要切分支、不要 rebase；先回報，問使用者要 commit 還是 stash。

## 1. 檢查（每次都做）

```bash
git fetch --prune origin
git status --short
git branch --show-current
```

對每個遠端分支（略過 `origin/HEAD`、`origin/main`）和每個本機分支，算出：

```bash
git rev-list --count origin/main..<分支>    # 領先 main 幾個 commit（> 0 = 有東西沒進 main）
git rev-list --count <分支>..origin/main    # 落後 main 幾個
git log -1 --format='%h %cs %s' <分支>      # 最後一個 commit
git rev-parse --abbrev-ref <本機分支>@{upstream}   # 有沒有推上 GitHub
```

- **領先 = 0** → 已合併（內容都在 main 裡）。
- **領先 > 0** → 有東西沒進 main。再用 `git cherry -v origin/main <分支>` 確認：開頭是 `-` 的 commit 代表 main 已經有同樣的改動（squash 或 cherry-pick 過），只有 `+` 的才是真的沒進去。
- 本機分支沒有 upstream → 還沒推上 GitHub。
- 本機 `main` 落後 `origin/main` → 提醒 `git switch main && git pull --ff-only`。

有 `gh` 時另外跑 `gh pr list --state open --json number,title,headRefName,url,isDraft,mergeable` 列出開著的 PR。沒有 `gh` 就說明看不到 PR 狀態（`brew install gh` 後 `gh auth login`），用下面的網址代替：
`https://github.com/<owner>/<repo>/compare/main...<分支>`，owner／repo 從 `git remote get-url origin` 解析（遠端可能是 `git@<ssh 別名>:<owner>/<repo>.git`）。

### 回報格式

```txt
目前分支：<分支>   未提交修改：<有／無，列檔案>

| 分支 | 在哪 | 領先 main | 落後 main | 最後 commit | 狀態 | 建議下一步 |
```

狀態只用：`已合併`、`未合併`、`只在本機`、`有未提交修改`、`有開著的 PR`。
最後一行總結：還有幾個分支要處理、哪些已合併可以刪（只列出，不刪）。

## 2. 合併 SOP（使用者給分支名時）

照順序做，每一步回報結果再往下：

1. **確認乾淨**：`git status --short` 沒東西。有 → 停下來，請使用者先跑 `/commit-push`（commit 的寫法以那個 skill 為準，不加任何 AI 署名）。
2. **跟上 main**：
   ```bash
   git switch <分支>
   git fetch origin
   git merge origin/main        # 預設用 merge，不改寫已推上去的歷史
   ```
   有衝突 → 列出衝突檔案和兩邊的差異，**問使用者要保留哪邊**，不要自己猜。分支從沒推上去且使用者偏好線性歷史時，才可以提議 `git rebase origin/main`。
3. **驗證**（這個專案沒有測試套件）：
   - 改到的 `.py`：`python3 -m py_compile <檔案>`。
   - 改到 `apps/`：重建對應服務（`docker compose up -d --build app` 或 `worker`），用 curl 或 Console 把改到的路徑實際走一次。
   - 只改文件、規格、`.claude/` → 不用跑服務，說明「文件改動，未執行」。
   - 行為有變，但 `specs/*.md` 沒跟著改 → 提醒使用者（規格要跟程式碼同一個 PR）。
   - 需要留紀錄時，提醒可以跑 `/notion-test`。
4. **推上去**（先問）：`git push -u origin <分支>`。合併 main 產生的 merge commit 用 git 預設訊息即可，不加任何 AI 署名。
5. **開 PR**（先問）：
   - 有 `gh`：`gh pr create --base main --head <分支> --title "<一行標題>" --body "<為什麼、改了什麼、怎麼驗證>"`。標題和 body 都不加任何 AI 署名或產生標記。
   - 沒有 `gh`：給使用者 compare 網址，請使用者在網頁上開。
6. **合併 PR**：預設由使用者在 GitHub 網頁按 **Create a merge commit**（跟現有歷史一致）。使用者明確要你合併時才用 `gh pr merge <號碼> --merge`。
7. **收尾**：
   ```bash
   git switch main
   git pull --ff-only
   git rev-list --count origin/main..<分支>   # 要是 0
   ```
   是 0 才提議刪分支（先問）：`git branch -d <分支>`（不要用 `-D`）、`git push origin --delete <分支>`。

## 3. 「清理」模式
列出 `領先 main = 0` 的本機與遠端分支，附最後 commit 日期，問使用者要刪哪些。只刪使用者點名的那些，本機用 `git branch -d`。
