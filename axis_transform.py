"""Coordinate conversion for static X-Plane OBJ8 to FlightGear AC3D.

X-Plane native local space: +X east, +Y up, +Z south.
FlightGear scenery default AC3D local space: +X south, +Y up, +Z west.
This is a proper +90-degree rotation about Y (preserves handedness).
Never recenter vertices: many airport assets share one intentionally distant pivot.
"""


def xp_to_fg(x, y, z):
    return z, y, -x


def fg_to_xp(x, y, z):
    return -z, y, x
