"""High level operations for creating and editing anime hair strands.

Phase 1 scope
-------------
* Create a hair strand from a selected NURBS curve using Maya's Sweep Mesh.
* Apply an initial anime-hair setup (Round profile, gentle taper).
* Change the profile of an existing strand (Round / Oval / Flat / Sharp /
  Diamond / TearDrop / Custom).
* Adjust thickness, width, height, root/middle/tip scale, twist, rotation
  and subdivisions on a single strand.

The functions in this module always operate through Maya's standard
``sweepMeshCreator`` attributes so the resulting nodes stay compatible
with any Maya session, even one where this tool is not installed.
"""

from __future__ import annotations

from typing import List, Optional

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # pragma: no cover
    cmds = None
    mel = None

from . import constants as C
from . import sweep_utils as su


# ---------------------------------------------------------------------------
# Curve creation shortcuts (skip the "have a curve first" precondition)
# ---------------------------------------------------------------------------

def start_curve_tool() -> None:
    """Activate Maya's CV Curve Tool for interactive drawing.

    The user then clicks in the viewport to place CVs and presses Enter
    to finish. The resulting curve is left selected so the next
    "Create Hair from Selected Curves" click picks it up.
    """
    if cmds is None:
        raise RuntimeError(
            "start_curve_tool() must be called inside Maya.")
    if mel is not None:
        try:
            mel.eval("CurveCVTool;")
            return
        except Exception:
            pass
    # Fallback: activate the context by name directly.
    try:
        cmds.setToolTo("curveCVCtx")
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] could not start CV Curve Tool: {0}. "
            "Open Create > Curve Tools > CV Curve Tool manually.".format(
                exc))


def create_default_curve(
    length: float = 6.0,
    cv_count: int = 6,
    axis: str = "-Y",
    name: str = "hair_curve",
) -> str:
    """Create a straight NURBS curve and leave it selected.

    Handy when the user just wants to try the tool without drawing a
    curve first. Six degree-3 CVs give enough control to reshape into
    a hair silhouette by dragging a couple of mid CVs. Direction
    defaults to ``-Y`` so the strand hangs down from the origin like
    hair off a head, but ``+Y``/``+X``/``-X``/``+Z``/``-Z`` are all
    accepted for setups where a different axis is more convenient.

    The returned string is the newly-created curve's transform name;
    caller can use it directly with :func:`create_hair_from_selected_curves`
    (the selection is set here) or query the CVs afterwards.
    """
    if cmds is None:
        raise RuntimeError(
            "create_default_curve() must be called inside Maya.")

    # NURBS degree 3 needs at least 4 CVs; clamp silently instead of
    # raising so a slightly-too-small slider value still produces
    # something usable.
    if cv_count < 4:
        cv_count = 4
    if length <= 0:
        length = 6.0

    axis_map = {
        "+X": (1.0, 0.0, 0.0),
        "-X": (-1.0, 0.0, 0.0),
        "+Y": (0.0, 1.0, 0.0),
        "-Y": (0.0, -1.0, 0.0),
        "+Z": (0.0, 0.0, 1.0),
        "-Z": (0.0, 0.0, -1.0),
    }
    direction = axis_map.get(axis, axis_map["-Y"])
    step = length / (cv_count - 1)
    points = [
        (direction[0] * i * step,
         direction[1] * i * step,
         direction[2] * i * step)
        for i in range(cv_count)
    ]

    # Pick a unique name so a second click doesn't collide.
    base = name
    candidate = base
    i = 1
    while cmds.objExists(candidate):
        i += 1
        candidate = "{0}_{1:02d}".format(base, i)

    curve = cmds.curve(degree=3, point=points, name=candidate)
    try:
        cmds.select(curve, replace=True)
    except Exception:
        pass
    return curve


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------

def create_hair_from_selected_curves(
    profile: str = C.PROFILE_ROUND,
    thickness: float = C.DEFAULT_THICKNESS,
    width: float = C.DEFAULT_WIDTH,
    height: float = C.DEFAULT_HEIGHT,
    root_scale: float = C.DEFAULT_ROOT_SCALE,
    middle_scale: float = C.DEFAULT_MIDDLE_SCALE,
    tip_scale: float = C.DEFAULT_TIP_SCALE,
    twist: float = C.DEFAULT_TWIST,
    rotation: float = C.DEFAULT_ROTATION,
    subdivisions_axis: int = C.DEFAULT_SUBDIVISIONS_AXIS,
    subdivisions_length: int = C.DEFAULT_SUBDIVISIONS_LENGTH,
    group: bool = True,
) -> List[str]:
    """Create one hair strand for every currently-selected NURBS curve.

    Returns the list of created sweepMeshCreator nodes.
    """
    curves = su.selected_curves()
    if not curves:
        cmds.warning("No NURBS curves selected. Select at least one curve "
                     "and try again.")
        return []

    created: List[str] = []
    created_meshes: List[str] = []
    hair_group = _ensure_hair_group() if group else None

    for curve in curves:
        name_hint = _unique_hair_name(curve)
        creator, mesh_xform = su.create_sweep_from_curve(curve, name_hint)

        _apply_settings(
            creator,
            profile=profile,
            thickness=thickness,
            width=width,
            height=height,
            root_scale=root_scale,
            middle_scale=middle_scale,
            tip_scale=tip_scale,
            twist=twist,
            rotation=rotation,
            subdivisions_axis=subdivisions_axis,
            subdivisions_length=subdivisions_length,
        )

        if hair_group and cmds.objExists(mesh_xform):
            try:
                parented = cmds.parent(mesh_xform, hair_group)
                # cmds.parent returns the new full path(s); use it so
                # created_meshes references the moved node instead of
                # the old short name (which can collide with a same-
                # named node elsewhere and cause a select mis-fire).
                if parented:
                    mesh_xform = parented[0]
            except RuntimeError as exc:
                cmds.warning(
                    "[maya_hair_tool] could not parent {0} under {1}: "
                    "{2}".format(mesh_xform, hair_group, exc))

        created.append(creator)
        if mesh_xform:
            created_meshes.append(mesh_xform)

    # Select the mesh transforms so users see the result in the viewport;
    # selecting the sweepMeshCreator (a DG node) leaves nothing visibly
    # highlighted and makes the next Duplicate press confusing.
    if created_meshes:
        cmds.select(created_meshes, replace=True)
    elif created:
        cmds.select(created, replace=True)
    return created


def _ensure_hair_group() -> str:
    if not cmds.objExists(C.HAIR_GROUP_NAME):
        return cmds.group(empty=True, name=C.HAIR_GROUP_NAME)
    return C.HAIR_GROUP_NAME


def _unique_hair_name(curve: str) -> str:
    base = curve.split("|")[-1]
    # Strip common curve suffixes so the resulting mesh name reads well.
    for suffix in ("_curve", "_crv", "Curve", "Crv"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    if not base:
        base = C.HAIR_STRAND_PREFIX
    candidate = base
    i = 1
    while cmds.objExists(candidate + C.HAIR_MESH_SUFFIX):
        i += 1
        candidate = "{0}_{1:02d}".format(base, i)
    return candidate


# ---------------------------------------------------------------------------
# Parameter editing (single strand)
# ---------------------------------------------------------------------------

def _apply_settings(
    creator: str,
    profile: Optional[str] = None,
    thickness: Optional[float] = None,
    width: Optional[float] = None,
    height: Optional[float] = None,
    root_scale: Optional[float] = None,
    middle_scale: Optional[float] = None,
    tip_scale: Optional[float] = None,
    twist: Optional[float] = None,
    rotation: Optional[float] = None,
    subdivisions_axis: Optional[int] = None,
    subdivisions_length: Optional[int] = None,
) -> None:
    """Apply the given values to ``creator``.

    Semantics for the size / rotation attributes (avoids overwriting
    ``set_profile``'s per-preset tweaks and avoids the UI passing 1.0
    default sliders that would silently override a Thickness change):

    * ``profile`` is applied first. It may set ``scaleProfileY`` and
      ``rotateProfile`` for Oval/Flat/Sharp/Diamond presets.
    * ``thickness`` (uniform scale) is treated as a shorthand: if it
      differs from the identity value (1.0), it wins over ``width`` /
      ``height``. Otherwise ``width`` / ``height`` are applied
      individually.
    * ``width`` / ``height`` / ``rotation`` only override the profile's
      preset values if the caller passed a non-identity value
      (i.e. the user actually moved the slider away from its default),
      so a slider still parked at 1.0 / 0.0 does not blast the preset.
    * ``None`` still means "skip entirely" (existing attribute untouched).
    """
    if profile is not None:
        set_profile(creator, profile)

    # Read the baseline scale that ``set_profile`` just applied. For
    # Oval/Flat/Sharp presets ``scaleProfileY`` may be 0.55/0.35/0.6
    # instead of 1.0; using it as a multiplier for ``thickness``
    # preserves the preset silhouette when the user cranks Thickness
    # up (e.g. Oval + Thickness 2.0 → Y = 0.55 × 2.0 = 1.1, still
    # oval, just bigger).
    baseline_x = 1.0
    baseline_y = 1.0
    if cmds is not None:
        try:
            baseline_x = float(cmds.getAttr(creator + ".scaleProfileX"))
            baseline_y = float(cmds.getAttr(creator + ".scaleProfileY"))
        except Exception:
            pass

    # Thickness = uniform scale shorthand. Non-identity wins over
    # width/height so a lone Thickness slider is not silently
    # overridden by width/height defaults.
    if thickness is not None and float(thickness) != 1.0:
        _safe_set(creator, "scaleProfileX", float(thickness) * baseline_x)
        _safe_set(creator, "scaleProfileY", float(thickness) * baseline_y)
    else:
        if width is not None and float(width) != 1.0:
            _safe_set(creator, "scaleProfileX", float(width))
        if height is not None and float(height) != 1.0:
            _safe_set(creator, "scaleProfileY", float(height))

    if twist is not None and float(twist) != 0.0:
        _safe_set(creator, "twistAngle", float(twist))
    # rotation only overrides when the user explicitly moved it —
    # otherwise Sharp/Diamond presets' 45° would be reset to 0.
    if rotation is not None and float(rotation) != 0.0:
        _safe_set(creator, "rotateProfile", float(rotation))
    if subdivisions_axis is not None:
        _safe_set(creator, "interpolationSteps", int(subdivisions_axis))
    if subdivisions_length is not None:
        _safe_set(creator, "interpolationPrecision", int(subdivisions_length))
    if root_scale is not None or middle_scale is not None or \
            tip_scale is not None:
        set_taper_profile(
            creator,
            root_scale=root_scale,
            middle_scale=middle_scale,
            tip_scale=tip_scale,
        )


def set_profile(creator: str, profile: str) -> None:
    """Switch the sweep to the requested profile preset.

    Always reset ``scaleProfileY`` and ``rotateProfile`` to identity
    values (1.0 / 0.0) before applying the preset. Without this reset
    a previous preset's leftover value (Flat's Y=0.35, Sharp's 45°)
    would bleed into the newly requested shape — e.g. Flat → Diamond
    would still be squashed to Y=0.35 because Diamond only sets
    ``rotateProfile``.
    """
    poly_type = C.PROFILE_POLY_TYPE.get(profile, 0)
    _safe_set(creator, "profilePolyType", poly_type)

    # Reset per-preset knobs to identity so a previous preset does not
    # bleed into the new shape.
    _safe_set(creator, "scaleProfileY", 1.0)
    _safe_set(creator, "rotateProfile", 0.0)

    # Adjust the aspect ratio for named preset variants that share a
    # profilePolyType with something else.
    if profile == C.PROFILE_OVAL:
        _safe_set(creator, "scaleProfileY", 0.55)
    elif profile == C.PROFILE_FLAT:
        _safe_set(creator, "scaleProfileY", 0.35)
    elif profile == C.PROFILE_SHARP:
        _safe_set(creator, "scaleProfileY", 0.6)
        _safe_set(creator, "rotateProfile", 45.0)
    elif profile == C.PROFILE_DIAMOND:
        _safe_set(creator, "rotateProfile", 45.0)
    # PROFILE_TEAR and PROFILE_CUSTOM keep the identity values set
    # above; the user is expected to edit the custom profile curve.


def set_taper_profile(
    creator: str,
    root_scale: Optional[float] = None,
    middle_scale: Optional[float] = None,
    tip_scale: Optional[float] = None,
) -> None:
    """Author the sweep scale-along-curve ramp using three control points.

    The sweepMeshCreator has a ``scaleProfile`` ramp attribute whose
    entries drive the profile scale from root (position 0) to tip
    (position 1). We author three entries so the strand can taper from
    root → middle → tip in a way that reads well for anime hair.

    ``None`` for any of the three scales means "preserve whatever value
    is currently at that position". This lets Batch Edit change only
    Root/Tip without resetting Middle to a default.
    """
    if not cmds.attributeQuery("scaleProfile", node=creator, exists=True):
        return

    # If any of the three is unspecified, read the existing ramp so we
    # can preserve those positions instead of overwriting with defaults.
    if root_scale is None or middle_scale is None or tip_scale is None:
        existing = read_taper_values(creator)
        if root_scale is None:
            root_scale = existing[0]
        if middle_scale is None:
            middle_scale = existing[1]
        if tip_scale is None:
            tip_scale = existing[2]

    ramp_attr = creator + ".scaleProfile"
    # Clear existing entries first.
    indices = cmds.getAttr(ramp_attr, multiIndices=True) or []
    for idx in indices:
        try:
            cmds.removeMultiInstance(
                "{0}[{1}]".format(ramp_attr, idx), b=True)
        except Exception:
            pass

    entries = [
        (0.0, float(root_scale), 2),    # root
        (0.5, float(middle_scale), 2),  # middle
        (1.0, float(tip_scale), 2),     # tip
    ]
    for i, (pos, val, interp) in enumerate(entries):
        cmds.setAttr("{0}[{1}].scaleProfile_Position".format(ramp_attr, i), pos)
        cmds.setAttr("{0}[{1}].scaleProfile_FloatValue".format(ramp_attr, i), val)
        cmds.setAttr("{0}[{1}].scaleProfile_Interp".format(ramp_attr, i), interp)


def read_taper_values(creator: str) -> tuple:
    """Return ``(root, middle, tip)`` from the current ``scaleProfile`` ramp.

    Reads whatever entries are currently on the sweepMeshCreator and
    returns the values at (or closest to) positions 0.0, 0.5, 1.0.
    Falls back to the module defaults when the ramp is empty or
    missing an entry near a given position — that way callers can
    treat the return value as a safe baseline for Absolute/Relative
    Batch Edit.
    """
    defaults = (
        C.DEFAULT_ROOT_SCALE,
        C.DEFAULT_MIDDLE_SCALE,
        C.DEFAULT_TIP_SCALE,
    )
    if not cmds.attributeQuery("scaleProfile", node=creator, exists=True):
        return defaults

    ramp_attr = creator + ".scaleProfile"
    indices = cmds.getAttr(ramp_attr, multiIndices=True) or []
    if not indices:
        return defaults

    entries = []  # list of (position, value)
    for idx in indices:
        try:
            pos = cmds.getAttr(
                "{0}[{1}].scaleProfile_Position".format(ramp_attr, idx))
            val = cmds.getAttr(
                "{0}[{1}].scaleProfile_FloatValue".format(ramp_attr, idx))
            entries.append((float(pos), float(val)))
        except Exception:
            continue

    if not entries:
        return defaults

    def _closest(target_pos, fallback):
        best = min(entries, key=lambda pv: abs(pv[0] - target_pos))
        # Tighter tolerance so a middle-of-nowhere entry (e.g. an
        # authored ramp point at position 0.3) is not mis-picked as
        # a "middle" reading. Anything more than 0.1 away is treated
        # as "no anchor at that position" and we fall back.
        if abs(best[0] - target_pos) > 0.1:
            return fallback
        return best[1]

    return (
        _closest(0.0, defaults[0]),
        _closest(0.5, defaults[1]),
        _closest(1.0, defaults[2]),
    )


def _safe_set(node: str, attr: str, value) -> None:
    full = "{0}.{1}".format(node, attr)
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return
    try:
        cmds.setAttr(full, value)
    except Exception as exc:
        # Attribute might be locked / connected / of incompatible type.
        # Surface a Maya warning so the user gets feedback (a silent
        # skip made typo'd attribute names impossible to debug).
        cmds.warning(
            "[maya_hair_tool] setAttr {0} = {1!r} failed: {2}".format(
                full, value, exc))


# ---------------------------------------------------------------------------
# Query helpers
# ---------------------------------------------------------------------------

def get_settings(creator: str) -> dict:
    """Return the current settings for a sweepMeshCreator, as a dict."""
    def _get(attr):
        if cmds.attributeQuery(attr, node=creator, exists=True):
            return cmds.getAttr(creator + "." + attr)
        return None

    return {
        "profilePolyType": _get("profilePolyType"),
        "scaleProfileX": _get("scaleProfileX"),
        "scaleProfileY": _get("scaleProfileY"),
        "twistAngle": _get("twistAngle"),
        "rotateProfile": _get("rotateProfile"),
        "interpolationSteps": _get("interpolationSteps"),
        "interpolationPrecision": _get("interpolationPrecision"),
    }
