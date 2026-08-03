# Session Log — Maya Anime Hair Sweep Tool

自宅 = local セッションと出先 = web セッションで交互に開発する
プロジェクトの **セッション間引き継ぎログ**。追記式・最新が下。

各セッションの開始時にこのファイルの末尾を読み、終了時に
新エントリを追記して push する。運用ルールは
`.claude/commands/session-sync.md` を参照 (スラッシュコマンド
`/session-sync` で読み込める)。

粒度は「議論の歴・検討した代替案・なぜその方針か」まで含む中粒度。
コードの What は git log に任せ、この log には Why と文脈を残す。

## エントリテンプレ

```
## YYYY-MM-DD (web|local) — <一行タイトル>

### やったこと
- <箇条書き>

### 検討した代替案
- <採用しなかった選択肢と理由。無ければ「特になし」>

### 悩みどころ / 未確定
- <次のセッションが判断する点、実機確認待ち等>

### 次にやること
- [ ] (担当: web|local) <具体的なアクション>
```

---

<!-- 追記はこの下から。最新エントリが下に来る。 -->

## 2026-08-04 (local) — セッション同期運用の設計 + 骨格構築

### やったこと
- 引き継ぎ資料一式 (HANDOFF.md, maya-hot-update-patterns.md,
  docs/design.md, README.md, maya_hair_tool/ 全 6 モジュール) を
  通読、現状把握: v0.2.0、Phase 2 完成、Phase 3 途中
- 2 拠点開発 (自宅 = local, 出先 = web) の運用方針を策定
  - 分担: 役割分担型 (同一ブランチを共有、担当ファイル宣言は不要)
  - 同期: セッション間で SESSION_LOG.md (追記式 1 ファイル) 経由
  - 粒度: 「議論の歴・検討した代替案・なぜその方針か」まで残す中粒度
- スラッシュコマンド `/session-sync` を新設 (プロジェクト固有ルール
  非依存な汎用版、他プロジェクトでも使用可)
  - プロジェクト版: `.claude/commands/session-sync.md`
    (この repo に commit、web セッションは git 経由で入手)
  - ユーザー版: `~/.claude/commands/session-sync.md`
    (local 側の他プロジェクトからも呼び出せる)
- HANDOFF.md § 12 に本ワークフローの参照を追記
- 上記 3 ファイルは `7b8ec17` にコミット済み

### 検討した代替案
- **HANDOFF.md 逐次更新型** — 静的ドキュメントに議論履歴を混ぜると
  時系列復元が git log 頼みになる、上書きで議論の細部が消える。却下
- **SESSION_LOG.md + NEXT.md の 2 ファイル** — 「次にやること」を
  ピンポイントで見やすくなるが、ファイル管理が増える。ミニマル方針で却下
- **機能ごとに別ブランチで並行** — 独立性は高いがマージ調整コストが
  乗る。同時編集が少ないので同一ブランチで足りると判断
- **プロジェクト固有コマンド `/hair-sync`** — 一旦 project-level で
  作成したが、「他の開発でも活用したい」というユーザー要望で汎用
  `/session-sync` に置き換え、user-level にも設置

### 悩みどころ / 未確定
- **ブランチ運用** — この commit は worktree branch
  `claude/maya-hair-sweep-handoff-daf0ba` 上にある。HANDOFF § 4-1 では
  「全作業は `claude/maya-anime-hair-sweep-5zpemq` で」とあるので、
  次に local セッションを開いたときにこのブランチを main dev ブランチに
  fast-forward merge してから作業開始するのが望ましい
- 初手を「local で v0.2.0 実機動作確認」から始めるか、「先に main への
  PR/merge を通す」から始めるかは未決
- Custom Profile Curve の attribute 名の Maya version 差
  (HANDOFF § 7-3) は local 実機での実測待ち
- Phase 3 の `scaleProfile` ramp readback 挙動も local 実機での確認が必要
  (HANDOFF § 7-1)

### 次にやること
- [ ] (担当: local) `claude/maya-hair-sweep-handoff-daf0ba` を
      `claude/maya-anime-hair-sweep-5zpemq` に fast-forward merge
      (両者は linear、divergence なし)
- [ ] (担当: local) `/session-sync` 実行 → v0.2.0 を実機インストール
      (`MAYA_HAIR_TOOL_USE_LOCAL=1` でローカル配布モード) →
      HANDOFF § 6-2, § 6-3 の手順を通し実行 → 結果を SESSION_LOG に記録
- [ ] (担当: 次に開いた側) Phase 3 実装と main への初回 merge の
      優先順位をユーザーに確認して着手

---

## 2026-08-04 (local) — スモークテスト + 過去エントリ訂正 + web セッション対応メモ

### やったこと
- `/session-sync` を初回実行、Step 1 (git status / SESSION_LOG.md tail /
  ref 比較) が正常動作することを確認
- Fast-forward merge 完了 (`7ac5b38..1b5657f`) を HEAD 比較で再確認、
  remote と完全同期
- 直前エントリの `(web)` タグを `(local)` に訂正 (自己認識ミスの修正)。
  実際は前 web セッションから引き継ぎを受けた本 local セッションが
  `/session-sync` 骨格構築・SESSION_LOG.md 新設・HANDOFF §12 追記を
  全て実施していた

### 検討した代替案
- 特になし (smoke test + 訂正回)

### 悩みどころ / 未確定
- **web セッションで /session-sync が discovery されない問題** —
  元開発の cloud Claude Code セッションでは `/session-sync` が
  未認識エラーになる。原因分析:
  - `.claude/commands/session-sync.md` を追加したのは commit `7b8ec17`
  - web セッションはそれ以前の ref で開かれた sandbox なので
    ファイルが repo に存在しない
  - user-level `~/.claude/commands/` は cloud sandbox に反映されない
    (ローカル PC の dotfile)
  - Claude Code は起動時に commands を scan するので、
    session 中に file を追加しても runtime では認識されない
- 上記への対処: web 側で `git pull` → 一度セッションを閉じて開き直す
- フォールバック: 手動で「SESSION_LOG.md 末尾 + HANDOFF §4 を読んで
  workflow に従って」と指示すれば slash command 無しでも同等動作

### 次にやること
- [ ] (担当: user) web セッションで
      `git pull origin claude/maya-anime-hair-sweep-5zpemq` 実行 →
      セッション再起動 → `/session-sync` discovery 確認
- [ ] (担当: local, 次回) v0.2.0 実機インストール
      (`MAYA_HAIR_TOOL_USE_LOCAL=1`) → HANDOFF § 6-2 / § 6-3 通し実行 →
      結果を SESSION_LOG に記録
- [ ] (担当: 次に開いた側) Phase 3 実装と main への初回 merge の
      優先順位をユーザーに確認して着手
