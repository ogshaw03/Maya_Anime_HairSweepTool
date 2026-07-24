"""Batch edit helpers.

Phase 3 preview — Absolute / Relative editing across multiple sweep
strands. Kept small and dependency-free so it can be wired into the UI
incrementally.
"""

from __future__ import annotations

from typing import Dict, Iterable, List

try:
    import maya.cmds as cmds
except ImportError:  # pragma: no cover
    cmds = None

from . import sweep_utils as su


ATTRS_BATCH = (
    "scaleProfileX",
    "scaleProfileY",
    "twistAngle",
    "rotateProfile",
    "interpolationSteps",
    "interpolationPrecision",
)


def apply_absolute(creators: Iterable[str], attr: str, value: float) -> None:
    for c in creators:
        if cmds.attributeQuery(attr, node=c, exists=True):
            try:
                cmds.setAttr(c + "." + attr, value)
            except Exception:
                pass


def apply_relative(creators: Iterable[str], attr: str, factor: float) -> None:
    for c in creators:
        if not cmds.attributeQuery(attr, node=c, exists=True):
            continue
        try:
            current = cmds.getAttr(c + "." + attr)
            cmds.setAttr(c + "." + attr, current * factor)
        except Exception:
            pass


def snapshot(creators: Iterable[str]) -> Dict[str, Dict[str, float]]:
    """Return the current batch-editable values for each creator."""
    result: Dict[str, Dict[str, float]] = {}
    for c in creators:
        values: Dict[str, float] = {}
        for attr in ATTRS_BATCH:
            if cmds.attributeQuery(attr, node=c, exists=True):
                try:
                    values[attr] = cmds.getAttr(c + "." + attr)
                except Exception:
                    pass
        result[c] = values
    return result


def creators_from_selection() -> List[str]:
    return su.sweep_creators_from_selection()
