"""The ``analysis`` stage of ``gst study``: one extractor ``analysis.<name>`` per registered analysis, one job each.

Stage 60 (after the exports, before the heavy tools); ``after_earlier_stages`` makes every analysis job wait
until no job of an earlier stage is pending or running, and reruns it whenever one of them ran again.
``after`` adds the analyses it reads (``analysis:<name>`` requirements), so ``systems`` waits for ``prefabs``,
and the code jobs it reads (``code.index`` -> ``code.index``, ``dumps.rea`` -> ``rea.import``): the asset track
runs beside the code track (``pipeline.levels``), so an asset analysis that reads the code index waits for it.
A job whose requirements are missing ends ``skipped`` with the reason. Options: ``gst study --opt
<name>.<key>=<value>`` (e.g. ``prefabs.marker=EnemyParent``) or a shared ``<key>=<value>``.
"""

from __future__ import annotations

import hashlib
from typing import ClassVar, Iterable

from gst import analysis
from gst.extractors.base import Context, Extractor, Result, Unit
from gst.extractors.registry import register

PIPELINE_OPTIONS = {"only", "skip", "force", "retry_failed", "min_free", "timeout"}


class AnalysisJob(Extractor):
    category = "other"
    resource = "cpu"
    stage = 60
    after_earlier_stages = True
    spec_name: ClassVar[str] = ""

    def discover(self, ctx: Context) -> Iterable[Unit]:
        spec = analysis.get(self.spec_name)
        build = str((ctx.game or {}).get("buildid") or "?")
        fp = hashlib.sha256(f"analysis|{spec.name}|{spec.version}|{build}".encode("utf-8")).hexdigest()
        yield Unit(id=spec.name, extractor=self.name, relpaths=[], size=0, fingerprint=fp)

    def run(self, unit: Unit, ctx: Context) -> Result:
        options = {k: v for k, v in (ctx.options or {}).items() if k not in PIPELINE_OPTIONS and v is not None}
        res = analysis.run(ctx.workspace, ctx.game, self.spec_name, analysis.options_for(self.spec_name, options),
                           log=lambda msg: ctx.log.info(msg))
        notes = [f"{k} {v}" for k, v in (res.get("summary") or {}).items()] + list(res.get("notes") or [])
        if res["status"] == "done":
            outs = [o for o in res["outputs"] if not o.endswith("/")]
            out_bytes = sum((ctx.workspace / o).stat().st_size for o in outs if (ctx.workspace / o).is_file())
            return Result(outputs=res["outputs"], out_bytes=out_bytes, tool="gst analyze", notes=notes)
        if res["status"] == "skipped":
            return Result(status="skipped", tool="gst analyze", notes=[res["reason"]])
        return Result(status="failed", error=res["reason"], tool="gst analyze")


CODE_INPUTS = {"code.index": "code.index", "dumps.rea": "rea.import"}


def _make(spec: analysis.Spec) -> type[Extractor]:
    from gst.pipeline import levels

    deps = tuple(f"analysis.{d}" for d in spec.deps())
    if levels.track_of(f"analysis.{spec.name}") == "code":
        # a code analysis never waits for the asset track on an optional input (systems uses prefabs when present)
        optional = {f"analysis.{n.split(':', 1)[1]}" for n in spec.uses if n.startswith("analysis:")}
        deps = tuple(d for d in deps if not (d in optional and levels.track_of(d) == "assets"))
    deps += tuple(dict.fromkeys(CODE_INPUTS[r] for r in (*spec.needs, *spec.uses) if r in CODE_INPUTS))
    cls = type(f"Analysis_{spec.name}", (AnalysisJob,), {"name": f"analysis.{spec.name}", "after": deps, "category": spec.category,
                                                          "spec_name": spec.name, "__module__": __name__})
    return register(*spec.engines)(cls)


JOBS = {name: _make(spec) for name, spec in analysis.all_specs().items()}
