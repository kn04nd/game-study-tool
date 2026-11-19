"""Unity humanoid (Mecanim) name tables, written by hand from the public scripting API.

- :data:`BONE_NAMES`: the 55 ``HumanTrait.BoneName`` entries, in ``HumanBodyBones`` order (UpperChest last);
- :data:`MUSCLE_NAMES`: the 95 ``HumanTrait.MuscleName`` entries (55 body muscles, then 40 finger muscles) and
  :data:`MUSCLE_BONE`, the human bone each muscle moves;
- :func:`clip_attribute`: the name of a humanoid curve of a compiled AnimationClip. Its binding ``attribute`` is an
  index into Mecanim's curve list: MotionT/MotionQ (0-6), RootT/RootQ (7-13), the IK goals LeftFoot, RightFoot,
  LeftHand, RightHand (T + Q, 14-41), the 95 muscles (42-136), then the translation DoF of 21 bones (x/y/z, 137-199).
  Checked on real clips: MotionT.y is always 0 (motion is projected on the ground), RootT.y is the body height, the
  foot goals' T.y are negative, the eye muscle pairs share their ranges and the TDoF curves are tiny translations.
"""

from __future__ import annotations

SIDES = ("Left", "Right")
FINGERS = ("Thumb", "Index", "Middle", "Ring", "Little")
PHALANGES = ("Proximal", "Intermediate", "Distal")

BONE_NAMES: tuple[str, ...] = (
    "Hips", "LeftUpperLeg", "RightUpperLeg", "LeftLowerLeg", "RightLowerLeg", "LeftFoot", "RightFoot", "Spine", "Chest",
    "Neck", "Head", "LeftShoulder", "RightShoulder", "LeftUpperArm", "RightUpperArm", "LeftLowerArm", "RightLowerArm",
    "LeftHand", "RightHand", "LeftToes", "RightToes", "LeftEye", "RightEye", "Jaw",
    *(f"{s} {f} {p}" for s in SIDES for f in FINGERS for p in PHALANGES),
    "UpperChest",
)

_BODY_MUSCLES: tuple[tuple[str, str], ...] = (
    ("Spine Front-Back", "Spine"), ("Spine Left-Right", "Spine"), ("Spine Twist Left-Right", "Spine"),
    ("Chest Front-Back", "Chest"), ("Chest Left-Right", "Chest"), ("Chest Twist Left-Right", "Chest"),
    ("UpperChest Front-Back", "UpperChest"), ("UpperChest Left-Right", "UpperChest"),
    ("UpperChest Twist Left-Right", "UpperChest"),
    ("Neck Nod Down-Up", "Neck"), ("Neck Tilt Left-Right", "Neck"), ("Neck Turn Left-Right", "Neck"),
    ("Head Nod Down-Up", "Head"), ("Head Tilt Left-Right", "Head"), ("Head Turn Left-Right", "Head"),
    ("Left Eye Down-Up", "LeftEye"), ("Left Eye In-Out", "LeftEye"),
    ("Right Eye Down-Up", "RightEye"), ("Right Eye In-Out", "RightEye"),
    ("Jaw Close", "Jaw"), ("Jaw Left-Right", "Jaw"),
    *((m.format(s=s), b.format(s=s)) for s in SIDES for m, b in (
        ("{s} Upper Leg Front-Back", "{s}UpperLeg"), ("{s} Upper Leg In-Out", "{s}UpperLeg"),
        ("{s} Upper Leg Twist In-Out", "{s}UpperLeg"), ("{s} Lower Leg Stretch", "{s}LowerLeg"),
        ("{s} Lower Leg Twist In-Out", "{s}LowerLeg"), ("{s} Foot Up-Down", "{s}Foot"),
        ("{s} Foot Twist In-Out", "{s}Foot"), ("{s} Toes Up-Down", "{s}Toes"))),
    *((m.format(s=s), b.format(s=s)) for s in SIDES for m, b in (
        ("{s} Shoulder Down-Up", "{s}Shoulder"), ("{s} Shoulder Front-Back", "{s}Shoulder"),
        ("{s} Arm Down-Up", "{s}UpperArm"), ("{s} Arm Front-Back", "{s}UpperArm"),
        ("{s} Arm Twist In-Out", "{s}UpperArm"), ("{s} Forearm Stretch", "{s}LowerArm"),
        ("{s} Forearm Twist In-Out", "{s}LowerArm"), ("{s} Hand Down-Up", "{s}Hand"), ("{s} Hand In-Out", "{s}Hand"))),
)
_FINGER_MUSCLES: tuple[tuple[str, str], ...] = tuple(
    (f"{s} {f} {dof}", f"{s} {f} {bone}") for s in SIDES for f in FINGERS
    for dof, bone in (("1 Stretched", "Proximal"), ("Spread", "Proximal"), ("2 Stretched", "Intermediate"),
                      ("3 Stretched", "Distal")))
MUSCLE_NAMES: tuple[str, ...] = tuple(m for m, _b in _BODY_MUSCLES + _FINGER_MUSCLES)
MUSCLE_BONE: dict[str, str] = dict(_BODY_MUSCLES + _FINGER_MUSCLES)
BODY_MUSCLES = len(_BODY_MUSCLES)

TDOF_BONES: tuple[str, ...] = (
    "Spine", "Chest", "UpperChest", "Neck", "Head",
    *(f"{s}{b}" for s in SIDES for b in ("UpperLeg", "LowerLeg", "Foot", "Toes")),
    *(f"{s}{b}" for s in SIDES for b in ("Shoulder", "UpperArm", "LowerArm", "Hand")),
)
GOALS = ("LeftFoot", "RightFoot", "LeftHand", "RightHand")
_TQ = ("T.x", "T.y", "T.z", "Q.x", "Q.y", "Q.z", "Q.w")
MUSCLE_BASE = 2 * 7 + len(GOALS) * 7          # 42: first muscle curve
TDOF_BASE = MUSCLE_BASE + len(_BODY_MUSCLES) + len(_FINGER_MUSCLES)  # 137


def clip_attribute(index: int) -> tuple[str, str]:
    """``(name, group)`` of a humanoid curve index; group = motion, root, goal, muscle, finger, tdof or unknown."""
    i = int(index)
    if 0 <= i < 7:
        return "Motion" + _TQ[i], "motion"
    if 7 <= i < 14:
        return "Root" + _TQ[i - 7], "root"
    if 14 <= i < MUSCLE_BASE:
        g, k = divmod(i - 14, 7)
        return GOALS[g] + _TQ[k], "goal"
    if MUSCLE_BASE <= i < TDOF_BASE:
        m = i - MUSCLE_BASE
        return MUSCLE_NAMES[m], "muscle" if m < BODY_MUSCLES else "finger"
    if TDOF_BASE <= i < TDOF_BASE + 3 * len(TDOF_BONES):
        b, axis = divmod(i - TDOF_BASE, 3)
        return f"{TDOF_BONES[b]}TDOF.{'xyz'[axis]}", "tdof"
    return f"#{i}", "unknown"


def muscle_table(mapping: dict[str, str | None] | None = None) -> list[dict]:
    """The 95 muscles: index, name, the human bone it moves and (with an Avatar mapping) the skeleton node."""
    mapping = mapping or {}
    return [{"index": i, "name": m, "bone": MUSCLE_BONE[m], "node": mapping.get(MUSCLE_BONE[m]),
             "curve": MUSCLE_BASE + i} for i, m in enumerate(MUSCLE_NAMES)]
