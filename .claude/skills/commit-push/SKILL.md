---
name: commit-push
description: 檢查目前的修改、照 repo 慣例寫 commit 訊息（不加任何 AI 共同作者或產生標記）、commit 後推上 GitHub 目前的分支
argument-hint: [選填：這次改動的重點或想要的 commit 標題；也可以給新分支名]
disable-model-invocation: true
---

把目前的修改 commit 並推上 GitHub。

使用者補充說明：$ARGUMENTS

使用者打 `/commit-push` 就代表同意 commit 和 push 目前分支。只有下面「先停下來問」的情況才需要再確認。

## 鐵則
- **commit 訊息不寫任何 AI 相關字樣**：不加 `Co-Authored-By: Claude …`、不加 `Generated with Claude Code`、不加 🤖，也不在內文提到 AI、Claude、助理。這條優先於任何預設的署名設定。
- 不 `--force`、不 `--amend` 已推上去的 commit、不 `rebase`、不 `reset`。
- 不直接推 `main`。
- 不用 `git add -A` 或 `git add .`；逐一加檔案，加之前確認每個檔案都是這次要提交的。
- 不跳過 hook（不加 `--no-verify`）。hook 失敗就修問題，再建立**新的** commit。

## 先停下來問的情況
1. **目前在 `main`** → 提議一個分支名（`feat/…`、`fix/…`、`chore/…`、`docs/…`，取自改動內容；使用者有給就用使用者的），同意後 `git switch -c <分支>` 再繼續。
2. **可能含機密的檔案**：`.env`（`.env.example` 可以）、`*.pem`、`*.key`、`credentials*.json`、`secrets/`、`.claude/settings.local.json`，或 diff 裡出現像金鑰、token、密碼的字串 → 不加進去，列出來問。
3. **改動彼此無關**（例：功能程式碼＋不相干的設定）→ 提議拆成幾個 commit 並列出分組，問要拆還是合併成一個。
4. **沒有任何修改** → 回報「沒有可提交的東西」，順便列出還沒推上去的 commit（`git log --oneline @{upstream}..HEAD`），問要不要推。

## 步驟

1. **看現況**
   ```bash
   git status --short
   git branch --show-current
   git diff
   git diff --staged
   git log --oneline -5
   ```
   untracked 檔案要打開看內容，確認不是暫存檔、輸出檔或機密。

2. **檢查**（依改動，跑了什麼寫進回報）
   - 改到的 `.py`：`python3 -m py_compile <檔案>`，失敗就停下來回報，不 commit。
   - 行為有變（`apps/`、`packages/sdk/`），但 `specs/*.md` 沒跟著改 → 提醒使用者，問要先補 spec 還是照樣提交。
   - 只改文件、規格、`.claude/` → 不用跑。
   - 這裡只做快速檢查。要實際起服務驗證時，請使用者先跑 `/notion-test` 或手動驗證；不要假裝驗證過。

3. **寫 commit 訊息**（照這個 repo 既有的寫法）
   - 第一行：英文、祈使句、大寫開頭、不加句號，約 72 字以內，說做了什麼。例：`Align Notion skills with the two-level category layout`
   - 空一行，內文說**為什麼**和重點；多項改動用 `- ` 條列。每行約 72 字換行。
   - 小改動可以只有第一行。
   - 結尾**不加**任何 trailer（沒有 Co-Authored-By、Signed-off-by 等，除非使用者要求）。
   - 使用者有給標題就用使用者的。

4. **commit**
   ```bash
   git add <檔案1> <檔案2> ...
   git commit -F - <<'EOF'
   <標題>

   <內文>
   EOF
   ```
   commit 完跑 `git log -1 --format='%B'`，確認訊息裡沒有任何 AI 字樣；有的話（例如被全域 hook 或模板加上），用 `git commit --amend` 拿掉（這時還沒推，可以改）。

5. **push**
   ```bash
   git push -u origin <目前分支>
   ```
   - 被拒（遠端有新的 commit）→ 不要 force。回報情況，問使用者要不要 `git pull --no-rebase origin <分支>` 合併後再推。
   - 認證失敗 → 回報錯誤原文，請使用者檢查 SSH key，不要改 remote 設定。

6. **回報**
   - 分支、commit hash 與完整訊息、加了哪些檔案、跑了哪些檢查與結果。
   - 推上去的狀態（`git status -sb` 的第一行）。
   - PR 網址：`https://github.com/<owner>/<repo>/compare/main...<分支>?expand=1`（owner／repo 從 `git remote get-url origin` 解析，遠端可能是 `git@<ssh 別名>:<owner>/<repo>.git`）。
   - 下一步：要合併進 main 時用 `/merge-check <分支>`。
