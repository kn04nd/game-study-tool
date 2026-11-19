# Game Study Toolkit: summarize a RenderDoc capture (runs inside qrenderdoc --python, RenderDoc's Python 3.8).
# Env: GS_RD_CAPTURE (the .rdc), GS_RD_OUT (output folder), GS_RD_MAX_IMAGES (default 16).
# Writes: frame.json, targets/*.png (render targets at the end of each pass, final image), shaders/*.txt
# (reflection + disassembly), export.log. Exits the process itself so the UI never opens.
import json
import os
import traceback

OUT = os.environ.get("GS_RD_OUT", ".")
LOG = open(os.path.join(OUT, "export.log"), "w", encoding="utf-8")


def log(text):
    LOG.write(text + "\n")
    LOG.flush()


def rid(resource):
    return str(resource).replace("ResourceId::", "")


def is_null(resource):
    return rid(resource) in ("0", "")


def walk(actions, sf, markers, out):
    import renderdoc as rd

    for a in actions:
        name = a.GetName(sf)
        flags = a.flags
        if a.children:
            walk(a.children, sf, markers + [name], out)
        if flags & (rd.ActionFlags.Drawcall | rd.ActionFlags.Dispatch | rd.ActionFlags.Clear | rd.ActionFlags.Present):
            out.append({"eid": a.eventId, "name": name, "markers": markers,
                        "draw": bool(flags & rd.ActionFlags.Drawcall), "dispatch": bool(flags & rd.ActionFlags.Dispatch),
                        "clear": bool(flags & rd.ActionFlags.Clear), "present": bool(flags & rd.ActionFlags.Present),
                        "indices": a.numIndices, "instances": a.numInstances,
                        "outputs": [rid(o) for o in a.outputs if not is_null(o)],
                        "depth": None if is_null(a.depthOut) else rid(a.depthOut)})


def reflection(refl):
    if refl is None:
        return None
    blocks = []
    for cb in refl.constantBlocks:
        blocks.append({"name": cb.name, "bytes": cb.byteSize,
                       "variables": [v.name for v in cb.variables][:24]})
    return {"entry": refl.entryPoint, "constant_blocks": blocks,
            "textures": [r.name for r in refl.readOnlyResources][:16],
            "rw_resources": [r.name for r in refl.readWriteResources][:16],
            "samplers": [s.name for s in refl.samplers][:16],
            "inputs": [s.semanticName or s.varName for s in refl.inputSignature][:16],
            "outputs": [s.semanticName or s.varName for s in refl.outputSignature][:16]}


def main():
    import renderdoc as rd

    max_images = int(os.environ.get("GS_RD_MAX_IMAGES", "16"))
    os.makedirs(os.path.join(OUT, "targets"), exist_ok=True)
    os.makedirs(os.path.join(OUT, "shaders"), exist_ok=True)
    cap = rd.OpenCaptureFile()
    status = cap.OpenFile(os.environ["GS_RD_CAPTURE"], "", None)
    if status != rd.ResultCode.Succeeded:
        raise RuntimeError("cannot open the capture: %s" % status)
    if cap.LocalReplaySupport() != rd.ReplaySupport.Supported:
        raise RuntimeError("this capture cannot be replayed on this machine: %s" % cap.LocalReplaySupport())
    status, ctrl = cap.OpenCapture(rd.ReplayOptions(), None)
    if status != rd.ResultCode.Succeeded:
        raise RuntimeError("replay failed: %s" % status)
    try:
        sf = ctrl.GetStructuredFile()
        props = ctrl.GetAPIProperties()
        textures = {rid(t.resourceId): t for t in ctrl.GetTextures()}
        names = {rid(r.resourceId): r.name for r in ctrl.GetResources()}
        events = []
        walk(ctrl.GetRootActions(), sf, [], events)

        # passes: consecutive draws/dispatches writing the same targets
        passes = []
        for e in events:
            if not (e["draw"] or e["dispatch"]):
                continue
            key = (tuple(e["outputs"]), e["depth"], e["dispatch"])
            if not passes or passes[-1]["key"] != key:
                passes.append({"key": key, "events": [], "markers": e["markers"]})
            passes[-1]["events"].append(e)

        disasm_target = None
        targets = list(ctrl.GetDisassemblyTargets(True))
        for wanted in ("DXBC", "DXIL", "SPIR-V (RenderDoc)", "SPIR-V"):
            if wanted in targets:
                disasm_target = wanted
                break
        disasm_target = disasm_target or (targets[0] if targets else None)

        shaders = {}
        images = 0
        stages = [("vs", rd.ShaderStage.Vertex), ("ps", rd.ShaderStage.Pixel), ("cs", rd.ShaderStage.Compute)]
        out_passes = []
        budget = 600  # replays to read shaders: a few events per pass, spread evenly (real frames have thousands)
        for index, p in enumerate(passes):
            used = {}
            step = max(1, len(p["events"]) // 8)
            sample = p["events"][::step][:8]
            for e in sample:
                if budget <= 0:
                    break
                budget -= 1
                ctrl.SetFrameEvent(e["eid"], True)
                state = ctrl.GetPipelineState()
                for label, stage in stages:
                    sid = state.GetShader(stage)
                    if is_null(sid):
                        continue
                    key = rid(sid)
                    used.setdefault(label, [])
                    if key not in used[label]:
                        used[label].append(key)
                    if key not in shaders:
                        refl = state.GetShaderReflection(stage)
                        entry = {"id": key, "stage": label, "name": names.get(key, ""), "reflection": reflection(refl)}
                        if refl is not None and disasm_target:
                            pipe = state.GetComputePipelineObject() if label == "cs" else state.GetGraphicsPipelineObject()
                            text = ctrl.DisassembleShader(pipe, refl, disasm_target)
                            path = os.path.join(OUT, "shaders", "%s_%s.txt" % (label, key))
                            with open(path, "w", encoding="utf-8") as fh:
                                fh.write("// %s %s %s\n" % (label, key, refl.entryPoint))
                                fh.write(text)
                            entry["disassembly"] = "shaders/%s_%s.txt" % (label, key)
                            entry["lines"] = text.count("\n")
                        shaders[key] = entry
            last = p["events"][-1]
            saved = []
            ctrl.SetFrameEvent(last["eid"], True)
            for target in list(last["outputs"]) + ([last["depth"]] if last["depth"] else []):
                if images >= max_images or target not in textures:
                    continue
                save = rd.TextureSave()
                save.resourceId = textures[target].resourceId
                save.destType = rd.FileType.PNG
                save.mip = 0
                save.slice.sliceIndex = 0
                save.alpha = rd.AlphaMapping.Discard
                path = os.path.join(OUT, "targets", "pass%02d_%s.png" % (index, target))
                ctrl.SaveTexture(save, path)
                saved.append("targets/pass%02d_%s.png" % (index, target))
                images += 1
            out_passes.append({"index": index, "markers": p["markers"], "first_eid": p["events"][0]["eid"],
                               "last_eid": last["eid"], "draws": len(p["events"]),
                               "dispatch": p["key"][2],
                               "indices": sum(e["indices"] for e in p["events"]),
                               "outputs": list(p["key"][0]), "depth": p["key"][1], "shaders": used, "images": saved,
                               "calls": sorted(set(e["name"].split("(")[0] for e in p["events"]))})

        final = None
        present = [e for e in events if e["present"]]
        if present and present[-1]["outputs"] and present[-1]["outputs"][0] in textures:
            ctrl.SetFrameEvent(present[-1]["eid"], True)
            save = rd.TextureSave()
            save.resourceId = textures[present[-1]["outputs"][0]].resourceId
            save.destType = rd.FileType.PNG
            save.alpha = rd.AlphaMapping.Discard
            final = "targets/final.png"
            ctrl.SaveTexture(save, os.path.join(OUT, final))

        tex_out = {}
        for key, t in textures.items():
            tex_out[key] = {"name": names.get(key, ""), "width": t.width, "height": t.height, "depth": t.depth,
                            "mips": t.mips, "array": t.arraysize, "format": t.format.Name(), "msaa": t.msSamp}
        frame = {"api": str(props.pipelineType).split(".")[-1], "degraded": bool(props.degraded),
                 "events": len(events), "draws": sum(1 for e in events if e["draw"]),
                 "dispatches": sum(1 for e in events if e["dispatch"]), "clears": sum(1 for e in events if e["clear"]),
                 "passes": out_passes, "shaders": shaders, "textures": tex_out, "final": final,
                 "disassembly": disasm_target}
        with open(os.path.join(OUT, "frame.json"), "w", encoding="utf-8") as fh:
            json.dump(frame, fh, indent=1)
        log("ok: %d passes, %d shaders, %d images" % (len(out_passes), len(shaders), images))
    finally:
        ctrl.Shutdown()
        cap.Shutdown()


try:
    main()
except Exception:
    log("ERROR " + traceback.format_exc())
LOG.close()
os._exit(0)
