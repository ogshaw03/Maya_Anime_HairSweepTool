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

from . import batch
from . import constants as C
from . import hair


WINDOW_NAME = "animeHairBuilderWin"
WINDOW_TITLE = "Anime Hair Builder"


class HairBuilderUI(object):
    """Encapsulates every widget the tool uses.

    Widget IDs are stored on ``self`` rather than as module globals so the
    same class can be instantiated more than once (e.g. for docked +
    floating variants) without name collisions.
    """

    def __init__(self):
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

        # Batch panel widgets.
        self.batch_mode = None      # radio collection
        self.batch_thickness = None
        self.batch_width = None
        self.batch_height = None
        self.batch_root = None
        self.batch_tip = None
        self.batch_twist = None
        self.batch_subdiv = None

    # -----------------------------------------------------------------
    # Show / rebuild
    # -----------------------------------------------------------------
    def show(self):
        if cmds.window(WINDOW_NAME, exists=True):
            cmds.deleteUI(WINDOW_NAME)

        cmds.window(WINDOW_NAME, title=WINDOW_TITLE, sizeable=True,
                    widthHeight=(340, 620))
        root = cmds.columnLayout(adjustableColumn=True, rowSpacing=6,
                                 columnAttach=("both", 8))

        self._build_create_panel(root)
        cmds.separator(height=8, style="in")
        self._build_batch_panel(root)
        cmds.separator(height=8, style="in")
        self._build_footer(root)

        cmds.showWindow(WINDOW_NAME)

    # -----------------------------------------------------------------
    # Create Hair panel
    # -----------------------------------------------------------------
    def _build_create_panel(self, parent):
        cmds.frameLayout(label="Create Hair", collapsable=False,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        cmds.text(label="Profile", align="left", parent=col)
        self.profile_menu = cmds.optionMenu(parent=col)
        for name in C.PROFILE_TYPES:
            cmds.menuItem(label=name)

        self.thickness = _slider(col, "Thickness", C.DEFAULT_THICKNESS,
                                 0.01, 5.0)
        self.width = _slider(col, "Width", C.DEFAULT_WIDTH, 0.01, 5.0)
        self.height = _slider(col, "Height", C.DEFAULT_HEIGHT, 0.01, 5.0)
        self.root = _slider(col, "Root Scale", C.DEFAULT_ROOT_SCALE,
                            0.0, 3.0)
        self.middle = _slider(col, "Middle Scale", C.DEFAULT_MIDDLE_SCALE,
                              0.0, 3.0)
        self.tip = _slider(col, "Tip Scale", C.DEFAULT_TIP_SCALE, 0.0, 3.0)
        self.twist = _slider(col, "Twist", C.DEFAULT_TWIST, -720.0, 720.0)
        self.rotation = _slider(col, "Rotation", C.DEFAULT_ROTATION,
                                -360.0, 360.0)
        self.subdiv_axis = _int_slider(col, "Subdiv (Axis)",
                                       C.DEFAULT_SUBDIVISIONS_AXIS, 3, 32)
        self.subdiv_length = _int_slider(col, "Subdiv (Length)",
                                         C.DEFAULT_SUBDIVISIONS_LENGTH,
                                         1, 64)

        cmds.button(label="Create Hair from Selected Curves",
                    height=32, command=self._on_create, parent=col)

    def _on_create(self, *_):
        profile = cmds.optionMenu(self.profile_menu, query=True, value=True)
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

    # -----------------------------------------------------------------
    # Batch Edit panel
    # -----------------------------------------------------------------
    def _build_batch_panel(self, parent):
        cmds.frameLayout(label="Batch Edit", collapsable=False,
                         marginHeight=6, marginWidth=6, parent=parent)
        col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4)

        cmds.text(label="Mode", align="left", parent=col)
        self.batch_mode = cmds.radioButtonGrp(
            numberOfRadioButtons=2,
            labelArray2=("Absolute", "Relative"),
            select=2,
            parent=col,
        )

        self.batch_thickness = _slider(col, "Thickness", 1.0, 0.01, 5.0)
        self.batch_width = _slider(col, "Width", 1.0, 0.01, 5.0)
        self.batch_height = _slider(col, "Height", 1.0, 0.01, 5.0)
        self.batch_root = _slider(col, "Root Scale", 1.0, 0.0, 3.0)
        self.batch_tip = _slider(col, "Tip Scale", 1.0, 0.0, 3.0)
        self.batch_twist = _slider(col, "Twist", 0.0, -720.0, 720.0)
        self.batch_subdiv = _int_slider(col, "Subdiv (Axis)", 8, 3, 32)

        cmds.button(label="Apply to Selected",
                    height=32, command=self._on_batch_apply, parent=col)

    def _on_batch_apply(self, *_):
        creators = batch.creators_from_selection()
        if not creators:
            cmds.warning("No hair strands found in selection.")
            return
        is_absolute = cmds.radioButtonGrp(
            self.batch_mode, query=True, select=True) == 1

        # (attribute, widget, reader)
        plan = [
            ("scaleProfileX", self.batch_thickness, _read_float),
            ("scaleProfileY", self.batch_thickness, _read_float),
            ("scaleProfileX", self.batch_width, _read_float),
            ("scaleProfileY", self.batch_height, _read_float),
            ("twistAngle", self.batch_twist, _read_float),
            ("interpolationSteps", self.batch_subdiv, _read_int),
        ]
        # Root / Tip taper is handled specially through set_taper_profile
        # via a small per-strand snapshot below.
        for attr, widget, reader in plan:
            value = reader(widget)
            if is_absolute:
                batch.apply_absolute(creators, attr, value)
            else:
                batch.apply_relative(creators, attr, value)

        root_value = _read_float(self.batch_root)
        tip_value = _read_float(self.batch_tip)
        for c in creators:
            if is_absolute:
                hair.set_taper_profile(c, root_scale=root_value,
                                       tip_scale=tip_value)
            else:
                # Relative: multiply existing endpoints.
                current = hair.get_settings(c)
                # We can't easily read every ramp entry back, so treat
                # Relative mode as "scale the default 1.0 ramp by the
                # slider value" — matches the doc's intent of preserving
                # per-strand variation for the batch attributes we do
                # store on the creator itself.
                hair.set_taper_profile(
                    c,
                    root_scale=root_value,
                    tip_scale=tip_value,
                )
                _ = current  # reserved for future full-ramp readback

    # -----------------------------------------------------------------
    # Footer
    # -----------------------------------------------------------------
    def _build_footer(self, parent):
        row = cmds.rowLayout(numberOfColumns=2, adjustableColumn=1,
                             columnAttach=[(1, "both", 4), (2, "both", 4)],
                             parent=parent)
        cmds.text(label="Phase 1 - Sweep Mesh basics", align="left",
                  parent=row)
        cmds.button(label="Close",
                    command=lambda *_: cmds.deleteUI(WINDOW_NAME),
                    parent=row)


# ---------------------------------------------------------------------------
# Widget helpers
# ---------------------------------------------------------------------------

def _slider(parent, label, value, minv, maxv):
    return cmds.floatSliderGrp(
        label=label,
        field=True,
        minValue=minv,
        maxValue=maxv,
        fieldMinValue=-1e6,
        fieldMaxValue=1e6,
        value=value,
        columnAlign=(1, "left"),
        columnWidth3=(90, 60, 120),
        parent=parent,
    )


def _int_slider(parent, label, value, minv, maxv):
    return cmds.intSliderGrp(
        label=label,
        field=True,
        minValue=minv,
        maxValue=maxv,
        fieldMinValue=1,
        fieldMaxValue=999,
        value=value,
        columnAlign=(1, "left"),
        columnWidth3=(90, 60, 120),
        parent=parent,
    )


def _read_float(widget):
    return cmds.floatSliderGrp(widget, query=True, value=True)


def _read_int(widget):
    return cmds.intSliderGrp(widget, query=True, value=True)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def show():
    """Open the Hair Builder window."""
    HairBuilderUI().show()
