# Game Study Toolkit: launch a game under RenderDoc and capture one frame after a delay (runs inside
# qrenderdoc --python, RenderDoc's Python 3.8). Env: GS_RD_EXE, GS_RD_WORKDIR, GS_RD_ARGS, GS_RD_CAPTURE
# (file template), GS_RD_AFTER (seconds after the graphics API registers), GS_RD_OUT (result folder).
# Writes capture.json: {"capture": path, "pid": the game's pid, "api": ...}; gst stops that exact pid.
import json
import os
import time
import traceback

OUT = os.environ.get("GS_RD_OUT", ".")
result = {"capture": None, "pid": None, "api": None, "error": None}


def main():
    import renderdoc as rd

    opts = rd.CaptureOptions()  # the defaults
    opts.captureCallstacks = False  # callstacks would want a symbol server
    launched = rd.ExecuteAndInject(os.environ["GS_RD_EXE"], os.environ.get("GS_RD_WORKDIR", ""),
                                   os.environ.get("GS_RD_ARGS", ""), [], os.environ["GS_RD_CAPTURE"], opts, False)
    code = getattr(launched.result, "code", launched.result)
    if code != rd.ResultCode.Succeeded or not launched.ident:
        raise RuntimeError("could not launch under RenderDoc: %s" % launched.result)
    tc = None
    for _ in range(50):
        tc = rd.CreateTargetControl("", launched.ident, "gst", True)
        if tc is not None:
            break
        time.sleep(0.2)
    if tc is None:
        raise RuntimeError("no target control connection")
    try:
        result["pid"] = tc.GetPID()
        registered = None
        deadline = time.time() + 120
        while time.time() < deadline and registered is None:
            msg = tc.ReceiveMessage(None)
            if msg.type == rd.TargetControlMessageType.RegisterAPI:
                registered = msg.apiUse.name
            elif msg.type == rd.TargetControlMessageType.Disconnected:
                raise RuntimeError("the game exited before creating a graphics device")
        if registered is None:
            raise RuntimeError("no graphics API registered within 120 s")
        result["api"] = registered
        wait_until = time.time() + float(os.environ.get("GS_RD_AFTER", "5"))
        while time.time() < wait_until:
            tc.ReceiveMessage(None)
        tc.TriggerCapture(1)
        deadline = time.time() + 60
        while time.time() < deadline:
            msg = tc.ReceiveMessage(None)
            if msg.type == rd.TargetControlMessageType.NewCapture:
                result["capture"] = msg.newCapture.path
                result["frame"] = msg.newCapture.frameNumber
                break
            if msg.type == rd.TargetControlMessageType.Disconnected:
                raise RuntimeError("the game exited before the capture")
        if result["capture"] is None:
            raise RuntimeError("no capture within 60 s of the trigger")
    finally:
        tc.Shutdown()


try:
    main()
except Exception:
    result["error"] = traceback.format_exc()
with open(os.path.join(OUT, "capture.json"), "w", encoding="utf-8") as fh:
    json.dump(result, fh)
os._exit(0)
