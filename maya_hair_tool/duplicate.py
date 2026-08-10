"""Phase 2 — Hair strand duplication.

Workflow the tool exists for::

    make one good strand -> duplicate it -> edit only the new curve

The duplicated strand is fully independent from the source: editing the
new guide curve or new sweep settings never touches the original, and
vice-versa. Everything the original stores on its ``sweepMeshCreator``
(profile type, thickness, taper ramp, twist, subdivisions, custom
profile curve) is copied to the duplicate so it starts as a visual
clone that the user then reshapes by editing the new guide curve.

Public API::

    duplicate_hair(creators, count=1, offset=(1, 0, 0))
        -> list of newly-created sweepMeshCreator nodes.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

try:
    import maya.cmds as cmds
except ImportError:  # pragma: no cover
    cmds = None

from . import constants as C
from . import sweep_utils as su


# Attributes we copy verbatim from the source sweepMeshCreator to the
# duplicate. Names were verified against Maya 2023's actual node
# schema; earlier versions of this list contained guessed names
# (twistAngle / profileSubDiv / etc.) that silently no-op'd because
# ``_copy_scalar_attrs`` filters via ``attributeQuery(exists=True)``.
_SCALAR_ATTRS = (
    # Profile shape
    "profilePolyType",
    "sweepProfileType",
    "profilePolySides",
    "profilePolyInnerRadius",
    # Scale / rotate / translate
    "scaleProfileX",
    "scaleProfileY",
    "scaleProfileUniform",
    "rotateProfile",
    "translateProfileX",
    "translateProfileY",
    "twist",
    "taper",
    # Interpolation along curve
    "interpolationSteps",
    "interpolationPrecision",
    "interpolationDistance",
    "interpolationMode",
    "interpolationOptimize",
    # Caps / UVs / normals
    "capsEnable",
    "createUVs",
    "normalsSmoothing",
    "normalsReverse",
    # Behaviour
    "automaticRoll",
    "alignProfileEnable",
    "alignProfileHorizontal",
    "alignProfileVertical",
    # Per-preset profile parameters (relevant when profilePolyType
    # picks the corresponding shape)
    "profileArcAngle",
    "profileArcSegments",
    "profileRectWidth",
    "profileRectHeight",
    "profileRectCornerRadius",
    "profileRectCornerDepth",
    "profileRectCornerSegments",
    "profileWaveAmplitude",
    "profileWaveCycles",
    "profileWaveOffset",
    "profileWaveSegments",
)


def duplicate_hair(
    creators: Iterable[str],
    count: int = 1,
    offset: Sequence[float] = (1.0, 0.0, 0.0),
) -> List[str]:
    """Duplicate every strand in ``creators`` ``count`` times.

    Each duplicate consists of a brand-new guide curve, a brand-new
    ``sweepMeshCreator`` node with the source's attribute values baked
    in, and a brand-new mesh transform. Duplicates are placed under the
    same parent as the source mesh so they stay grouped with their
    origin (typically ``HairGroup``).

    Returns the list of newly-created sweepMeshCreator nodes across all
    inputs and iterations, in the order they were made.
    """
    su._ensure_maya()
    creators = [c for c in creators if cmds.objExists(c)]
    if not creators:
        cmds.warning("No hair strands to duplicate.")
        return []
    if count < 1:
        return []

    created: List[str] = []
    cmds.undoInfo(openChunk=True, chunkName="HairDuplicate")
    try:
        for src_creator in creators:
            for i in range(count):
                step = (i + 1)
                dx = offset[0] * step
                dy = offset[1] * step
                dz = offset[2] * step
                new_creator = _duplicate_one(src_creator, (dx, dy, dz))
                if new_creator:
                    created.append(new_creator)
    finally:
        cmds.undoInfo(closeChunk=True)

    if created:
        cmds.select(created, replace=True)
    return created


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

def _duplicate_one(
    src_creator: str,
    offset: Tuple[float, float, float],
) -> Optional[str]:
    src_curve = su.curve_from_creator(src_creator)
    src_mesh = su.mesh_from_creator(src_creator)
    if not src_curve:
        cmds.warning(
            "sweepMeshCreator {0!r} has no guide curve; skipped".format(
                src_creator))
        return None

    name_hint = _unique_hair_name(src_curve, src_mesh)

    # 1) Duplicate the guide curve. rr = returnRootsOnly.
    try:
        dup_result = cmds.duplicate(
            src_curve, returnRootsOnly=True,
            name=name_hint + C.HAIR_CURVE_SUFFIX,
        ) or []
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] guide curve duplicate failed for "
            "{0!r}: {1}".format(src_curve, exc))
        return None
    if not dup_result:
        cmds.warning(
            "[maya_hair_tool] guide curve duplicate returned nothing "
            "for {0!r}; skipped".format(src_curve))
        return None
    dup_curve = dup_result[0]
    if any(offset):
        cmds.move(offset[0], offset[1], offset[2], dup_curve,
                  relative=True, worldSpace=True)

    # 2) Build a fresh sweep for the duplicated curve. This is the exact
    #    same wiring create_sweep_from_curve uses, so downstream code
    #    (batch edit, further duplication) sees the duplicate as a
    #    first-class tool-created strand.
    new_creator, new_mesh = su.create_sweep_from_curve(dup_curve, name_hint)

    # 3) Copy every plain scalar attribute from source to duplicate.
    _copy_scalar_attrs(src_creator, new_creator)

    # 4) Copy the scaleProfile ramp (taper root -> middle -> tip).
    _copy_scale_profile_ramp(src_creator, new_creator)

    # 5) If the source uses a Custom Profile curve, duplicate that too
    #    and wire it into the new creator. This keeps the anime-hair
    #    silhouette identical without linking the two creators.
    _duplicate_custom_profile_curve(src_creator, new_creator, name_hint)

    # 6) Match parenting so the duplicate stays with its origin group.
    _match_parent(src_mesh, new_mesh)

    return new_creator


def _copy_scalar_attrs(src: str, dst: str) -> None:
    for attr in _SCALAR_ATTRS:
        if not cmds.attributeQuery(attr, node=src, exists=True):
            continue
        if not cmds.attributeQuery(attr, node=dst, exists=True):
            continue
        # Skip attributes that are driven by an incoming connection on
        # the source — copying the static value would be wrong there.
        src_full = "{0}.{1}".format(src, attr)
        if cmds.connectionInfo(src_full, isDestination=True):
            continue
        dst_full = "{0}.{1}".format(dst, attr)
        # Also skip if the destination is already being driven — force
        # setting it would break the driver relationship and give a
        # confusing "value stuck at input" behavior.
        if cmds.connectionInfo(dst_full, isDestination=True):
            continue
        try:
            value = cmds.getAttr(src_full)
            if isinstance(value, str):
                cmds.setAttr(dst_full, value, type="string")
            else:
                cmds.setAttr(dst_full, value)
        except Exception as exc:
            # Attribute might be locked or of an unsupported type.
            # Warn so users can spot when a duplicate silently omits
            # a preset attribute (e.g. a Maya-version-dependent name).
            cmds.warning(
                "[maya_hair_tool] copy {0} -> {1} failed: {2}".format(
                    src_full, dst_full, exc))


def _copy_scale_profile_ramp(src: str, dst: str) -> None:
    """Clone the ``taperCurve`` ramp entries wholesale.

    Rewritten as clear-then-fill rather than trying to patch existing
    entries — Maya's ramp attributes are multi-index and their default
    single-entry state must be wiped first, otherwise the new curve
    inherits a phantom entry from the fresh sweep.

    (The function name still says "scale profile" for backwards
    compatibility, but the Maya attribute is ``taperCurve``.)
    """
    if not cmds.attributeQuery("taperCurve", node=src, exists=True):
        return
    if not cmds.attributeQuery("taperCurve", node=dst, exists=True):
        return

    dst_attr = dst + ".taperCurve"
    dst_indices = cmds.getAttr(dst_attr, multiIndices=True) or []
    for idx in dst_indices:
        try:
            cmds.removeMultiInstance(
                "{0}[{1}]".format(dst_attr, idx), b=True)
        except Exception:
            pass

    src_attr = src + ".taperCurve"
    src_indices = cmds.getAttr(src_attr, multiIndices=True) or []
    for out_i, src_i in enumerate(src_indices):
        try:
            pos = cmds.getAttr(
                "{0}[{1}].taperCurve_Position".format(src_attr, src_i))
            val = cmds.getAttr(
                "{0}[{1}].taperCurve_FloatValue".format(src_attr, src_i))
            interp = cmds.getAttr(
                "{0}[{1}].taperCurve_Interp".format(src_attr, src_i))
            cmds.setAttr(
                "{0}[{1}].taperCurve_Position".format(dst_attr, out_i), pos)
            cmds.setAttr(
                "{0}[{1}].taperCurve_FloatValue".format(dst_attr, out_i),
                val)
            cmds.setAttr(
                "{0}[{1}].taperCurve_Interp".format(dst_attr, out_i),
                interp)
        except Exception as exc:
            # Warn instead of silently dropping the entry — same
            # policy as _copy_scalar_attrs so users find out when a
            # taper point failed to replicate.
            cmds.warning(
                "[maya_hair_tool] taperCurve entry {0} → {1} at "
                "index {2}/{3} failed to copy: {4}".format(
                    src, dst, src_i, out_i, exc))


def _duplicate_custom_profile_curve(src: str, dst: str,
                                    name_hint: str) -> None:
    """If the source uses a Custom Profile, clone that profile curve too.

    The Custom Profile input attribute name differs between Maya
    versions (``inCustomCurve``, ``customProfileCurve``, …), so we
    detect at runtime which one exists rather than hard-coding one.
    """
    candidates = (
        "inCustomCurve",
        "customProfileCurve",
        "inProfileCurve",
        "customPolyProfileCurve",
    )
    src_attr = None
    for attr in candidates:
        if cmds.attributeQuery(attr, node=src, exists=True):
            src_attr = attr
            break
    if src_attr is None:
        return
    if not cmds.attributeQuery(src_attr, node=dst, exists=True):
        return

    src_plug = "{0}.{1}".format(src, src_attr)
    incoming = cmds.listConnections(src_plug, source=True, destination=False,
                                    plugs=True) or []
    if not incoming:
        return

    src_curve_shape = incoming[0].split(".")[0]
    src_curve = _shape_to_transform(src_curve_shape)
    if not src_curve:
        return

    try:
        dup_result = cmds.duplicate(
            src_curve, returnRootsOnly=True,
            name=name_hint + C.HAIR_PROFILE_SUFFIX,
        ) or []
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] custom profile duplicate failed: "
            "{0}".format(exc))
        return
    if not dup_result:
        return
    dup_profile = dup_result[0]

    def _delete_orphan():
        # Clean up the freshly-duplicated ``_profile`` transform when
        # we couldn't wire it up — leaving it in the scene would give
        # the user a mystery node with no history.
        try:
            if cmds.objExists(dup_profile):
                cmds.delete(dup_profile)
        except Exception:
            pass

    # Filter intermediate shapes ("...Orig" from Maya's construction
    # history): connecting to an intermediate would put the profile on
    # the deformer stack input instead of the visible curve and the
    # duplicate would look wrong.
    dup_shapes = cmds.listRelatives(dup_profile, shapes=True,
                                    fullPath=True) or []
    dup_shape = None
    for shape in dup_shapes:
        try:
            if cmds.getAttr(shape + ".intermediateObject"):
                continue
        except Exception:
            pass
        dup_shape = shape
        break
    if dup_shape is None:
        _delete_orphan()
        return

    # Match the source's plug type — worldSpace vs local.
    src_plug_name = incoming[0].split(".", 1)[1]
    try:
        cmds.connectAttr(
            "{0}.{1}".format(dup_shape, src_plug_name),
            "{0}.{1}".format(dst, src_attr),
            force=True,
        )
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] custom profile connect failed: "
            "{0}".format(exc))
        _delete_orphan()


def _match_parent(src_mesh: Optional[str], new_mesh: Optional[str]) -> None:
    if not src_mesh or not new_mesh or not cmds.objExists(src_mesh):
        return
    parents = cmds.listRelatives(src_mesh, parent=True, fullPath=True) or []
    if not parents:
        return
    try:
        cmds.parent(new_mesh, parents[0])
    except RuntimeError as exc:
        # Parenting can legitimately fail (e.g. already under the same
        # group) — warn so the user knows why the duplicate isn't
        # grouped, instead of a silent "world root" placement.
        cmds.warning(
            "[maya_hair_tool] could not parent {0} under {1}: "
            "{2}".format(new_mesh, parents[0], exc))


def _shape_to_transform(node: str) -> Optional[str]:
    if not cmds.objExists(node):
        return None
    parents = cmds.listRelatives(node, parent=True, fullPath=True) or []
    if parents:
        return parents[0]
    return node


def _unique_hair_name(src_curve: str, src_mesh: Optional[str]) -> str:
    """Pick a fresh name based on the source's mesh transform.

    Preference order: mesh transform base → curve base → generic prefix.
    Suffix increments ``_02``, ``_03``, … until unused.
    """
    if src_mesh:
        base = src_mesh.split("|")[-1]
        for suffix in (C.HAIR_MESH_SUFFIX, "_geo"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    else:
        base = src_curve.split("|")[-1]
        for suffix in (C.HAIR_CURVE_SUFFIX, "_crv", "Curve", "Crv"):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
    if not base:
        base = C.HAIR_STRAND_PREFIX

    i = 1
    candidate = base
    while (cmds.objExists(candidate + C.HAIR_MESH_SUFFIX)
            or cmds.objExists(candidate + C.HAIR_CURVE_SUFFIX)
            or cmds.objExists(candidate + C.HAIR_SWEEP_SUFFIX)
            or cmds.objExists(candidate + C.HAIR_PROFILE_SUFFIX)):
        i += 1
        candidate = "{0}_{1:02d}".format(base, i)
    return candidate
