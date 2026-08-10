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
    profile: str = C.PROFILE_CIRCLE,
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


# ---------------------------------------------------------------------------
# Hair group organisation (前髪 / アホ毛 / … と束を分けて調整するため)
# ---------------------------------------------------------------------------

def _is_hair_strand_transform(node: str) -> bool:
    """True if ``node`` is a transform whose mesh shape's history
    includes an animeHairTool-tagged sweepMeshCreator (i.e. one of
    our strands, not just any random group)."""
    if not cmds.objExists(node):
        return False
    shapes = cmds.listRelatives(
        node, shapes=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        history = cmds.listHistory(shape) or []
        for h in history:
            if cmds.nodeType(h) == "sweepMeshCreator" and \
                    cmds.attributeQuery(
                        C.TOOL_TAG_ATTR, node=h, exists=True):
                return True
    return False


def list_hair_groups() -> List[str]:
    """Return the full DAG paths of every hair group — i.e. every
    transform child of HairGroup that isn't itself a strand."""
    if not cmds.objExists(C.HAIR_GROUP_NAME):
        return []
    children = cmds.listRelatives(
        C.HAIR_GROUP_NAME, children=True, type="transform",
        fullPath=True) or []
    groups = []
    for c in children:
        if _is_hair_strand_transform(c):
            continue
        groups.append(c)
    return groups


def create_hair_group(name: str) -> str:
    """Create an empty hair group under HairGroup (or return an
    existing one with the same name). Returns the group's full path."""
    if cmds is None:
        raise RuntimeError("create_hair_group requires Maya.")
    parent = _ensure_hair_group()
    if cmds.objExists(name):
        parents = cmds.listRelatives(
            name, parent=True, fullPath=True) or []
        if parents and parents[0].endswith("|" + C.HAIR_GROUP_NAME):
            return parents[0] + "|" + name.split("|")[-1]
    grp = cmds.group(empty=True, name=name, parent=parent)
    return grp


def strands_under(node: str) -> List[str]:
    """All hair strand transforms beneath ``node`` (any depth)."""
    if not cmds.objExists(node):
        return []
    if _is_hair_strand_transform(node):
        return [node]
    all_descendants = cmds.listRelatives(
        node, allDescendents=True, type="transform",
        fullPath=True) or []
    return [d for d in all_descendants
            if _is_hair_strand_transform(d)]


def strands_in_group(group: str) -> List[str]:
    """Convenience: strands beneath a specific group node."""
    return strands_under(group)


def ungrouped_strands() -> List[str]:
    """Strand transforms that are direct children of HairGroup
    (not tucked into a sub-group)."""
    if not cmds.objExists(C.HAIR_GROUP_NAME):
        return []
    children = cmds.listRelatives(
        C.HAIR_GROUP_NAME, children=True, type="transform",
        fullPath=True) or []
    return [c for c in children if _is_hair_strand_transform(c)]


def all_hair_strands() -> List[str]:
    """Every hair strand in the scene, regardless of group."""
    return strands_under(C.HAIR_GROUP_NAME)


def get_group_color(group: str) -> Optional[tuple]:
    """Return the group's stored (r, g, b) tuple, or None when
    the colour attributes haven't been added yet."""
    if cmds is None or not cmds.objExists(group):
        return None
    if not cmds.attributeQuery(
            C.GROUP_COLOR_R_ATTR, node=group, exists=True):
        return None
    try:
        return (
            float(cmds.getAttr(group + "." + C.GROUP_COLOR_R_ATTR)),
            float(cmds.getAttr(group + "." + C.GROUP_COLOR_G_ATTR)),
            float(cmds.getAttr(group + "." + C.GROUP_COLOR_B_ATTR)),
        )
    except Exception:
        return None


def set_group_color(group: str, rgb: tuple, apply_now: bool = True) -> None:
    """Store ``(r, g, b)`` (each in 0..1) on the group transform
    and, when ``apply_now`` is True, push the override colour to
    every strand under the group so the viewport reflects it
    immediately."""
    if cmds is None or not cmds.objExists(group):
        return
    for attr, val in (
        (C.GROUP_COLOR_R_ATTR, float(rgb[0])),
        (C.GROUP_COLOR_G_ATTR, float(rgb[1])),
        (C.GROUP_COLOR_B_ATTR, float(rgb[2])),
    ):
        if not cmds.attributeQuery(attr, node=group, exists=True):
            try:
                cmds.addAttr(
                    group, longName=attr,
                    attributeType="float",
                    defaultValue=val,
                    min=0.0, max=1.0)
            except Exception:
                pass
        try:
            cmds.setAttr(group + "." + attr, val)
        except Exception:
            pass
    if apply_now:
        _apply_group_color_to_strands(group, rgb)


def _sanitize_shader_name(name: str) -> str:
    """Reduce a group name to a Maya-safe identifier so we can use
    it as part of a shader / shadingEngine node name."""
    import re
    return re.sub(r"[^A-Za-z0-9_]", "_", name)


def _ensure_group_shader(group: str, rgb: tuple) -> Optional[str]:
    """Create (or reuse + recolour) a Lambert + shadingEngine pair
    for ``group`` and return the shadingEngine name so
    ``cmds.sets(..., forceElement=sg)`` can assign it."""
    if cmds is None:
        return None
    short = group.split("|")[-1]
    mat_name = C.GROUP_COLOR_MATERIAL_PREFIX + _sanitize_shader_name(short)
    sg_name = mat_name + "SG"

    if not cmds.objExists(mat_name):
        mat_name = cmds.shadingNode(
            "lambert", asShader=True, name=mat_name)
    try:
        cmds.setAttr(
            mat_name + ".color",
            float(rgb[0]), float(rgb[1]), float(rgb[2]),
            type="double3")
    except Exception:
        pass

    if not cmds.objExists(sg_name):
        sg_name = cmds.sets(
            name=sg_name, empty=True, renderable=True,
            noSurfaceShader=True)
        try:
            cmds.connectAttr(
                mat_name + ".outColor",
                sg_name + ".surfaceShader",
                force=True)
        except Exception:
            pass
    else:
        # Re-hook the connection in case someone tampered with it.
        conns = cmds.listConnections(
            sg_name + ".surfaceShader", source=True) or []
        if mat_name not in conns:
            try:
                cmds.connectAttr(
                    mat_name + ".outColor",
                    sg_name + ".surfaceShader",
                    force=True)
            except Exception:
                pass
    return sg_name


def _capture_original_sg(mesh_xform: str) -> Optional[str]:
    """Remember the strand's current shading group on a string attr
    of the mesh transform so it can be restored later. Called ONCE
    per strand — subsequent calls are no-ops so a colour-toggle
    round-trip doesn't accidentally record the colour SG as the
    original."""
    if cmds is None or not cmds.objExists(mesh_xform):
        return None
    if cmds.attributeQuery(
            C.ORIGINAL_SHADING_GROUP_ATTR,
            node=mesh_xform, exists=True):
        # Already captured — do NOT overwrite (that'd lose the
        # real original if we're currently in colour-view mode).
        try:
            return cmds.getAttr(
                mesh_xform + "." + C.ORIGINAL_SHADING_GROUP_ATTR)
        except Exception:
            return None

    shapes = cmds.listRelatives(
        mesh_xform, shapes=True, type="mesh", fullPath=True) or []
    if not shapes:
        return None
    sgs = cmds.listConnections(shapes[0], type="shadingEngine") or []
    if not sgs:
        return None
    original_sg = sgs[0]
    try:
        cmds.addAttr(
            mesh_xform,
            longName=C.ORIGINAL_SHADING_GROUP_ATTR,
            dataType="string")
        cmds.setAttr(
            mesh_xform + "." + C.ORIGINAL_SHADING_GROUP_ATTR,
            original_sg, type="string")
    except Exception:
        pass
    return original_sg


def _assign_sg_to_strand(mesh_xform: str, sg: str) -> None:
    """Force-assign a shading engine to every mesh shape under a
    strand transform."""
    shapes = cmds.listRelatives(
        mesh_xform, shapes=True, type="mesh", fullPath=True) or []
    for shape in shapes:
        try:
            cmds.sets(shape, edit=True, forceElement=sg)
        except Exception:
            pass


def _apply_group_color_to_strands(group: str, rgb: tuple) -> None:
    """Assign the group's coloured Lambert to every strand.
    Captures the strand's original SG the first time so a later
    restore lands back on the right material."""
    sg = _ensure_group_shader(group, rgb)
    if not sg:
        return
    for mesh_xform in strands_under(group):
        _capture_original_sg(mesh_xform)
        _assign_sg_to_strand(mesh_xform, sg)


def _restore_original_sg(mesh_xform: str) -> bool:
    """Reassign the strand's stored ``hairOriginalSG`` shading
    engine. Falls back to ``initialShadingGroup`` if the stored
    value is missing / points at a deleted SG."""
    if cmds is None:
        return False
    target = "initialShadingGroup"
    if cmds.attributeQuery(
            C.ORIGINAL_SHADING_GROUP_ATTR,
            node=mesh_xform, exists=True):
        try:
            saved = cmds.getAttr(
                mesh_xform + "." + C.ORIGINAL_SHADING_GROUP_ATTR)
        except Exception:
            saved = None
        if saved and cmds.objExists(saved):
            target = saved
    if not cmds.objExists(target):
        return False
    _assign_sg_to_strand(mesh_xform, target)
    return True


def apply_all_group_colors() -> int:
    """Walk every group with a stored colour and swap its strands
    to the group's coloured Lambert. Returns the number of groups
    processed."""
    if cmds is None:
        return 0
    count = 0
    for group in list_hair_groups():
        rgb = get_group_color(group)
        if rgb is None:
            continue
        _apply_group_color_to_strands(group, rgb)
        count += 1
    return count


def clear_all_group_colors() -> int:
    """Restore every strand under HairGroup to its original
    shading group. Preserves the per-group RGB attributes so
    re-enabling brings the same colours back.

    Returns the number of strands touched."""
    if cmds is None:
        return 0
    count = 0
    for strand in all_hair_strands():
        if _restore_original_sg(strand):
            count += 1
    return count


def clear_group_color(group: str) -> int:
    """Restore just the strands under one group to their original
    shading. Same semantics as ``clear_all_group_colors`` but
    scoped to a single group."""
    if cmds is None:
        return 0
    count = 0
    for strand in strands_under(group):
        if _restore_original_sg(strand):
            count += 1
    return count


def move_strand_to_group(mesh_xform: str, group: Optional[str]) -> str:
    """Reparent a strand mesh under ``group`` (or under HairGroup
    directly if ``group`` is None/empty). Creates the group if it
    doesn't exist yet. Returns the strand's new full path."""
    if cmds is None:
        raise RuntimeError("move_strand_to_group requires Maya.")
    if not cmds.objExists(mesh_xform):
        return mesh_xform
    target = _ensure_hair_group()
    if group:
        if not cmds.objExists(group):
            target = create_hair_group(group)
        else:
            target = group
    try:
        result = cmds.parent(mesh_xform, target) or []
        if result:
            return result[0]
    except RuntimeError as exc:
        cmds.warning(
            "[maya_hair_tool] {0} を {1} へ移動できません: {2}".format(
                mesh_xform, target, exc))
    return mesh_xform


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

    # Scale attribute wiring for Maya 2023's sweepMeshCreator:
    # ``scaleProfileUniform`` is a *boolean* toggle (not a scale
    # multiplier) that links X → Y when True. So:
    #   * Thickness = "uniform scale" → Uniform=True + set X.
    #     Y is auto-mirrored to X. Round preset stays circular,
    #     but non-1.0 preset ratios (Oval Y=0.55) are lost — that's
    #     Maya's Uniform mode behaviour, not ours to override.
    #   * Width / Height = per-axis → Uniform=False + set X / Y.
    if thickness is not None and float(thickness) != 1.0:
        _safe_set(creator, "scaleProfileUniform", True)
        _safe_set(creator, "scaleProfileX", float(thickness))
    else:
        touched_axis = (
            (width is not None and float(width) != 1.0)
            or (height is not None and float(height) != 1.0)
        )
        if touched_axis:
            _safe_set(creator, "scaleProfileUniform", False)
        if width is not None and float(width) != 1.0:
            _safe_set(creator, "scaleProfileX", float(width))
        if height is not None and float(height) != 1.0:
            _safe_set(creator, "scaleProfileY", float(height))

    if twist is not None and float(twist) != 0.0:
        _safe_set(creator, "twist", float(twist))
    # rotation only overrides when the user explicitly moved it —
    # otherwise Sharp/Diamond presets' 45° would be reset to 0.
    if rotation is not None and float(rotation) != 0.0:
        _safe_set(creator, "rotateProfile", float(rotation))
    # subdivisions_axis = around the profile (polygon side count).
    # subdivisions_length = along the curve — ``interpolationPrecision``
    # is active in the default Precision mode. Users on a different
    # ``interpolationMode`` need to touch the mode via the Attribute
    # Editor for now.
    if subdivisions_axis is not None:
        _safe_set(creator, "profilePolySides", int(subdivisions_axis))
    if subdivisions_length is not None:
        _safe_set(creator, "interpolationPrecision",
                  float(subdivisions_length))
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

    Maya 2023's sweepMeshCreator uses a two-level shape enum:
    ``sweepProfileType`` picks the top-level shape family (Regular
    Polygon / Rounded Rectangle / Line / Arc / Wave / Custom) and,
    for Regular Polygon, ``profilePolyType`` picks Convex vs Star.

    We always reset the per-preset knobs (Y scale, rotation, Uniform
    link) to their identity values before applying the new preset,
    so a previous preset's tweak (Ellipse's Y=0.5 etc.) doesn't
    bleed into the new shape.
    """
    # Reset per-preset knobs to identity first.
    _safe_set(creator, "rotateProfile", 0.0)
    _safe_set(creator, "scaleProfileUniform", True)
    _safe_set(creator, "scaleProfileX", 1.0)
    _safe_set(creator, "scaleProfileY", 1.0)

    if profile == C.PROFILE_CIRCLE:
        _safe_set(creator, "sweepProfileType", 0)     # Regular Polygon
        _safe_set(creator, "profilePolyType", 0)      # Convex
        _safe_set(creator, "profilePolySides", 12)
    elif profile == C.PROFILE_ELLIPSE:
        _safe_set(creator, "sweepProfileType", 0)     # Regular Polygon
        _safe_set(creator, "profilePolyType", 0)      # Convex
        _safe_set(creator, "profilePolySides", 12)
        # Ellipse needs X ≠ Y, so Uniform must be off.
        _safe_set(creator, "scaleProfileUniform", False)
        _safe_set(creator, "scaleProfileY", 0.5)
    elif profile == C.PROFILE_RIBBON:
        _safe_set(creator, "sweepProfileType", 2)     # Line
    elif profile == C.PROFILE_STAR:
        _safe_set(creator, "sweepProfileType", 0)     # Regular Polygon
        _safe_set(creator, "profilePolyType", 1)      # Star
        _safe_set(creator, "profilePolySides", 5)
    elif profile == C.PROFILE_RECTANGLE:
        _safe_set(creator, "sweepProfileType", 1)     # Rounded Rectangle
    elif profile == C.PROFILE_ARC:
        _safe_set(creator, "sweepProfileType", 3)     # Arc
    elif profile == C.PROFILE_WAVE:
        _safe_set(creator, "sweepProfileType", 4)     # Wave
    elif profile == C.PROFILE_CUSTOM:
        _safe_set(creator, "sweepProfileType", 5)     # Custom
        # User then edits the custom profile curve in the AE.


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

    NB: the Maya attribute this authors is ``taperCurve`` (with
    sub-attributes ``taperCurve_Position`` / ``_FloatValue`` /
    ``_Interp``), NOT ``scaleProfile`` — a naming mismatch in earlier
    versions meant this whole function silently no-op'd because
    ``attributeQuery('scaleProfile', ...)`` returned False.
    """
    if not cmds.attributeQuery("taperCurve", node=creator, exists=True):
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

    ramp_attr = creator + ".taperCurve"
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
        cmds.setAttr("{0}[{1}].taperCurve_Position".format(ramp_attr, i), pos)
        cmds.setAttr("{0}[{1}].taperCurve_FloatValue".format(ramp_attr, i), val)
        cmds.setAttr("{0}[{1}].taperCurve_Interp".format(ramp_attr, i), interp)


def read_taper_ramp_entries(creator: str) -> list:
    """Return every entry on the ``taperCurve`` ramp, sorted by
    position, as a list of ``(position, value, interpolation)``
    tuples. Used by the UI's inline taper curve editor to round-trip
    an arbitrary number of ramp points."""
    if not cmds.attributeQuery("taperCurve", node=creator, exists=True):
        return []
    ramp_attr = creator + ".taperCurve"
    indices = cmds.getAttr(ramp_attr, multiIndices=True) or []
    entries = []
    for idx in indices:
        try:
            pos = cmds.getAttr(
                "{0}[{1}].taperCurve_Position".format(ramp_attr, idx))
            val = cmds.getAttr(
                "{0}[{1}].taperCurve_FloatValue".format(ramp_attr, idx))
            interp = cmds.getAttr(
                "{0}[{1}].taperCurve_Interp".format(ramp_attr, idx))
            entries.append((float(pos), float(val), int(interp)))
        except Exception:
            continue
    entries.sort(key=lambda e: e[0])
    return entries


def write_taper_ramp_entries(creator: str, entries) -> None:
    """Replace the ``taperCurve`` ramp with ``entries``.

    ``entries`` is an iterable of ``(position, value, interpolation)``
    tuples. Existing entries are wiped first so no phantom points
    survive; interpolation defaults to Spline (2) when the caller
    doesn't specify it.
    """
    if not cmds.attributeQuery("taperCurve", node=creator, exists=True):
        return
    ramp_attr = creator + ".taperCurve"
    for idx in list(cmds.getAttr(ramp_attr, multiIndices=True) or []):
        try:
            cmds.removeMultiInstance(
                "{0}[{1}]".format(ramp_attr, idx), b=True)
        except Exception:
            pass
    for i, entry in enumerate(entries):
        if len(entry) >= 3:
            pos, val, interp = entry[0], entry[1], entry[2]
        else:
            pos, val = entry[0], entry[1]
            interp = 2
        try:
            cmds.setAttr(
                "{0}[{1}].taperCurve_Position".format(ramp_attr, i),
                float(pos))
            cmds.setAttr(
                "{0}[{1}].taperCurve_FloatValue".format(ramp_attr, i),
                float(val))
            cmds.setAttr(
                "{0}[{1}].taperCurve_Interp".format(ramp_attr, i),
                int(interp))
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] taperCurve entry {0} write failed: "
                "{1}".format(i, exc))


def read_taper_values(creator: str) -> tuple:
    """Return ``(root, middle, tip)`` from the current ``taperCurve`` ramp.

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
    if not cmds.attributeQuery("taperCurve", node=creator, exists=True):
        return defaults

    ramp_attr = creator + ".taperCurve"
    indices = cmds.getAttr(ramp_attr, multiIndices=True) or []
    if not indices:
        return defaults

    entries = []  # list of (position, value)
    for idx in indices:
        try:
            pos = cmds.getAttr(
                "{0}[{1}].taperCurve_Position".format(ramp_attr, idx))
            val = cmds.getAttr(
                "{0}[{1}].taperCurve_FloatValue".format(ramp_attr, idx))
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
        "scaleProfileUniform": _get("scaleProfileUniform"),
        "twist": _get("twist"),
        "rotateProfile": _get("rotateProfile"),
        "interpolationSteps": _get("interpolationSteps"),
        "interpolationPrecision": _get("interpolationPrecision"),
    }
