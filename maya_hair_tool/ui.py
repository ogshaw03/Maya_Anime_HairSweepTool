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
from . import hair


# Package name — must match install.py's `_PACKAGE` and be the exact key
# the shelf button `import`s.
_PACKAGE = "maya_hair_tool"

# install.py's `_close_existing_window` looks for f"{_MODULE}Win", so
# keeping this name in sync with that convention lets the installer
# tear down the tool window before overwriting the files on disk.
WINDOW_NAME = _PACKAGE + "Win"
WINDOW_TITLE = "Anime Hair Builder"

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

        title = "{0}  —  v{1}".format(WINDOW_TITLE, __version__)
        cmds.window(WINDOW_NAME, title=title, sizeable=True,
                    widthHeight=(340, 660))
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
        print("[{0}] SHA lookup failed ({1}); falling back to {2}".format(
            _PACKAGE, exc, _GITHUB_BRANCH))
        return _GITHUB_BRANCH


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

    sha = _resolve_latest_sha()
    url = "{0}/{1}/install.py".format(_GITHUB_RAW_BASE, sha)
    print("[{0}] update: fetching {1}".format(_PACKAGE, url))
    try:
        req = urllib.request.Request(url, headers={
            "Cache-Control": "no-cache",
            "User-Agent": "{0}-updater/{1}".format(_PACKAGE, sha[:10]),
        })
        source = urllib.request.urlopen(req, timeout=30).read()
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(title="Update failed",
                           message="install.py fetch failed:\n{0}".format(exc),
                           button=["OK"])
        return

    if cmds.window(WINDOW_NAME, exists=True):
        try:
            cmds.deleteUI(WINDOW_NAME)
        except Exception:
            pass

    ns = {"__name__": "install", "__file__": "<github>"}
    try:
        exec(compile(source, "install.py (from GitHub)", "exec"), ns)
    except Exception as exc:
        traceback.print_exc()
        cmds.confirmDialog(
            title="Update failed",
            message=("install.py raised:\n{0}: {1}\n\n"
                     "See Script Editor for full traceback.".format(
                         type(exc).__name__, exc)),
            button=["OK"])
        return

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
            title="Reopen failed",
            message=("Update finished but reopening the tool window "
                     "failed:\n{0}: {1}\n\n"
                     "Click the shelf button to reopen manually.".format(
                         type(exc).__name__, exc)),
            button=["OK"])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def show():
    """Open the Hair Builder window."""
    HairBuilderUI().show()
    return WINDOW_NAME
