# 設計メモ — Maya Anime Hair Sweep Tool

このドキュメントは元となった構想メモを、実装時に参照できる形に整理した
ものです。フェーズ分けや UI 案、命名規則などをまとめています。

## 基本思想

1. Curve を作成 → Sweep Mesh で毛束化 → Curve を編集して髪型を調整
2. Maya 標準の Sweep Mesh をそのまま使う（独自メッシュ生成器は作らない）
3. 良い毛束を 1 本作って、Curve だけ変えて増やしていくワークフロー

## ノード構造

```
guideCurve  ──(worldSpace)──▶  sweepMeshCreator  ──(outMeshArray)──▶  hairMesh(shape)
                                     │
                                     └─ profile / taper / twist / subdiv 属性
```

* `sweepMeshCreator` 側だけで太さ・ねじれ・分割数・Profile を保持する。
* Curve は Sweep への入力なので、CV を編集するだけでメッシュが追従する。
* ツールが作成した `sweepMeshCreator` にはブール属性 `animeHairTool` を
  1 つ追加し、後から自分の毛束だけを検索できるようにする。

## Profile プリセット

| 名前     | profilePolyType | 補足                                 |
| -------- | --------------- | ------------------------------------ |
| Round    | 0               | 既定                                 |
| Oval     | 0               | Y スケールを 0.55 に落とす           |
| Flat     | 1               | Y スケール 0.35                     |
| Sharp    | 1               | Y スケール 0.6 + 45° 回転           |
| Diamond  | 1               | 45° 回転                             |
| TearDrop | 3 (Custom)      | Custom Profile を後から自由に編集する |
| Custom   | 3               | Sweep Mesh の Custom Profile 機能    |

## Batch Edit

* Absolute: すべての毛束を同値へ設定
* Relative: 各毛束の現在値に係数を掛ける（バリエーション維持）

Phase 3 では以下を Undo チャンクにまとめる:

```python
cmds.undoInfo(openChunk=True, chunkName="HairBatchEdit")
try:
    ...
finally:
    cmds.undoInfo(closeChunk=True)
```

## 命名規則

* 毛束メッシュ: `{stem}_mesh` （`stem` は元 Curve 名から `_curve` などを削ったもの）
* Sweep ノード: `{stem}_sweep`
* デフォルト集約グループ: `HairGroup`

## Phase 2 以降のメモ

### Duplicate
* Guide Curve と `sweepMeshCreator` を対で複製する。
* Curve は `cmds.duplicate(rr=True)`、Sweep はノード + 属性のコピーで再構築。
* 複製先の Curve → 新しい Sweep への接続を張り直す。

### Library
* 毛束を `.ma` として `library/<category>/<name>.ma` に保存。
* サムネイル `.png` を隣に置き、UI 側で `iconTextButton` として並べる。
* Import 時は名前空間を切って読み込むと衝突を避けやすい。

### Group
* まずは Maya 標準の `transform` グループを使う。
* Hair Group パネルからは `listRelatives` で配下のメッシュ→Sweep を辿る。

### Braid
* Main Guide Curve から 3 本のガイドをプロシージャル生成する。
* `nurbsCurve` の CV サンプリング + Twist Deformer / MASH で編み込みを表現する。
