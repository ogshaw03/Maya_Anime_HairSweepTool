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
    ("カスタム (Custom)", C.PROFILE_CUSTOM),
]
_PROFILE_LABEL_TO_KEY = {label: key for label, key in _PROFILE_DISPLAY}

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
                    widthHeight=(560, 780))

        # Top-level split: left = hair list, right = existing panels.
        # paneLayout gives the user a draggable divider between them.
        pane = cmds.paneLayout(configuration="vertical2",
                               paneSize=[1, 30, 100])

        # Each pane holds a single stretchy formLayout so the child
        # widget fills the pane's full height — otherwise a plain
        # columnLayout would keep its children at their natural size
        # and leave a gap between the list and the pane's bottom edge.
        left_form = cmds.formLayout(parent=pane)
        self._build_hair_list_panel(left_form)

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
        # SelectionChanged drives the inline taper editor sync so the
        # widget always mirrors the strand you're currently editing.
        try:
            jid = cmds.scriptJob(
                parent=WINDOW_NAME,
                event=["SelectionChanged",
                        self._sync_taper_editor_from_creator])
            self._script_jobs.append(jid)
        except Exception:
            pass

        cmds.showWindow(WINDOW_NAME)
        # Populate the list + taper editor once on show.
        self._refresh_hair_list()
        self._sync_taper_editor_from_creator()

    # -----------------------------------------------------------------
    # Hair list panel — enumerate strands tagged animeHairTool so the
    # user can jump-select from the UI instead of poking at the
    # Outliner. Populated on show + auto-refreshed via scriptJob.
    # -----------------------------------------------------------------
    def _build_hair_list_panel(self, parent):
        # ``parent`` is expected to be a formLayout so the frame can
        # stretch to the full pane height. We build the frame + inner
        # widgets, then wire attachForm on both this-level form and
        # the inner form so the textScrollList grows / shrinks with
        # the window.
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
            label=("このツールで作った毛束の一覧です。\n"
                   "クリックで選択、Ctrl / Shift クリックで複数選択。\n"
                   "選択すると Create パネルの調整が反映されます。"),
            align="left", font="smallObliqueLabelFont",
            wordWrap=True, parent=inner,
        )

        self.hair_list = cmds.textScrollList(
            allowMultiSelection=True,
            selectCommand=self._on_hair_list_select,
            doubleClickCommand=self._on_hair_list_double_click,
            parent=inner,
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
                (refresh_btn, "left", 4),
                (refresh_btn, "right", 4),
                (refresh_btn, "bottom", 4),
            ],
            attachControl=[
                (self.hair_list, "top", 4, help_text),
                (self.hair_list, "bottom", 4, refresh_btn),
            ],
        )

    def _hair_creators_in_scene(self):
        """Return every sweepMeshCreator tagged ``animeHairTool``."""
        all_creators = cmds.ls(type="sweepMeshCreator") or []
        return [c for c in all_creators
                if cmds.attributeQuery(
                    C.TOOL_TAG_ATTR, node=c, exists=True)]

    def _hair_list_entries(self):
        """Return ``[(display_name, mesh_transform_path)]`` for the
        UI list. Falls back to the creator name when a strand has
        no downstream mesh (shouldn't normally happen)."""
        entries = []
        for c in self._hair_creators_in_scene():
            mesh = su.mesh_from_creator(c)
            target = mesh if mesh else c
            display = target.split("|")[-1]
            entries.append((display, target))
        entries.sort(key=lambda pv: pv[0].lower())
        return entries

    def _refresh_hair_list(self, *_):
        """Repopulate the list widget. Safe to call from scriptJob
        event handlers — silently no-ops when the widget is gone."""
        if not self.hair_list:
            return
        try:
            if not cmds.textScrollList(
                    self.hair_list, exists=True):
                return
        except Exception:
            return

        # Preserve current selection where possible.
        try:
            prev_indices = cmds.textScrollList(
                self.hair_list, query=True, selectIndexedItem=True) or []
        except Exception:
            prev_indices = []
        prev_labels = []
        try:
            all_prev = cmds.textScrollList(
                self.hair_list, query=True, allItems=True) or []
            for idx in prev_indices:
                if 1 <= idx <= len(all_prev):
                    prev_labels.append(all_prev[idx - 1])
        except Exception:
            pass

        cmds.textScrollList(self.hair_list, edit=True, removeAll=True)
        for display, _target in self._hair_list_entries():
            cmds.textScrollList(self.hair_list, edit=True, append=display)

        # Restore selection by label if the same strand is still there.
        current = cmds.textScrollList(
            self.hair_list, query=True, allItems=True) or []
        for label in prev_labels:
            if label in current:
                cmds.textScrollList(
                    self.hair_list, edit=True, selectItem=label)

    def _on_refresh_hair_list(self, *_):
        self._refresh_hair_list()

    def _on_hair_list_select(self, *_):
        """Sync scene selection with the list selection so the
        live-edit callbacks pick up the newly-highlighted strand(s)."""
        labels = cmds.textScrollList(
            self.hair_list, query=True, selectItem=True) or []
        if not labels:
            return
        entries = dict(self._hair_list_entries())
        targets = [entries[label] for label in labels if label in entries]
        if targets:
            try:
                cmds.select(targets, replace=True)
            except Exception:
                pass

    def _on_hair_list_double_click(self, *_):
        """Double-click focuses the viewport on the selected strand."""
        self._on_hair_list_select()
        try:
            cmds.viewFit(all=False)
        except Exception:
            pass

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
        cmds.frameLayout(label="毛束を作成", collapsable=False,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        cmds.text(
            label=("毛束 (カーブ / メッシュ / sweep) を選択中は\n"
                   "スライダーの調整がリアルタイムで反映されます。\n"
                   "何も選択していない場合は次回「毛束を生成」時の\n"
                   "初期値として使われます。"),
            align="left", parent=col, font="smallObliqueLabelFont",
        )

        cmds.text(label="プロファイル", align="left", parent=col)
        self.profile_menu = cmds.optionMenu(
            parent=col, changeCommand=self._cb_profile_change)
        for display, _key in _PROFILE_DISPLAY:
            cmds.menuItem(label=display)

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

    def _set_uniform_scale(self, value):
        """Set Thickness = uniform scale.

        ``scaleProfileUniform`` is a *bool* (link X ↔ Y). We turn it
        on, then set ``scaleProfileX`` — Y auto-mirrors. Preset ratios
        (Oval Y=0.55 etc.) are lost when Thickness is touched; that
        matches Maya's Uniform-mode behaviour.
        """
        def setter(c):
            v = float(value)
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
        Uniform off so the sibling axis isn't force-linked."""
        def setter(c):
            if cmds.attributeQuery("scaleProfileUniform",
                                    node=c, exists=True):
                cmds.setAttr(c + ".scaleProfileUniform", False)
            if cmds.attributeQuery(axis_attr, node=c, exists=True):
                cmds.setAttr(c + "." + axis_attr, float(value))
            else:
                self._warn_missing(axis_attr)
        return setter

    def _set_attr(self, attr, value, cast=float):
        def setter(c):
            if not cmds.attributeQuery(attr, node=c, exists=True):
                self._warn_missing(attr)
                return
            cmds.setAttr(c + "." + attr, cast(value))
        return setter

    def _set_taper(self, root=None, middle=None, tip=None):
        def setter(c):
            existing = hair.read_taper_values(c)
            r = existing[0] if root is None else float(root)
            m = existing[1] if middle is None else float(middle)
            t = existing[2] if tip is None else float(tip)
            hair.set_taper_profile(
                c, root_scale=r, middle_scale=m, tip_scale=t)
        return setter

    # --- Profile ---
    def _cb_profile_change(self, *_):
        label = cmds.optionMenu(
            self.profile_menu, query=True, value=True)
        key = _PROFILE_LABEL_TO_KEY.get(label, C.PROFILE_CIRCLE)
        def setter(c):
            hair.set_profile(c, key)
        self._live_apply(setter, record_undo=True)

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
    # Footer
    # -----------------------------------------------------------------
    def _build_footer(self, parent):
        row = cmds.rowLayout(
            numberOfColumns=3,
            adjustableColumn=1,
            columnAttach=[(1, "both", 4), (2, "both", 4), (3, "both", 4)],
            columnWidth3=(160, 110, 60),
            parent=parent,
        )
        cmds.text(
            label="{0}  v{1}".format(_PACKAGE, __version__),
            align="left",
            font="smallObliqueLabelFont",
            parent=row,
        )
        cmds.button(label="GitHub から更新", height=24,
                    command=update_from_github, parent=row)
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
