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

# --------------------------------------------------------------------------- #
# Density profile — per-region weave tightness (top/middle/bottom).
# --------------------------------------------------------------------------- #

def _density_at(u: float, top: float, middle: float, bottom: float) -> float:
    """Piecewise-linear density multiplier at normalised spine
    position ``u`` (0 = root, 1 = tip). Knots: (0, top), (0.5,
    middle), (1, bottom)."""
    if u <= 0.0:
        return top
    if u >= 1.0:
        return bottom
    if u <= 0.5:
        t = u / 0.5
        return top * (1.0 - t) + middle * t
    t = (u - 0.5) / 0.5
    return middle * (1.0 - t) + bottom * t


def _cumulative_density(u: float, top: float, middle: float,
                        bottom: float) -> float:
    """Analytic ∫₀ᵘ density(v) dv for the piecewise-linear density
    profile. When all three multipliers are 1.0 this equals ``u``,
    so the "constant total turns" formula still holds at defaults.

    Result units are dimensionless (u ∈ [0,1]); multiply by
    ``turns_per_length × spine_length`` to convert to total turns
    completed by position ``u``.
    """
    if u <= 0.0:
        return 0.0
    if u >= 1.0:
        # Full integral = first half + second half of the piecewise
        # linear function. Each half's integral is (start+end)/2 × width.
        return (top + middle) * 0.25 + (middle + bottom) * 0.25
    if u <= 0.5:
        # Density from 0 to u: linear top → mid at u=0.5.
        # ∫ = top·u + (middle − top)·u²
        return top * u + (middle - top) * u * u
    # Region 0.5 → u: linear mid → bottom.
    first_half = (top + middle) * 0.25
    ou = u - 0.5
    return first_half + middle * ou + (bottom - middle) * ou * ou


def _strand_offset_at(
    u: float,
    phase_base: float,
    turns: float,
    radius: float,
    tip_taper: float,
    depth_ratio: float,
    density_top: float,
    density_middle: float,
    density_bottom: float,
) -> tuple:
    """Return the (width, depth) offset in the (N, B) plane for
    one BRAID (woven) strand at parameter u ∈ [0, 1]. The braid
    region only — the tail below the tie is handled by separate
    strands built via ``_build_tail_strand_curve``."""
    cum = _cumulative_density(
        u, density_top, density_middle, density_bottom)
    theta = phase_base + 2.0 * math.pi * turns * cum
    taper = max(0.0, 1.0 - tip_taper * u)
    w = radius * math.sin(theta) * taper
    d = radius * depth_ratio * math.sin(2.0 * theta) * taper
    return w, d


def _sample_frame_interpolated(u, positions, normals, binormals):
    """Linearly interpolate position and frame vectors at fractional
    sample index derived from u ∈ [0,1]. Used to snap a curve's
    endpoint exactly onto the tie boundary rather than the nearest
    coarse sample."""
    n = len(positions)
    if n < 2:
        return positions[0], normals[0], binormals[0]
    idx_f = u * (n - 1)
    lo = int(math.floor(idx_f))
    hi = min(n - 1, lo + 1)
    t = idx_f - lo
    def _lerp(a, b, tt):
        return (a[0] * (1 - tt) + b[0] * tt,
                a[1] * (1 - tt) + b[1] * tt,
                a[2] * (1 - tt) + b[2] * tt)
    p = _lerp(positions[lo], positions[hi], t)
    N = _normalize(_lerp(normals[lo], normals[hi], t))
    B = _normalize(_lerp(binormals[lo], binormals[hi], t))
    return p, N, B


def _append_endpoint_at(points, positions, normals, binormals,
                         u, phase_base, turns, radius, tip_taper,
                         depth_ratio, density_top, density_middle,
                         density_bottom):
    p, N, B = _sample_frame_interpolated(
        u, positions, normals, binormals)
    w, d = _strand_offset_at(
        u, phase_base, turns, radius, tip_taper, depth_ratio,
        density_top, density_middle, density_bottom)
    points.append((
        p[0] + w * N[0] + d * B[0],
        p[1] + w * N[1] + d * B[1],
        p[2] + w * N[2] + d * B[2],
    ))


def _tail_shape(t: float) -> float:
    """Radius multiplier along the tail (t = 0 at the tie, 1 at the
    spine tip). Real hair braids tied with an elastic show:

    * t = 0 : strand thickness matches the last woven point (1.0 —
      continuity with the braid region)
    * ~0.1  : sharp pinch to ``TAIL_PINCH`` where the elastic
      squeezes the strands together
    * ~0.4  : bulge out to ``TAIL_BULGE`` where the freed strands
      splay open beyond the elastic
    * 1.0   : 0, so each strand tapers to its own point

    Piecewise-linear for cheap analytic evaluation; the four knots
    give the classic tassel silhouette without needing bezier
    control.
    """
    pinch = 0.20
    bulge = 1.30
    if t <= 0.0:
        return 1.0
    if t >= 1.0:
        return 0.0
    if t <= 0.1:
        u = t / 0.1
        return 1.0 + (pinch - 1.0) * u
    if t <= 0.4:
        u = (t - 0.1) / 0.3
        return pinch + (bulge - pinch) * u
    u = (t - 0.4) / 0.6
    return bulge * (1.0 - u)


def _build_strand_curve(
    positions: List[Vec3],
    normals: List[Vec3],
    binormals: List[Vec3],
    phase_base: float,
    turns: float,
    radius: float,
    tip_taper: float,
    depth_ratio: float,
    density_top: float,
    density_middle: float,
    density_bottom: float,
    tail_length: float,
    name: str,
) -> str:
    """Build a cubic NURBS curve for one strand of a flat 3-strand braid.

    A real hair braid is essentially planar with a shallow depth for
    the over/under crossings — not a rotating helix. Formula:

        θ_i(u) = phase_base + 2π · turns · u
        width  = R * sin(θ_i)          # N direction, 1× frequency
        depth  = R * depth_ratio * sin(2 · θ_i)  # B direction, 2× freq

    The three strands (phase_base = 0, 2π/3, 4π/3) oscillate as
    three sine waves in the N (width) axis with 120° phase offsets;
    they visibly cross each other because the sine values coincide
    at fixed points. The B (depth) axis oscillates at DOUBLE the
    width frequency so each strand traces a figure-8 in the (N, B)
    plane — the two lobes of the figure-8 are the "over" and
    "under" halves of the crossings, and offsetting three of them
    by 120° gives the braid's alternating over-under pattern.

    ``depth_ratio`` controls how thick (front-to-back) the braid
    reads; 0 collapses it to a flat 2D zig-zag, 1 makes depth and
    width equal (chunky braid). ``tip_taper`` shrinks both axes
    together so the braid tapers to a point at the tip.
    """
    n = len(positions)
    tie_off = 1.0 - max(0.0, min(0.99, tail_length))
    points: List[Vec3] = []
    for i in range(n):
        u = float(i) / float(n - 1)
        if u > tie_off:
            break  # braid strand ends at the tie; tail is separate
        w, d = _strand_offset_at(
            u, phase_base, turns, radius, tip_taper, depth_ratio,
            density_top, density_middle, density_bottom)
        N = normals[i]
        B = binormals[i]
        offset = (
            w * N[0] + d * B[0],
            w * N[1] + d * B[1],
            w * N[2] + d * B[2],
        )
        points.append(_add(positions[i], offset))
    # If tie_off falls between samples, extend one more point at
    # exactly tie_off so the strand terminates cleanly at the tie
    # (avoids a visible gap between braid and tail strands).
    if points and tie_off > 0.0 and tie_off < 1.0:
        _append_endpoint_at(
            points, positions, normals, binormals, tie_off,
            phase_base, turns, radius, tip_taper, depth_ratio,
            density_top, density_middle, density_bottom)

    # Guard against tail_length so large that the braid region
    # produces fewer than 2 points — cmds.curve needs at least
    # ``degree+1`` points, and a 0- or 1-point call raises. Empty
    # returns are handled by the caller (falls back to skipping
    # this strand rather than crashing the whole braid).
    if len(points) < 2:
        return None
    # Cubic BSpline through the samples. If we ever hit < 4 points
    # ``degree=3`` is illegal; fall back to linear. Use ``len(points)``
    # not ``n`` — n counts input spine samples, points can be fewer
    # once the ``u > tie_off`` break trims the trailing samples.
    degree = 3 if len(points) >= 4 else 1
    return cmds.curve(name=name, p=points, degree=degree)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #

def _cleanup_partial_braid(
    strand_curves: List[str],
    keep_meshes: Optional[List[str]] = None,
) -> None:
    """Undo an in-flight braid generation. Called when
    ``create_hair_from_selected_curves`` raises or under-delivers
    (fewer than 3 strand meshes made it back), so the user doesn't
    end up with a lopsided half-braid + orphan curves.

    Iterates the curves we spawned via ``_build_strand_curve``; for
    each still-alive curve, walks its outConnections to any
    ``sweepMeshCreator`` and deletes the resulting mesh transform
    before deleting the curve itself. ``keep_meshes`` skips the
    still-good strand meshes so a re-raise upstream doesn't wipe
    everything.
    """
    keep = set(keep_meshes or [])
    for curve in strand_curves:
        if not curve or not cmds.objExists(curve):
            continue
        # Curve → sweepMeshCreator → mesh transform.
        try:
            creators = su.sweep_creators_from_nodes([curve]) or []
        except Exception:
            creators = []
        for creator in creators:
            try:
                mesh = su.mesh_from_creator(creator)
            except Exception:
                mesh = None
            if mesh and mesh not in keep and cmds.objExists(mesh):
                try:
                    cmds.delete(mesh)
                except Exception:
                    pass
            if cmds.objExists(creator):
                try:
                    cmds.delete(creator)
                except Exception:
                    pass
        try:
            cmds.delete(curve)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Braid metadata (stamped on the group transform so we can re-sync
# sliders on selection and live-rebuild the braid when they change).
# --------------------------------------------------------------------------- #

_ATTR_MARKER = "isBraidGroup"        # bool, tags the group as a braid
_ATTR_SPINE_UUID = "braidSpineUuid"  # str, spine curve UUID
_ATTR_STRAND_UUIDS = "braidStrandMeshUuids"  # str, pipe-joined mesh UUIDs
_ATTR_TURNS = "braidTurnsPerLength"
_ATTR_RADIUS = "braidRadius"
_ATTR_THICKNESS = "braidStrandThickness"
_ATTR_TIP_TAPER = "braidTipTaper"
_ATTR_DEPTH_RATIO = "braidDepthRatio"
_ATTR_TAIL_LENGTH = "braidTailLength"
_ATTR_DENSITY_TOP = "braidDensityTop"
_ATTR_DENSITY_MIDDLE = "braidDensityMiddle"
_ATTR_DENSITY_BOTTOM = "braidDensityBottom"
_ATTR_TIE_UUID = "braidHairTieUuid"      # UUID of the elastic torus mesh
_ATTR_TAIL_STRAND_UUIDS = "braidTailMeshUuids"  # pipe-joined tail mesh UUIDs
_ATTR_TAIL_STRAND_COUNT = "braidTailStrandCount"
_ATTR_TAIL_THICKNESS = "braidTailThickness"


def _ensure_bool_attr(node: str, attr: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="bool")


def _ensure_str_attr(node: str, attr: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, dataType="string")


def _ensure_float_attr(node: str, attr: str) -> None:
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, attributeType="float")


def _stamp_tail_meta(group: str, tail_mesh_uuids: List[str],
                      tail_strand_count: int,
                      tail_thickness: float) -> None:
    """Stamp the tail-strand metadata so rebuild can decide whether
    to update tail CVs in place (count matches) or fully recreate
    (count changed)."""
    _ensure_str_attr(group, _ATTR_TAIL_STRAND_UUIDS)
    cmds.setAttr(
        group + "." + _ATTR_TAIL_STRAND_UUIDS,
        "|".join(tail_mesh_uuids), type="string")
    _ensure_float_attr(group, _ATTR_TAIL_STRAND_COUNT)
    cmds.setAttr(
        group + "." + _ATTR_TAIL_STRAND_COUNT,
        float(tail_strand_count))
    _ensure_float_attr(group, _ATTR_TAIL_THICKNESS)
    cmds.setAttr(group + "." + _ATTR_TAIL_THICKNESS,
                 float(tail_thickness))


def _stamp_tie_uuid(group: str, tie_uuid: str) -> None:
    """Stamp the hair-tie mesh's UUID on the group so rebuild can
    find it later. Called separately from ``_stamp_braid_params``
    because the tie is (re)created after the params are stamped."""
    _ensure_str_attr(group, _ATTR_TIE_UUID)
    cmds.setAttr(group + "." + _ATTR_TIE_UUID, tie_uuid or "",
                 type="string")


def _stamp_braid_params(
    group: str,
    spine_uuid: str,
    strand_mesh_uuids: List[str],
    params: dict,
) -> None:
    """Write the params used to build a braid onto the group transform
    so the UI can (a) re-populate sliders when the group is selected
    and (b) rebuild the braid in-place when a slider changes."""
    _ensure_bool_attr(group, _ATTR_MARKER)
    cmds.setAttr(group + "." + _ATTR_MARKER, True)
    _ensure_str_attr(group, _ATTR_SPINE_UUID)
    cmds.setAttr(
        group + "." + _ATTR_SPINE_UUID, spine_uuid, type="string")
    _ensure_str_attr(group, _ATTR_STRAND_UUIDS)
    cmds.setAttr(
        group + "." + _ATTR_STRAND_UUIDS,
        "|".join(strand_mesh_uuids), type="string")
    for attr, key in (
        (_ATTR_TURNS, "turns_per_length"),
        (_ATTR_RADIUS, "radius"),
        (_ATTR_THICKNESS, "strand_thickness"),
        (_ATTR_TIP_TAPER, "tip_taper"),
        (_ATTR_DEPTH_RATIO, "depth_ratio"),
        (_ATTR_TAIL_LENGTH, "tail_length"),
        (_ATTR_DENSITY_TOP, "density_top"),
        (_ATTR_DENSITY_MIDDLE, "density_middle"),
        (_ATTR_DENSITY_BOTTOM, "density_bottom"),
    ):
        _ensure_float_attr(group, attr)
        cmds.setAttr(group + "." + attr, float(params[key]))


def is_braid_group(node: str) -> bool:
    """Fast check whether ``node`` is a Braid group."""
    if cmds is None or not node or not cmds.objExists(node):
        return False
    return bool(cmds.attributeQuery(
        _ATTR_MARKER, node=node, exists=True))


def read_braid_params(group: str) -> Optional[dict]:
    """Return the stored braid metadata dict, or None if the group
    isn't a Braid group. Dict keys mirror ``create_braid_from_spine``
    kwargs plus ``spine_uuid`` and ``strand_mesh_uuids``."""
    if not is_braid_group(group):
        return None
    def _read_float_or(attr, default):
        # Backwards-compat: older Braid groups (v0.4.5 and earlier)
        # don't have the tail / density attrs. Return the default
        # instead of failing so those groups still live-edit.
        if not cmds.attributeQuery(attr, node=group, exists=True):
            return float(default)
        try:
            return float(cmds.getAttr(group + "." + attr))
        except Exception:
            return float(default)

    try:
        uuids_str = cmds.getAttr(group + "." + _ATTR_STRAND_UUIDS) or ""
        return {
            "spine_uuid":
                cmds.getAttr(group + "." + _ATTR_SPINE_UUID) or "",
            "strand_mesh_uuids":
                [u for u in uuids_str.split("|") if u],
            "turns_per_length":
                float(cmds.getAttr(group + "." + _ATTR_TURNS)),
            "radius": float(cmds.getAttr(group + "." + _ATTR_RADIUS)),
            "strand_thickness":
                float(cmds.getAttr(group + "." + _ATTR_THICKNESS)),
            "tip_taper":
                float(cmds.getAttr(group + "." + _ATTR_TIP_TAPER)),
            "depth_ratio":
                float(cmds.getAttr(group + "." + _ATTR_DEPTH_RATIO)),
            "tail_length":
                _read_float_or(_ATTR_TAIL_LENGTH,
                               C.DEFAULT_BRAID_TAIL_LENGTH),
            "density_top":
                _read_float_or(_ATTR_DENSITY_TOP,
                               C.DEFAULT_BRAID_DENSITY_TOP),
            "density_middle":
                _read_float_or(_ATTR_DENSITY_MIDDLE,
                               C.DEFAULT_BRAID_DENSITY_MIDDLE),
            "density_bottom":
                _read_float_or(_ATTR_DENSITY_BOTTOM,
                               C.DEFAULT_BRAID_DENSITY_BOTTOM),
            "tie_uuid":
                (cmds.getAttr(group + "." + _ATTR_TIE_UUID) or ""
                 if cmds.attributeQuery(_ATTR_TIE_UUID, node=group,
                                        exists=True)
                 else ""),
            "tail_mesh_uuids":
                ([u for u in
                  (cmds.getAttr(group + "."
                                + _ATTR_TAIL_STRAND_UUIDS) or ""
                   ).split("|") if u]
                 if cmds.attributeQuery(
                     _ATTR_TAIL_STRAND_UUIDS, node=group,
                     exists=True)
                 else []),
            "tail_strand_count":
                int(_read_float_or(_ATTR_TAIL_STRAND_COUNT,
                                    C.DEFAULT_BRAID_TAIL_STRAND_COUNT)),
            "tail_thickness":
                _read_float_or(_ATTR_TAIL_THICKNESS,
                               C.DEFAULT_BRAID_TAIL_THICKNESS),
        }
    except Exception:
        return None


def _sample_spine_frame_at(spine_curve: str, u: float) -> tuple:
    """Return (position, tangent, normal, binormal) at normalised
    parameter ``u`` (0..1) along the spine curve. Used by the
    hair-tie placement — we don't need the whole per-sample frame
    array, just the values at one point."""
    mn, mx = _spine_param_range(spine_curve)
    param = mn + (mx - mn) * max(0.0, min(1.0, u))
    p_here = cmds.pointOnCurve(spine_curve, parameter=param,
                                position=True)
    # Tangent via a small finite-difference around param.
    span = mx - mn
    d = max(1e-6, span * 1e-3)
    p0 = cmds.pointOnCurve(
        spine_curve,
        parameter=max(mn, param - d),
        position=True)
    p1 = cmds.pointOnCurve(
        spine_curve,
        parameter=min(mx, param + d),
        position=True)
    T = _normalize((p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]))
    if _length(T) < 1e-6:
        T = (0.0, 1.0, 0.0)
    # Arbitrary perpendicular normal.
    ref = (0.0, 1.0, 0.0) if abs(T[1]) < 0.9 else (1.0, 0.0, 0.0)
    N = _normalize(_cross(ref, T))
    if _length(N) < 1e-6:
        N = _normalize(_cross((1.0, 0.0, 0.0), T))
    B = _normalize(_cross(T, N))
    pos = (float(p_here[0]), float(p_here[1]), float(p_here[2]))
    return pos, T, N, B


def _create_hair_tie(
    spine_curve: str,
    tail_length: float,
    braid_radius: float,
    name: str,
) -> Optional[str]:
    """Build a small torus around the spine at the tie point
    (u = 1 − tail_length) oriented so the torus axis follows the
    spine tangent. Returns the transform's full path, or None when
    ``tail_length`` is effectively zero (no tail, no tie needed).
    """
    if tail_length <= 1e-4:
        return None
    tie_off = max(0.0, min(0.99, 1.0 - tail_length))
    pos, T, N, B = _sample_spine_frame_at(spine_curve, tie_off)
    # Snug around the pinched strands: their max radial offset at
    # pinch ≈ braid_radius × TAIL_PINCH; tie sits just outside that.
    tie_major_r = float(braid_radius) * 0.30
    tie_section_r = float(braid_radius) * 0.06
    torus = cmds.polyTorus(
        radius=tie_major_r,
        sectionRadius=tie_section_r,
        subdivisionsAxis=16,
        subdivisionsHeight=8,
        name=name,
    )
    torus_xform = torus[0]
    # Default polyTorus axis is +Y — build a world matrix that maps
    # the torus's local Y onto the spine tangent T and place it at
    # the tie point. N / B fill the perpendicular axes so the
    # torus is orthonormally oriented.
    matrix = [
        N[0], N[1], N[2], 0.0,
        T[0], T[1], T[2], 0.0,
        B[0], B[1], B[2], 0.0,
        pos[0], pos[1], pos[2], 1.0,
    ]
    try:
        cmds.xform(torus_xform, matrix=matrix, worldSpace=True)
    except Exception:
        pass
    return torus_xform


# --------------------------------------------------------------------------- #
# Spine live-watching (scriptJob) — makes the braid follow both
# transform moves AND CV edits on the spine.
# --------------------------------------------------------------------------- #

# module-level state — watcher IDs keyed by Braid group UUID, plus
# a "dirty" set used to coalesce bursts of spine edits into a
# single deferred rebuild per idle.
_spine_watcher_jobs: dict = {}
_pending_rebuilds: set = set()


def _spine_shape_of(spine_curve: str) -> Optional[str]:
    """Return the nurbsCurve shape node for ``spine_curve`` (which
    may be a transform or a shape). scriptJob attributeChange needs
    a shape's ``worldSpace[0]`` — the transform doesn't have one."""
    if not spine_curve or not cmds.objExists(spine_curve):
        return None
    if cmds.nodeType(spine_curve) == "nurbsCurve":
        return spine_curve
    shapes = cmds.listRelatives(
        spine_curve, shapes=True, fullPath=True,
        type="nurbsCurve") or []
    return shapes[0] if shapes else None


def _schedule_rebuild(group_uuid: str) -> None:
    """Enqueue a deferred rebuild for ``group_uuid``. Multiple
    triggers within the same idle cycle coalesce to ONE rebuild.
    ``lowestPriority=True`` lets Maya finish its current DG
    evaluation before we start rewriting curve CVs."""
    if group_uuid in _pending_rebuilds:
        return
    _pending_rebuilds.add(group_uuid)
    try:
        cmds.evalDeferred(
            lambda uid=group_uuid: _do_deferred_rebuild(uid),
            lowestPriority=True)
    except Exception:
        # Fallback — do it immediately if evalDeferred is unavailable
        # (e.g. batch mode).
        _do_deferred_rebuild(group_uuid)


def _do_deferred_rebuild(group_uuid: str) -> None:
    _pending_rebuilds.discard(group_uuid)
    matches = cmds.ls(group_uuid) or []
    if not matches:
        return
    group = matches[0]
    if not is_braid_group(group):
        return
    try:
        rebuild_braid(group)
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] spine 変更に追従する rebuild 失敗: "
            "{0}".format(exc))


def _install_spine_watcher(group: str, spine_curve: str) -> None:
    """Register a scriptJob on the spine's ``worldSpace[0]`` output
    so any change (transform OR CV edit) triggers a coalesced
    rebuild of the braid. Removes any prior watcher for this group
    first so repeated calls don't stack listeners."""
    group_uuids = cmds.ls(group, uuid=True) or []
    if not group_uuids:
        return
    group_uuid = group_uuids[0]
    _uninstall_spine_watcher(group_uuid)
    shape = _spine_shape_of(spine_curve)
    if not shape:
        return
    try:
        job_id = cmds.scriptJob(
            attributeChange=[
                shape + ".worldSpace[0]",
                lambda uid=group_uuid: _schedule_rebuild(uid),
            ],
            killWithScene=True,
        )
        _spine_watcher_jobs[group_uuid] = job_id
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] spine watcher 登録失敗: "
            "{0}".format(exc))


def _uninstall_spine_watcher(group_uuid: str) -> None:
    """Kill the scriptJob associated with this Braid group (if
    any). Safe to call when nothing is registered."""
    job_id = _spine_watcher_jobs.pop(group_uuid, None)
    if job_id is None:
        return
    try:
        if cmds.scriptJob(exists=job_id):
            cmds.scriptJob(kill=job_id, force=True)
    except Exception:
        pass


def install_watchers_for_existing_braids() -> None:
    """Walk every Braid group currently in the scene and register
    its spine watcher. Called on UI startup so braids in a freshly-
    opened scene follow spine edits without needing manual
    re-registration."""
    if cmds is None:
        return
    for g in (hair.list_hair_groups() or []):
        if not is_braid_group(g):
            continue
        params = read_braid_params(g)
        if not params or not params.get("spine_uuid"):
            continue
        spine_matches = cmds.ls(params["spine_uuid"]) or []
        if not spine_matches:
            continue
        _install_spine_watcher(g, spine_matches[0])


def _tail_strand_points(
    spine_curve: str,
    phase: float,
    tail_length: float,
    braid_radius: float,
    num_samples: int,
) -> List[Vec3]:
    """Compute world-space points for one tail strand curve. The
    strand starts at the tie point on the spine, spirals slightly
    (angular ``phase``), and follows the pinch → bulge → tip shape
    down to the spine tip. Independent of the woven-braid formula
    — tail strands don't cross each other, they just splay out."""
    tie_off = 1.0 - max(0.0, min(0.99, tail_length))
    if tie_off >= 1.0:
        return []
    # Sample spine specifically across the tail region for tighter
    # control (we don't need to reuse the braid's sample array).
    mn, mx = _spine_param_range(spine_curve)
    span = mx - mn
    points: List[Vec3] = []
    for j in range(num_samples):
        t = float(j) / float(num_samples - 1)  # 0..1 across tail
        u = tie_off + t * (1.0 - tie_off)
        param = mn + span * u
        p = cmds.pointOnCurve(spine_curve, parameter=param,
                              position=True)
        # Local frame at u — use small finite difference for tangent.
        d = max(1e-6, span * 1e-3)
        p0 = cmds.pointOnCurve(
            spine_curve, parameter=max(mn, param - d),
            position=True)
        p1 = cmds.pointOnCurve(
            spine_curve, parameter=min(mx, param + d),
            position=True)
        T = _normalize(
            (p1[0] - p0[0], p1[1] - p0[1], p1[2] - p0[2]))
        if _length(T) < 1e-6:
            T = (0.0, 1.0, 0.0)
        ref = (0.0, 1.0, 0.0) if abs(T[1]) < 0.9 else (1.0, 0.0, 0.0)
        N = _normalize(_cross(ref, T))
        if _length(N) < 1e-6:
            N = _normalize(_cross((1.0, 0.0, 0.0), T))
        B = _normalize(_cross(T, N))
        # Pinch → bulge → tip radius envelope.
        r_shape = _tail_shape(t)
        r = braid_radius * r_shape
        offset_w = r * math.cos(phase)
        offset_d = r * math.sin(phase)
        points.append((
            float(p[0]) + offset_w * N[0] + offset_d * B[0],
            float(p[1]) + offset_w * N[1] + offset_d * B[1],
            float(p[2]) + offset_w * N[2] + offset_d * B[2],
        ))
    return points


def _create_tail_strands(
    spine_curve: str,
    tail_length: float,
    braid_radius: float,
    tail_strand_count: int,
    tail_thickness: float,
    parent_group: Optional[str],
    base_name: str,
) -> List[str]:
    """Generate N tail strand curves and feed them through the hair
    pipeline so each becomes an independent hair strand (with its
    own sweepMeshCreator, its own thickness / taper etc.). Returns
    the list of resulting strand mesh transforms.

    ``parent_group`` is the geom-side Braid group — the created
    meshes get reparented under it so they show up as children of
    the Braid_NN group in the outliner and move with it.
    """
    if tail_length <= 1e-4 or tail_strand_count < 1:
        return []
    tail_count = max(1, int(tail_strand_count))
    tail_samples = max(4, int(C.DEFAULT_BRAID_TAIL_SAMPLES))
    strand_curves: List[str] = []
    for i in range(tail_count):
        phase = 2.0 * math.pi * (float(i) / float(tail_count))
        pts = _tail_strand_points(
            spine_curve, phase, tail_length, braid_radius,
            tail_samples)
        if len(pts) < 2:
            continue
        cname = "{0}_tail{1:02d}_curve".format(base_name, i + 1)
        degree = 3 if len(pts) >= 4 else 1
        strand_curves.append(
            cmds.curve(name=cname, p=pts, degree=degree))

    if not strand_curves:
        return []

    # Push through the standard hair pipeline so each tail strand
    # becomes a first-class hair strand (thickness / taper / colour
    # / group operations all "just work" on them).
    try:
        cmds.select(strand_curves, replace=True)
        hair.create_hair_from_selected_curves(
            thickness=tail_thickness)
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] tail strand の hair 化に失敗: "
            "{0}".format(exc))
        for c in strand_curves:
            if cmds.objExists(c):
                try:
                    cmds.delete(c)
                except Exception:
                    pass
        return []

    tail_meshes = [
        m for m in (cmds.ls(selection=True, long=True) or [])
        if hair._is_hair_strand_transform(m)
    ]

    # Reparent tail meshes under the Braid geom group so the tail
    # moves with the braid and appears together in the outliner.
    # ALSO move each mesh's guide curve into the matching Curve_group
    # container so the split-hierarchy pairing (Geometry_group /
    # Curve_group same-name mirror) that ``hair.move_strand_to_group``
    # normally enforces is preserved for tail strands too — otherwise
    # the tail curves get stranded in Curve_group root while their
    # meshes sit under Braid_NN, breaking the outliner grouping.
    parent_short = ""
    if parent_group:
        parent_short = parent_group.split("|")[-1]
    if parent_group and cmds.objExists(parent_group):
        moved: List[str] = []
        for m in tail_meshes:
            try:
                if parent_short:
                    moved.append(hair.move_strand_to_group(
                        m, parent_short))
                else:
                    r = cmds.parent(m, parent_group) or []
                    moved.append(r[0] if r else m)
            except Exception:
                moved.append(m)
        tail_meshes = moved

    return tail_meshes


def _delete_existing_tail_strands(group: str) -> None:
    """Delete the tail meshes (and their guide curves) referenced
    from the group's stored UUID list. Called before every tail
    (re)creation so we never accumulate orphans."""
    if not cmds.attributeQuery(_ATTR_TAIL_STRAND_UUIDS, node=group,
                                exists=True):
        return
    raw = cmds.getAttr(group + "." + _ATTR_TAIL_STRAND_UUIDS) or ""
    for uid in raw.split("|"):
        if not uid:
            continue
        matches = cmds.ls(uid) or []
        for m in matches:
            if not cmds.objExists(m):
                continue
            # Also delete the guide curve.
            creators = su.sweep_creators_from_nodes([m]) or []
            for c in creators:
                curve = su.curve_from_creator(c)
                if curve and cmds.objExists(curve):
                    try:
                        cmds.delete(curve)
                    except Exception:
                        pass
            try:
                cmds.delete(m)
            except Exception:
                pass


def _update_tail_strands_in_place(
    group: str,
    spine_curve: str,
    tail_length: float,
    braid_radius: float,
    tail_thickness: float,
    tail_mesh_uuids: List[str],
) -> bool:
    """When the tail strand COUNT hasn't changed, update each tail
    strand's guide curve CVs in-place so per-strand hair tweaks
    (thickness override, taper, colour) survive the rebuild.
    Also propagates ``tail_thickness`` to each strand's
    sweepMeshCreator (matches braid-side thickness propagation in
    ``rebuild_braid``). Returns True on success, False when any
    UUID couldn't be resolved (caller should then fall back to full
    delete + recreate)."""
    tail_samples = max(4, int(C.DEFAULT_BRAID_TAIL_SAMPLES))
    count = len(tail_mesh_uuids)
    if count < 1:
        return False
    curves = _find_strand_curves_for_rebuild(tail_mesh_uuids)
    if any(not c or not cmds.objExists(c) for c in curves):
        return False
    for i, curve in enumerate(curves):
        phase = 2.0 * math.pi * (float(i) / float(count))
        pts = _tail_strand_points(
            spine_curve, phase, tail_length, braid_radius,
            tail_samples)
        if len(pts) < 2:
            return False
        _replace_curve_cvs(curve, pts)
    # Propagate the tail-thickness slider value to every tail
    # sweepMeshCreator so the "尾ストランドの初期太さ" slider still
    # takes effect during in-place rebuild — the in-place path used
    # to skip this and the value only landed on full recreate.
    for uid in tail_mesh_uuids:
        matches = cmds.ls(uid) or []
        if not matches:
            continue
        creators = su.sweep_creators_from_nodes(
            [matches[0]]) or []
        for c in creators:
            try:
                cmds.setAttr(c + ".scaleProfileUniform", True)
                cmds.setAttr(c + ".scaleProfileX", tail_thickness)
            except Exception:
                pass
    return True


def _resolve_spine_transform(spine_curve: str) -> Optional[str]:
    """Given a curve (shape or transform), return its transform's
    full path. Constraints need to attach to a transform, not a
    shape, and users occasionally hand us a shape by accident."""
    if not spine_curve or not cmds.objExists(spine_curve):
        return None
    if cmds.nodeType(spine_curve) == "nurbsCurve":
        parents = cmds.listRelatives(
            spine_curve, parent=True, fullPath=True) or []
        return parents[0] if parents else None
    # Transform — verify it holds a nurbsCurve shape.
    shapes = cmds.listRelatives(
        spine_curve, shapes=True, fullPath=True,
        type="nurbsCurve") or []
    if shapes:
        return spine_curve
    return spine_curve


def _constrain_braid_to_spine(
    braid_group_geom: str,
    spine_curve: str,
) -> None:
    """Attach ``parentConstraint``s so the whole Braid group follows
    the spine's transform (translate / rotate) automatically.

    This handles the common "I moved my spine and the braid stayed
    behind" case. CV edits on the spine still don't update the
    braid shape — that needs an explicit slider drag or re-create;
    live-procedural reshaping was ruled out as too complex for
    this pass (no custom plugin nodes per HANDOFF.md §11).

    Constrains BOTH the geom-side Braid group and its curve-side
    mirror so the strand curves come along too — the
    sweepMeshCreator will then keep the meshes in sync with the
    moved curves.
    """
    if not cmds.objExists(braid_group_geom):
        return
    spine_xform = _resolve_spine_transform(spine_curve)
    if not spine_xform or not cmds.objExists(spine_xform):
        return
    try:
        cmds.parentConstraint(
            spine_xform, braid_group_geom,
            maintainOffset=True, weight=1.0)
    except Exception as exc:
        cmds.warning(
            "[maya_hair_tool] Braid geom 側 parentConstraint 失敗: "
            "{0}".format(exc))
    curve_side = hair._curve_group_side(braid_group_geom)
    if curve_side and cmds.objExists(curve_side):
        try:
            cmds.parentConstraint(
                spine_xform, curve_side,
                maintainOffset=True, weight=1.0)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] Braid curve 側 parentConstraint "
                "失敗: {0}".format(exc))


def _remove_existing_braid_constraints(braid_group_geom: str) -> None:
    """Delete any parentConstraints currently driving the Braid
    group (both sides). Called before a rebuild-time re-constrain
    so the maintainOffset math doesn't accumulate stale offsets
    across multiple rebuilds."""
    for target in (braid_group_geom,
                   hair._curve_group_side(braid_group_geom)):
        if not target or not cmds.objExists(target):
            continue
        try:
            constraints = cmds.listRelatives(
                target, children=True, type="parentConstraint",
                fullPath=True) or []
        except Exception:
            constraints = []
        for c in constraints:
            try:
                cmds.delete(c)
            except Exception:
                pass


def _delete_existing_tie(group: str) -> None:
    """Delete the hair-tie mesh referenced from the group's
    stored UUID (if any). No-op if the UUID is empty or the node
    has already been removed."""
    if not cmds.attributeQuery(_ATTR_TIE_UUID, node=group,
                                exists=True):
        return
    tie_uuid = cmds.getAttr(group + "." + _ATTR_TIE_UUID) or ""
    if not tie_uuid:
        return
    matches = cmds.ls(tie_uuid) or []
    for n in matches:
        if cmds.objExists(n):
            try:
                cmds.delete(n)
            except Exception:
                pass


def find_containing_braid_group(node: str) -> Optional[str]:
    """Given any node (a braid group itself, a strand mesh under it,
    or a strand curve under its Curve_group side), return the geom-
    side Braid group full path — or None if we can't map back to one.
    """
    if cmds is None or not node or not cmds.objExists(node):
        return None
    if is_braid_group(node):
        return node
    # Walk parents up to at most 4 levels (strand mesh → user group →
    # Geometry_group → HairGroup).
    cur = node
    for _ in range(4):
        parents = cmds.listRelatives(
            cur, parent=True, fullPath=True) or []
        if not parents:
            break
        cur = parents[0]
        if is_braid_group(cur):
            return cur
    return None


def _find_strand_curves_for_rebuild(
    strand_mesh_uuids: List[str],
) -> List[str]:
    """Look up the guide curve for each stored mesh UUID, preserving
    order. Empty entries where the UUID no longer resolves."""
    curves: List[str] = []
    for uid in strand_mesh_uuids:
        mesh_matches = cmds.ls(uid) or []
        if not mesh_matches:
            curves.append("")
            continue
        creators = su.sweep_creators_from_nodes([mesh_matches[0]]) or []
        if not creators:
            curves.append("")
            continue
        curve = su.curve_from_creator(creators[0])
        curves.append(curve or "")
    return curves


def _replace_curve_cvs(curve: str, points: List[Vec3]) -> None:
    """Replace the CVs of an existing NURBS curve in-place using
    ``cmds.curve(replace=True)``. The connected sweepMeshCreator
    picks up the change automatically."""
    cmds.curve(curve, replace=True, point=points,
               degree=3 if len(points) >= 4 else 1)


def rebuild_braid(group: str, **overrides) -> None:
    """Live-rebuild the 3 strand curves of a Braid group using the
    stored params plus any ``overrides``. sweepMeshCreator auto-
    updates from the modified curves. Also propagates
    ``strand_thickness`` to each sweepMeshCreator's width attr so
    the change is visible without a full curve rebuild.

    Raises RuntimeError if the group isn't a braid, if the spine
    can't be located (deleted / renamed away from its UUID), or if
    fewer than 3 strand curves are recoverable.
    """
    params = read_braid_params(group)
    if params is None:
        raise RuntimeError(
            "Braid metadata が見つかりません: {0}".format(group))
    params.update(overrides)

    spine_matches = cmds.ls(params["spine_uuid"]) or []
    if not spine_matches:
        raise RuntimeError(
            "スパインカーブが見つかりません (削除された可能性)。UUID: "
            "{0}".format(params["spine_uuid"]))
    spine_curve = spine_matches[0]

    # Purge any legacy v0.4.8 parentConstraints on this Braid group.
    # v0.4.9 switched to a scriptJob-driven rebuild that writes strand
    # CVs in WORLD space — a live parentConstraint on the group would
    # re-interpret those world coords in the constrained parent's
    # local space, giving a visible double-transform. Idempotent when
    # no constraint is present.
    _remove_existing_braid_constraints(group)

    curves = _find_strand_curves_for_rebuild(
        params["strand_mesh_uuids"])
    live_curves = [c for c in curves if c and cmds.objExists(c)]
    if len(live_curves) < 3:
        raise RuntimeError(
            "Braid ストランドカーブが 3 本揃っていません (現在 {0} 本)。"
            "ストランドが手動削除された可能性があります。".format(
                len(live_curves)))

    # Recompute frames using the same Nyquist bump as fresh creation.
    num_samples = C.DEFAULT_BRAID_SAMPLES
    coarse = _sample_positions(spine_curve, num_samples)
    spine_length = _arc_length(coarse)
    if spine_length < 1e-6:
        raise RuntimeError(
            "スパインカーブの長さがゼロ相当のため rebuild できません。")
    total_turns = params["turns_per_length"] * spine_length
    density_scale = _cumulative_density(
        1.0,
        params["density_top"],
        params["density_middle"],
        params["density_bottom"])
    effective_total_turns = total_turns * max(1.0, density_scale)
    required = int(math.ceil(
        abs(effective_total_turns) * C.BRAID_SAMPLES_PER_TURN))
    if required > num_samples:
        num_samples = required
        positions = _sample_positions(spine_curve, num_samples)
    else:
        positions = coarse
    tangents = _tangents_from_positions(positions)
    normals, binormals = _parallel_transport_frames(tangents)

    # Update each strand curve's CVs and propagate thickness.
    for idx, curve in enumerate(curves[:3]):
        if not curve or not cmds.objExists(curve):
            continue
        phase = 2.0 * math.pi * (float(idx) / 3.0)
        n = len(positions)
        # v0.5.0: braid strand only covers 0..tie_off; the tail is
        # a separate set of hair strands. Match _build_strand_curve's
        # loop so rebuild produces the same shape as fresh creation.
        tie_off = 1.0 - max(0.0, min(0.99, params["tail_length"]))
        points: List[Vec3] = []
        for j in range(n):
            u = float(j) / float(n - 1)
            if u > tie_off:
                break
            w, d = _strand_offset_at(
                u,
                phase,
                total_turns,
                params["radius"],
                params["tip_taper"],
                params["depth_ratio"],
                params["density_top"],
                params["density_middle"],
                params["density_bottom"],
            )
            N = normals[j]
            B = binormals[j]
            points.append((
                positions[j][0] + w * N[0] + d * B[0],
                positions[j][1] + w * N[1] + d * B[1],
                positions[j][2] + w * N[2] + d * B[2],
            ))
        # Snap the endpoint exactly to tie_off when the tie falls
        # between samples — matches fresh-creation behaviour.
        if points and tie_off > 0.0 and tie_off < 1.0:
            _append_endpoint_at(
                points, positions, normals, binormals, tie_off,
                phase, total_turns, params["radius"],
                params["tip_taper"], params["depth_ratio"],
                params["density_top"], params["density_middle"],
                params["density_bottom"])
        if len(points) < 2:
            # Degenerate tail_length ≈ 1.0 leaves the braid with
            # nothing to draw; skip this strand rather than fail
            # the whole rebuild.
            continue
        _replace_curve_cvs(curve, points)

    # Propagate strand_thickness to each mesh's sweepMeshCreator.
    thickness = params["strand_thickness"]
    for uid in params["strand_mesh_uuids"]:
        mesh_matches = cmds.ls(uid) or []
        if not mesh_matches:
            continue
        creators = su.sweep_creators_from_nodes(
            [mesh_matches[0]]) or []
        for c in creators:
            # Match hair.py's thickness convention: uniform=True +
            # scaleProfileX carries the value (scaleProfileY follows
            # via the uniform toggle).
            try:
                cmds.setAttr(c + ".scaleProfileUniform", True)
                cmds.setAttr(c + ".scaleProfileX", thickness)
            except Exception:
                pass

    # Update stored params so the next slider change reads current.
    _stamp_braid_params(
        group, params["spine_uuid"],
        params["strand_mesh_uuids"], params)

    # Tail strands — try in-place CV update first (preserves per-
    # strand hair tweaks like thickness / taper / colour). Falls
    # back to full delete + recreate when the count changed OR
    # when any stored tail UUID no longer resolves.
    base_name_hint = group.split("|")[-1]
    existing_tail_uuids = params.get("tail_mesh_uuids") or []
    desired_count = int(params.get(
        "tail_strand_count",
        C.DEFAULT_BRAID_TAIL_STRAND_COUNT))
    tail_thickness = float(params.get(
        "tail_thickness", C.DEFAULT_BRAID_TAIL_THICKNESS))
    tail_updated_in_place = False
    if (existing_tail_uuids
            and len(existing_tail_uuids) == desired_count
            and params["tail_length"] > 1e-4):
        tail_updated_in_place = _update_tail_strands_in_place(
            group, spine_curve, params["tail_length"],
            params["radius"], tail_thickness, existing_tail_uuids)
        if tail_updated_in_place:
            # In-place path skips _stamp_tail_meta by design (UUIDs
            # unchanged) — but ``tail_thickness`` may have changed
            # and must be persisted for the next selection-sync.
            _stamp_tail_meta(
                group, existing_tail_uuids, desired_count,
                tail_thickness)
    if not tail_updated_in_place:
        _delete_existing_tail_strands(group)
        new_tail_meshes = _create_tail_strands(
            spine_curve, params["tail_length"],
            params["radius"], desired_count, tail_thickness,
            group, base_name_hint)
        new_tail_uuids: List[str] = []
        for m in new_tail_meshes:
            try:
                u = cmds.ls(m, uuid=True) or []
                if u:
                    new_tail_uuids.append(u[0])
            except Exception:
                pass
        _stamp_tail_meta(group, new_tail_uuids, desired_count,
                          tail_thickness)

    # Hair tie — replace in-place so radius / position stay in sync
    # with the current params. Delete any existing tie, then build a
    # fresh one if the current tail_length calls for it. Reuse the
    # already-resolved ``spine_curve`` from above.
    _delete_existing_tie(group)
    tie_name_hint = "{0}_tie".format(group.split("|")[-1])
    tie_xform = _create_hair_tie(
        spine_curve, params["tail_length"],
        params["radius"], tie_name_hint)
    if tie_xform:
        try:
            reparented = cmds.parent(tie_xform, group) or []
            if reparented:
                tie_xform = reparented[0]
        except RuntimeError:
            pass
        uids = cmds.ls(tie_xform, uuid=True) or []
        if uids:
            _stamp_tie_uuid(group, uids[0])
    else:
        # No tail → no tie. Clear the stored UUID so we don't leave
        # stale metadata behind.
        _stamp_tie_uuid(group, "")

    # Re-install the spine watcher (scriptJob) after rebuild —
    # covers the case where the spine reference changed between
    # rebuilds. Also removes stale watchers so we don't accumulate
    # duplicates across rebuilds.
    _install_spine_watcher(group, spine_curve)


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
    depth_ratio: float = C.DEFAULT_BRAID_DEPTH_RATIO,
    tail_length: float = C.DEFAULT_BRAID_TAIL_LENGTH,
    tail_strand_count: int = C.DEFAULT_BRAID_TAIL_STRAND_COUNT,
    tail_thickness: float = C.DEFAULT_BRAID_TAIL_THICKNESS,
    density_top: float = C.DEFAULT_BRAID_DENSITY_TOP,
    density_middle: float = C.DEFAULT_BRAID_DENSITY_MIDDLE,
    density_bottom: float = C.DEFAULT_BRAID_DENSITY_BOTTOM,
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

    # Coarse sample first — just to estimate arc length. This is
    # what tells us how many turns the braid will actually complete
    # (``turns_per_length`` is a rate); we can't set a sensible
    # final ``num_samples`` until we know that.
    coarse_positions = _sample_positions(spine_curve, num_samples)
    spine_length = _arc_length(coarse_positions)
    if spine_length < 1e-6:
        raise RuntimeError(
            "スパインカーブの長さがゼロ相当です。CV が重複していないか "
            "確認してください。")

    # turns_per_length is a rate; total turns depends on the length.
    total_turns = turns_per_length * spine_length

    # Density curve boosts (or reduces) effective total turns —
    # cumulative_density(1) is the average multiplier over the
    # spine. Account for that when computing the Nyquist floor so
    # a heavily-densified region still gets enough samples.
    density_scale = _cumulative_density(
        1.0, density_top, density_middle, density_bottom)
    effective_total_turns = total_turns * max(1.0, density_scale)

    # Nyquist / aliasing floor: bump ``num_samples`` up so the helix
    # gets at least BRAID_SAMPLES_PER_TURN points per revolution.
    # Without this, a long spine + high turns setting produces a
    # zig-zag instead of a smooth spiral because the offset direction
    # wraps around faster than we're sampling it.
    required = int(math.ceil(
        abs(effective_total_turns) * C.BRAID_SAMPLES_PER_TURN))
    if required > num_samples:
        num_samples = required
        positions = _sample_positions(spine_curve, num_samples)
        spine_length = _arc_length(positions)
    else:
        positions = coarse_positions

    tangents = _tangents_from_positions(positions)
    normals, binormals = _parallel_transport_frames(tangents)

    # Undo chunking so 3-strand generation is one Ctrl+Z.
    cmds.undoInfo(openChunk=True, chunkName="createBraid")
    try:
        base_name = "braid_{0:03d}".format(_next_braid_index_hint())
        strand_curves: List[str] = []
        for i in range(3):
            phase = 2.0 * math.pi * (float(i) / 3.0)
            cname = "{0}_s{1}_curve".format(base_name, i + 1)
            built = _build_strand_curve(
                positions, normals, binormals,
                phase_base=phase,
                turns=total_turns,
                radius=radius,
                tip_taper=tip_taper,
                depth_ratio=depth_ratio,
                density_top=density_top,
                density_middle=density_middle,
                density_bottom=density_bottom,
                tail_length=tail_length,
                name=cname,
            )
            if not built:
                # Degenerate config produced fewer than 2 CVs.
                # Roll back anything already built and bail out.
                _cleanup_partial_braid(strand_curves)
                raise RuntimeError(
                    "三つ編みのカーブを作成できません — "
                    "tail_length が大きすぎる可能性があります "
                    "(braid 部分が短すぎて 2 点未満)。")
            strand_curves.append(built)

        # Feed the three strand curves through the standard hair
        # pipeline. Select-then-call keeps this decoupled from any
        # refactor of create_hair_from_selected_curves.
        #
        # If mesh creation partially fails mid-loop (e.g. Maya's
        # sweep plugin refuses to load on strand 2 of 3), clean up
        # every curve/mesh we produced so the user gets an all-or-
        # nothing result rather than a lopsided half-braid.
        cmds.select(strand_curves, replace=True)
        try:
            hair.create_hair_from_selected_curves(
                thickness=strand_thickness,
            )
        except Exception as exc:
            _cleanup_partial_braid(strand_curves)
            raise RuntimeError(
                "三つ編み生成中にエラーが発生し、生成途中のカーブ / "
                "メッシュを破棄しました: {0}".format(exc))

        # ``create_hair_from_selected_curves`` selects the resulting
        # mesh transforms — grab those now.
        strand_meshes = cmds.ls(selection=True, long=True) or []
        # Filter to strand-mesh transforms (defensive — should be
        # exactly 3).
        strand_meshes = [
            m for m in strand_meshes
            if hair._is_hair_strand_transform(m)
        ]
        if len(strand_meshes) < 3:
            _cleanup_partial_braid(strand_curves, keep_meshes=strand_meshes)
            raise RuntimeError(
                "三つ編み生成に失敗しました (期待 3 本 / 実際 {0} 本)。"
                "sweepMeshCreator プラグインがロードされているか "
                "確認してください。".format(len(strand_meshes)))

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

        # Stamp braid metadata on the geom-side group so the UI can
        # sync sliders + live-rebuild when the group is re-selected.
        # Falls back to per-strand attributes if grouping was disabled
        # so the same lookup helpers still work.
        spine_uuid = ""
        try:
            uuids = cmds.ls(spine_curve, uuid=True) or []
            if uuids:
                spine_uuid = uuids[0]
        except Exception:
            pass
        strand_mesh_uuids: List[str] = []
        for m in strand_meshes:
            try:
                mu = cmds.ls(m, uuid=True) or []
                if mu:
                    strand_mesh_uuids.append(mu[0])
            except Exception:
                pass
        params_dict = {
            "turns_per_length": turns_per_length,
            "radius": radius,
            "strand_thickness": strand_thickness,
            "tip_taper": tip_taper,
            "depth_ratio": depth_ratio,
            "tail_length": tail_length,
            "density_top": density_top,
            "density_middle": density_middle,
            "density_bottom": density_bottom,
            "tail_strand_count": tail_strand_count,
            "tail_thickness": tail_thickness,
        }
        stamp_target = None
        if group and strand_meshes:
            parents = cmds.listRelatives(
                strand_meshes[0], parent=True, fullPath=True) or []
            if parents:
                stamp_target = parents[0]
        if stamp_target and cmds.objExists(stamp_target):
            _stamp_braid_params(
                stamp_target, spine_uuid,
                strand_mesh_uuids, params_dict)

        # Tail — N separate hair strands below the tie. Each
        # becomes a first-class hair strand so the user can tweak
        # thickness / taper / colour individually via the normal
        # hair sliders. Parented under the Braid geom group so
        # they group visually with the woven strands.
        tail_meshes = _create_tail_strands(
            spine_curve, tail_length, radius,
            tail_strand_count, tail_thickness,
            stamp_target, base_name)
        tail_uuids: List[str] = []
        for m in tail_meshes:
            try:
                u = cmds.ls(m, uuid=True) or []
                if u:
                    tail_uuids.append(u[0])
            except Exception:
                pass
        if stamp_target and cmds.objExists(stamp_target):
            _stamp_tail_meta(
                stamp_target, tail_uuids, tail_strand_count,
                tail_thickness)

        # Hair tie — a small torus around the spine at the tie
        # point. Only created when there IS a tail (nothing to tie
        # otherwise). Parented under the same Braid group so it
        # moves with the strands, and its UUID is stamped on the
        # group so rebuild can update it later.
        tie_name_hint = "{0}_tie".format(base_name)
        tie_xform = _create_hair_tie(
            spine_curve, tail_length, radius, tie_name_hint)
        if tie_xform and stamp_target and cmds.objExists(stamp_target):
            try:
                reparented = cmds.parent(
                    tie_xform, stamp_target) or []
                if reparented:
                    tie_xform = reparented[0]
            except RuntimeError:
                pass
            tie_uuids = cmds.ls(tie_xform, uuid=True) or []
            if tie_uuids:
                _stamp_tie_uuid(stamp_target, tie_uuids[0])

        # Install a spine watcher (scriptJob) so subsequent
        # transform moves AND CV edits on the spine trigger a live
        # rebuild of the braid. Coalesces bursts into one deferred
        # rebuild per idle cycle.
        if stamp_target and cmds.objExists(stamp_target):
            _install_spine_watcher(stamp_target, spine_curve)

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

# NOTE (design): braid strands are baked from the spine at creation
# time — the spine is not connected via a DG dependency. Editing the
# spine CV after creation does NOT update the braid; the user must
# re-run "三つ編みを作成" against the edited spine. Chose baking over
# live procedural per the design decision recorded in HANDOFF.md.
_braid_call_counter = [0]


def _next_braid_index_hint() -> int:
    _braid_call_counter[0] += 1
    return _braid_call_counter[0]
