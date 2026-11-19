"""Godot 3/4 (+C#), stdlib parsers: ``pck`` (PCK directory v1-v3, embedded in the exe, encryption reported),
``tres`` (``.tscn``/``.tres`` text -> Python/JSON), ``scripts`` (exported variables of GDScript/C# sources),
``animtree`` (Animation tracks, AnimationTree graphs, Skeleton3D, PhysicalBone3D, joints). GDRE stays in
``assets/godot.py`` / ``decompile/godot.py``; the analyses that use these are in ``gst.analysis.godot``."""
