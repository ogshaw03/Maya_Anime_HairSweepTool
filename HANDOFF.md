# Handoff — Maya Anime Hair Sweep Tool

このドキュメントは、Claude Code on the web の作業セッションから
ローカル Claude Code セッションへ本ツールの開発を引き継ぐためのものです。
このファイルを読めば、直近までの状況・設計判断・次にやるべきことが把握できます。

---

## 1. リポジトリ情報

- **リポジトリ**: `ogshaw03/Maya_Anime_HairSweepTool`
- **開発ブランチ**: `claude/maya-anime-hair-sweep-5zpemq`
- **既定 default branch**: `main`（現時点で `main` にはまだ何もマージされていない）
- **現在のバージョン**: `0.2.0`
- **最新コミット**:
  - `194ed43` — Phase 2: hair strand duplication
  - `82949cd` — Add hot-update install.py + UI Update-from-GitHub flow
  - `009dacc` — Add files via upload（`maya-hot-update-patterns.md`）
  - `a1f1e95` — Phase 1: Sweep Mesh based anime hair tool scaffold

---

## 2. 何ができているか（Phase 別）

### ✅ Phase 1 — Sweep Mesh ベースの基本ツール
- 選択 Curve から `sweepMeshCreator` で毛束を生成
- Profile 切替（Round / Oval / Flat / Sharp / Diamond / TearDrop / Custom）
- Thickness / Width / Height / Root / Middle / Tip / Twist / Rotation / Subdivision
- 生成毛束は `HairGroup` 配下に集約
- ツールが作った Sweep には `animeHairTool` ブール属性を追加してタグ付け

### ✅ Phase 2 — Duplicate
- Curve / Mesh / Sweep ノードのどれを選んでも複製可
- Guide Curve + Sweep 属性 + `scaleProfile` ランプ + Custom Profile Curve を完全独立コピー
- Count 指定でまとめて複製、Offset で位置ずらし
- 全処理を 1 Undo に統合

### 🟨 Phase 3 — Batch Edit
- 基本の Absolute / Relative 一括編集は実装済み
- Undo チャンク統合 / Group 選択 / 部分未実装

### ⬜ Phase 4 — Preset / Hair Library
### ⬜ Phase 5 — Hair Group（Front / Side / Back など）
### ⬜ Phase 6 — Braid Generator など手続き型毛束

### ✅ 配布インフラ（Phase 別ではなく横断）
- `install.py` をドラッグ&ドロップで `maya_hair_tool/*` を自動配置
- Hair Builder UI 下部「GitHub から更新」ボタン
- シェルフボタン右クリック → Update from GitHub
- SHA-pinned raw URL / atomic write / `__pycache__` clean / `sys.modules` flush /
  `evalDeferred` 3 段階 — すべて `maya-hot-update-patterns.md` 準拠

---

## 3. リポジトリ構成

```
Maya_Anime_HairSweepTool/
├── install.py                       # ドラッグ&ドロップ 用インストーラ
├── maya_hair_tool/                  # ツール本体
│   ├── __init__.py                  # __version__, show()
│   ├── constants.py                 # Profile 定義・デフォルト値・命名規則
│   ├── sweep_utils.py               # sweepMeshCreator の生成 / 探索
│   ├── hair.py                      # 毛束作成、Attribute / Ramp 適用
│   ├── duplicate.py                 # Phase 2: 独立コピー
│   ├── batch.py                     # Absolute / Relative プリミティブ
│   └── ui.py                        # Hair Builder + Update フロー
├── maya-hot-update-patterns.md      # ★★ 配布・更新の設計ノート（必読）
├── docs/design.md                   # 設計メモ
├── README.md                        # ユーザー向け説明
└── HANDOFF.md                       # 本ファイル
```

---

## 4. 引き継ぎ時に絶対守るルール

### 4-1. 開発ブランチ

- **全ての作業は `claude/maya-anime-hair-sweep-5zpemq` で行う**
- 直接 `main` に push しない
- 別ブランチが必要になったら都度ユーザーに確認

### 4-2. `maya-hot-update-patterns.md` を尊重する

- **ホットアップデート関連の実装は必ずこのファイルを読んでから修正**
- 特に以下の関数群は「§X-Y の対策」というコメント付きなので、
  意図なくいじらない:
  - `install.py`: `_atomic_write_bytes`, `_resolve_latest_sha`,
    `_fetch_package`, `_clean_pycache`, `_flush_imports`,
    `_add_shelf_button`, `_SHELF_UPDATE_CMD`
  - `maya_hair_tool/ui.py`: `_resolve_latest_sha`,
    `update_from_github`, `_run_update`, `_reopen_after_update`

### 4-3. `_REMOTE_FILES` の同期義務（§1-10）

- **`maya_hair_tool/` 配下に `.py` を追加したら、必ず同じコミットで
  `install.py` の `_REMOTE_FILES` タプルに追記する**
- 忘れると次回 Update した後にユーザー側で `ModuleNotFoundError`

### 4-4. §8（オプション拡張）を勝手に足さない

- バージョンロールバック、新版通知、Changelog 表示、Update badge 等の
  §8 拡張は、**ユーザーが明示的に希望しない限り実装しない**
- 話題になった場合は §8-5 のテンプレートに沿って A/B 方式を提示して選ばせる

### 4-5. Sweep Mesh 標準ノードのみ使う

- 独自ノード（プラグイン）を作らない
- `sweepMeshCreator` の attribute で表現できないものは、
  Curve Warp / MASH など既存 Maya 機能で構成する
- 別 Maya セッションで開いてもシーンが壊れないことを最優先

### 4-6. Executing actions with care

- `git push --force`, `git reset --hard`, `rm -rf`, ブランチ削除など
  破壊的操作は必ずユーザー確認を挟む
- ユーザーが 1 度承認しても、別の文脈では都度確認する

---

## 5. モジュール別の設計ポイント

### `constants.py`
- Profile 名 → `sweepMeshCreator.profilePolyType` enum (0=Round, 1=Square,
  3=Custom) のマップを保持
- Oval / Sharp / Diamond / TearDrop は同じ enum 値でも
  `scaleProfileY` や `rotateProfile` を変えて表現している

### `sweep_utils.py`
- `create_sweep_from_curve(curve, name_hint) -> (creator, mesh_xform)`
  が全ての生成の起点
- 生成された creator にはブール属性 `animeHairTool` を lock 付きで足す
  → 将来「ツールが作った毛束だけ」を検索する用途に使う
- `sweep_creators_from_selection()` は curve / mesh / sweep のどれからでも
  対応する creator を辿れる

### `hair.py`
- Root/Middle/Tip の Taper は `scaleProfile` ランプに 3 エントリを書き込む
- `_apply_settings()` は `thickness` 指定と `width`/`height` 個別指定を
  両方受ける。同時指定は上書き順に注意（Thickness → Width → Height）

### `duplicate.py`
- 属性コピーは `_SCALAR_ATTRS` タプルで管理（`listAttr` 総なめは危険）
- 入力接続で駆動されている属性は `connectionInfo(isDestination=True)` で除外
- `scaleProfile` ランプは「一旦全削除 → 再構築」がベスト
- Custom Profile Curve の attribute 名は Maya バージョン差があるので
  候補リストから存在するものを検出

### `batch.py`
- 現状 Absolute / Relative の基本 primitive のみ
- **Phase 3 で欲しい追加**:
  - `undoInfo(openChunk/closeChunk)` でまとめる
  - `scaleProfile` ランプに対する Relative（現状は「デフォルト 1.0 に
    スライダー値を乗算」の代替実装）
  - Group 単位 Batch（Phase 5 と統合）

### `ui.py`
- Class ベース（`HairBuilderUI`）に統一。widget ID は self に持たせる
- `WINDOW_NAME = _PACKAGE + "Win"` は `install.py._close_existing_window`
  の探し方と一致させるための命名。変えるなら install.py も同時に修正

---

## 6. 動作確認手順（Maya 上）

### 6-1. 初回インストール
1. `install.py` を Maya ビューポートにドラッグ → 完了ダイアログ
2. シェルフに `HairTool` ボタンが出る
3. クリックで Hair Builder 起動、タイトルに `v0.2.0` が表示

### 6-2. Phase 1 動作
1. `CV Curve Tool` で数本 Curve を描く
2. Curve を選択 → `Create Hair from Selected Curves`
3. `HairGroup/<curve>_mesh` が生成される
4. Curve の CV を動かすとメッシュが追従

### 6-3. Phase 2 動作
1. 作った毛束（curve でも mesh でも OK）を選択
2. Duplicate パネルで Count=3, Offset=(1,0,0) → `Duplicate Selected Hair`
3. X 方向に 1/2/3 単位ずれて 3 本の別毛束が生成される
4. 元 Curve の CV を動かしても複製先は追従しない = 独立性 OK

### 6-4. Update フロー
1. コード修正 → push
2. UI 下部「GitHub から更新」または シェルフボタン右クリック → Update
3. ダイアログに `previous → current` バージョンが表示
4. UI が自動再オープン、Maya 再起動不要

### 6-5. ローカル開発モード
GitHub を経由せずローカルのリポジトリ内容をテストしたい場合:
```python
import os
os.environ["MAYA_HAIR_TOOL_USE_LOCAL"] = "1"
exec(open(r"/path/to/repo/install.py").read())
```

---

## 7. 既知の未解決事項 / TODO

### 7-1. Batch Edit の Relative 表現
`ui.py._on_batch_apply` の Relative モードで、`scaleProfile` ランプの
Root/Tip を「元の値 × factor」で正しく再現できていない
（現状はデフォルト 1.0 を基準に上書き）。Phase 3 で ramp 全エントリ
readback → 各エントリを乗算 → 書き戻す実装に置き換える予定。

### 7-2. `_REMOTE_FILES` の自動列挙
現状は手動同期。将来的には GitHub API の tree walk で自動化する余地あり
（`maya-hot-update-patterns.md` §1-10 の教訓）。

### 7-3. Custom Profile Curve の attribute 名
Maya 2023 / 2024 で attribute 名が違う可能性。実機での動作確認を
してから attribute 名候補を絞り込みたい。

### 7-4. GitHub `main` へのマージ
現時点で `main` は空。Phase 3 まで実装するか、Phase 2 で一度
PR を出してマージするかは未定。マージ前に配布 URL は動作しない。

### 7-5. Maya 2022 対応
`sweepMeshCreator` は Maya 2022 で導入。動作するはずだが実機未検証。

---

## 8. 参考ドキュメント（必読順）

1. **`maya-hot-update-patterns.md`** — 配布/更新の設計・落とし穴集
   （§1 の失敗パターンは特に §1-7, §1-8, §1-9, §1-10 を頭に入れる）
2. **`docs/design.md`** — 元の構想メモから抽出した設計ポイント
3. **`README.md`** — ユーザー向け説明。仕様変更時は同時更新
4. **`HANDOFF.md`**（本ファイル）— 引き継ぎ時に更新

---

## 9. コミット・PR 規約

- コミットメッセージは英語、命令形一行 + 空行 + 詳細段落
- Phase を進めた時のフォーマット例:
  ```
  Phase N: <feature name>

  <詳細 3-6 行>
  ```
- PR を作る場合は、`.github/PULL_REQUEST_TEMPLATE.md` を先に確認
  （現状は無い）
- **ユーザーが明示的に頼まない限り PR を作らない**

---

## 10. 次にやること候補（優先度順の目安）

1. **Phase 3 完成** — Batch Edit の Undo チャンク統合、`scaleProfile`
   ランプ Relative の正しい実装
2. **`main` への初回マージ** — Phase 2 まで完成しているので、この
   タイミングで PR を出す価値あり（配布 URL が動くようになる）
3. **Phase 4 着手** — Preset / Hair Library（サムネイル管理は後回し可）
4. **Phase 5** — Hair Group（Maya の transform group ベースで開始）
5. **Phase 6** — Braid Generator（後回し可）

---

## 11. ハンドオフ時の質問可能領域

引き継ぎ後のセッションでユーザーから次のような要求が来た場合は
「独断で決めずにユーザー確認を求める」項目:

- Phase 4 で Library の保存形式（`.ma` / `.mb` / JSON+curve data）を選ぶ場面
- Phase 6 の Braid Generator のアプローチ（MASH 系 vs 手動 CV サンプリング）
- §8 のオプション拡張（新版通知等）
- Maya のバージョン依存で挙動が変わる箇所（attribute 名の吸収など）
- 破壊的な git 操作（force push, main への直 push, ブランチ削除）
