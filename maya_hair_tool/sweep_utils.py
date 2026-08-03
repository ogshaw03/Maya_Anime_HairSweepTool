"""Low-level Sweep Mesh helpers used by the Anime Hair tool.

All Maya-specific commands are wrapped in small utility functions so the
higher level modules (hair.py, ui.py) stay easy to read.

The tool relies on Maya's native ``sweepMeshCreator`` node — introduced in
Maya 2022 as part of the Sweep Mesh feature — and never creates its own
mesh from scratch. That way the resulting scene works even when opened
without this tool installed.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

try:
    import maya.cmds as cmds
    import maya.mel as mel
except ImportError:  # pragma: no cover - allow import outside Maya
    cmds = None
    mel = None

from . import constants as C


def _ensure_maya() -> None:
    if cmds is None:
        raise RuntimeError(
            "maya.cmds is not available. This module must be executed "
            "inside a Maya session."
        )


def load_sweep_plugin() -> None:
    """Ensure the SweepMesh plugin is loaded.

    Maya's Sweep Mesh feature has shipped under two plugin names across
    versions (``sweep`` in 2022+, historical ``sweepMesh``). Try both
    and warn only if *neither* loaded — otherwise a downstream
    ``createNode('sweepMeshCreator')`` would fail with "unknown node
    type" and the actual cause (missing plugin) is invisible.
    """
    _ensure_maya()
    loaded = False
    for plugin in ("sweep", "sweepMesh"):
        try:
            if cmds.pluginInfo(plugin, query=True, loaded=True):
                loaded = True
                continue
            cmds.loadPlugin(plugin, quiet=True)
            if cmds.pluginInfo(plugin, query=True, loaded=True):
                loaded = True
        except Exception:
            # This plugin name isn't valid for this Maya version — try
            # the other one in the loop.
            pass
    if not loaded:
        cmds.warning(
            "[maya_hair_tool] failed to load the Sweep Mesh plugin "
            "('sweep' or 'sweepMesh'). This tool requires Maya 2022+ "
            "with the Sweep Mesh feature; sweepMeshCreator creation "
            "will fail with 'unknown node type' without it.")


def selected_curves() -> List[str]:
    """Return currently-selected NURBS curve transforms."""
    _ensure_maya()
    sel = cmds.ls(selection=True, long=True) or []
    curves: List[str] = []
    for node in sel:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        for shape in shapes:
            if cmds.nodeType(shape) == "nurbsCurve":
                curves.append(node)
                break
    return curves


def create_sweep_from_curve(curve: str, name_hint: str) -> Tuple[str, str]:
    """Create a Sweep Mesh from ``curve``.

    Returns ``(sweep_creator_node, hair_mesh_transform)``.
    """
    _ensure_maya()
    load_sweep_plugin()

    curve_shape = _first_curve_shape(curve)
    if curve_shape is None:
        raise ValueError("{0!r} has no nurbsCurve shape".format(curve))

    creator = cmds.createNode("sweepMeshCreator", name=name_hint + "_sweep")
    mesh_xform = cmds.createNode(
        "transform", name=name_hint + C.HAIR_MESH_SUFFIX
    )
    mesh_shape = cmds.createNode(
        "mesh", name=mesh_xform + "Shape", parent=mesh_xform
    )

    cmds.connectAttr(curve_shape + ".worldSpace[0]",
                     creator + ".inCurveArray[0]")
    cmds.connectAttr(creator + ".outMeshArray[0]", mesh_shape + ".inMesh")

    # Assign the default shading group so the mesh is visible immediately.
    try:
        cmds.sets(mesh_shape, edit=True, forceElement="initialShadingGroup")
    except Exception:
        pass

    # Tag the creator so the tool can find its own nodes later.
    if not cmds.attributeQuery(C.TOOL_TAG_ATTR, node=creator, exists=True):
        cmds.addAttr(creator, longName=C.TOOL_TAG_ATTR, attributeType="bool",
                     defaultValue=True)
        cmds.setAttr(creator + "." + C.TOOL_TAG_ATTR, True, lock=True)

    return creator, mesh_xform


def _first_curve_shape(curve: str) -> Optional[str]:
    _ensure_maya()
    if cmds.nodeType(curve) == "nurbsCurve":
        return curve
    shapes = cmds.listRelatives(curve, shapes=True, fullPath=True) or []
    for shape in shapes:
        if cmds.nodeType(shape) == "nurbsCurve":
            return shape
    return None


def sweep_creators_from_selection() -> List[str]:
    """Return sweepMeshCreator nodes reachable from the current selection.

    Accepts curves, hair mesh transforms, or the sweep node itself.
    """
    _ensure_maya()
    sel = cmds.ls(selection=True, long=True) or []
    return sweep_creators_from_nodes(sel)


def sweep_creators_from_nodes(nodes: Iterable[str]) -> List[str]:
    _ensure_maya()
    found: List[str] = []
    seen = set()
    for node in nodes:
        for creator in _find_creators_related_to(node):
            if creator not in seen:
                found.append(creator)
                seen.add(creator)
    return found


def _find_creators_related_to(node: str) -> List[str]:
    _ensure_maya()
    if not cmds.objExists(node):
        return []
    node_type = cmds.nodeType(node)
    if node_type == "sweepMeshCreator":
        return [node]

    # From a mesh shape/transform → walk inMesh.
    shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or [node]
    creators: List[str] = []
    for shape in shapes:
        stype = cmds.nodeType(shape)
        if stype == "mesh":
            history = cmds.listHistory(shape) or []
            for h in history:
                if cmds.nodeType(h) == "sweepMeshCreator":
                    creators.append(h)
        elif stype == "nurbsCurve":
            downstream = cmds.listConnections(
                shape + ".worldSpace",
                type="sweepMeshCreator",
                source=False,
                destination=True,
            ) or []
            creators.extend(downstream)
    return creators


def curve_from_creator(creator: str) -> Optional[str]:
    """Return the transform of the guide curve that feeds ``creator``."""
    _ensure_maya()
    conns = cmds.listConnections(
        creator + ".inCurveArray",
        source=True,
        destination=False,
        shapes=True,
    ) or []
    for shape in conns:
        if cmds.nodeType(shape) == "nurbsCurve":
            parents = cmds.listRelatives(shape, parent=True, fullPath=True)
            if parents:
                return parents[0]
            return shape
    return None


def mesh_from_creator(creator: str) -> Optional[str]:
    _ensure_maya()
    conns = cmds.listConnections(
        creator + ".outMeshArray",
        source=False,
        destination=True,
        shapes=True,
    ) or []
    for shape in conns:
        if cmds.nodeType(shape) == "mesh":
            parents = cmds.listRelatives(shape, parent=True, fullPath=True)
            if parents:
                return parents[0]
            return shape
    return None
