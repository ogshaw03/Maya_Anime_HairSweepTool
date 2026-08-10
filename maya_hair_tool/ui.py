"""Maya UI for the Anime Hair Sweep Tool (Phase 1).

Opens a small dockable-friendly window with two panels:

* Create Hair       - creates a strand from each selected NURBS curve.
* Batch Edit        - applies Absolute or Relative changes to every
                      strand reachable from the current selection.

The UI is intentionally small so it can grow into the full Phase 2-6
feature set (Duplicate, Hair Library, Group edit, Braid Generator)
without a rewrite.
"""

from __future__ import annotations

try:
    import maya.cmds as cmds
except ImportError:  # pragma: no cover
    cmds = None

from . import __version__
from . import batch
from . import constants as C
from . import duplicate
from . import hair
from . import library
from . import sweep_utils as su


# Package name — must match install.py's `_PACKAGE` and be the exact key
# the shelf button `import`s.
_PACKAGE = "maya_hair_tool"

# install.py's `_close_existing_window` looks for f"{_MODULE}Win", so
# keeping this name in sync with that convention lets the installer
# tear down the tool window before overwriting the files on disk.
WINDOW_NAME = _PACKAGE + "Win"
WINDOW_TITLE = "アニメヘアビルダー"


# Widget-to-taperCurve value scale — ``gradientControlNoAttr`` shows
# values in a 0..1 band, but our taperCurve slider range is 0..3
# (Root/Middle/Tip). We map linearly through this constant.
TAPER_EDITOR_MAX = 3.0


# Profile presets are stored in ``constants.py`` under English keys.
# The UI shows Japanese-first labels; ``_PROFILE_LABEL_TO_KEY`` maps
# the selected label back to the key ``hair.set_profile`` accepts.
# Preset set was reworked for Maya 2023's real two-level shape enum
# (sweepProfileType + profilePolyType); the previous
# Round/Oval/Flat/Sharp/Diamond/TearDrop naming was based on a Maya
# schema that never existed.
_PROFILE_DISPLAY = [
    ("円 (Circle)", C.PROFILE_CIRCLE),
    ("楕円 (Ellipse)", C.PROFILE_ELLIPSE),
    ("リボン (Ribbon)", C.PROFILE_RIBBON),
    ("星 (Star)", C.PROFILE_STAR),
    ("長方形 (Rectangle)", C.PROFILE_RECTANGLE),
    ("円弧 (Arc)", C.PROFILE_ARC),
    ("波 (Wave)", C.PROFILE_WAVE),
    ("カスタム (Custom)", C.PROFILE_CUSTOM),
]
_PROFILE_LABEL_TO_KEY = {label: key for label, key in _PROFILE_DISPLAY}


# Profile-specific attribute sliders: each entry is
#   (label, sweepMeshCreator attr, default, slider_min, slider_max, is_int)
# Sliders are rebuilt when the profile menu changes so users only
# see knobs that actually do something for the current profile.
_PROFILE_ATTR_SLIDERS = {
    C.PROFILE_CIRCLE: [
        ("側面数 (Sides)", "profilePolySides", 12, 3, 64, True),
    ],
    C.PROFILE_ELLIPSE: [
        ("側面数 (Sides)", "profilePolySides", 12, 3, 64, True),
    ],
    C.PROFILE_STAR: [
        ("星頂点数 (Points)", "profilePolySides", 5, 3, 20, True),
        ("内側半径 (Sharpness)", "profilePolyInnerRadius",
         0.5, 0.0, 1.0, False),
    ],
    C.PROFILE_RECTANGLE: [
        ("幅 (Width)", "profileRectWidth", 1.0, 0.01, 5.0, False),
        ("高さ (Height)", "profileRectHeight", 1.0, 0.01, 5.0, False),
        ("角丸半径 (Corner)", "profileRectCornerRadius",
         0.1, 0.0, 1.0, False),
        ("角丸深さ (Depth)", "profileRectCornerDepth",
         0.1, 0.0, 1.0, False),
        ("角丸分割 (Corner Seg)", "profileRectCornerSegments",
         4, 1, 16, True),
    ],
    C.PROFILE_ARC: [
        ("弧角度 (Angle)", "profileArcAngle", 180.0, 0.0, 360.0, False),
        ("弧分割 (Segments)", "profileArcSegments", 8, 3, 32, True),
    ],
    C.PROFILE_WAVE: [
        ("振幅 (Amplitude)", "profileWaveAmplitude",
         0.3, 0.0, 2.0, False),
        ("周期数 (Cycles)", "profileWaveCycles", 3.0, 0.5, 20.0, False),
        ("位相 (Offset)", "profileWaveOffset", 0.0, -1.0, 1.0, False),
        ("波分割 (Segments)", "profileWaveSegments", 16, 4, 64, True),
    ],
    C.PROFILE_RIBBON: [],
    C.PROFILE_CUSTOM: [],
}

# ─── CUSTOMIZE (must match install.py) ────────────────────────────────────
_GITHUB_OWNER = "ogshaw03"
_GITHUB_REPO = "Maya_Anime_HairSweepTool"
_GITHUB_BRANCH = "main"
# ─── END CUSTOMIZE ────────────────────────────────────────────────────────

_GITHUB_API = "https://api.github.com/repos/{0}/{1}".format(
    _GITHUB_OWNER, _GITHUB_REPO)
_GITHUB_RAW_BASE = "https://raw.githubusercontent.com/{0}/{1}".format(
    _GITHUB_OWNER, _GITHUB_REPO)


class HairBuilderUI(object):
    """Encapsulates every widget the tool uses.

    Widget IDs are stored on ``self`` rather than as module globals so the
    same class can be instantiated more than once (e.g. for docked +
    floating variants) without name collisions.
    """

    def __init__(self):
        # Emitted-once tracker so a missing sweepMeshCreator attribute
        # (Maya-version rename etc.) is warned about the first time a
        # slider tries to touch it, not every drag frame.
        self._warned_missing_attrs = set()

        # Hair list panel widget + scriptJob id for cleanup.
        self.hair_list = None
        self._script_jobs = []

        # Taper curve editor widget + re-entrancy guard so the two
        # sync directions (widget → taperCurve and taperCurve → widget)
        # don't ping-pong each other during a drag.
        self.taper_editor = None
        self._taper_syncing = False

        # Adjustment mode:
        #   'creation' → nothing selected; sliders act as defaults
        #                for the next 「毛束を生成」click.
        #   'absolute' → exactly one strand selected; sliders reflect
        #                its current attribute values and edits set
        #                absolute values.
        #   'relative' → multiple strands or a group / 全体 selected;
        #                sliders are multipliers (identity = 1.0)
        #                applied on top of the per-strand baselines
        #                snapshotted at selection time.
        self._adjust_mode = "creation"
        self._baselines = {}
        # Widget id set in _build_create_panel; updated from the
        # selection handler.
        self.mode_indicator = None
        # Re-entrancy guard: setting slider values programmatically
        # would otherwise fire drag/change callbacks and re-apply.
        self._syncing_sliders = False

        # Profile-specific slider container + widget map, rebuilt
        # every time the profile menu changes so users only see the
        # knobs that actually affect the chosen shape.
        self.profile_specific_layout = None
        self.profile_specific_widgets = {}
        # Track the profile the current slider set was built for so
        # a redundant rebuild is skipped.
        self._current_profile_key = None

        # Library panel — icon grid rebuilt from library.list_library_entries()
        self.library_grid = None
        self.library_frame = None

        # Curve panel widgets (create-a-curve shortcuts).
        self.curve_length = None
        self.curve_cv_count = None
        self.curve_axis_menu = None

        # Create panel widgets.
        self.profile_menu = None
        self.thickness = None
        self.width = None
        self.height = None
        self.root = None
        self.middle = None
        self.tip = None
        self.twist = None
        self.rotation = None
        self.subdiv_axis = None
        self.subdiv_length = None

        # Duplicate panel widgets.
        self.dup_count = None
        self.dup_offset = None

        # Batch panel widgets.
        self.batch_mode = None      # radio collection
        self.batch_thickness = None
        self.batch_width = None
        self.batch_height = None
        self.batch_root = None
        self.batch_middle = None
        self.batch_tip = None
        self.batch_twist = None
        self.batch_subdiv = None

    # -----------------------------------------------------------------
    # Show / rebuild
    # -----------------------------------------------------------------
    def show(self):
        if cmds.window(WINDOW_NAME, exists=True):
            cmds.deleteUI(WINDOW_NAME)

        title = "{0}  —  v{1}".format(WINDOW_TITLE, __version__)
        cmds.window(WINDOW_NAME, title=title, sizeable=True,
                    widthHeight=(560, 780), menuBar=True)

        # Menu bar — kept lightweight (Edit + About) so users can
        # find bulk actions and updates without hunting through the
        # panels. Additional menus can be added here without touching
        # the panel layout.
        edit_menu = cmds.menu(
            label="Edit", tearOff=True, parent=WINDOW_NAME)
        cmds.menuItem(
            label="全スライダーをリセット",
            annotation=("Create パネルおよび Batch パネルの全"
                        "スライダーを初期値に戻します。個別絶対"
                        "モード / 相対乗算モードでは、選択中"
                        "毛束にもリセット結果が反映されます。"),
            command=self._on_reset_all_sliders, parent=edit_menu,
        )

        about_menu = cmds.menu(
            label="About", tearOff=True, parent=WINDOW_NAME)
        cmds.menuItem(
            label="GitHub から更新",
            annotation=("SHA-pinned raw URL でリポジトリ最新版を"
                        "取得し、ウィンドウを再オープンします。"),
            command=update_from_github, parent=about_menu,
        )
        cmds.menuItem(divider=True, parent=about_menu)
        cmds.menuItem(
            label="バージョン情報",
            command=self._on_show_version, parent=about_menu,
        )

        # Top-level split: left = hair list + library stacked
        # vertically (Substance Painter–like: layers-panel on top,
        # asset shelf underneath), right = the rest of the panels.
        pane = cmds.paneLayout(configuration="vertical2",
                               paneSize=[1, 32, 100])

        # LEFT — vertical split via a nested paneLayout so the
        # divider between hair list and library is draggable.
        left_split = cmds.paneLayout(
            configuration="horizontal2",
            paneSize=[1, 100, 62],
            parent=pane)

        list_form = cmds.formLayout(parent=left_split)
        self._build_hair_list_panel(list_form)

        library_form = cmds.formLayout(parent=left_split)
        self._build_library_panel(library_form)

        # RIGHT
        right_form = cmds.formLayout(parent=pane)
        right_scroll = cmds.scrollLayout(
            horizontalScrollBarThickness=0,
            childResizable=True, parent=right_form)
        cmds.formLayout(
            right_form, edit=True,
            attachForm=[
                (right_scroll, "top", 0),
                (right_scroll, "bottom", 0),
                (right_scroll, "left", 0),
                (right_scroll, "right", 0),
            ],
        )
        right_body = cmds.columnLayout(adjustableColumn=True, rowSpacing=6,
                                       columnAttach=("both", 4),
                                       parent=right_scroll)

        self._build_curve_panel(right_body)
        cmds.separator(height=8, style="in", parent=right_body)
        self._build_create_panel(right_body)
        cmds.separator(height=8, style="in", parent=right_body)
        self._build_duplicate_panel(right_body)
        cmds.separator(height=8, style="in", parent=right_body)
        self._build_batch_panel(right_body)
        cmds.separator(height=8, style="in", parent=right_body)
        self._build_footer(right_body)

        # scriptJob to auto-refresh the hair list when the scene
        # changes. Scoped to the window so it dies with it.
        for evt in ("SceneOpened", "NewSceneOpened",
                    "DagObjectCreated", "NameChanged"):
            try:
                jid = cmds.scriptJob(
                    parent=WINDOW_NAME, event=[evt, self._refresh_hair_list])
                self._script_jobs.append(jid)
            except Exception:
                pass
        # SelectionChanged drives the adjustment-mode dispatch
        # (creation / absolute / relative) AND the taper editor
        # sync — both need to react to a new strand being picked.
        try:
            jid = cmds.scriptJob(
                parent=WINDOW_NAME,
                event=["SelectionChanged", self._on_selection_changed])
            self._script_jobs.append(jid)
        except Exception:
            pass

        cmds.showWindow(WINDOW_NAME)
        # Populate list + kick the mode dispatch once so the panel
        # header shows the right mode from the start.
        self._refresh_hair_list()
        self._on_selection_changed()

    # -----------------------------------------------------------------
    # Hair list panel — enumerate strands tagged animeHairTool so the
    # user can jump-select from the UI instead of poking at the
    # Outliner. Populated on show + auto-refreshed via scriptJob.
    # -----------------------------------------------------------------
    def _build_hair_list_panel(self, parent):
        # ``parent`` is expected to be a formLayout so the frame can
        # stretch to the full pane height. We build the frame + inner
        # widgets, then wire attachForm on both this-level form and
        # the inner form so the tree grows / shrinks with the window.
        frame = cmds.frameLayout(
            label="毛束一覧", collapsable=False,
            marginHeight=4, marginWidth=4, parent=parent)
        cmds.formLayout(
            parent, edit=True,
            attachForm=[
                (frame, "top", 0),
                (frame, "bottom", 0),
                (frame, "left", 0),
                (frame, "right", 0),
            ],
        )

        inner = cmds.formLayout(parent=frame)

        help_text = cmds.text(
            label=("[全体] → 全毛束、グループ名 → そのグループ、\n"
                   "個別行 → その 1 本を選択します。\n"
                   "Ctrl/Shift クリックで複数選択も可。"),
            align="left", font="smallObliqueLabelFont",
            wordWrap=True, parent=inner,
        )

        self.hair_list = cmds.treeView(
            numberOfButtons=0,
            allowMultiSelection=True,
            allowReparenting=False,
            selectionChangedCommand=self._on_tree_select,
            parent=inner,
        )

        # Group management buttons.
        new_group_btn = cmds.button(
            label="新規グループ作成",
            annotation=("新しい空のグループを HairGroup 直下に"
                        "作成します。"),
            command=self._on_new_group, parent=inner,
        )
        move_btn = cmds.button(
            label="選択毛束をグループへ移動",
            annotation=("シーンで選択中の毛束を、指定した"
                        "グループへ移動します。"),
            command=self._on_move_to_group, parent=inner,
        )
        refresh_btn = cmds.button(
            label="更新",
            annotation=("シーン内の毛束を再スキャンしてリストを"
                        "作り直します。"),
            command=self._on_refresh_hair_list, parent=inner,
        )

        cmds.formLayout(
            inner, edit=True,
            attachForm=[
                (help_text, "top", 4),
                (help_text, "left", 4),
                (help_text, "right", 4),
                (self.hair_list, "left", 4),
                (self.hair_list, "right", 4),
                (new_group_btn, "left", 4),
                (new_group_btn, "right", 4),
                (move_btn, "left", 4),
                (move_btn, "right", 4),
                (refresh_btn, "left", 4),
                (refresh_btn, "right", 4),
                (refresh_btn, "bottom", 4),
            ],
            attachControl=[
                (self.hair_list, "top", 4, help_text),
                (self.hair_list, "bottom", 4, new_group_btn),
                (new_group_btn, "bottom", 4, move_btn),
                (move_btn, "bottom", 4, refresh_btn),
            ],
        )

    # ---- Tree id encoding --------------------------------------
    # treeView item names must be unique across the whole tree, so
    # we encode the tree row's kind + payload into the id:
    #   "all"                  → 全体 pseudo-item (applies to all strands)
    #   "grp:<full_path>"      → a hair group (children are strands)
    #   "str:<full_path>"      → an individual strand mesh transform
    # The display label shown in the widget is the short (leaf) name.

    _ALL_ID = "all"
    _GROUP_PREFIX = "grp:"
    _STRAND_PREFIX = "str:"

    def _refresh_hair_list(self, *_):
        """Rebuild the tree from HairGroup's children — sections for
        each named group + a flat list of ungrouped strands, all
        under a top-level [全体] pseudo-item."""
        if not self.hair_list:
            return
        try:
            if not cmds.treeView(self.hair_list, exists=True):
                return
        except Exception:
            return

        prev_sel = []
        try:
            prev_sel = list(cmds.treeView(
                self.hair_list, query=True, selectItem=True) or [])
        except Exception:
            pass

        cmds.treeView(self.hair_list, edit=True, removeAll=True)

        # [全体] top-level pseudo-item.
        cmds.treeView(
            self.hair_list, edit=True,
            addItem=(self._ALL_ID, ""))
        cmds.treeView(
            self.hair_list, edit=True,
            displayLabel=(self._ALL_ID, "[全体]"))

        # Named groups + their strands.
        for group_path in hair.list_hair_groups():
            gid = self._GROUP_PREFIX + group_path
            gname = group_path.split("|")[-1]
            cmds.treeView(
                self.hair_list, edit=True, addItem=(gid, ""))
            cmds.treeView(
                self.hair_list, edit=True,
                displayLabel=(gid, "▼ " + gname))
            try:
                cmds.treeView(
                    self.hair_list, edit=True, expandItem=(gid, True))
            except Exception:
                pass
            for strand in hair.strands_in_group(group_path):
                sid = self._STRAND_PREFIX + strand
                cmds.treeView(
                    self.hair_list, edit=True, addItem=(sid, gid))
                cmds.treeView(
                    self.hair_list, edit=True,
                    displayLabel=(sid, strand.split("|")[-1]))

        # Ungrouped strands (direct children of HairGroup).
        for strand in hair.ungrouped_strands():
            sid = self._STRAND_PREFIX + strand
            cmds.treeView(
                self.hair_list, edit=True, addItem=(sid, ""))
            cmds.treeView(
                self.hair_list, edit=True,
                displayLabel=(sid, strand.split("|")[-1]))

        # Restore selection by id.
        for iid in prev_sel:
            try:
                cmds.treeView(
                    self.hair_list, edit=True, selectItem=(iid, True))
            except Exception:
                pass

    def _on_refresh_hair_list(self, *_):
        self._refresh_hair_list()

    def _resolve_tree_selection(self):
        """Turn tree selection into ``(mode, strands)``.

        mode ∈ {'all', 'group', 'strand'} — the semantic level the
        user clicked at, so the adjustment layer can decide whether
        to treat the change as relative (group/all) or absolute
        (strand)."""
        sel = cmds.treeView(
            self.hair_list, query=True, selectItem=True) or []
        if not sel:
            return "none", []

        # If ANY tree row is [全体] or a group, treat the whole
        # selection as "relative multi" and gather every underlying
        # strand.
        has_all = False
        has_group = False
        strand_ids = []
        for iid in sel:
            if iid == self._ALL_ID:
                has_all = True
            elif iid.startswith(self._GROUP_PREFIX):
                has_group = True
                strand_ids.append(iid)
            elif iid.startswith(self._STRAND_PREFIX):
                strand_ids.append(iid)

        targets = []
        if has_all:
            targets = hair.all_hair_strands()
            return "all", targets

        for iid in strand_ids:
            if iid.startswith(self._GROUP_PREFIX):
                group_path = iid[len(self._GROUP_PREFIX):]
                targets.extend(hair.strands_in_group(group_path))
            elif iid.startswith(self._STRAND_PREFIX):
                targets.append(iid[len(self._STRAND_PREFIX):])
        # Dedup while preserving order.
        seen = set()
        deduped = []
        for t in targets:
            if t not in seen:
                seen.add(t)
                deduped.append(t)

        if has_group or len(deduped) > 1:
            return "group", deduped
        return "strand", deduped

    def _on_tree_select(self, *_):
        """Sync scene selection with the tree selection so live-edit
        callbacks pick up the newly-highlighted strands."""
        mode, targets = self._resolve_tree_selection()
        if not targets:
            return
        try:
            cmds.select(targets, replace=True)
        except Exception:
            pass

    def _on_new_group(self, *_):
        result = cmds.promptDialog(
            title="新規グループ", message="グループ名:",
            button=["作成", "キャンセル"], defaultButton="作成",
            cancelButton="キャンセル", dismissString="キャンセル",
            text="Bangs",
        )
        if result != "作成":
            return
        name = cmds.promptDialog(query=True, text=True).strip()
        if not name:
            return
        try:
            hair.create_hair_group(name)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] グループ作成失敗: {0}".format(exc))
            return
        self._refresh_hair_list()

    def _on_move_to_group(self, *_):
        selection = cmds.ls(selection=True, long=True) or []
        strands = [n for n in selection
                   if hair._is_hair_strand_transform(n)]
        if not strands:
            cmds.warning(
                "移動する毛束が選択されていません。"
                "毛束メッシュを選択してから押してください。")
            return

        existing = [g.split("|")[-1]
                     for g in hair.list_hair_groups()]
        prompt_text = existing[0] if existing else "Bangs"
        message = ("移動先グループ名 (既存: {0}):".format(
            ", ".join(existing)) if existing else
            "新しいグループ名:")
        result = cmds.promptDialog(
            title="グループへ移動", message=message,
            button=["移動", "HairGroup 直下へ", "キャンセル"],
            defaultButton="移動", cancelButton="キャンセル",
            dismissString="キャンセル", text=prompt_text,
        )
        if result == "キャンセル":
            return
        if result == "HairGroup 直下へ":
            group = None
        else:
            group = cmds.promptDialog(query=True, text=True).strip()
            if not group:
                return
        for s in strands:
            try:
                hair.move_strand_to_group(s, group)
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] {0} 移動失敗: {1}".format(s, exc))
        self._refresh_hair_list()

    # -----------------------------------------------------------------
    # Curve panel — shortcuts for when the user has no curve yet
    # -----------------------------------------------------------------
    def _build_curve_panel(self, parent):
        cmds.frameLayout(label="カーブ", collapsable=True,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        cmds.text(
            label=("既存カーブが無いときのショートカット。\n"
                   "「カーブを描く」は Maya の CV Curve Tool を起動し、\n"
                   "ビューポートで頂点を打ってから Enter で確定します。\n"
                   "「直線カーブを作成」は指定した長さ・方向・CV 数で\n"
                   "直線 NURBS カーブを 1 本作って選択します。"),
            align="left", parent=col, font="smallObliqueLabelFont",
        )

        self.curve_length = cmds.floatFieldGrp(
            label="長さ",
            numberOfFields=1,
            value1=6.0,
            columnAlign=(1, "left"),
            columnWidth2=(90, 60),
            parent=col,
        )
        self.curve_cv_count = cmds.intFieldGrp(
            label="CV 数",
            numberOfFields=1,
            value1=6,
            columnAlign=(1, "left"),
            columnWidth2=(90, 60),
            parent=col,
        )

        cmds.text(label="方向", align="left", parent=col)
        self.curve_axis_menu = cmds.optionMenu(parent=col)
        for axis in ("-Y", "+Y", "+X", "-X", "+Z", "-Z"):
            cmds.menuItem(label=axis)

        row = cmds.rowLayout(
            numberOfColumns=2, adjustableColumn=1,
            columnAttach=[(1, "both", 2), (2, "both", 2)],
            columnWidth2=(160, 160), parent=col,
        )
        cmds.button(
            label="カーブを描く (CV Tool)",
            annotation=("Maya の CV Curve Tool を起動します。"
                        "ビューポートで頂点を打ち、Enter で確定してください。"),
            command=self._on_start_curve_tool, parent=row,
        )
        cmds.button(
            label="直線カーブを作成",
            annotation=("上のパラメータで直線 NURBS カーブを作成し、"
                        "選択状態にします。"),
            command=self._on_create_default_curve, parent=row,
        )
        cmds.setParent("..")

    def _on_start_curve_tool(self, *_):
        hair.start_curve_tool()

    def _on_create_default_curve(self, *_):
        length = cmds.floatFieldGrp(
            self.curve_length, query=True, value1=True)
        cv_count = cmds.intFieldGrp(
            self.curve_cv_count, query=True, value1=True)
        axis = cmds.optionMenu(
            self.curve_axis_menu, query=True, value=True)
        hair.create_default_curve(
            length=length, cv_count=cv_count, axis=axis)

    # -----------------------------------------------------------------
    # Create Hair panel
    # -----------------------------------------------------------------
    def _build_create_panel(self, parent):
        cmds.frameLayout(label="毛束を作成 / 調整", collapsable=False,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        # Mode indicator — updated by _on_selection_changed.
        self.mode_indicator = cmds.text(
            label="モード: 生成モード (未選択)",
            align="left", font="boldLabelFont", parent=col,
        )
        cmds.text(
            label=("・未選択 → 生成モード。スライダー値は「毛束を生成」の初期値になります。\n"
                   "・毛束 1 本選択 → 個別絶対モード。スライダーはその毛束の現在値を表示、絶対値で編集。\n"
                   "・グループ / [全体] / 複数選択 → 相対乗算モード。スライダー×1.0 = 変化なし、"
                   "×2.0 で全対象を 2 倍等、各毛束の個性を保ちつつまとめて調整。"),
            align="left", parent=col, font="smallObliqueLabelFont",
            wordWrap=True,
        )

        cmds.text(label="プロファイル", align="left", parent=col)
        self.profile_menu = cmds.optionMenu(
            parent=col, changeCommand=self._cb_profile_change)
        for display, _key in _PROFILE_DISPLAY:
            cmds.menuItem(label=display)

        # Profile-specific slider container. Contents (side counts,
        # rectangle dims, wave amplitude etc.) are rebuilt on
        # profile change so only relevant knobs are shown.
        self.profile_specific_layout = cmds.columnLayout(
            adjustableColumn=True, rowSpacing=4, parent=col)
        self._rebuild_profile_specific_sliders(C.PROFILE_CIRCLE)

        self.thickness = _slider_with_reset(
            col, "太さ (均一)", C.DEFAULT_THICKNESS, 0.01, 5.0,
            drag_cb=self._cb_thickness_drag,
            change_cb=self._cb_thickness_change)
        self.width = _slider_with_reset(
            col, "幅 (X)", C.DEFAULT_WIDTH, 0.01, 5.0,
            drag_cb=self._cb_width_drag,
            change_cb=self._cb_width_change)
        self.height = _slider_with_reset(
            col, "高さ (Y)", C.DEFAULT_HEIGHT, 0.01, 5.0,
            drag_cb=self._cb_height_drag,
            change_cb=self._cb_height_change)
        self.root = _slider_with_reset(
            col, "根本スケール", C.DEFAULT_ROOT_SCALE, 0.0, 3.0,
            drag_cb=self._cb_root_drag,
            change_cb=self._cb_root_change)
        self.middle = _slider_with_reset(
            col, "中間スケール", C.DEFAULT_MIDDLE_SCALE, 0.0, 3.0,
            drag_cb=self._cb_middle_drag,
            change_cb=self._cb_middle_change)
        self.tip = _slider_with_reset(
            col, "先端スケール", C.DEFAULT_TIP_SCALE, 0.0, 3.0,
            drag_cb=self._cb_tip_drag,
            change_cb=self._cb_tip_change)
        # Inline taper curve editor (works without leaving the panel).
        self._build_taper_editor(col)

        cmds.button(
            label="テーパーカーブを Attribute Editor で開く",
            annotation=("Maya ネイティブの Taper Curve ramp widget を"
                        "開きます。カーブエディタと同じ内容を編集"
                        "できます。"),
            command=self._on_open_taper_editor, parent=col,
        )
        self.twist = _slider_with_reset(
            col, "ねじれ", C.DEFAULT_TWIST, -720.0, 720.0,
            drag_cb=self._cb_twist_drag,
            change_cb=self._cb_twist_change)
        self.rotation = _slider_with_reset(
            col, "回転", C.DEFAULT_ROTATION, -360.0, 360.0,
            drag_cb=self._cb_rotation_drag,
            change_cb=self._cb_rotation_change)
        self.subdiv_axis = _int_slider_with_reset(
            col, "断面分割数", C.DEFAULT_SUBDIVISIONS_AXIS, 3, 32,
            drag_cb=self._cb_subdiv_axis_drag,
            change_cb=self._cb_subdiv_axis_change)
        self.subdiv_length = _int_slider_with_reset(
            col, "長さ分割数", C.DEFAULT_SUBDIVISIONS_LENGTH, 1, 128,
            drag_cb=self._cb_subdiv_length_drag,
            change_cb=self._cb_subdiv_length_change)

        cmds.text(
            label=("長さ分割数は interpolationPrecision (精度値) を"
                   "書き込みます。既定 12 付近では変化しません — "
                   "実際にポリゴン数が増えるのは 30 以上 (60 で "
                   "約 4 倍、100 以上で更に細かく)。"),
            align="left", parent=col, font="smallObliqueLabelFont",
            wordWrap=True,
        )

        cmds.button(label="選択カーブから毛束を生成",
                    height=32, command=self._on_create, parent=col)

    def _on_open_taper_editor(self, *_):
        """Open Maya's Attribute Editor for the selected sweep node
        so the user can edit ``taperCurve`` (root/middle/tip ramp)
        with Maya's native ramp widget — arbitrary point count +
        smooth interpolation."""
        creators = su.sweep_creators_from_selection()
        if not creators:
            cmds.warning(
                "毛束を選択してからボタンを押してください。")
            return
        cmds.select(creators[0], replace=True)
        try:
            import maya.mel as mel
            mel.eval("openAEWindow;")
        except Exception:
            try:
                mel.eval("AttributeEditor;")
            except Exception as exc:
                cmds.warning(
                    "AE を開けませんでした: {0}".format(exc))

    # -----------------------------------------------------------------
    # Adjust-mode dispatch (creation / absolute / relative)
    # -----------------------------------------------------------------
    # Attributes we snapshot per strand when entering relative mode.
    # Kept in one place so the setters and the snapshot stay in sync.
    _BASELINE_SCALAR_ATTRS = (
        "scaleProfileX",
        "scaleProfileY",
        "scaleProfileUniform",
        "twist",
        "rotateProfile",
        "profilePolySides",
        "interpolationPrecision",
    )

    def _update_mode_indicator(self, msg):
        if self.mode_indicator:
            try:
                cmds.text(self.mode_indicator, edit=True, label=msg)
            except Exception:
                pass

    def _snapshot_baselines(self, creators):
        """Read every relative-mode attribute (scalars + taper ramp)
        for each creator and stash it in ``self._baselines`` so the
        setters can compute ``value = baseline × slider``."""
        self._baselines = {}
        for c in creators:
            values = {}
            for attr in self._BASELINE_SCALAR_ATTRS:
                if cmds.attributeQuery(attr, node=c, exists=True):
                    try:
                        values[attr] = cmds.getAttr(c + "." + attr)
                    except Exception:
                        pass
            try:
                r, m, t = hair.read_taper_values(c)
                values["taper_root"] = r
                values["taper_middle"] = m
                values["taper_tip"] = t
            except Exception:
                pass
            self._baselines[c] = values

    def _reset_sliders_to_identity(self):
        """Snap every Create-panel slider to its multiplier identity
        (1.0 for scales, 0.0 for angles, defaults for subdiv). Guarded
        by ``_syncing_sliders`` so the programmatic writes don't fire
        the live-edit callbacks."""
        self._syncing_sliders = True
        try:
            pairs_float = (
                (self.thickness, 1.0),
                (self.width, 1.0),
                (self.height, 1.0),
                (self.root, 1.0),
                (self.middle, 1.0),
                (self.tip, 1.0),
                (self.twist, 0.0),
                (self.rotation, 0.0),
            )
            for w, v in pairs_float:
                if w:
                    try:
                        cmds.floatSliderGrp(w, edit=True, value=v)
                    except Exception:
                        pass
            pairs_int = (
                (self.subdiv_axis, C.DEFAULT_SUBDIVISIONS_AXIS),
                (self.subdiv_length, C.DEFAULT_SUBDIVISIONS_LENGTH),
            )
            for w, v in pairs_int:
                if w:
                    try:
                        cmds.intSliderGrp(w, edit=True, value=v)
                    except Exception:
                        pass
        finally:
            self._syncing_sliders = False

    def _sync_sliders_to_creator(self, creator):
        """Absolute mode: pull the strand's current values into the
        sliders so users see (and edit) what it actually is."""
        self._syncing_sliders = True
        try:
            def _get(attr, default):
                if cmds.attributeQuery(attr, node=creator, exists=True):
                    try:
                        return cmds.getAttr(creator + "." + attr)
                    except Exception:
                        return default
                return default

            x = _get("scaleProfileX", C.DEFAULT_WIDTH)
            y = _get("scaleProfileY", C.DEFAULT_HEIGHT)
            twist_v = _get("twist", C.DEFAULT_TWIST)
            rot_v = _get("rotateProfile", C.DEFAULT_ROTATION)
            sides = _get("profilePolySides",
                         C.DEFAULT_SUBDIVISIONS_AXIS)
            prec = _get("interpolationPrecision",
                        C.DEFAULT_SUBDIVISIONS_LENGTH)

            # Thickness ≈ scaleProfileUniform-mode X. When Uniform is
            # on the two are equal; when off, Thickness reflecting X
            # is still a reasonable proxy.
            for w, v in (
                (self.thickness, x),
                (self.width, x),
                (self.height, y),
                (self.twist, twist_v),
                (self.rotation, rot_v),
            ):
                if w:
                    try:
                        cmds.floatSliderGrp(w, edit=True, value=float(v))
                    except Exception:
                        pass
            for w, v in (
                (self.subdiv_axis, sides),
                (self.subdiv_length, prec),
            ):
                if w:
                    try:
                        cmds.intSliderGrp(w, edit=True, value=int(v))
                    except Exception:
                        pass

            try:
                r, m, t = hair.read_taper_values(creator)
                for widget, val in (
                    (self.root, r), (self.middle, m), (self.tip, t)
                ):
                    if widget:
                        try:
                            cmds.floatSliderGrp(
                                widget, edit=True, value=float(val))
                        except Exception:
                            pass
            except Exception:
                pass
        finally:
            self._syncing_sliders = False

    def _on_selection_changed(self, *_):
        """Called from the SelectionChanged scriptJob (fires on
        viewport / Outliner / tree edits). Decides which mode the
        Create panel should be in and refreshes sliders + indicator
        accordingly. Also drives the inline taper editor sync."""
        # Determine mode from the tree selection first — a tree row of
        # [全体] / group means relative even when only one strand is
        # under it. Fall back to scene selection when the tree isn't
        # showing.
        mode, tree_targets = self._resolve_tree_selection()
        creators = su.sweep_creators_from_selection()
        if not creators and tree_targets:
            creators = su.sweep_creators_from_nodes(tree_targets)
        creators = list(dict.fromkeys(creators))

        if not creators:
            self._adjust_mode = "creation"
            self._baselines = {}
            self._update_mode_indicator("モード: 生成モード (未選択)")
        elif mode == "strand" and len(creators) == 1:
            self._adjust_mode = "absolute"
            self._baselines = {}
            self._sync_sliders_to_creator(creators[0])
            leaf = (tree_targets[0].split("|")[-1]
                    if tree_targets else creators[0])
            self._update_mode_indicator(
                "モード: 個別絶対 — {0}".format(leaf))
        else:
            self._adjust_mode = "relative"
            self._snapshot_baselines(creators)
            self._reset_sliders_to_identity()
            scope = "[全体]" if mode == "all" else (
                "グループ" if mode == "group" else "複数")
            self._update_mode_indicator(
                "モード: 相対乗算 — {0} ({1} 本)".format(
                    scope, len(creators)))

        # Keep the taper editor in sync (single-strand only — for a
        # multi-strand relative selection there's no unambiguous
        # taperCurve to show).
        if self._adjust_mode == "absolute":
            self._sync_taper_editor_from_creator()

    # -----------------------------------------------------------------
    # Inline taper curve editor (gradientControlNoAttr wrapper)
    # -----------------------------------------------------------------
    def _build_taper_editor(self, parent):
        cmds.text(
            label=("テーパーカーブ (プロファイル):"),
            align="left", parent=parent, font="smallBoldLabelFont",
        )
        cmds.text(
            label=("左右ドラッグで既存ポイント移動、右クリックで"
                   "ポイント追加/削除。カーブ Y=1.0 が最大 (0-3 に "
                   "スケール)。Root/Middle/Tip スライダーは 3 点"
                   "上書きするので、追加ポイントを残したい場合は"
                   "スライダー↺リセットを使わず、このエディタで"
                   "調整してください。"),
            align="left", parent=parent,
            font="smallObliqueLabelFont", wordWrap=True,
        )
        try:
            self.taper_editor = cmds.gradientControlNoAttr(
                height=100,
                changeCommand=self._on_taper_editor_change,
                parent=parent,
            )
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] テーパーエディタを作成できません: "
                "{0}".format(exc))
            self.taper_editor = None
            return

        row = cmds.rowLayout(
            numberOfColumns=2, adjustableColumn=1,
            columnAttach=[(1, "both", 2), (2, "both", 2)],
            columnWidth2=(160, 160), parent=parent,
        )
        cmds.button(
            label="毛束から再読み込み",
            annotation=("選択毛束の現在の taperCurve を"
                        "エディタに読み込みます。"),
            command=self._on_taper_reload, parent=row,
        )
        cmds.button(
            label="3 点デフォルトに戻す",
            annotation=("Root=1.0 / Middle=1.0 / Tip=0.05 の"
                        "3 点構成に戻します。"),
            command=self._on_taper_reset_default, parent=row,
        )
        cmds.setParent("..")

    def _sync_taper_editor_from_creator(self, creator=None):
        """Load the first selected creator's taperCurve into the
        editor widget. Values are compressed by ``TAPER_EDITOR_MAX``
        so the widget's 0-1 canvas covers the full 0-3 taper range."""
        if self._taper_syncing or not self.taper_editor:
            return
        if creator is None:
            creators = su.sweep_creators_from_selection()
            if not creators:
                return
            creator = creators[0]
        try:
            entries = hair.read_taper_ramp_entries(creator)
        except Exception:
            entries = []
        if not entries:
            return
        parts = []
        for pos, val, interp in entries:
            w_val = max(0.0, min(1.0, val / TAPER_EDITOR_MAX))
            parts.append("{0},{1},{2}".format(pos, w_val, interp))
        gradient_str = ",".join(parts)
        self._taper_syncing = True
        try:
            cmds.gradientControlNoAttr(
                self.taper_editor, edit=True, asString=gradient_str)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] テーパーエディタへの反映に失敗: "
                "{0}".format(exc))
        finally:
            self._taper_syncing = False

    def _on_taper_editor_change(self, *_):
        """User dragged / added / removed a point in the widget →
        parse ``asString`` and push back to every selected creator's
        taperCurve. Wrapped in an undo chunk for a single Ctrl+Z."""
        if self._taper_syncing or not self.taper_editor:
            return
        try:
            s = cmds.gradientControlNoAttr(
                self.taper_editor, query=True, asString=True) or ""
        except Exception:
            return
        entries = []
        # asString format is "pos,val,interp,pos,val,interp,..."
        # (comma separated). We split every 3 fields.
        parts = [p for p in s.split(",") if p.strip()]
        for i in range(0, len(parts) - 2, 3):
            try:
                pos = float(parts[i])
                w_val = float(parts[i + 1])
                interp = int(float(parts[i + 2]))
                entries.append((pos, w_val * TAPER_EDITOR_MAX, interp))
            except Exception:
                continue
        if not entries:
            return
        creators = su.sweep_creators_from_selection()
        if not creators:
            return
        self._taper_syncing = True
        try:
            with batch.batch_undo_chunk("HairTaperCurveEdit"):
                for c in creators:
                    try:
                        hair.write_taper_ramp_entries(c, entries)
                    except Exception as exc:
                        cmds.warning(
                            "[maya_hair_tool] taperCurve 書き込み失敗 "
                            "({0}): {1}".format(c, exc))
        finally:
            self._taper_syncing = False

    def _on_taper_reload(self, *_):
        self._sync_taper_editor_from_creator()

    def _on_taper_reset_default(self, *_):
        creators = su.sweep_creators_from_selection()
        entries = [
            (0.0, C.DEFAULT_ROOT_SCALE, 2),
            (0.5, C.DEFAULT_MIDDLE_SCALE, 2),
            (1.0, C.DEFAULT_TIP_SCALE, 2),
        ]
        if creators:
            with batch.batch_undo_chunk("HairTaperReset"):
                for c in creators:
                    hair.write_taper_ramp_entries(c, entries)
        self._sync_taper_editor_from_creator()

    def _on_create(self, *_):
        label = cmds.optionMenu(
            self.profile_menu, query=True, value=True)
        profile = _PROFILE_LABEL_TO_KEY.get(label, C.PROFILE_CIRCLE)
        hair.create_hair_from_selected_curves(
            profile=profile,
            thickness=_read_float(self.thickness),
            width=_read_float(self.width),
            height=_read_float(self.height),
            root_scale=_read_float(self.root),
            middle_scale=_read_float(self.middle),
            tip_scale=_read_float(self.tip),
            twist=_read_float(self.twist),
            rotation=_read_float(self.rotation),
            subdivisions_axis=_read_int(self.subdiv_axis),
            subdivisions_length=_read_int(self.subdiv_length),
        )
        # Explicit refresh — DagObjectCreated scriptJob usually catches
        # this too but firing immediately makes the new strand appear
        # in the list before any DG lag.
        self._refresh_hair_list()

    # -----------------------------------------------------------------
    # Live-edit callbacks (Create panel sliders → selected strands)
    #
    # Undo policy:
    #   * ``dragCommand`` fires many times per second while the user
    #     drags the slider. We disable undo recording for the duration
    #     of each setAttr so the stack doesn't get flooded with dozens
    #     of intermediate values.
    #   * ``changeCommand`` fires once when the drag ends and when the
    #     numeric field is edited. We wrap that final write in a single
    #     ``undoInfo`` chunk so Ctrl+Z rolls back the whole slider
    #     interaction as one step.
    # -----------------------------------------------------------------
    def _live_apply(self, setter, record_undo):
        # Programmatic slider writes (mode switch, reset, absolute
        # sync) must not round-trip back through the setter and
        # multiply values a second time.
        if self._syncing_sliders:
            return
        creators = su.sweep_creators_from_selection()
        if not creators:
            return
        if record_undo:
            cmds.undoInfo(openChunk=True, chunkName="HairLiveEdit")
            try:
                for c in creators:
                    try:
                        setter(c)
                    except Exception as exc:
                        cmds.warning(
                            "[maya_hair_tool] live edit on {0} failed: "
                            "{1}".format(c, exc))
            finally:
                cmds.undoInfo(closeChunk=True)
        else:
            prev = cmds.undoInfo(query=True, state=True)
            cmds.undoInfo(stateWithoutFlush=False)
            try:
                for c in creators:
                    try:
                        setter(c)
                    except Exception:
                        # Silently skip during drag — errors will
                        # surface via the changeCommand path when the
                        # drag ends and it retries with undo recording.
                        pass
            finally:
                cmds.undoInfo(stateWithoutFlush=prev)

    def _warn_missing(self, attr):
        """Print a Script Editor warning at most once per session per
        missing attribute — the drag path calls setters dozens of times
        per second, so an unconditional warning would flood the log."""
        if attr in self._warned_missing_attrs:
            return
        self._warned_missing_attrs.add(attr)
        cmds.warning(
            "[maya_hair_tool] sweepMeshCreator に {0!r} 属性が"
            "見つかりません。Maya バージョンで attribute 名が異なる"
            "可能性があります (このメッセージはセッション中 1 回のみ"
            "表示)".format(attr))

    def _resolve_value(self, c, attr, slider_value, cast=float):
        """Absolute mode: return the raw slider value.
        Relative mode: return baseline[attr] × slider_value.

        Returns a value already cast to the correct numeric type."""
        if self._adjust_mode == "relative":
            baseline = self._baselines.get(c, {}).get(attr)
            if baseline is None:
                # Nothing to multiply — snapshot missed this attr,
                # so treat slider value as absolute (safer fallback).
                return cast(slider_value)
            return cast(float(baseline) * float(slider_value))
        return cast(slider_value)

    def _set_uniform_scale(self, value):
        """Set Thickness = uniform scale.

        ``scaleProfileUniform`` is a *bool* (link X ↔ Y). We turn it
        on, then set ``scaleProfileX`` — Y auto-mirrors. Preset ratios
        (Oval Y=0.55 etc.) are lost when Thickness is touched; that
        matches Maya's Uniform-mode behaviour.

        In relative mode, ``value`` is a multiplier: X is set to
        ``baseline_X × value``. Y follows via the Uniform toggle.
        """
        def setter(c):
            v = self._resolve_value(c, "scaleProfileX", value, cast=float)
            if cmds.attributeQuery("scaleProfileUniform",
                                    node=c, exists=True):
                cmds.setAttr(c + ".scaleProfileUniform", True)
            else:
                self._warn_missing("scaleProfileUniform")
            if cmds.attributeQuery("scaleProfileX", node=c, exists=True):
                cmds.setAttr(c + ".scaleProfileX", v)
            else:
                self._warn_missing("scaleProfileX")
        return setter

    def _set_axis_scale(self, axis_attr, value):
        """Set Width (X) or Height (Y) independently by first turning
        Uniform off so the sibling axis isn't force-linked.

        In relative mode, uses ``baseline[axis_attr] × value``."""
        def setter(c):
            v = self._resolve_value(c, axis_attr, value, cast=float)
            if cmds.attributeQuery("scaleProfileUniform",
                                    node=c, exists=True):
                cmds.setAttr(c + ".scaleProfileUniform", False)
            if cmds.attributeQuery(axis_attr, node=c, exists=True):
                cmds.setAttr(c + "." + axis_attr, v)
            else:
                self._warn_missing(axis_attr)
        return setter

    def _set_attr(self, attr, value, cast=float):
        def setter(c):
            if not cmds.attributeQuery(attr, node=c, exists=True):
                self._warn_missing(attr)
                return
            v = self._resolve_value(c, attr, value, cast=cast)
            cmds.setAttr(c + "." + attr, v)
        return setter

    def _set_attr_absolute(self, attr, value, cast=float):
        """Set an attribute to a literal absolute value regardless of
        ``_adjust_mode``. Used by profile-specific sliders where a
        multiplier semantic makes no sense (e.g. "星頂点数 = 5" is a
        count, not a scale factor; multiplying 12×5 = 60 gives 60
        sides, not the star with 5 points the user asked for)."""
        def setter(c):
            if not cmds.attributeQuery(attr, node=c, exists=True):
                self._warn_missing(attr)
                return
            cmds.setAttr(c + "." + attr, cast(value))
        return setter

    def _set_taper(self, root=None, middle=None, tip=None):
        def setter(c):
            existing = hair.read_taper_values(c)
            if self._adjust_mode == "relative":
                # Multiply per-strand baseline (captured at select
                # time) by the slider multiplier. Positions the
                # user didn't touch (None sliders) stay untouched.
                baseline = self._baselines.get(c, {})
                br = baseline.get("taper_root", existing[0])
                bm = baseline.get("taper_middle", existing[1])
                bt = baseline.get("taper_tip", existing[2])
                r = float(br) * float(root) if root is not None else existing[0]
                m = float(bm) * float(middle) if middle is not None else existing[1]
                t = float(bt) * float(tip) if tip is not None else existing[2]
            else:
                r = existing[0] if root is None else float(root)
                m = existing[1] if middle is None else float(middle)
                t = existing[2] if tip is None else float(tip)
            hair.set_taper_profile(
                c, root_scale=r, middle_scale=m, tip_scale=t)
        return setter

    # --- Profile ---
    def _cb_profile_change(self, *_):
        if self._syncing_sliders:
            return
        label = cmds.optionMenu(
            self.profile_menu, query=True, value=True)
        key = _PROFILE_LABEL_TO_KEY.get(label, C.PROFILE_CIRCLE)
        # Swap out the profile-specific slider set so the panel only
        # exposes knobs that actually affect the chosen shape.
        self._rebuild_profile_specific_sliders(key)
        def setter(c):
            hair.set_profile(c, key)
        self._live_apply(setter, record_undo=True)

    # -----------------------------------------------------------------
    # Profile-specific slider set (rebuilt on profile change)
    # -----------------------------------------------------------------
    def _rebuild_profile_specific_sliders(self, profile_key):
        """Tear down and rebuild the ``profile_specific_layout``
        column with the sliders for ``profile_key`` — every profile
        has its own attribute list in ``_PROFILE_ATTR_SLIDERS``."""
        if not self.profile_specific_layout:
            return
        try:
            if not cmds.columnLayout(
                    self.profile_specific_layout, exists=True):
                return
        except Exception:
            return
        if self._current_profile_key == profile_key and \
                self.profile_specific_widgets:
            # Already built for this profile — nothing to do.
            return

        # Remove existing children.
        children = cmds.columnLayout(
            self.profile_specific_layout, query=True,
            childArray=True) or []
        for child in children:
            try:
                cmds.deleteUI(child)
            except Exception:
                pass
        self.profile_specific_widgets = {}
        self._current_profile_key = profile_key

        specs = _PROFILE_ATTR_SLIDERS.get(profile_key, [])
        if not specs:
            cmds.text(
                label="(このプロファイル固有のパラメータはありません)",
                align="left",
                parent=self.profile_specific_layout,
                font="smallObliqueLabelFont",
            )
            return

        cmds.text(
            label="プロファイル固有パラメータ:",
            align="left",
            parent=self.profile_specific_layout,
            font="smallBoldLabelFont",
        )
        for label, attr, default, minv, maxv, is_int in specs:
            drag_cb, change_cb = self._make_profile_attr_callbacks(
                attr, is_int)
            if is_int:
                w = _int_slider_with_reset(
                    self.profile_specific_layout, label, default,
                    minv, maxv, drag_cb=drag_cb, change_cb=change_cb)
            else:
                w = _slider_with_reset(
                    self.profile_specific_layout, label, default,
                    minv, maxv, drag_cb=drag_cb, change_cb=change_cb)
            self.profile_specific_widgets[attr] = (w, is_int)

    def _make_profile_attr_callbacks(self, attr, is_int):
        """Build (drag_cb, change_cb) closures for a profile-specific
        slider. Bound to ``attr`` and reads the slider's current
        value lazily so the same setter works after a rebuild swaps
        widget ids."""
        cast = int if is_int else float

        def _read():
            wtup = self.profile_specific_widgets.get(attr)
            if not wtup:
                return default_of(attr)
            w, _ = wtup
            try:
                if is_int:
                    return int(cmds.intSliderGrp(
                        w, query=True, value=True))
                return float(cmds.floatSliderGrp(
                    w, query=True, value=True))
            except Exception:
                return default_of(attr)

        def default_of(_attr):
            for k, entries in _PROFILE_ATTR_SLIDERS.items():
                for spec in entries:
                    if spec[1] == _attr:
                        return spec[2]
            return 0

        def drag_cb(*_):
            self._live_apply(
                self._set_attr_absolute(attr, _read(), cast=cast),
                record_undo=False)

        def change_cb(*_):
            self._live_apply(
                self._set_attr_absolute(attr, _read(), cast=cast),
                record_undo=True)

        return drag_cb, change_cb

    # --- Thickness (uniform X = Y) ---
    def _cb_thickness_drag(self, *_):
        self._live_apply(
            self._set_uniform_scale(_read_float(self.thickness)),
            record_undo=False)

    def _cb_thickness_change(self, *_):
        self._live_apply(
            self._set_uniform_scale(_read_float(self.thickness)),
            record_undo=True)

    # --- Width (X only, Uniform disabled) ---
    def _cb_width_drag(self, *_):
        self._live_apply(
            self._set_axis_scale("scaleProfileX", _read_float(self.width)),
            record_undo=False)

    def _cb_width_change(self, *_):
        self._live_apply(
            self._set_axis_scale("scaleProfileX", _read_float(self.width)),
            record_undo=True)

    # --- Height (Y only, Uniform disabled) ---
    def _cb_height_drag(self, *_):
        self._live_apply(
            self._set_axis_scale("scaleProfileY", _read_float(self.height)),
            record_undo=False)

    def _cb_height_change(self, *_):
        self._live_apply(
            self._set_axis_scale("scaleProfileY", _read_float(self.height)),
            record_undo=True)

    # --- Root / Middle / Tip taper ---
    def _cb_root_drag(self, *_):
        self._live_apply(
            self._set_taper(root=_read_float(self.root)),
            record_undo=False)

    def _cb_root_change(self, *_):
        self._live_apply(
            self._set_taper(root=_read_float(self.root)),
            record_undo=True)

    def _cb_middle_drag(self, *_):
        self._live_apply(
            self._set_taper(middle=_read_float(self.middle)),
            record_undo=False)

    def _cb_middle_change(self, *_):
        self._live_apply(
            self._set_taper(middle=_read_float(self.middle)),
            record_undo=True)

    def _cb_tip_drag(self, *_):
        self._live_apply(
            self._set_taper(tip=_read_float(self.tip)),
            record_undo=False)

    def _cb_tip_change(self, *_):
        self._live_apply(
            self._set_taper(tip=_read_float(self.tip)),
            record_undo=True)

    # --- Twist ---
    def _cb_twist_drag(self, *_):
        self._live_apply(
            self._set_attr("twist", _read_float(self.twist)),
            record_undo=False)

    def _cb_twist_change(self, *_):
        self._live_apply(
            self._set_attr("twist", _read_float(self.twist)),
            record_undo=True)

    # --- Rotation ---
    def _cb_rotation_drag(self, *_):
        self._live_apply(
            self._set_attr("rotateProfile", _read_float(self.rotation)),
            record_undo=False)

    def _cb_rotation_change(self, *_):
        self._live_apply(
            self._set_attr("rotateProfile", _read_float(self.rotation)),
            record_undo=True)

    # --- Subdivision ---
    # 断面分割数: profilePolySides (Regular Polygon 系プロファイルで
    #   側面数を決める)。Round では効きが薄いことがあるので断面が
    #   はっきり分割される profile (Star / Square 等) を選んでから
    #   触るのが分かりやすい。
    # 長さ分割数: interpolationPrecision (interpolationMode が既定
    #   の "Precision" の場合に有効)。
    def _cb_subdiv_axis_drag(self, *_):
        self._live_apply(
            self._set_attr(
                "profilePolySides", _read_int(self.subdiv_axis), cast=int),
            record_undo=False)

    def _cb_subdiv_axis_change(self, *_):
        self._live_apply(
            self._set_attr(
                "profilePolySides", _read_int(self.subdiv_axis), cast=int),
            record_undo=True)

    def _cb_subdiv_length_drag(self, *_):
        self._live_apply(
            self._set_attr(
                "interpolationPrecision", _read_int(self.subdiv_length),
                cast=float),
            record_undo=False)

    def _cb_subdiv_length_change(self, *_):
        self._live_apply(
            self._set_attr(
                "interpolationPrecision", _read_int(self.subdiv_length),
                cast=float),
            record_undo=True)

    # -----------------------------------------------------------------
    # Duplicate panel  (Phase 2)
    # -----------------------------------------------------------------
    def _build_duplicate_panel(self, parent):
        cmds.frameLayout(label="毛束を複製", collapsable=False,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        self.dup_count = _int_slider(col, "個数", 1, 1, 20)
        self.dup_offset = cmds.floatFieldGrp(
            label="オフセット (X Y Z)",
            numberOfFields=3,
            value1=1.0, value2=0.0, value3=0.0,
            columnAlign=(1, "left"),
            columnWidth4=(90, 60, 60, 60),
            parent=col,
        )
        cmds.text(
            label=("カーブ / メッシュ / sweep のいずれかを選択してから "
                   "複製してください。"),
            align="left", parent=col, font="smallObliqueLabelFont",
        )
        cmds.button(label="選択毛束を複製",
                    height=32, command=self._on_duplicate, parent=col)

    def _on_duplicate(self, *_):
        creators = su.sweep_creators_from_selection()
        if not creators:
            cmds.warning(
                "選択に毛束が見つかりません。"
                "カーブ / メッシュ / sweepMeshCreator のいずれかを"
                "選択してください。")
            return
        count = _read_int(self.dup_count)
        offset = (
            cmds.floatFieldGrp(self.dup_offset, query=True, value1=True),
            cmds.floatFieldGrp(self.dup_offset, query=True, value2=True),
            cmds.floatFieldGrp(self.dup_offset, query=True, value3=True),
        )
        duplicate.duplicate_hair(creators, count=count, offset=offset)
        self._refresh_hair_list()

    # -----------------------------------------------------------------
    # Batch Edit panel
    # -----------------------------------------------------------------
    def _build_batch_panel(self, parent):
        cmds.frameLayout(label="一括編集", collapsable=False,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        cmds.text(label="モード", align="left", parent=col)
        self.batch_mode = cmds.radioButtonGrp(
            numberOfRadioButtons=2,
            labelArray2=("絶対値", "相対"),
            select=2,
            parent=col,
        )

        self.batch_thickness = _batch_slider_with_reset(
            col, "太さ", 1.0, 0.01, 5.0)
        self.batch_width = _batch_slider_with_reset(
            col, "幅", 1.0, 0.01, 5.0)
        self.batch_height = _batch_slider_with_reset(
            col, "高さ", 1.0, 0.01, 5.0)
        self.batch_root = _batch_slider_with_reset(
            col, "根本スケール", 1.0, 0.0, 3.0)
        self.batch_middle = _batch_slider_with_reset(
            col, "中間スケール", 1.0, 0.0, 3.0)
        self.batch_tip = _batch_slider_with_reset(
            col, "先端スケール", 1.0, 0.0, 3.0)
        self.batch_twist = _batch_slider_with_reset(
            col, "ねじれ", 0.0, -720.0, 720.0)
        self.batch_subdiv = _batch_int_slider_with_reset(
            col, "断面分割数", 8, 3, 64)

        cmds.text(
            label=("スライダーが 1.0 (ねじれは 0.0、断面分割数は既定 8) "
                   "のままの項目は「変更なし」として扱われます。"
                   "絶対値モードでは既存値を保持、相対モードでは ×1 の "
                   "no-op になります。ねじれの相対モードは加算 "
                   "(例: 相対 +45 → 各毛束の現在ねじれに +45°)、"
                   "断面分割数は絶対値モードのみ適用されます。"
                   "太さは Uniform=ON、幅/高さは Uniform=OFF に切替 "
                   "してから適用します。"),
            align="left", parent=col, font="smallObliqueLabelFont",
            wordWrap=True,
        )

        cmds.button(label="選択に適用",
                    height=32, command=self._on_batch_apply, parent=col)

    def _on_batch_apply(self, *_):
        creators = batch.creators_from_selection()
        if not creators:
            cmds.warning("選択に毛束が見つかりません。")
            return
        is_absolute = cmds.radioButtonGrp(
            self.batch_mode, query=True, select=True) == 1

        thickness = _read_float(self.batch_thickness)
        width = _read_float(self.batch_width)
        height_v = _read_float(self.batch_height)
        root = _read_float(self.batch_root)
        middle = _read_float(self.batch_middle)
        tip = _read_float(self.batch_tip)
        twist = _read_float(self.batch_twist)
        subdiv = _read_int(self.batch_subdiv)

        # Convention: sliders at their identity value are treated as
        # "no change". Use ``batch.is_identity`` with a small tolerance
        # so slider-drag FP residues (0.99999998 etc.) still count as
        # identity.
        # Resolve scaleProfileX/Y so each is written at most once per
        # Apply (previous plan wrote thickness *then* width to X, which
        # cancelled thickness in Absolute and double-applied it in
        # Relative).
        scalar_plan = []      # multiplicative attrs
        delta_plan = []       # additive attrs (Relative uses +, not *)
        # ``scaleProfileUniform`` is a bool toggle (link X ↔ Y) so it
        # needs its own pre-step, not a multiplicative row here.
        uniform_pre = None  # True / False / None (leave as-is)
        if not batch.is_identity(thickness, 1.0):
            uniform_pre = True
            scalar_plan.append(("scaleProfileX", thickness))
        else:
            if (not batch.is_identity(width, 1.0)
                    or not batch.is_identity(height_v, 1.0)):
                uniform_pre = False
            if not batch.is_identity(width, 1.0):
                scalar_plan.append(("scaleProfileX", width))
            if not batch.is_identity(height_v, 1.0):
                scalar_plan.append(("scaleProfileY", height_v))
        if not batch.is_identity(twist, 0.0):
            # Twist is angular: Relative "×45" is nonsensical, treat
            # the slider as a delta added to each strand's current
            # twist. Absolute is a straight set as before.
            if is_absolute:
                scalar_plan.append(("twist", twist))
            else:
                delta_plan.append(("twist", twist))
        # 断面分割数 = profilePolySides (only meaningful on Regular
        # Polygon profile types). Absolute only; Relative ×N on an
        # integer count is ill-defined.
        if is_absolute and subdiv != C.DEFAULT_SUBDIVISIONS_AXIS:
            scalar_plan.append(("profilePolySides", subdiv))

        # Skip the taper stage entirely when every taper slider is at
        # identity — otherwise ``set_taper_profile`` would wipe any
        # extra ramp entries the user authored in the Attribute Editor
        # (a real Phase 4 preset-usage regression risk).
        taper_touched = (
            not batch.is_identity(root, 1.0)
            or not batch.is_identity(middle, 1.0)
            or not batch.is_identity(tip, 1.0)
        )

        with batch.batch_undo_chunk("HairBatchApply"):
            # Toggle scaleProfileUniform once up-front so subsequent
            # X/Y setAttr's aren't clobbered by Maya's Uniform link.
            if uniform_pre is not None:
                for c in creators:
                    if cmds.attributeQuery(
                            "scaleProfileUniform", node=c, exists=True):
                        try:
                            cmds.setAttr(
                                c + ".scaleProfileUniform", uniform_pre)
                        except Exception:
                            pass
            for attr, value in scalar_plan:
                if is_absolute:
                    batch.apply_absolute(creators, attr, value)
                else:
                    batch.apply_relative(creators, attr, value)
            for attr, delta in delta_plan:
                batch.apply_delta(creators, attr, delta)

            if taper_touched:
                # Taper (Root / Middle / Tip) — read the current ramp
                # per strand so a slider left at 1.0 preserves the
                # existing value (Absolute) or is a 1× no-op (Relative).
                for c in creators:
                    existing = hair.read_taper_values(c)
                    if is_absolute:
                        new_r = (root if not batch.is_identity(root, 1.0)
                                 else existing[0])
                        new_m = (middle if not batch.is_identity(middle, 1.0)
                                 else existing[1])
                        new_t = (tip if not batch.is_identity(tip, 1.0)
                                 else existing[2])
                    else:
                        new_r = existing[0] * root
                        new_m = existing[1] * middle
                        new_t = existing[2] * tip
                    hair.set_taper_profile(
                        c,
                        root_scale=new_r,
                        middle_scale=new_m,
                        tip_scale=new_t,
                    )

    # -----------------------------------------------------------------
    # Hair library panel (Phase 4)
    # -----------------------------------------------------------------
    def _build_library_panel(self, parent):
        """``parent`` is expected to be a formLayout so the frame
        stretches to fill the left pane's bottom half. Layout mirrors
        Substance Painter's shelf: a header, a scrollable icon grid
        that grows / shrinks with the pane, then a two-button strip
        pinned to the bottom."""
        self.library_frame = cmds.frameLayout(
            label="ヘアライブラリ", collapsable=False,
            marginHeight=4, marginWidth=4, parent=parent)
        cmds.formLayout(
            parent, edit=True,
            attachForm=[
                (self.library_frame, "top", 0),
                (self.library_frame, "bottom", 0),
                (self.library_frame, "left", 0),
                (self.library_frame, "right", 0),
            ],
        )

        inner = cmds.formLayout(parent=self.library_frame)

        try:
            root_path = library.library_root()
        except Exception:
            root_path = "(unknown)"
        header = cmds.text(
            label=("保存先: {0}".format(root_path)),
            align="left", font="smallObliqueLabelFont",
            wordWrap=True, parent=inner,
        )

        # Scrollable icon grid so N presets scroll instead of pushing
        # the buttons off screen.
        grid_scroll = cmds.scrollLayout(
            horizontalScrollBarThickness=0,
            childResizable=True,
            parent=inner,
        )
        self.library_grid = cmds.rowColumnLayout(
            numberOfColumns=2,
            columnWidth=[(1, 90), (2, 90)],
            rowSpacing=(1, 4), columnSpacing=(1, 4),
            parent=grid_scroll,
        )

        # Save / refresh strip pinned to the bottom.
        btn_row = cmds.rowLayout(
            numberOfColumns=2, adjustableColumn=1,
            columnAttach=[(1, "both", 2), (2, "both", 2)],
            columnWidth2=(140, 50), parent=inner,
        )
        cmds.button(
            label="選択毛束を保存",
            annotation=("選択中の毛束 (メッシュ / カーブ / sweep) を"
                        "1 本、library ディレクトリに .ma として保存し、"
                        "playblast でサムネイル (.png) を生成します。"),
            command=self._on_save_to_library, parent=btn_row,
        )
        cmds.button(
            label="更新",
            annotation="ライブラリを再スキャンしてグリッドを作り直します。",
            command=self._on_refresh_library, parent=btn_row,
        )
        cmds.setParent("..")

        cmds.formLayout(
            inner, edit=True,
            attachForm=[
                (header, "top", 4),
                (header, "left", 4),
                (header, "right", 4),
                (grid_scroll, "left", 0),
                (grid_scroll, "right", 0),
                (btn_row, "left", 4),
                (btn_row, "right", 4),
                (btn_row, "bottom", 4),
            ],
            attachControl=[
                (grid_scroll, "top", 4, header),
                (grid_scroll, "bottom", 4, btn_row),
            ],
        )

        self._refresh_library_grid()

    def _refresh_library_grid(self):
        if not self.library_grid:
            return
        try:
            if not cmds.rowColumnLayout(
                    self.library_grid, exists=True):
                return
        except Exception:
            return

        # Wipe existing icons.
        for child in cmds.rowColumnLayout(
                self.library_grid, query=True, childArray=True) or []:
            try:
                cmds.deleteUI(child)
            except Exception:
                pass

        entries = library.list_library_entries()
        if not entries:
            cmds.text(
                label="(ライブラリは空です)",
                parent=self.library_grid,
                font="smallObliqueLabelFont", align="left")
            return

        for name, ma_path, png_path in entries:
            image = png_path if png_path else "pythonFamily.png"
            # Two closures — one for import (click), one for
            # delete (right-click). Both need to capture the
            # loop values as defaults.
            def _cb_import(_ma=ma_path, *_):
                self._import_from_library(_ma)

            def _cb_delete(_name=name, *_):
                self._delete_from_library(_name)

            btn = cmds.iconTextButton(
                label=name,
                image=image,
                width=86, height=106,
                style="iconAndTextVertical",
                annotation=("{0} をインポート — 右クリックで"
                            "メニュー".format(name)),
                command=_cb_import,
                parent=self.library_grid,
            )
            # Right-click popup for delete.
            popup = cmds.popupMenu(parent=btn, button=3)
            cmds.menuItem(
                label="インポート", command=_cb_import,
                parent=popup)
            cmds.menuItem(divider=True, parent=popup)
            cmds.menuItem(
                label="ライブラリから削除",
                command=_cb_delete, parent=popup)

    def _on_save_to_library(self, *_):
        result = cmds.promptDialog(
            title="ライブラリに保存",
            message="プリセット名:",
            button=["保存", "キャンセル"],
            defaultButton="保存",
            cancelButton="キャンセル",
            dismissString="キャンセル",
            text="my_hair",
        )
        if result != "保存":
            return
        name = cmds.promptDialog(query=True, text=True) or ""
        name = name.strip()
        if not name:
            cmds.warning("プリセット名が空です。")
            return
        try:
            ma_path = library.save_hair_to_library(name)
        except Exception as exc:
            cmds.warning(str(exc))
            return
        cmds.inViewMessage(
            statusMessage=("ライブラリに保存しました: "
                            "{0}".format(name)),
            fade=True, position="topCenter")
        self._refresh_library_grid()

    def _on_refresh_library(self, *_):
        self._refresh_library_grid()

    def _import_from_library(self, ma_path):
        try:
            library.import_hair_from_library(ma_path)
        except Exception as exc:
            cmds.warning(str(exc))
            return
        # New strand landed in the scene → refresh both lists.
        self._refresh_hair_list()

    def _delete_from_library(self, name):
        # Guard the destructive op behind a confirmation.
        result = cmds.confirmDialog(
            title="削除確認",
            message=("プリセット '{0}' をライブラリから削除"
                     "しますか?\n(.ma とサムネイル .png "
                     "両方を消します)".format(name)),
            button=["削除", "キャンセル"],
            defaultButton="キャンセル",
            cancelButton="キャンセル",
            dismissString="キャンセル",
        )
        if result != "削除":
            return
        try:
            library.delete_library_entry(name)
        except Exception as exc:
            cmds.warning(str(exc))
            return
        self._refresh_library_grid()

    # -----------------------------------------------------------------
    # Menu bar handlers
    # -----------------------------------------------------------------
    def _on_reset_all_sliders(self, *_):
        """Reset every Create-panel and Batch-panel slider (including
        the profile-specific set) to its factory default.

        Each per-slider ↺ button already does this individually; this
        wraps the whole set into one undo chunk so the user can Ctrl+Z
        an accidental "全部リセット" in one step. The change callbacks
        are invoked so the reset applies live to any selected strand
        (absolute mode → strand's attrs go to defaults; relative mode
        → each strand goes back to its baseline since multiplier 1.0)."""
        with batch.batch_undo_chunk("HairResetAllSliders"):
            # Create panel — float sliders + their change callbacks.
            for widget, default, change_cb in (
                (self.thickness, C.DEFAULT_THICKNESS,
                 self._cb_thickness_change),
                (self.width, C.DEFAULT_WIDTH,
                 self._cb_width_change),
                (self.height, C.DEFAULT_HEIGHT,
                 self._cb_height_change),
                (self.root, C.DEFAULT_ROOT_SCALE,
                 self._cb_root_change),
                (self.middle, C.DEFAULT_MIDDLE_SCALE,
                 self._cb_middle_change),
                (self.tip, C.DEFAULT_TIP_SCALE,
                 self._cb_tip_change),
                (self.twist, C.DEFAULT_TWIST,
                 self._cb_twist_change),
                (self.rotation, C.DEFAULT_ROTATION,
                 self._cb_rotation_change),
            ):
                if not widget:
                    continue
                try:
                    cmds.floatSliderGrp(
                        widget, edit=True, value=float(default))
                except Exception:
                    continue
                try:
                    change_cb()
                except Exception:
                    pass

            # Create panel — int sliders.
            for widget, default, change_cb in (
                (self.subdiv_axis, C.DEFAULT_SUBDIVISIONS_AXIS,
                 self._cb_subdiv_axis_change),
                (self.subdiv_length, C.DEFAULT_SUBDIVISIONS_LENGTH,
                 self._cb_subdiv_length_change),
            ):
                if not widget:
                    continue
                try:
                    cmds.intSliderGrp(
                        widget, edit=True, value=int(default))
                except Exception:
                    continue
                try:
                    change_cb()
                except Exception:
                    pass

            # Profile-specific sliders — reset each to its spec
            # default and apply via the absolute setter.
            for attr, wtup in list(
                    self.profile_specific_widgets.items()):
                widget, is_int = wtup
                default = None
                for specs in _PROFILE_ATTR_SLIDERS.values():
                    for spec in specs:
                        if spec[1] == attr:
                            default = spec[2]
                            break
                    if default is not None:
                        break
                if default is None:
                    continue
                try:
                    if is_int:
                        cmds.intSliderGrp(
                            widget, edit=True, value=int(default))
                    else:
                        cmds.floatSliderGrp(
                            widget, edit=True, value=float(default))
                except Exception:
                    continue
                cast = int if is_int else float
                try:
                    self._live_apply(
                        self._set_attr_absolute(
                            attr, default, cast=cast),
                        record_undo=False)
                except Exception:
                    pass

            # Batch panel — pure UI reset (no scene apply; user runs
            # 「選択に適用」explicitly for Batch, so we only clear the
            # sliders back to their identity values here).
            batch_float_defaults = (
                (self.batch_thickness, 1.0),
                (self.batch_width, 1.0),
                (self.batch_height, 1.0),
                (self.batch_root, 1.0),
                (self.batch_middle, 1.0),
                (self.batch_tip, 1.0),
                (self.batch_twist, 0.0),
            )
            batch_int_defaults = (
                (self.batch_subdiv, C.DEFAULT_SUBDIVISIONS_AXIS),
            )
            for widget, default in batch_float_defaults:
                if not widget:
                    continue
                try:
                    cmds.floatSliderGrp(
                        widget, edit=True, value=float(default))
                except Exception:
                    pass
            for widget, default in batch_int_defaults:
                if not widget:
                    continue
                try:
                    cmds.intSliderGrp(
                        widget, edit=True, value=int(default))
                except Exception:
                    pass

    def _on_show_version(self, *_):
        """About → バージョン情報 dialog."""
        try:
            cmds.confirmDialog(
                title="バージョン情報",
                message=(
                    "{title}\n\n"
                    "バージョン: {ver}\n"
                    "リポジトリ: https://github.com/{owner}/{repo}\n"
                    "ブランチ: {branch}\n\n"
                    "更新は About → GitHub から更新 で可能。\n"
                    "配布ファイル: install.py \n"
                ).format(
                    title=WINDOW_TITLE,
                    ver=__version__,
                    owner=_GITHUB_OWNER,
                    repo=_GITHUB_REPO,
                    branch=_GITHUB_BRANCH,
                ),
                button=["OK"],
            )
        except Exception:
            pass

    # -----------------------------------------------------------------
    # Footer
    # -----------------------------------------------------------------
    def _build_footer(self, parent):
        row = cmds.rowLayout(
            numberOfColumns=2,
            adjustableColumn=1,
            columnAttach=[(1, "both", 4), (2, "both", 4)],
            columnWidth2=(280, 80),
            parent=parent,
        )
        cmds.text(
            label=("{0}  v{1}   (更新は About メニューから)".format(
                _PACKAGE, __version__)),
            align="left",
            font="smallObliqueLabelFont",
            parent=row,
        )
        cmds.button(label="閉じる",
                    command=lambda *_: cmds.deleteUI(WINDOW_NAME),
                    parent=row)


# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------

def _slider(parent, label, value, minv, maxv,
            field_min=-1e6, field_max=1e6,
            drag_cb=None, change_cb=None):
    kwargs = dict(
        label=label,
        field=True,
        minValue=minv,
        maxValue=maxv,
        fieldMinValue=field_min,
        fieldMaxValue=field_max,
        value=value,
        columnAlign=(1, "left"),
        columnWidth3=(90, 60, 120),
        parent=parent,
    )
    if drag_cb is not None:
        kwargs["dragCommand"] = drag_cb
    if change_cb is not None:
        kwargs["changeCommand"] = change_cb
    return cmds.floatSliderGrp(**kwargs)


def _int_slider(parent, label, value, minv, maxv,
                field_min=1, field_max=999,
                drag_cb=None, change_cb=None):
    kwargs = dict(
        label=label,
        field=True,
        minValue=minv,
        maxValue=maxv,
        fieldMinValue=field_min,
        fieldMaxValue=field_max,
        value=value,
        columnAlign=(1, "left"),
        columnWidth3=(90, 60, 120),
        parent=parent,
    )
    if drag_cb is not None:
        kwargs["dragCommand"] = drag_cb
    if change_cb is not None:
        kwargs["changeCommand"] = change_cb
    return cmds.intSliderGrp(**kwargs)


def _with_reset(parent, factory, default, reset_kind, change_cb=None):
    """Wrap ``factory(row, ...)`` with a small reset button.

    * ``factory`` is called with a single ``row`` argument and returns the
      slider widget id.
    * ``default`` is the value the reset button restores.
    * ``reset_kind`` is 'float' or 'int' — controls which cmds edit-call
      is used to set the widget's value.
    * ``change_cb`` is invoked after reset so live-edit callbacks apply
      the restored value immediately.
    """
    row = cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                         columnWidth2=(300, 26), parent=parent)
    slider = factory(row)

    def _do_reset(*_):
        if reset_kind == "int":
            cmds.intSliderGrp(slider, edit=True, value=int(default))
        else:
            cmds.floatSliderGrp(slider, edit=True, value=float(default))
        if change_cb is not None:
            try:
                change_cb()
            except Exception:
                pass

    cmds.button(
        label="↺", width=24, height=22,
        annotation="初期値 ({0}) にリセット".format(default),
        command=_do_reset, parent=row,
    )
    cmds.setParent("..")
    return slider


def _slider_with_reset(parent, label, default, minv, maxv,
                        drag_cb=None, change_cb=None,
                        field_min=-1e6, field_max=1e6):
    def _factory(row):
        return _slider(row, label, default, minv, maxv,
                       field_min=field_min, field_max=field_max,
                       drag_cb=drag_cb, change_cb=change_cb)
    return _with_reset(parent, _factory, default, "float", change_cb)


def _int_slider_with_reset(parent, label, default, minv, maxv,
                            drag_cb=None, change_cb=None,
                            field_min=1, field_max=999):
    def _factory(row):
        return _int_slider(row, label, default, minv, maxv,
                            field_min=field_min, field_max=field_max,
                            drag_cb=drag_cb, change_cb=change_cb)
    return _with_reset(parent, _factory, default, "int", change_cb)


def _batch_slider_with_reset(parent, label, default, minv, maxv,
                              drag_cb=None, change_cb=None):
    def _factory(row):
        return _batch_slider(row, label, default, minv, maxv,
                              drag_cb=drag_cb, change_cb=change_cb)
    return _with_reset(parent, _factory, default, "float", change_cb)


def _batch_int_slider_with_reset(parent, label, default, minv, maxv,
                                  drag_cb=None, change_cb=None):
    def _factory(row):
        return _batch_int_slider(row, label, default, minv, maxv,
                                  drag_cb=drag_cb, change_cb=change_cb)
    return _with_reset(parent, _factory, default, "int", change_cb)


def _batch_slider(parent, label, value, minv, maxv,
                  drag_cb=None, change_cb=None):
    """Batch-panel slider with a tight field range.

    Numeric-field entry is capped to a sensible domain (roughly the
    slider's own range with headroom) so a typo like ``1000000`` in
    Relative mode can't multiply every strand's thickness a million-
    fold in a single Apply.
    """
    # Give the field a bit of headroom past the slider max so users
    # can still exceed the slider knob's range when they really want,
    # but not by ~5 orders of magnitude.
    headroom = 5.0
    return _slider(
        parent, label, value, minv, maxv,
        field_min=min(minv, 0.0) - headroom,
        field_max=maxv + headroom,
        drag_cb=drag_cb, change_cb=change_cb,
    )


def _batch_int_slider(parent, label, value, minv, maxv,
                      drag_cb=None, change_cb=None):
    return _int_slider(
        parent, label, value, minv, maxv,
        field_min=minv,
        field_max=maxv,
        drag_cb=drag_cb, change_cb=change_cb,
    )


def _read_float(widget):
    return cmds.floatSliderGrp(widget, query=True, value=True)


def _read_int(widget):
    return cmds.intSliderGrp(widget, query=True, value=True)


# ---------------------------------------------------------------------------
# Update-from-GitHub flow  (patterns doc §1-7, §1-8, §1-9)
# ---------------------------------------------------------------------------

def _resolve_latest_sha():
    """Return the tip commit SHA of `_GITHUB_BRANCH`.

    SHA-pinned raw URLs are the only reliable cache-buster for
    ``raw.githubusercontent.com`` — the CDN keys on path only, so a
    ``?_=timestamp`` cache-buster does nothing there. We ask the GitHub
    API for the current tip SHA and use that in the raw URL, so a new
    commit always produces a new cache key.
    """
    import json
    import random
    import time
    import urllib.request

    salt = "{0:.6f}_{1}".format(time.time(), random.randint(0, 2 ** 32))
    req = urllib.request.Request(
        "{0}/branches/{1}?_={2}".format(_GITHUB_API, _GITHUB_BRANCH, salt),
        headers={
            "Accept": "application/vnd.github+json",
            "Cache-Control": "no-cache",
            "User-Agent": "{0}-updater/{1}".format(_PACKAGE, salt),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(
                resp.read().decode("utf-8"))["commit"]["sha"]
    except Exception as exc:
        # Do NOT fall back to branch-name URL (patterns doc §1-7:
        # raw.githubusercontent.com CDN caches by path only, so a
        # branch URL would serve stale content). Raise so the caller's
        # try/except can surface a proper error dialog.
        raise RuntimeError(
            "SHA lookup failed for branch {0!r}: {1}\n"
            "Refusing to fall back to branch-name URL (would hit CDN "
            "cache and serve stale content). Check network / GitHub "
            "status and try again.".format(_GITHUB_BRANCH, exc))


def update_from_github(*_args):
    """UI button callback.

    Returns immediately — the actual work runs on the next Maya idle so
    we don't tear down the window that owns this callback while the
    callback is still on the stack (patterns doc §1-9).
    """
    cmds.evalDeferred(_run_update, lowestPriority=True)


def _run_update():
    import sys
    import traceback
    import urllib.request

    # Resolve SHA and fetch install.py — either step can fail
    # (offline / rate-limited / DNS / branch missing). Surface a single
    # unified error dialog rather than an uncaught exception in the
    # Script Editor.
    try:
        sha = _resolve_latest_sha()
        url = "{0}/{1}/install.py".format(_GITHUB_RAW_BASE, sha)
        print("[{0}] update: fetching {1}".format(_PACKAGE, url))
        req = urllib.request.Request(url, headers={
            "Cache-Control": "no-cache",
            "User-Agent": "{0}-updater/{1}".format(_PACKAGE, sha[:10]),
        })
        source = urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(
            title="更新失敗",
            message="GitHub からの更新に失敗しました:\n{0}\n\n"
                    "詳細は Script Editor を確認してください。".format(exc),
            button=["OK"])
        return

    if cmds.window(WINDOW_NAME, exists=True):
        try:
            cmds.deleteUI(WINDOW_NAME)
        except Exception:
            pass

    # Pin the SHA we already resolved so ``install._fetch_package`` /
    # ``install._resolve_latest_sha`` reuses it instead of hitting
    # ``/branches/main`` a second time. Halves the GitHub API budget
    # for the Update flow and guarantees both halves of the update
    # (fetch install.py + fetch package files) reference the *same*
    # commit even if a new push lands between them.
    import os
    env_pin = _PACKAGE.upper() + "_PIN_SHA"
    os.environ[env_pin] = sha
    ns = {"__name__": "install", "__file__": "<github>"}
    try:
        exec(compile(source, "install.py (from GitHub)", "exec"), ns)
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(
            title="更新失敗",
            message=("install.py の実行でエラーが発生しました:\n"
                     "{0}: {1}\n\n"
                     "詳細は Script Editor を確認してください。".format(
                         type(exc).__name__, exc)),
            button=["OK"])
        return
    finally:
        os.environ.pop(env_pin, None)

    # Flush the whole package so the next import re-reads from disk
    # (patterns doc §1-6).
    for m in [k for k in list(sys.modules)
              if k == _PACKAGE or k.startswith(_PACKAGE + ".")]:
        sys.modules.pop(m, None)

    # Defer reopen so install()'s confirmDialog is fully dismissed first.
    cmds.evalDeferred(_reopen_after_update, lowestPriority=True)


def _reopen_after_update():
    import importlib
    import sys
    import traceback
    try:
        if _PACKAGE in sys.modules:
            importlib.reload(sys.modules[_PACKAGE])
        mod = importlib.import_module(_PACKAGE)
        mod.show()
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(
            title="再オープン失敗",
            message=("更新は完了しましたが、ウィンドウの再表示に"
                     "失敗しました:\n{0}: {1}\n\n"
                     "シェルフボタンから手動で再オープンして"
                     "ください。".format(type(exc).__name__, exc)),
            button=["OK"])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def show():
    """Open the Hair Builder window."""
    HairBuilderUI().show()
    return WINDOW_NAME
