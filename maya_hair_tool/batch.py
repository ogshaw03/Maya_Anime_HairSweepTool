"""Batch edit helpers.

Absolute / Relative editing across multiple sweep strands.

Primitives (``apply_absolute`` / ``apply_relative``) do NOT open their
own undo chunk — they are meant to be composed by callers who know
which set of edits belong to one user gesture. The UI wraps a whole
Apply press in one chunk via :func:`batch_undo_chunk`.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Dict, Iterable, List

try:
    import maya.cmds as cmds
except ImportError:  # pragma: no cover
    cmds = None

from . import hair
from . import sweep_utils as su


# Absolute tolerance for "is this slider value at its identity?" checks.
# Slider drags leave tiny FP residues like 0.99999998 that would defeat
# a strict ``value == 1.0`` comparison and re-write attributes the user
# didn't intend to touch.
IDENTITY_TOL = 1e-4


def is_identity(value: float, identity: float = 1.0) -> bool:
    """Return True when ``value`` is close enough to ``identity`` to
    be treated as "no change" by the Batch Edit sentinel logic."""
    return math.isclose(value, identity, abs_tol=IDENTITY_TOL)


ATTRS_BATCH = (
    "scaleProfileX",
    "scaleProfileY",
    "scaleProfileUniform",
    "twist",
    "rotateProfile",
    "interpolationSteps",
    "interpolationPrecision",
)

# Marker keys used in :func:`snapshot` for the three ``scaleProfile``
# ramp positions. Read via :func:`hair.read_taper_values` because a
# ramp attribute cannot be fetched with a single ``getAttr``.
TAPER_ROOT_KEY = "taperRoot"
TAPER_MIDDLE_KEY = "taperMiddle"
TAPER_TIP_KEY = "taperTip"


@contextmanager
def batch_undo_chunk(name: str = "HairBatchEdit"):
    """Group a burst of ``setAttr`` calls into a single Ctrl+Z step."""
    cmds.undoInfo(openChunk=True, chunkName=name)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


def apply_absolute(creators: Iterable[str], attr: str, value: float) -> None:
    for c in creators:
        if cmds.attributeQuery(attr, node=c, exists=True):
            try:
                cmds.setAttr(c + "." + attr, value)
            except Exception as exc:
                cmds.warning(
                    "[maya_hair_tool] batch set {0}.{1} = {2!r} failed: "
                    "{3}".format(c, attr, value, exc))


def apply_relative(creators: Iterable[str], attr: str, factor: float) -> None:
    for c in creators:
        if not cmds.attributeQuery(attr, node=c, exists=True):
            continue
        try:
            current = cmds.getAttr(c + "." + attr)
            cmds.setAttr(c + "." + attr, current * factor)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] batch scale {0}.{1} by {2!r} failed: "
                "{3}".format(c, attr, factor, exc))


def apply_delta(creators: Iterable[str], attr: str, delta: float) -> None:
    """Add ``delta`` to each creator's current ``attr`` value.

    Used for attributes where a multiplicative "Relative" doesn't make
    semantic sense — notably ``twistAngle``, where "Relative 45" ought
    to mean "add 45° to whatever twist each strand already has", not
    "multiply the twist by 45"."""
    for c in creators:
        if not cmds.attributeQuery(attr, node=c, exists=True):
            continue
        try:
            current = cmds.getAttr(c + "." + attr)
            cmds.setAttr(c + "." + attr, current + delta)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] batch add {0}.{1} += {2!r} failed: "
                "{3}".format(c, attr, delta, exc))


def snapshot(creators: Iterable[str]) -> Dict[str, Dict[str, float]]:
    """Return the current batch-editable values for each creator.

    Includes the three ``scaleProfile`` ramp positions (root / middle /
    tip) under the ``taperRoot``/``taperMiddle``/``taperTip`` keys, so
    a future Preset / snapshot-restore pipeline can round-trip taper
    values along with the scalar attributes.
    """
    result: Dict[str, Dict[str, float]] = {}
    for c in creators:
        values: Dict[str, float] = {}
        for attr in ATTRS_BATCH:
            if cmds.attributeQuery(attr, node=c, exists=True):
                try:
                    values[attr] = cmds.getAttr(c + "." + attr)
                except Exception:
                    pass
        try:
            root, middle, tip = hair.read_taper_values(c)
            values[TAPER_ROOT_KEY] = root
            values[TAPER_MIDDLE_KEY] = middle
            values[TAPER_TIP_KEY] = tip
        except Exception:
            pass
        result[c] = values
    return result


def creators_from_selection() -> List[str]:
    return su.sweep_creators_from_selection()
