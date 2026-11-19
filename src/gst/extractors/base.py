"""Extractor contract shared by the pipeline and the engine backends.

An extractor turns part of a game install into readable files in the study workspace. The pipeline
asks it to ``discover`` units of work (one serialized file, one bundle, one assembly, one pak...) and
then runs each unit in its own worker process (``python -X utf8 -m gst.pipeline.worker <job>``), so a
crash in a parser kills one job, never the study.

Rules every extractor follows (see docs/INTERFACES.md):

- No ceilings: no default limits on count, size or time. A limit is only an explicit option.
- Write only under ``ctx.out_root`` (inside the workspace), first into ``.partial/`` then rename
  atomically; the sidecar (``<file>.gs.json``) is written last.
- Never write into the game install; check ``policy_need`` against the verdict before running.
- ``run`` returns a :class:`Result`; it raises only for bugs. Expected failures (unsupported
  version, encrypted container without a published key) are ``status`` values with a reason.
- Full error text in ``Result.error`` (never truncated).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal

Category = Literal[
    "code", "texture", "mesh", "audio", "animation", "prefab_scene", "material_shader", "data",
    "localization", "font", "video", "map", "config_save", "other",
]
CATEGORIES: tuple[str, ...] = (
    "code", "texture", "mesh", "audio", "animation", "prefab_scene", "material_shader", "data",
    "localization", "font", "video", "map", "config_save", "other",
)

Resource = Literal["cpu", "io", "heavy"]
"""Scheduler pool: ``cpu`` (parsers), ``io`` (copies, listings), ``heavy`` (one at a time: Ghidra,
AssetRipper, ilspycmd on a whole game)."""
RESOURCES: tuple[str, ...] = ("cpu", "io", "heavy")

PolicyNeed = Literal["static", "decrypt-published-key", "runtime-offline", "none"]
"""What the extractor needs from the policy verdict. ``static`` = reads files only (allowed for
every verdict except ``refuse``); ``decrypt-published-key`` = opens an encrypted container with a
publicly published key; ``runtime-offline`` = runs the game or injects (sandbox + ``offline-ok``)."""

Status = Literal["done", "failed", "unsupported", "blocked-policy", "paused-disk", "skipped"]
STATUSES: tuple[str, ...] = ("done", "failed", "unsupported", "blocked-policy", "paused-disk", "skipped")


@dataclass
class Unit:
    """One unit of work. ``fingerprint`` = hash of ``(relpath, size, mtime_ns)`` of every input +
    buildid; the pipeline skips a unit whose fingerprint already has a ``done`` job."""

    id: str                      # stable within the extractor, e.g. "Data/level0" or "Managed/Assembly-CSharp.dll"
    extractor: str               # Extractor.name
    relpaths: list[str]          # inputs, relative to the install dir (forward slashes)
    size: int                    # total input bytes (drives RAM weight and the disk estimate)
    fingerprint: str = ""
    meta: dict[str, Any] = field(default_factory=dict)  # extractor-private, JSON-serialisable


@dataclass
class Result:
    outputs: list[str] = field(default_factory=list)  # files written, relative to the workspace
    out_bytes: int = 0
    status: Status = "done"
    error: str | None = None     # full text (traceback or tool stderr), never truncated
    tool: str = ""               # e.g. "UnityPy", "ilspycmd", "gdre_tools"
    tool_version: str = ""
    notes: list[str] = field(default_factory=list)  # human remarks that go to COVERAGE.md


@dataclass
class Context:
    workspace: Path              # GS_HOME/<slug>
    game: dict                   # game.json
    install_dir: Path            # read-only
    out_root: Path               # where this extractor writes (inside the workspace)
    policy: str                  # verdict: offline-ok | static-only | refuse ...
    log: logging.Logger = field(default_factory=lambda: logging.getLogger("gst.extractor"))
    options: dict[str, Any] = field(default_factory=dict)  # CLI options (--only, --timeout, limits if explicit)


class Extractor(ABC):
    """Base class. Subclasses set the class attributes and implement ``discover`` and ``run``."""

    name: ClassVar[str] = ""
    category: ClassVar[Category] = "other"
    resource: ClassVar[Resource] = "cpu"
    policy_need: ClassVar[PolicyNeed] = "static"
    # Optional: study order and dependencies. ``stage`` is the study order
    # (10 loose files, 20 code, 30 code index, 40 asset inventory, 50 exports, 60 analyze, 70 heavy);
    # ``after`` names extractors whose jobs must all finish before this one's start.
    stage: ClassVar[int] = 50
    after: ClassVar[tuple[str, ...]] = ()
    # Optional: where ``ctx.out_root`` points, relative to the workspace ("" = the workspace itself),
    # e.g. ``assets/unity`` for the Unity extractors.
    out_subdir: ClassVar[str] = ""
    # Optional: wait until no job of an earlier ``stage`` is pending or running, and rerun whenever one of
    # them runs again (the analysis stage reads everything extracted before it).
    after_earlier_stages: ClassVar[bool] = False

    def applies(self, ctx: Context) -> bool:
        """Cheap check: does this extractor have anything to do for this game? Default: yes."""
        return True

    @abstractmethod
    def discover(self, ctx: Context) -> Iterable[Unit]:
        """Yield every unit of work (stream: never build a full list of a 13 GB install in RAM)."""

    @abstractmethod
    def run(self, unit: Unit, ctx: Context) -> Result:
        """Process one unit inside a worker process and describe what was written."""

    def finalize(self, ctx: Context) -> Result | None:
        """Optional: one step after every unit of this extractor finished (merge per-unit tables into
        an index, ...). The pipeline runs it as its own job (unit id ``<finalize>``, resumable, rerun
        whenever any unit of the extractor ran again) once no unit of the extractor is pending or
        running; extractors that depend on this one (``after``) wait for it too. Must be idempotent.
        Return None when there is nothing to do (the job is recorded as ``skipped``)."""
        return None

    @classmethod
    def has_finalize(cls) -> bool:
        return cls.finalize is not Extractor.finalize

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name} {self.category}/{self.resource}>"
