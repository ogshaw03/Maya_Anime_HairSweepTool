# Maya Anime Hair Sweep Tool

Maya の Curve + Sweep Mesh をベースに、アニメ・セルルック向けの髪の毛を
効率よく作成するための Python ツールです。

Maya 標準の `sweepMeshCreator` をそのまま利用し、独自メッシュ生成器を作
らないため、ツールが無い環境でシーンを開いてもデータが壊れにくい構成に
なっています。

対応バージョン: **Maya 2023 / Python 3**（Sweep Mesh 機能があれば 2022
以降でも動作します）。

---

## インストール（ドラッグ & ドロップ 1 発）

配布されるファイルは `install.py` **1 つだけ** です。以降、Maya の再起動なし
で GitHub 最新版に更新できます。詳細な設計・落とし穴の理由は
[`maya-hot-update-patterns.md`](./maya-hot-update-patterns.md) を参照。

1. GitHub raw から `install.py` を保存
   `https://raw.githubusercontent.com/ogshaw03/Maya_Anime_HairSweepTool/main/install.py`
2. **Maya のビューポートに `install.py` をドラッグ&ドロップ**
3. 自動的に:
   - `maya_hair_tool/` が Maya ユーザースクリプトフォルダにダウンロードされる
   - シェルフに `HairTool` ボタンが追加される
   - 完了ダイアログにインストール前後のバージョンが表示される

### 起動

- **左クリック** シェルフの `HairTool` ボタン → Hair Builder ウィンドウ

### アップデート

Maya を再起動せずに GitHub 最新版に差し替えられます。**3 通り** の導線を
用意しています（どれも同じ結果）:

1. Hair Builder ウィンドウ下部の「**GitHub から更新**」ボタン
2. シェルフボタンを**右クリック** → `Update from GitHub`
3. Script Editor から `install.py` を再実行（ドラッグは同一セッション
   内では 1 回しか反応しないので使わない）

---

## Phase 1 でできること

* 選択した NURBS Curve から Sweep Mesh 毛束を生成
* 断面プロファイルの切り替え
  * Round / Oval / Flat / Sharp / Diamond / TearDrop / Custom
* Thickness / Width / Height の調整
* Root / Middle / Tip の Taper（`sweepMeshCreator.scaleProfile` ランプで実装）
* Twist / Rotation
* Subdivision（Axis / Length）
* Batch Edit パネル
  * Absolute（全毛束を同値に）
  * Relative（毛束ごとの差を維持したまま倍率調整）

生成される毛束は `HairGroup` トランスフォームの下に整理されます。

---

## リポジトリ構成

```
Maya_Anime_HairSweepTool/
├── install.py                       # エンドユーザーが唯一手動で扱うファイル
├── maya_hair_tool/                  # ツール本体（Maya ユーザースクリプトへコピーされる）
│   ├── __init__.py                  # __version__ と show() のエクスポート
│   ├── constants.py                 # プロファイル定義・デフォルト値
│   ├── sweep_utils.py               # sweepMeshCreator の生成 / 探索
│   ├── hair.py                      # 毛束の作成と Attribute 適用
│   ├── batch.py                     # Absolute / Relative 一括編集
│   └── ui.py                        # Hair Builder + Update-from-GitHub フロー
├── maya-hot-update-patterns.md      # ホットアップデート実装ノート
├── docs/design.md                   # 設計メモ
└── README.md
```

### 開発者向け（ローカルの変更をテスト）

GitHub を経由せず、ローカルのリポジトリ内容を直接インストールする場合は
環境変数 `MAYA_HAIR_TOOL_USE_LOCAL=1` を立てた状態で `install.py` を実行
してください（Maya 起動時に環境変数を設定するか、Script Editor から
`os.environ["MAYA_HAIR_TOOL_USE_LOCAL"] = "1"` を先に実行）。

新しい `.py` を `maya_hair_tool/` 配下に追加した場合は、**必ず同じ
コミットで `install.py` の `_REMOTE_FILES` にもファイルパスを追記** して
ください（未追記だとユーザーが Update した後に `ModuleNotFoundError`
になります — `maya-hot-update-patterns.md` §1-10）。

---

## ロードマップ

| Phase | 内容 | 状況 |
| ----- | ---- | ---- |
| 1     | Sweep Mesh ベースの基本ツール           | 実装済み |
| 2     | 毛束の Duplicate（Curve + Sweep 設定の複製） | 未着手 |
| 3     | Batch Edit の拡張（Group 選択 / Undo チャンク統合） | 一部実装 |
| 4     | Sweep Preset / Hair Preset / Hair Library | 未着手 |
| 5     | Hair Group（Front / Side / Back …）      | 未着手 |
| 6     | Braid Generator, Twist Hair, その他手続き型毛束 | 未着手 |

Phase 2 以降で追加するモジュール（例: `duplicate.py`, `library.py`,
`group.py`, `braid.py`）はすべて `maya_hair_tool/` 配下に置き、`ui.py`
から段階的にパネルを追加していく想定です。追加時は上記の
`_REMOTE_FILES` 更新を忘れずに。

---

## 開発方針

* Maya 標準の Sweep Mesh を最大限利用する。独自ノードを増やさない。
* Curve を編集するだけで毛束が追従するワークフローを優先する。
* 生成した毛束は「別の Maya セッションで開いても壊れない」ことを常に検証する。
* Phase 3 で導入する Relative 編集は、毛束ごとのバリエーションを壊さず
  「全体を◯◯％太くする」といった調整ができることを最優先に設計する。
