"""Procedural three-strand braid generator (Phase 6).

Takes a user-drawn "spine" NURBS curve and generates three helical
strand curves that weave around it. Each strand curve is then fed
into the existing hair pipeline (``hair.create_hair_from_selected_curves``)
so every braid strand becomes a first-class hair strand — the user
can adjust its width, twist, profile, taper etc. via the normal UI,
and the auto group-color / grouping features work unchanged.

The three strand curves are placed at 120° phase offsets around
the spine and rotate together at ``turns_per_length`` turns per
spine-length unit. Radius tapers to zero toward the tip based on
``tip_taper``.

Frames along the spine are computed via parallel transport (rather
than pure Frenet) so the strands don't flip 180° when the spine
passes through an inflection point — a classic Frenet failure mode
that would tear the braid geometry.

Design constraint (per HANDOFF.md §11): no custom plugin nodes.
Everything here is pure ``cmds`` + math.
"""

from __future__ import annotations

import math
from typing import List, Optional, Tuple

try:
    from maya import cmds
except ImportError:
    cmds = None

from . import constants as C
from . import hair
from . import sweep_utils as su


Vec3 = Tuple[float, float, float]


# --------------------------------------------------------------------------- #
# Vector math helpers (pure Python — avoids the openMaya dependency dance
# and keeps this module importable outside Maya for linting).
# --------------------------------------------------------------------------- #

def _add(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Vec3, b: Vec3) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a: Vec3, s: float) -> Vec3:
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a: Vec3, b: Vec3) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec3, b: Vec3) -> Vec3:
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


def _length(a: Vec3) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def _normalize(a: Vec3) -> Vec3:
    L = _length(a)
    if L < 1e-9:
        return (0.0, 0.0, 0.0)
    return (a[0] / L, a[1] / L, a[2] / L)


def _rotate_axis_angle(v: Vec3, axis: Vec3, angle: float) -> Vec3:
    """Rotate vector ``v`` around unit ``axis`` by ``angle`` radians
    (Rodrigues' rotation formula)."""
    c = math.cos(angle)
    s = math.sin(angle)
    k_dot_v = _dot(axis, v)
    k_cross_v = _cross(axis, v)
    return _add(
        _add(_scale(v, c), _scale(k_cross_v, s)),
        _scale(axis, k_dot_v * (1.0 - c)),
    )


# --------------------------------------------------------------------------- #
# Spine sampling
# --------------------------------------------------------------------------- #

def _spine_param_range(spine_curve: str) -> Tuple[float, float]:
    """NURBS curves in Maya are parameterised over an arbitrary
    ``[minU, maxU]`` — not always ``[0, 1]``. Read the actual range
    so ``pointOnCurve`` samples land where we expect."""
    mn = cmds.getAttr(spine_curve + ".minValue")
    mx = cmds.getAttr(spine_curve + ".maxValue")
    return float(mn), float(mx)


def _sample_positions(spine_curve: str, num_samples: int) -> List[Vec3]:
    """Sample ``num_samples`` positions uniformly along the spine
    curve's parameter range (NOT arc length — for smooth hair
    spines the visual difference is minor and parameter sampling
    is one ``pointOnCurve`` call per sample instead of an arc-length
    integration)."""
    mn, mx = _spine_param_range(spine_curve)
    span = mx - mn
    positions: List[Vec3] = []
    for i in range(num_samples):
        u = mn + span * (float(i) / float(num_samples - 1))
        p = cmds.pointOnCurve(
            spine_curve, parameter=u, position=True)
        positions.append((float(p[0]), float(p[1]), float(p[2])))
    return positions


def _tangents_from_positions(positions: List[Vec3]) -> List[Vec3]:
    """Central-difference tangents at each sample; endpoints use
    forward/backward differences. Cheap and stable for typical
    hair spines (no sharp corners)."""
    n = len(positions)
    tangents: List[Vec3] = []
    for i in range(n):
        if i == 0:
            t = _sub(positions[1], positions[0])
        elif i == n - 1:
            t = _sub(positions[n - 1], positions[n - 2])
        else:
            t = _sub(positions[i + 1], positions[i - 1])
        tangents.append(_normalize(t))
    return tangents


def _parallel_transport_frames(
    tangents: List[Vec3],
) -> Tuple[List[Vec3], List[Vec3]]:
    """Given per-sample tangents, compute (normals, binormals) that
    don't twist artificially — using the "parallel transport" method
    (rotate the previous normal by the minimum rotation that carries
    the previous tangent onto the current one, then re-orthonormalize).

    Frenet frame's normal is defined by the second derivative and
    flips 180° at inflection points; parallel transport threads a
    stable frame past those.
    """
    n_samples = len(tangents)
    normals: List[Vec3] = []
    binormals: List[Vec3] = []

    # Pick an initial normal perpendicular to the first tangent.
    # Reference the axis with the smallest |t| component so the
    # cross product is well-conditioned.
    t0 = tangents[0]
    if abs(t0[1]) < 0.9:
        ref = (0.0, 1.0, 0.0)
    else:
        ref = (1.0, 0.0, 0.0)
    n0 = _normalize(_cross(ref, t0))
    if _length(n0) < 1e-6:
        # Fallback if ref happened to be nearly parallel.
        n0 = _normalize(_cross((1.0, 0.0, 0.0), t0))
    normals.append(n0)
    binormals.append(_normalize(_cross(t0, n0)))

    for i in range(1, n_samples):
        t_prev = tangents[i - 1]
        t_curr = tangents[i]
        n_prev = normals[i - 1]

        axis = _cross(t_prev, t_curr)
        axis_len = _length(axis)
        if axis_len < 1e-6:
            # Tangents parallel — no rotation needed.
            n_curr = n_prev
        else:
            axis = _scale(axis, 1.0 / axis_len)
            # Signed angle via atan2(cross, dot) — stable across
            # the full 0..π range where acos(dot) loses precision.
            cos_a = max(-1.0, min(1.0, _dot(t_prev, t_curr)))
            angle = math.atan2(axis_len, cos_a)
            n_curr = _rotate_axis_angle(n_prev, axis, angle)

        # Re-orthogonalize (numerical drift over many samples adds
        # up otherwise).
        n_curr = _sub(n_curr, _scale(t_curr, _dot(n_curr, t_curr)))
        n_curr = _normalize(n_curr)
        normals.append(n_curr)
        binormals.append(_normalize(_cross(t_curr, n_curr)))

    return normals, binormals


def _arc_length(positions: List[Vec3]) -> float:
    total = 0.0
    for i in range(1, len(positions)):
        total += _length(_sub(positions[i], positions[i - 1]))
    return total


# --------------------------------------------------------------------------- #
# Strand curve generation
# --------------------------------------------------------------------------- #

def _build_strand_curve(
    positions: List[Vec3],
    normals: List[Vec3],
    binormals: List[Vec3],
    phase_base: float,
    turns: float,
    radius: float,
    tip_taper: float,
    name: str,
) -> str:
    """Offset each spine sample radially by (r*cosθ * N + r*sinθ * B)
    with θ = phase_base + 2π*turns*u, then build a cubic NURBS curve
    through the offset points."""
    n = len(positions)
    points: List[Vec3] = []
    for i in range(n):
        u = float(i) / float(n - 1)
        theta = phase_base + 2.0 * math.pi * turns * u
        eff_radius = radius * max(0.0, 1.0 - tip_taper * u)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        N = normals[i]
        B = binormals[i]
        offset = (
            eff_radius * (cos_t * N[0] + sin_t * B[0]),
            eff_radius * (cos_t * N[1] + sin_t * B[1]),
            eff_radius * (cos_t * N[2] + sin_t * B[2]),
        )
        points.append(_add(positions[i], offset))

    # Cubic BSpline through the samples. If we ever hit < 4 points
    # ``degree=3`` is illegal; fall back to linear.
    degree = 3 if n >= 4 else 1
    return cmds.curve(name=name, p=points, degree=degree)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _next_braid_group_name() -> str:
    """Return the next available ``Braid_NN`` name (scans existing
    hair groups so the numbering doesn't reuse a deleted slot's
    number until higher ones are also gone)."""
    existing = set()
    for g in hair.list_hair_groups():
        short = g.split("|")[-1]
        if short.startswith(C.BRAID_GROUP_PREFIX):
            existing.add(short)
    i = 1
    while True:
        candidate = "{0}{1:02d}".format(C.BRAID_GROUP_PREFIX, i)
        if candidate not in existing and not cmds.objExists(candidate):
            return candidate
        i += 1


def create_braid_from_spine(
    spine_curve: Optional[str] = None,
    turns_per_length: float = C.DEFAULT_BRAID_TURNS_PER_LENGTH,
    radius: float = C.DEFAULT_BRAID_RADIUS,
    strand_thickness: float = C.DEFAULT_BRAID_STRAND_THICKNESS,
    tip_taper: float = C.DEFAULT_BRAID_TIP_TAPER,
    num_samples: int = C.DEFAULT_BRAID_SAMPLES,
    group: bool = True,
) -> List[str]:
    """Generate a three-strand braid around ``spine_curve``.

    If ``spine_curve`` is None the current selection is used (expects
    exactly one NURBS curve). Returns the list of created strand
    mesh transforms (typically length 3).

    Parameters
    ----------
    turns_per_length : how many full 360° twists the braid completes
        per unit of spine arc length. Small = loose braid, large =
        tight rope.
    radius : how far each strand is offset from the spine (i.e. the
        braid's overall radius, not the individual strand thickness).
    strand_thickness : sweepMeshCreator width applied to each strand
        after generation — matches the "太さ" slider on normal hair.
    tip_taper : 0..1, fraction the braid radius shrinks by the tip
        (0 = constant radius, 1 = radius → 0 at the tip). Independent
        of the sweepMeshCreator's own taperCurve.
    num_samples : samples along the spine. More = smoother helix
        but heavier curve. 32 covers most anime-length spines cleanly.
    group : if True (default), wrap the three strands into a new
        ``Braid_NN`` hair group so the user can adjust them together
        via existing group-relative-multiplier controls.
    """
    if cmds is None:
        raise RuntimeError("create_braid_from_spine requires Maya.")

    if spine_curve is None:
        sel = cmds.ls(selection=True, long=True) or []
        curves = []
        for s in sel:
            shapes = cmds.listRelatives(
                s, shapes=True, fullPath=True,
                type="nurbsCurve") or []
            if shapes:
                curves.append(s)
            elif cmds.nodeType(s) == "nurbsCurve":
                # Shape was selected directly.
                parents = cmds.listRelatives(
                    s, parent=True, fullPath=True) or []
                if parents:
                    curves.append(parents[0])
        if not curves:
            raise RuntimeError(
                "スパインカーブが選択されていません。NURBS カーブを "
                "1 本選択してから実行してください。")
        if len(curves) > 1:
            raise RuntimeError(
                "スパインカーブを 1 本だけ選択してください "
                "(選択中 {0} 本)。".format(len(curves)))
        spine_curve = curves[0]

    if not cmds.objExists(spine_curve):
        raise RuntimeError(
            "スパインカーブが存在しません: {0}".format(spine_curve))

    # Clamp sample count so degenerate inputs don't blow up.
    num_samples = max(4, int(num_samples))

    # Sample spine and compute parallel-transport frames.
    positions = _sample_positions(spine_curve, num_samples)
    tangents = _tangents_from_positions(positions)
    normals, binormals = _parallel_transport_frames(tangents)

    spine_length = _arc_length(positions)
    if spine_length < 1e-6:
        raise RuntimeError(
            "スパインカーブの長さがゼロ相当です。CV が重複していないか "
            "確認してください。")

    # turns_per_length is a rate; total turns depends on the length.
    total_turns = turns_per_length * spine_length

    # Undo chunking so 3-strand generation is one Ctrl+Z.
    cmds.undoInfo(openChunk=True, chunkName="createBraid")
    try:
        base_name = "braid_{0:03d}".format(_next_braid_index_hint())
        strand_curves: List[str] = []
        for i in range(3):
            phase = 2.0 * math.pi * (float(i) / 3.0)
            cname = "{0}_s{1}_curve".format(base_name, i + 1)
            strand_curves.append(_build_strand_curve(
                positions, normals, binormals,
                phase_base=phase,
                turns=total_turns,
                radius=radius,
                tip_taper=tip_taper,
                name=cname,
            ))

        # Feed the three strand curves through the standard hair
        # pipeline. Select-then-call keeps this decoupled from any
        # refactor of create_hair_from_selected_curves.
        cmds.select(strand_curves, replace=True)
        # Use the same defaults as a normal hair strand except for
        # thickness (we honour the user's braid slider) — the caller
        # can always tweak individual strands afterward.
        hair.create_hair_from_selected_curves(
            thickness=strand_thickness,
        )

        # ``create_hair_from_selected_curves`` selects the resulting
        # mesh transforms — grab those now.
        strand_meshes = cmds.ls(selection=True, long=True) or []
        # Filter to strand-mesh transforms (defensive — should be
        # exactly 3).
        strand_meshes = [
            m for m in strand_meshes
            if hair._is_hair_strand_transform(m)
        ]

        if group and strand_meshes:
            group_name = _next_braid_group_name()
            try:
                hair.create_hair_group(group_name)
            except RuntimeError as exc:
                cmds.warning(
                    "[maya_hair_tool] Braid group 作成失敗: "
                    "{0}".format(exc))
                group_name = None
            if group_name:
                moved: List[str] = []
                for m in strand_meshes:
                    try:
                        moved.append(
                            hair.move_strand_to_group(m, group_name))
                    except Exception as exc:
                        cmds.warning(
                            "[maya_hair_tool] Braid strand を "
                            "グループへ移動失敗: {0}".format(exc))
                        moved.append(m)
                strand_meshes = moved

        if strand_meshes:
            try:
                cmds.select(strand_meshes, replace=True)
            except Exception:
                pass
        return strand_meshes
    finally:
        cmds.undoInfo(closeChunk=True)


# Module-local counter so consecutive create_braid calls without a
# refresh don't hand out the same base name (each call before any
# hair.list_hair_groups sync would otherwise return "braid_001" and
# collide on the strand-curve names). Not persistent across scene
# reloads — that's fine, the group-name uniquifier below is the
# canonical numbering.
_braid_call_counter = [0]


def _next_braid_index_hint() -> int:
    _braid_call_counter[0] += 1
    return _braid_call_counter[0]
