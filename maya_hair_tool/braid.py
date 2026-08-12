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

try:
    from maya.api import OpenMaya as _om
except ImportError:
    _om = None

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


def _default_perpendicular(t: Vec3) -> Vec3:
    """Any unit vector perpendicular to ``t``. Used as a last-
    resort when we can't derive a smarter reference from the
    curve shape."""
    ref = (0.0, 1.0, 0.0) if abs(t[1]) < 0.9 else (1.0, 0.0, 0.0)
    n = _normalize(_cross(ref, t))
    if _length(n) < 1e-6:
        n = _normalize(_cross((1.0, 0.0, 0.0), t))
    return n


def _pick_initial_normal(tangents: List[Vec3], t0: Vec3) -> Vec3:
    """Pick n0 to be perpendicular to the curve's dominant plane.

    Scans all tangents and finds the pair with the largest cross
    product magnitude — that pair spans a plane and its cross gives
    the plane's normal. For a planar curve every pair returns the
    exact plane normal (up to sign); for a 3D curve we get the
    single direction that best approximates the "average" curve
    normal. Then project onto t0's perpendicular plane and
    normalise so the result is strictly perpendicular to t0.

    Falls back to ``_default_perpendicular`` on near-straight
    tangent arrays (no plane to detect)."""
    if len(tangents) < 2:
        return _default_perpendicular(t0)
    max_cross_len = 0.0
    plane_normal = None
    for i in range(len(tangents) - 1):
        for j in range(i + 1, min(len(tangents), i + 5)):
            c = _cross(tangents[i], tangents[j])
            cl = _length(c)
            if cl > max_cross_len:
                max_cross_len = cl
                plane_normal = c
    if plane_normal is None or max_cross_len < 0.05:
        return _default_perpendicular(t0)
    # Make plane_normal strictly perpendicular to t0.
    n = _sub(plane_normal, _scale(t0, _dot(t0, plane_normal)))
    nl = _length(n)
    if nl < 1e-6:
        return _default_perpendicular(t0)
    return _scale(n, 1.0 / nl)


def _parallel_transport_frames(
    tangents: List[Vec3],
    positions: Optional[List[Vec3]] = None,
) -> Tuple[List[Vec3], List[Vec3]]:
    """Rotation Minimising Frame (RMF) computed via Wang et al
    2008's Double Reflection method.

    Function name kept for callsite compatibility. When
    ``positions`` are supplied (both callsites do) this runs the
    proper Double Reflection algorithm, which is the industry-
    standard RMF and provably minimises rotation around the
    tangent between adjacent samples. Naive projection-based or
    axis-angle "parallel transport" implementations both
    accumulate residual twist on sharply-bent curves — that's what
    caused the "braid rotates when you bend the spine" complaint
    that v0.5.23 → v0.5.25 kept trying to fix.

    The algorithm: for each step (x_i, t_i) → (x_{i+1}, t_{i+1})
      1. Reflect (r_i, t_i) across the plane bisected by
         v1 = x_{i+1} − x_i     → (r_L, t_L)
      2. Reflect r_L across the plane bisected by
         v2 = t_{i+1} − t_L     → r_{i+1}
    The double reflection is provably the minimum-rotation frame
    transport for arbitrary curvature between samples.

    ``positions=None`` falls back to prev-normal projection (v0.5.25
    behaviour) — good enough for straight-ish curves, worse on
    sharp bends but the callers always pass positions so this
    path is only for defensive tests.
    """
    n_samples = len(tangents)
    normals: List[Vec3] = []
    binormals: List[Vec3] = []
    if n_samples == 0:
        return normals, binormals

    # Initial normal choice matters — RMF only guarantees minimum
    # ROTATION between adjacent samples, not zero total rotation.
    # If n0 happens to lie inside the curve's plane (rather than
    # being perpendicular to it), every subsequent RMF sample has
    # to rotate to keep n perpendicular to t — the braid's flat
    # side ends up rotated at the tip vs the root even for a
    # perfectly planar curve.
    #
    # Fix: pick n0 to be the curve's approximate plane normal.
    # For a planar spine this is exactly perpendicular to every
    # tangent, so RMF preserves it with zero rotation. For a 3D
    # spine there's no true plane — pick the direction of maximum
    # cross-product magnitude across sampled tangents (best
    # available proxy for "average" curve normal).
    t0 = tangents[0]
    r0 = _pick_initial_normal(tangents, t0)
    normals.append(r0)
    binormals.append(_normalize(_cross(t0, r0)))

    have_positions = (
        positions is not None and len(positions) == n_samples)

    for i in range(1, n_samples):
        t_prev = tangents[i - 1]
        t_curr = tangents[i]
        r_prev = normals[i - 1]

        if have_positions:
            # Wang 2008 Double Reflection.
            x_prev = positions[i - 1]
            x_curr = positions[i]
            v1 = _sub(x_curr, x_prev)
            c1 = _dot(v1, v1)
            if c1 < 1e-12:
                r_L = r_prev
                t_L = t_prev
            else:
                factor1 = 2.0 / c1
                r_L = _sub(
                    r_prev,
                    _scale(v1, factor1 * _dot(v1, r_prev)))
                t_L = _sub(
                    t_prev,
                    _scale(v1, factor1 * _dot(v1, t_prev)))
            v2 = _sub(t_curr, t_L)
            c2 = _dot(v2, v2)
            if c2 < 1e-12:
                r_next = r_L
            else:
                factor2 = 2.0 / c2
                r_next = _sub(
                    r_L, _scale(v2, factor2 * _dot(v2, r_L)))
        else:
            # Projection-only fallback (v0.5.25 behaviour) —
            # only used when the caller didn't pass positions.
            r_next = _sub(
                r_prev, _scale(t_curr, _dot(r_prev, t_curr)))

        # Ensure r_next is perpendicular to t_curr and unit-length
        # (numerical safety after either path).
        r_next = _sub(
            r_next, _scale(t_curr, _dot(t_curr, r_next)))
        rn_len = _length(r_next)
        if rn_len < 1e-6:
            fallback = (
                (0.0, 1.0, 0.0) if abs(t_curr[1]) < 0.9
                else ((1.0, 0.0, 0.0) if abs(t_curr[0]) < 0.9
                      else (0.0, 0.0, 1.0)))
            r_next = _sub(
                fallback,
                _scale(t_curr, _dot(fallback, t_curr)))
            rn_len = _length(r_next)
            if rn_len < 1e-6:
                r_next = (0.0, 1.0, 0.0)
                rn_len = 1.0
        r_next = _scale(r_next, 1.0 / rn_len)
        normals.append(r_next)
        binormals.append(_normalize(_cross(t_curr, r_next)))

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


_BRAID_TIE_SQUEEZE_ZONE = 0.10  # fraction of the spine over which the
                                # elastic-squeeze applies (right before tie_off)
_BRAID_TIE_SQUEEZE_TARGET = 0.30  # radius multiplier at exactly tie_off —
                                  # matches the tail's pinch value so the
                                  # braid → tail transition reads continuous


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
    tail_length: float = 0.0,
    taper_ramp=None,
) -> tuple:
    """Return the (width, depth) offset in the (N, B) plane for
    one BRAID (woven) strand at parameter u ∈ [0, 1].

    Taper: when ``taper_ramp`` is provided (list of ``(pos, value)``
    tuples) the strand radius is multiplied by that piecewise-linear
    envelope. Falls back to the old single-scalar ``tip_taper``
    (linear ``1 − tip_taper·u``) when no ramp is supplied — kept
    for legacy Braid groups that don't yet have the ramp attr.

    Tie squeeze: when ``tail_length > 0`` an additional multiplier
    pinches the braid inward over the last
    ``_BRAID_TIE_SQUEEZE_ZONE`` of the spine so the woven strands
    narrow down to the tail's starting radius (the elastic).
    """
    cum = _cumulative_density(
        u, density_top, density_middle, density_bottom)
    theta = phase_base + 2.0 * math.pi * turns * cum
    if taper_ramp:
        taper = max(0.0, _sample_taper_ramp(u, taper_ramp))
    else:
        taper = max(0.0, 1.0 - tip_taper * u)
    # Elastic squeeze — see docstring.
    if tail_length > 1e-4:
        tie_off = 1.0 - max(0.0, min(0.99, tail_length))
        squeeze_start = tie_off - _BRAID_TIE_SQUEEZE_ZONE
        if u >= squeeze_start:
            span = max(1e-6, tie_off - squeeze_start)
            t = max(0.0, min(1.0, (u - squeeze_start) / span))
            # 1.0 at squeeze_start → SQUEEZE_TARGET at tie_off.
            squeeze = 1.0 - (1.0 - _BRAID_TIE_SQUEEZE_TARGET) * t
            taper *= squeeze
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
                         density_bottom, tail_length=0.0,
                         taper_ramp=None):
    p, N, B = _sample_frame_interpolated(
        u, positions, normals, binormals)
    w, d = _strand_offset_at(
        u, phase_base, turns, radius, tip_taper, depth_ratio,
        density_top, density_middle, density_bottom,
        tail_length=tail_length, taper_ramp=taper_ramp)
    points.append((
        p[0] + w * N[0] + d * B[0],
        p[1] + w * N[1] + d * B[1],
        p[2] + w * N[2] + d * B[2],
    ))


# Tail CV template extracted from the hand-authored reference
# ``ma/test001.ma`` (``braid_001_tail01_curve``). Each pair is
# ``(t_along_tail, radial_offset_×braid_radius)``.
#
# CRUCIAL: the ``t`` values are NOT uniform. The reference author
# placed CVs where they were needed to shape the silhouette — dense
# just after the tie (0.204) to lock the puff peak, dense again
# near the pinch (0.886..0.931), then a long stretch to the tip.
# Sampling uniformly and returning these values at uniform t (what
# v0.5.12–v0.5.14 did) produced a curve whose NURBS interpolation
# looked "distorted" relative to the reference — the Y spacing of
# the CVs was wrong even though the radial values matched.
# ``_tail_strand_points`` now iterates this table directly so each
# generated CV lands at the reference's exact spine parameter AND
# at the reference's exact radial offset.
_TAIL_TEMPLATE = (
    (0.000, 0.210),   # tie-adjacent, pinched inside elastic
    (0.204, 0.410),   # ▲ puff peak (compact — near the top)
    (0.413, 0.310),
    (0.459, 0.270),
    (0.490, 0.250),   # ▽ shoulder — hair still visible full width
    (0.552, 0.190),
    (0.666, 0.120),
    (0.809, 0.075),
    (0.886, 0.041),
    (0.911, 0.034),   # ▽ near-converged on spine
    (0.931, 0.043),
    (1.000, 0.114),   # ▲ tiny tip flare (wispy ends)
)


def _tail_shape(t: float) -> float:
    """Backwards-compatible interpolator over ``_TAIL_TEMPLATE`` —
    kept for any caller that still asks for the shape at an
    arbitrary t. Fresh strand generation and rebuild both iterate
    the template directly (see ``_tail_strand_points``)."""
    knots = _TAIL_TEMPLATE
    if t <= knots[0][0]:
        return knots[0][1]
    if t >= knots[-1][0]:
        return knots[-1][1]
    for i in range(len(knots) - 1):
        t0, v0 = knots[i]
        t1, v1 = knots[i + 1]
        if t <= t1:
            u = (t - t0) / (t1 - t0)
            return v0 + (v1 - v0) * u
    return knots[-1][1]


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
    taper_ramp=None,
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
            density_top, density_middle, density_bottom,
            tail_length=tail_length, taper_ramp=taper_ramp)
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
            density_top, density_middle, density_bottom,
            tail_length=tail_length, taper_ramp=taper_ramp)

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
_ATTR_TAIL_TIP_TAPER = "braidTailTipTaper"
_ATTR_TAPER_RAMP = "braidTaperRamp"      # str, "pos:val|pos:val|..."


def _default_braid_taper_ramp() -> List[tuple]:
    """Default 3-point taper ramp — approximates the old
    ``tip_taper=0.6`` linear behaviour (root full, middle 0.7,
    tip 0.4) so first-generation braids look similar to what
    v0.5.x users are used to."""
    return [(0.0, 1.0), (0.5, 0.7), (1.0, 0.4)]


def _encode_ramp(entries) -> str:
    """Pipe-joined ``pos:val`` string for stamping on the group."""
    return "|".join(
        "{0:.4f}:{1:.4f}".format(float(p), float(v))
        for p, v in entries)


def _decode_ramp(s: str) -> List[tuple]:
    """Parse the stamped ramp string back into a sorted list of
    ``(position, value)`` tuples."""
    entries: List[tuple] = []
    for token in (s or "").split("|"):
        if not token:
            continue
        try:
            p, v = token.split(":")
            entries.append((float(p), float(v)))
        except (ValueError, TypeError):
            continue
    entries.sort(key=lambda e: e[0])
    return entries


def _sample_taper_ramp(u: float, ramp) -> float:
    """Piecewise-linear evaluation of the taper ramp at ``u`` ∈
    [0, 1]. Clamped to first/last knot outside the range."""
    if not ramp:
        return 1.0
    uu = max(0.0, min(1.0, float(u)))
    if uu <= ramp[0][0]:
        return ramp[0][1]
    if uu >= ramp[-1][0]:
        return ramp[-1][1]
    for i in range(len(ramp) - 1):
        p0, v0 = ramp[i]
        p1, v1 = ramp[i + 1]
        if uu <= p1:
            t = (uu - p0) / max(1e-9, p1 - p0)
            return v0 + (v1 - v0) * t
    return ramp[-1][1]


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
                      tail_thickness: float,
                      tail_tip_taper: float) -> None:
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
    _ensure_float_attr(group, _ATTR_TAIL_TIP_TAPER)
    cmds.setAttr(group + "." + _ATTR_TAIL_TIP_TAPER,
                 float(tail_tip_taper))


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
            "tail_tip_taper":
                _read_float_or(_ATTR_TAIL_TIP_TAPER,
                               C.DEFAULT_BRAID_TAIL_TIP_TAPER),
            "taper_ramp":
                (_decode_ramp(
                    cmds.getAttr(group + "." + _ATTR_TAPER_RAMP) or "")
                 if cmds.attributeQuery(
                     _ATTR_TAPER_RAMP, node=group, exists=True)
                 else _default_braid_taper_ramp()),
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
    # Torus dimensions — sized so the elastic wraps snugly around
    # the PINCHED hair at the tie point (where the braid squeeze
    # + tail pinch both bring the strand centres down to ~0.20 ×
    # braid_radius, plus strand thickness on either side).
    #
    # Previous values (0.30 / 0.06) rendered a tiny ring that
    # barely showed up next to the braid — the user asked for a
    # larger, more prominent tie.
    tie_major_r = float(braid_radius) * 0.65
    tie_section_r = float(braid_radius) * 0.12
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
# Throttle live rebuild during drag to at most one every
# _MIN_REBUILD_INTERVAL seconds per Braid group. The API 2.0
# NodeDirtyPlug callback fires many times per drag frame — running
# a full rebuild every callback saturates the main thread and
# makes the drag feel laggy. Keyed by group UUID + last-run epoch.
_MIN_REBUILD_INTERVAL = 0.05  # seconds — 20 rebuilds/sec cap
_last_rebuild_time: dict = {}


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
    """Run a rebuild SYNCHRONOUSLY in the scriptJob callback.

    Previously deferred via ``evalDeferred`` so the rebuild ran
    outside the attributeChange callback context. Turns out Maya
    doesn't run deferred callbacks in the middle of an interactive
    CV drag — they queue up and all drain at once when the drag
    releases. Result: the braid didn't visually track the spine
    until the user let go of the mouse ("パッ" behaviour).

    Running the rebuild inline in the callback fires it on every
    dirty signal from the spine's ``worldSpace[0]``, which happens
    per-frame during drag → the braid follows the spine as it
    moves. Recursion is safe: rebuild only writes to STRAND curve
    CVs (different nodes than the spine), so it doesn't re-fire
    the spine's own attributeChange. The ``_pending_rebuilds``
    guard is still there as a defensive belt.

    A ``cmds.refresh`` at the end forces the viewport to redraw
    even when Maya is mid-drag — without it the sweepMeshCreator
    would recompute the mesh but not paint the new pixels until
    the next natural refresh.
    """
    if group_uuid in _pending_rebuilds:
        return
    # Rate-limit — the API 2.0 dirty callback fires many times per
    # drag frame; a full rebuild every fire saturates the main
    # thread. Skip if we ran the same group within the last
    # _MIN_REBUILD_INTERVAL seconds.
    import time as _time
    now = _time.time()
    last = _last_rebuild_time.get(group_uuid, 0.0)
    if now - last < _MIN_REBUILD_INTERVAL:
        return
    _last_rebuild_time[group_uuid] = now
    _pending_rebuilds.add(group_uuid)
    try:
        _do_deferred_rebuild(group_uuid)
    finally:
        _pending_rebuilds.discard(group_uuid)


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
    """Install a real-time watcher on the spine.

    Prefers ``OpenMaya.MNodeMessage.addNodeDirtyPlugCallback`` when
    the API 2.0 module is available — that callback fires on every
    dirty-flag propagation, including per-mouse-move during an
    interactive CV drag. ``scriptJob(attributeChange=...)`` on the
    same attribute does NOT fire during drag in modern Maya: the
    interactive tool batches attribute updates and only pushes them
    at drag release, so the braid used to snap-at-end instead of
    tracking.

    Falls back to the pair of scriptJobs (worldSpace + controlPoints)
    if the API module is missing.

    Any previously-registered watcher for this group — API callback
    OR scriptJob, from this session or a prior module load — is
    torn down first so we don't stack duplicates.
    """
    group_uuids = cmds.ls(group, uuid=True) or []
    if not group_uuids:
        return
    group_uuid = group_uuids[0]
    _uninstall_spine_watcher(group_uuid)
    shape = _spine_shape_of(spine_curve)
    if not shape:
        return

    installed = False
    if _om is not None:
        try:
            sel = _om.MSelectionList()
            sel.add(shape)
            dep_node = sel.getDependNode(0)

            def _om_cb(_node, _plug, _client=None, uid=group_uuid):
                # NodeDirtyPlug fires from inside DG evaluation.
                # ``executeDeferred`` schedules ``cmds`` work
                # safely onto the main thread — Maya API callbacks
                # aren't allowed to run cmds directly.
                try:
                    _om.MGlobal.executeInMainThreadWithResult(
                        lambda: _schedule_rebuild(uid))
                except Exception:
                    _schedule_rebuild(uid)

            cb_id = _om.MNodeMessage.addNodeDirtyPlugCallback(
                dep_node, _om_cb)
            _spine_watcher_jobs[group_uuid] = {
                "om_cb": cb_id, "job_ids": []}
            installed = True
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] openMaya watcher 登録失敗 → "
                "scriptJob にフォールバック: {0}".format(exc))

    if installed:
        return

    # scriptJob fallback path.
    job_ids = []
    for attr in ("worldSpace[0]", "controlPoints"):
        try:
            jid = cmds.scriptJob(
                attributeChange=[
                    shape + "." + attr,
                    lambda uid=group_uuid: _schedule_rebuild(uid),
                ],
                killWithScene=True,
            )
            job_ids.append(jid)
        except Exception as exc:
            cmds.warning(
                "[maya_hair_tool] spine watcher 登録失敗 ({0}): "
                "{1}".format(attr, exc))
    if job_ids:
        _spine_watcher_jobs[group_uuid] = {
            "om_cb": None, "job_ids": job_ids}


def _uninstall_spine_watcher(group_uuid: str) -> None:  # noqa: E301
    """Kill the scriptJob associated with this Braid group (if
    any). Safe to call when nothing is registered. Handles all
    three storage shapes:

    * ``{"om_cb": id, "job_ids": [...]}`` — v0.5.26+
    * ``[jid, jid]``                     — v0.5.25 (list of ids)
    * ``jid``                             — pre-v0.5.25 (single id)
    """
    entry = _spine_watcher_jobs.pop(group_uuid, None)
    if entry is None:
        return
    om_cb = None
    job_ids = []
    if isinstance(entry, dict):
        om_cb = entry.get("om_cb")
        job_ids = list(entry.get("job_ids") or [])
    elif isinstance(entry, (list, tuple)):
        job_ids = list(entry)
    else:
        job_ids = [entry]

    if om_cb is not None and _om is not None:
        try:
            _om.MMessage.removeCallback(om_cb)
        except Exception:
            pass
    for job_id in job_ids:
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
    num_samples: int = 0,
) -> List[Vec3]:
    """Compute world-space points for one tail strand curve.

    Iterates ``_TAIL_TEMPLATE`` directly so each CV lands at the
    reference's exact (spine parameter, radial offset). ``num_samples``
    is retained in the signature for callers that used to pass it
    but is ignored — the CV count is fixed at ``len(_TAIL_TEMPLATE)``
    so the resulting NURBS matches the reference exactly.
    """
    tie_off = 1.0 - max(0.0, min(0.99, tail_length))
    if tie_off >= 1.0:
        return []
    mn, mx = _spine_param_range(spine_curve)
    span = mx - mn
    points: List[Vec3] = []
    for t, r_shape in _TAIL_TEMPLATE:
        u = tie_off + t * (1.0 - tie_off)
        param = mn + span * u
        p = cmds.pointOnCurve(spine_curve, parameter=param,
                              position=True)
        # Local frame at u — small finite-difference tangent.
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
        r = braid_radius * r_shape
        offset_w = r * math.cos(phase)
        offset_d = r * math.sin(phase)
        points.append((
            float(p[0]) + offset_w * N[0] + offset_d * B[0],
            float(p[1]) + offset_w * N[1] + offset_d * B[1],
            float(p[2]) + offset_w * N[2] + offset_d * B[2],
        ))
    return points


def _apply_tail_taper(creators, tail_thickness: float,
                      tail_tip_taper: float,
                      braid_strand_thickness: float) -> None:
    """Set each tail sweepMeshCreator's ``taperCurve`` so the
    strand's ROOT (the end that emerges from the hair tie)
    matches the braid strand thickness, and the TIP follows the
    user's ``tail_tip_taper`` slider. Middle stays uniform.

    taperCurve is a multiplier on ``scaleProfileX``:
        effective_width(u) = scaleProfileX × taperCurve(u)
    We keep scaleProfileX = tail_thickness (the user's slider)
    and compensate at the root:
        root_scale = braid_strand_thickness / tail_thickness

    Result at the root: ``tail_thickness × root_scale``
                      = ``braid_strand_thickness`` — matches the
                        braid strand it comes out of, regardless
                        of what the user has set the tail_thickness
                        slider to. Tail can then narrow (or widen)
                        toward the tip via tail_tip_taper.
    """
    if tail_thickness <= 1e-6 or not creators:
        return
    root_scale = float(braid_strand_thickness) / float(tail_thickness)
    for c in creators:
        if not cmds.objExists(c):
            continue
        try:
            hair.set_taper_profile(
                c, root_scale=root_scale,
                middle_scale=1.0,
                tip_scale=float(tail_tip_taper))
        except Exception:
            pass


def _create_tail_strands(
    spine_curve: str,
    tail_length: float,
    braid_radius: float,
    tail_strand_count: int,
    tail_thickness: float,
    tail_tip_taper: float,
    braid_strand_thickness: float,
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
        # Tail-tuned subdivisions (lower than the woven strand
        # because tail curves are simple arcs, not helices) but
        # still above the global default so the tail reads as
        # smooth hair strands. tip_scale here just seeds the
        # taperCurve — the real taper (including the root =
        # braid_thickness compensation) is applied via
        # ``_apply_tail_taper`` after the mesh exists.
        hair.create_hair_from_selected_curves(
            thickness=tail_thickness,
            tip_scale=tail_tip_taper,
            subdivisions_axis=C.DEFAULT_BRAID_TAIL_SUBDIV_AXIS,
            subdivisions_length=C.DEFAULT_BRAID_TAIL_SUBDIV_LENGTH)
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
    # Start-to-End mode with fixed steps — see helper docstring
    # for why tail strands don't use the Distance mode the woven
    # braid strands use.
    _apply_start_to_end_interpolation(
        tail_meshes, C.DEFAULT_BRAID_TAIL_INTERP_STEPS)

    # Root-matches-braid taper — override the seed taperCurve set
    # by create_hair_from_selected_curves so each tail strand's
    # emergent width at the tie equals braid_strand_thickness.
    tail_creators: List[str] = []
    for m in tail_meshes:
        cs = su.sweep_creators_from_nodes([m]) or []
        tail_creators.extend(cs)
    _apply_tail_taper(
        tail_creators, tail_thickness, tail_tip_taper,
        braid_strand_thickness)

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
    tail_tip_taper: float,
    braid_strand_thickness: float,
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
    # Propagate the tail-thickness AND tail-tip-taper slider values
    # to every tail sweepMeshCreator so both sliders take effect
    # during in-place rebuild (the in-place path used to skip
    # thickness — and now also handles tip taper via the
    # taperCurve ramp).
    all_creators: List[str] = []
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
            all_creators.append(c)
    # Root scale = braid_thickness / tail_thickness so the tail's
    # emergent width at the tie always matches the braid regardless
    # of the tail_thickness slider. tip follows the tail_tip_taper
    # slider, middle stays uniform.
    _apply_tail_taper(
        all_creators, tail_thickness, tail_tip_taper,
        braid_strand_thickness)
    return True


def _apply_distance_interpolation(
    mesh_transforms: List[str],
    distance: float,
) -> None:
    """Force each strand mesh's sweepMeshCreator into Distance-mode
    interpolation with the given spacing along the curve. Bypasses
    the Precision-mode dead zone (values 1..74 all resolve to the
    same coarse mesh) so long helical curves render as smooth
    spirals from the first click without any manual slider tuning.

    Best for the woven braid strands (long, high curvature —
    density needs to scale with length). Not appropriate for short
    tail strands (see ``_apply_start_to_end_interpolation``)."""
    if not mesh_transforms:
        return
    for m in mesh_transforms:
        if not cmds.objExists(m):
            continue
        creators = su.sweep_creators_from_nodes([m]) or []
        for c in creators:
            try:
                if cmds.attributeQuery(
                        "interpolationMode", node=c, exists=True):
                    cmds.setAttr(
                        c + ".interpolationMode",
                        C._INTERP_MODE_DISTANCE)
                if cmds.attributeQuery(
                        "interpolationDistance", node=c,
                        exists=True):
                    cmds.setAttr(
                        c + ".interpolationDistance",
                        float(distance))
            except Exception:
                pass


def _apply_start_to_end_interpolation(
    mesh_transforms: List[str],
    steps: int,
) -> None:
    """Force each strand mesh's sweepMeshCreator into Start-to-End
    mode with a fixed step count. Used for tail strands: tails are
    short (typically < 1 unit) so a Distance-mode sampler would put
    only a handful of cross-sections on each strand and make them
    look like chunky spikes; a fixed step count guarantees enough
    density regardless of the tail's absolute length."""
    if not mesh_transforms:
        return
    for m in mesh_transforms:
        if not cmds.objExists(m):
            continue
        creators = su.sweep_creators_from_nodes([m]) or []
        for c in creators:
            try:
                if cmds.attributeQuery(
                        "interpolationMode", node=c, exists=True):
                    cmds.setAttr(
                        c + ".interpolationMode",
                        C._INTERP_MODE_START_TO_END)
                if cmds.attributeQuery(
                        "interpolationSteps", node=c,
                        exists=True):
                    cmds.setAttr(
                        c + ".interpolationSteps", int(steps))
            except Exception:
                pass


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

    Selection preservation: every ``cmds.select`` call inside
    (``hair.create_hair_from_selected_curves`` feed, tail recreate
    etc.) mutates Maya's selection. During a slider drag on a live
    Braid this used to drop the user's selection between callbacks
    — subsequent slider events found no braid selected and became
    no-ops. Snapshot at entry, restore in ``finally``.
    """
    prev_selection = cmds.ls(selection=True, long=True) or []
    try:
        _rebuild_braid_inner(group, overrides)
    finally:
        try:
            if prev_selection:
                cmds.select(prev_selection, replace=True)
            else:
                cmds.select(clear=True)
        except Exception:
            pass


def _rebuild_braid_inner(group: str, overrides: dict) -> None:
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
    normals, binormals = _parallel_transport_frames(
        tangents, positions)

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
                tail_length=params["tail_length"],
                taper_ramp=params.get("taper_ramp"),
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
                params["density_bottom"],
                tail_length=params["tail_length"],
                taper_ramp=params.get("taper_ramp"))
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
    # Taper ramp separately (string attr, not scalar).
    ramp = params.get("taper_ramp")
    if ramp:
        _ensure_str_attr(group, _ATTR_TAPER_RAMP)
        cmds.setAttr(group + "." + _ATTR_TAPER_RAMP,
                     _encode_ramp(ramp), type="string")

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
    tail_tip_taper = float(params.get(
        "tail_tip_taper", C.DEFAULT_BRAID_TAIL_TIP_TAPER))
    tail_updated_in_place = False
    if (existing_tail_uuids
            and len(existing_tail_uuids) == desired_count
            and params["tail_length"] > 1e-4):
        tail_updated_in_place = _update_tail_strands_in_place(
            group, spine_curve, params["tail_length"],
            params["radius"], tail_thickness, tail_tip_taper,
            params["strand_thickness"], existing_tail_uuids)
        if tail_updated_in_place:
            # In-place path skips _stamp_tail_meta by design (UUIDs
            # unchanged) — but the numeric params may have
            # changed and must be persisted for selection-sync.
            _stamp_tail_meta(
                group, existing_tail_uuids, desired_count,
                tail_thickness, tail_tip_taper)
    if not tail_updated_in_place:
        _delete_existing_tail_strands(group)
        new_tail_meshes = _create_tail_strands(
            spine_curve, params["tail_length"],
            params["radius"], desired_count, tail_thickness,
            tail_tip_taper, params["strand_thickness"],
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
                          tail_thickness, tail_tip_taper)

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
    tail_tip_taper: float = C.DEFAULT_BRAID_TAIL_TIP_TAPER,
    taper_ramp=None,
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

    # Default taper ramp when caller didn't supply one.
    if not taper_ramp:
        taper_ramp = _default_braid_taper_ramp()

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
    normals, binormals = _parallel_transport_frames(
        tangents, positions)

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
                taper_ramp=taper_ramp,
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
            # Braid-tuned defaults so the first generation already
            # reads as a smooth braid — the built-in hair defaults
            # (axis=8, length=12, tip=0.05) produce a chain of
            # angular blocks with tapered ends when applied to a
            # 20+ turn helix.
            hair.create_hair_from_selected_curves(
                thickness=strand_thickness,
                subdivisions_axis=C.DEFAULT_BRAID_STRAND_SUBDIV_AXIS,
                subdivisions_length=C.DEFAULT_BRAID_STRAND_SUBDIV_LENGTH,
                tip_scale=C.DEFAULT_BRAID_STRAND_TIP_SCALE,
            )
        except Exception as exc:
            _cleanup_partial_braid(strand_curves)
            raise RuntimeError(
                "三つ編み生成中にエラーが発生し、生成途中のカーブ / "
                "メッシュを破棄しました: {0}".format(exc))

        # ``create_hair_from_selected_curves`` selects the resulting
        # mesh transforms — grab those now.
        strand_meshes = cmds.ls(selection=True, long=True) or []
        # Push each strand's sweepMeshCreator into Distance-mode
        # interpolation so the helical mesh is dense from the start
        # (see constants.py — Precision mode has a dead zone that
        # makes the sliders feel broken on twisted curves).
        _apply_distance_interpolation(
            strand_meshes,
            C.DEFAULT_BRAID_STRAND_INTERP_DISTANCE)
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
            "tail_tip_taper": tail_tip_taper,
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
            # Separate stamp for the taper ramp since it's stored as
            # a string attr and _stamp_braid_params only handles the
            # scalar float attrs.
            _ensure_str_attr(stamp_target, _ATTR_TAPER_RAMP)
            cmds.setAttr(
                stamp_target + "." + _ATTR_TAPER_RAMP,
                _encode_ramp(taper_ramp), type="string")

        # Tail — N separate hair strands below the tie. Each
        # becomes a first-class hair strand so the user can tweak
        # thickness / taper / colour individually via the normal
        # hair sliders. Parented under the Braid geom group so
        # they group visually with the woven strands.
        tail_meshes = _create_tail_strands(
            spine_curve, tail_length, radius,
            tail_strand_count, tail_thickness, tail_tip_taper,
            strand_thickness, stamp_target, base_name)
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
                tail_thickness, tail_tip_taper)

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
