# Maya Anime Hair Sweep Tool

Maya の Curve + Sweep Mesh をベースに、アニメ・セルルック向けの髪の毛を
効率よく作成するための Python ツールです。

Maya 標準の `sweepMeshCreator` をそのまま利用し、独自メッシュ生成器を作
らないため、ツールが無い環境でシーンを開いてもデータが壊れにくい構成に
なっています。

対応バージョン: **Maya 2023 / Python 3**（Sweep Mesh 機能があれば 2022
以降でも動作します）。

---

## セットアップ

1. このリポジトリをローカルへクローンする。
2. `scripts/` を `MAYA_SCRIPT_PATH` に追加する。あるいは `userSetup.py`
   に以下を書く:

   ```python
   import sys, os
   sys.path.append("/path/to/Maya_Anime_HairSweepTool/scripts")
   ```

3. Maya を起動し、Script Editor の Python タブで次を実行:

   ```python
   from maya_hair_tool import ui
   ui.show()
   ```

   同じ 2 行をシェルフボタンにドラッグしておくと便利です。

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

## モジュール構成

```
scripts/
  launch_hair_tool.py        # シェルフから叩く 2 行スクリプト
  maya_hair_tool/
    __init__.py
    constants.py             # プロファイル定義・デフォルト値
    sweep_utils.py           # sweepMeshCreator の生成／探索
    hair.py                  # 毛束の作成と Attribute 適用
    batch.py                 # Absolute / Relative 一括編集
    ui.py                    # Maya cmds UI (Hair Builder)
```

---

## ロードマップ

| Phase | 内容 | 状況 |
| ----- | ---- | ---- |
| 1     | Sweep Mesh ベースの基本ツール           | 実装済み（本コミット） |
| 2     | 毛束の Duplicate（Curve + Sweep 設定の複製） | 未着手 |
| 3     | Batch Edit の拡張（Group 選択 / Undo チャンク統合） | 一部実装（Batch Edit パネル） |
| 4     | Sweep Preset / Hair Preset / Hair Library | 未着手 |
| 5     | Hair Group（Front / Side / Back …）      | 未着手 |
| 6     | Braid Generator, Twist Hair, その他手続き型毛束 | 未着手 |

Phase 2 以降で追加するモジュール（例: `duplicate.py`, `library.py`,
`group.py`, `braid.py`）はすべて `maya_hair_tool/` 配下に置き、`ui.py`
から段階的にパネルを追加していく想定です。

---

## 開発方針

* Maya 標準の Sweep Mesh を最大限利用する。独自ノードを増やさない。
* Curve を編集するだけで毛束が追従するワークフローを優先する。
* 生成した毛束は「別の Maya セッションで開いても壊れない」ことを常に検証する。
* Phase 3 で導入する Relative 編集は、毛束ごとのバリエーションを壊さず
  「全体を◯◯％太くする」といった調整ができることを最優先に設計する。
