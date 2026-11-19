"""``gst analyze controller``: AnimatorControllers and AnimatorOverrideControllers (ports of ``safari-tv/mdl_ctrl.py``,
``peak/char_anim.py``, ``repo/player_anim.py`` and the ``peak/mdl_hash*.py`` brute force).

Per controller: parameters (name, type, default), layers (weight, blending, IK pass, synced layer, mask), states
(speed, loop, write defaults, tag, motion = clip or blend tree with its parameters, children and thresholds),
transitions (destination, conditions, duration, exit time, interruption), Any State transitions and the clips.
Override controllers: base controller and the replaced clips.

Names: the controller's own ``m_TOS`` first; hashes it lacks (``Animator.StringToHash`` = CRC32) are looked up in
the TOS of every controller, the string literals of the code index (``code.index``, with ``path:line``), strings of
``dumps/rea`` and clip names, then brute forced as one or two words (``bruteforce=2``, the default: words of the
names above and identifiers of the code index joined by "", " " or "_", solved by CRC32 linearity in
O(words x lengths)). Brute-force names are marked low confidence (CRC32 collisions are common at this size).
"""

from __future__ import annotations

import collections
import json
import re
import zlib
from pathlib import Path
from typing import Iterable

from gst.analysis import Report, UNITY, analysis, counted, md_table
from gst.analysis.unity import Resolver, basename, builtin, ptr
from gst.analysis.unity.animcore import (BLEND_TYPES, CONDITION_MODES, NO_CLIP, PARAM_TYPES, SELECTOR_BASE, clip_key, crc,
                                         iter_typed, short_float, tos_map, un)

WORD_RX = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|[0-9]+")
TOKEN_RX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
CONFIDENCE = {"tos": "exact", "tos_other": "exact", "code": "high", "rea": "medium", "clip": "medium",
              "bruteforce": "low"}


# --- names -------------------------------------------------------------------------------------------

class Names:
    """hash -> (name, source, evidence). Sources in priority order: own TOS, other TOS, code, rea, clip, bruteforce."""

    def __init__(self):
        self.known: dict[int, tuple[str, str, str | None]] = {}
        self.alternatives: dict[int, list[str]] = {}

    def add(self, text: str, source: str, evidence: str | None = None) -> None:
        if not text:
            return
        h = crc(text)
        if h not in self.known:
            self.known[h] = (text, source, evidence)

    def add_many(self, texts: Iterable[str], source: str) -> None:
        for t in texts:
            self.add(t, source)

    def get(self, h: int, own: dict[int, str] | None = None) -> str:
        if own and h in own:
            return own[h]
        k = self.known.get(h)
        return k[0] if k else f"#{h}"

    def source(self, h: int, own: dict[int, str] | None = None) -> str | None:
        if own and h in own:
            return "tos"
        k = self.known.get(h)
        return k[1] if k else None


def words_of(texts: Iterable[str]) -> set[str]:
    """Words and whole identifiers of some strings, with lower/Capitalized variants (brute-force vocabulary)."""
    out: set[str] = set()
    for t in texts:
        if not t or len(t) > 200:
            continue
        for tok in TOKEN_RX.findall(t):
            if len(tok) <= 40:
                out.add(tok)
            for w in WORD_RX.findall(tok):
                if 1 <= len(w) <= 20:
                    out.update((w, w.lower(), w.capitalize()))
    return out


def bruteforce(targets: set[int], words: Iterable[str], seps: tuple[str, ...] = ("", " ", "_")) -> dict[int, list[str]]:
    """Strings of one or two ``words`` (joined by each separator) whose CRC32 is in ``targets``.

    CRC32 is affine: ``crc32(B, state) = crc32(0^n, state) ^ crc32(0^n, 0) ^ crc32(B, 0)`` for ``n = len(B)``, so
    for each first word A and each length n the second word's CRC is ``target ^ L_n(crc32(A + sep))`` - a dict
    lookup instead of a loop over every B."""
    toks = sorted(set(words))
    by_len: dict[int, dict[int, list[str]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for b in toks:
        bb = b.encode("utf-8")
        by_len[len(bb)][zlib.crc32(bb)].append(b)
    zero = {n: zlib.crc32(b"\0" * n) for n in by_len}
    zeros = {n: b"\0" * n for n in by_len}
    found: dict[int, set[str]] = collections.defaultdict(set)
    for d in by_len.values():
        for t in targets:
            for b in d.get(t, ()):
                found[t].add(b)
    for a in toks:
        for sep in seps:
            ca = zlib.crc32((a + sep).encode("utf-8"))
            for n, d in by_len.items():
                lin = zlib.crc32(zeros[n], ca) ^ zero[n]
                for t in targets:
                    hit = d.get(t ^ lin)
                    if hit:
                        for b in hit:
                            s = a + sep + b
                            if crc(s) == t:
                                found[t].add(s)
    return {t: sorted(v, key=_plausible) for t, v in found.items()}


def _plausible(s: str) -> tuple:
    """Order brute-force hits: Title Case words with spaces or CamelCase first, then shorter."""
    words = s.replace("_", " ").split()
    title = all(w[:1].isupper() for w in words)
    return (not title, "_" in s, len(s), s)


def unquote(value: str) -> str:
    """A string literal as the code index stores it (``"Input X"``, ``@"a\\b"``, ``'x'``) -> its text."""
    v = value.strip()
    if v[:2] in ('@"', '$"'):
        v = v[1:]
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        v = v[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return v


def code_strings(db: Path) -> tuple[list[tuple[str, str]], set[str]]:
    """``[(literal, "path:line")]`` from the code index and the identifier names (symbols + refs)."""
    import sqlite3

    lits: list[tuple[str, str]] = []
    idents: set[str] = set()
    con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
    try:
        for value, path, line in con.execute("SELECT s.value, f.path, s.line FROM strings s JOIN files f ON f.id = s.file_id "
                                             "ORDER BY f.path, s.line"):
            value = unquote(value or "")
            if value and len(value) <= 120:
                lits.append((value, f"{path}:{line}"))
        for (name,) in con.execute("SELECT DISTINCT name FROM symbols"):
            if name:
                idents.add(name)
        try:
            for (name,) in con.execute("SELECT DISTINCT name FROM refs"):
                if name:
                    idents.add(name)
        except sqlite3.Error:
            pass
    finally:
        con.close()
    return lits, idents


def rea_strings(rea: Path) -> set[str]:
    out: set[str] = set()
    for p in sorted(rea.rglob("*")):
        if p.suffix.lower() not in (".json", ".md", ".txt") or not p.is_file():
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        out.update(re.findall(r'"([^"\n]{1,60})"', t))
        out.update(re.findall(r"`([^`\n]{1,60})`", t))
    return out


# --- decoding ----------------------------------------------------------------------------------------

def _clip_ref(idx, clips: list[dict]) -> str | None:
    if idx is None:
        return None
    i = int(idx)
    if i < 0 or (i & 0xFFFFFFFF) == NO_CLIP:
        return None
    return clips[i]["name"] if i < len(clips) else f"clip#{i}"


def decode_blend_tree(bt: dict, clips: list[dict], N) -> dict:
    nodes = bt.get("m_NodeArray") or []

    def node(i: int, depth: int = 0) -> dict:
        n = nodes[i] if 0 <= i < len(nodes) else {}
        kids = n.get("m_ChildIndices") or []
        if not kids:
            return {"clip": _clip_ref(n.get("m_ClipID"), clips), "duration": short_float(n.get("m_Duration"), 3)}
        out = {"blend": BLEND_TYPES.get(n.get("m_BlendType"), n.get("m_BlendType")), "param": N(n.get("m_BlendEventID"))}
        if n.get("m_BlendType") in (1, 2, 3):
            out["param_y"] = N(n.get("m_BlendEventYID"))
        th = ((n.get("m_Blend1dData") or {}).get("m_ChildThresholdArray")) or []
        pos = ((n.get("m_Blend2dData") or {}).get("m_ChildPositionArray")) or []
        direct = ((n.get("m_BlendDirectData") or {}).get("m_ChildBlendEventIDArray")) or []
        children = []
        for k, ci in enumerate(kids):
            child = node(int(ci), depth + 1) if depth < 32 else {"clip": "?"}
            if k < len(th):
                child["threshold"] = short_float(th[k], 3)
            if k < len(pos) and isinstance(pos[k], dict):
                child["position"] = [short_float(pos[k].get("x"), 3), short_float(pos[k].get("y"), 3)]
            if k < len(direct):
                child["param"] = N(direct[k])
            children.append(child)
        out["children"] = children
        return out

    return node(0) if nodes else {}


def motion_clips(m: dict) -> list[str]:
    if not m:
        return []
    if "children" in m:
        return [c for ch in m["children"] for c in motion_clips(ch)]
    return [m["clip"]] if m.get("clip") else []


def decode_controller(tree: dict, clips: list[dict], names: Names) -> dict:
    """Plain-dict description of an AnimatorController typetree (``clips`` = its m_AnimationClips as
    ``[{"key", "name"}]``)."""
    own = tos_map(tree)
    ctl = un(tree.get("m_Controller") or {})
    unresolved: set[int] = set()

    def N(h) -> str | None:
        if h is None:
            return None
        h = int(h) & 0xFFFFFFFF
        if h in (0, 0xFFFFFFFF):
            return None
        name = names.get(h, own)
        if name.startswith("#"):
            unresolved.add(h)
        return name

    values = ((ctl.get("m_Values") or {}).get("m_ValueArray")) or []
    dv = ctl.get("m_DefaultValues") or {}
    params = []
    for v in values:
        t = int(v.get("m_Type", 0))
        idx = int(v.get("m_Index", 0))
        arr = {1: "m_FloatValues", 3: "m_IntValues", 4: "m_BoolValues", 9: "m_BoolValues"}.get(t)
        default = None
        if arr and idx < len(dv.get(arr) or []):
            default = dv[arr][idx]
            default = short_float(default, 4) if t == 1 else (int(default) if t == 3 else bool(default))
        h = int(v["m_ID"]) & 0xFFFFFFFF
        p = {"name": N(h), "hash": h, "type": PARAM_TYPES.get(t, t), "default": default}
        src = names.source(h, own)
        if src and src != "tos":
            p["name_source"] = src
            p["confidence"] = CONFIDENCE.get(src, "?")
            k = names.known.get(h)
            if k and k[2]:
                p["evidence"] = k[2]
            if h in names.alternatives and len(names.alternatives[h]) > 1:
                p["alternatives"] = names.alternatives[h][1:6]
        params.append(p)
    pmap = {p["hash"]: p["name"] for p in params}

    sms = ctl.get("m_StateMachineArray") or []
    layers = []
    for li, L in enumerate(ctl.get("m_LayerArray") or []):
        smi = int(L.get("m_StateMachineIndex", 0))
        sm = sms[smi] if 0 <= smi < len(sms) else {}
        sts = sm.get("m_StateConstantArray") or []
        sels = sm.get("m_SelectorStateConstantArray") or []
        state_names = [N(s.get("m_NameID")) for s in sts]

        def dest(d) -> str:
            d = int(d)
            if 0 <= d < len(state_names):
                return state_names[d]
            if d >= SELECTOR_BASE and d - SELECTOR_BASE < len(sels):
                sel = sels[d - SELECTOR_BASE]
                return f"{'entry' if sel.get('m_IsEntry') else 'exit'}:{N(sel.get('m_FullPathID'))}"
            return f"state#{d}"

        def trans(T: dict) -> dict:
            out = {"to": dest(T.get("m_DestinationState", -1)),
                   "conditions": [{"param": N(c.get("m_EventID")), "mode": CONDITION_MODES.get(c.get("m_ConditionMode"),
                                                                                              c.get("m_ConditionMode")),
                                   "threshold": short_float(c.get("m_EventThreshold"), 3)}
                                  for c in T.get("m_ConditionConstantArray") or []],
                   "duration": short_float(T.get("m_TransitionDuration"), 3), "fixed": bool(T.get("m_HasFixedDuration")),
                   "offset": short_float(T.get("m_TransitionOffset"), 3)}
            if T.get("m_HasExitTime"):
                out["exit_time"] = short_float(T.get("m_ExitTime"), 3)
            if T.get("m_InterruptionSource"):
                out["interruption"] = T["m_InterruptionSource"]
            return out

        states = []
        for s in sts:
            bts = s.get("m_BlendTreeConstantArray") or []
            motion = decode_blend_tree(bts[0], clips, N) if bts else {}
            st = {"name": N(s.get("m_NameID")), "path": N(s.get("m_FullPathID")), "speed": short_float(s.get("m_Speed"), 3),
                  "loop": bool(s.get("m_Loop")), "write_defaults": bool(s.get("m_WriteDefaultValues")),
                  "motion": motion, "clips": motion_clips(motion),
                  "transitions": [trans(T) for T in s.get("m_TransitionConstantArray") or []]}
            for key, label in (("m_TagID", "tag"), ("m_SpeedParamID", "speed_param"), ("m_TimeParamID", "time_param"),
                               ("m_MirrorParamID", "mirror_param"), ("m_CycleOffsetParamID", "cycle_offset_param")):
                if s.get(key):
                    st[label] = N(s[key])
            if s.get("m_IKOnFeet"):
                st["ik_on_feet"] = True
            if s.get("m_Mirror"):
                st["mirror"] = True
            states.append(st)
        ds = int(sm.get("m_DefaultState", 0) or 0)
        mask = L.get("m_BodyMask") or {}
        skel = ((L.get("m_SkeletonMask") or {}).get("m_Data")) or []
        layers.append({"name": N(L.get("m_Binding")), "index": li, "weight": short_float(L.get("m_DefaultWeight"), 3),
                       "blending": {0: "Override", 1: "Additive"}.get(L.get("(int&)m_LayerBlendingMode"),
                                                                       L.get("(int&)m_LayerBlendingMode")),
                       "ik_pass": bool(L.get("m_IKPass")), "state_machine": smi,
                       "synced_from": next((k for k, x in enumerate((ctl.get("m_LayerArray") or [])[:li])
                                            if int(x.get("m_StateMachineIndex", -1)) == smi), None),
                       "body_mask_full": all(int(mask.get(f"word{i}", 0xFFFFFFFF)) in (0xFFFFFFFF, 524287)
                                             for i in range(3)) if mask else None,
                       "skeleton_mask": len(skel),
                       "default_state": state_names[ds] if 0 <= ds < len(state_names) else None,
                       "states": states, "any_state": [trans(T) for T in sm.get("m_AnyStateTransitionConstantArray") or []],
                       "selectors": len(sels)})
    blend_trees = sum(1 for L in layers for s in L["states"] if "children" in (s["motion"] or {}))
    return {"name": tree.get("m_Name") or "", "kind": "controller", "params": params, "layers": layers,
            "clips": clips, "param_names": pmap, "unresolved": sorted(unresolved),
            "counts": {"params": len(params), "layers": len(layers), "states": sum(len(L["states"]) for L in layers),
                       "transitions": sum(len(s["transitions"]) for L in layers for s in L["states"])
                       + sum(len(L["any_state"]) for L in layers), "blend_trees": blend_trees, "clips": len(clips),
                       "unresolved_params": sum(1 for p in params if str(p["name"]).startswith("#"))}}


def decode_override(tree: dict, ref) -> dict:
    """``ref(pptr) -> {"key", "name"}`` resolves clip/controller PPtrs."""
    pairs = []
    for c in tree.get("m_Clips") or []:
        o, n = ref(c.get("m_OriginalClip")), ref(c.get("m_OverrideClip"))
        pairs.append({"original": o, "override": n})
    return {"name": tree.get("m_Name") or "", "kind": "override", "base": ref(tree.get("m_Controller")),
            "overrides": pairs}


# --- analysis ----------------------------------------------------------------------------------------

@analysis("controller", engines=UNITY, needs=("unity.inventory", "unity.install"), uses=("code.index", "dumps.rea"),
          description="AnimatorControllers: parameters, layers, states, transitions, blend trees, overrides, #hash names",
          version=1,
          options={"bruteforce": "0 = no brute force, 1 = single words, 2 = one or two words (default)",
                   "tos": "0 = drop parameter names from every m_TOS (what a stripped build looks like: exercises "
                          "the code/REA/brute-force naming)",
                   "file": "regex on install relpaths", "name": "regex on controller names"})
def controller(ctx) -> Report:
    name_rx = re.compile(ctx.opt("name"), re.I) if ctx.opt("name") else None
    depth = int(ctx.opt("bruteforce", 2) or 0)
    keep_tos = str(ctx.opt("tos", "1")).lower() not in ("0", "false", "no")
    raw: list[dict] = []
    for rel, uf, sf, cab, obj, t in iter_typed(ctx, ("AnimatorController", "AnimatorOverrideController")):
        tree = builtin(obj)
        if tree is None:
            raw.append({"file": rel, "cab": cab, "path_id": obj.path_id, "type": t, "error": "typetree read failed"})
            continue
        if name_rx and not name_rx.search(tree.get("m_Name") or ""):
            continue
        res = Resolver(uf)

        def ref(p, _sf=sf, _res=res):
            fid, pid = ptr(p)
            if not pid:
                return None
            return {"key": clip_key(_sf, fid, pid), "name": _res.name(_sf, p)}

        item = {"file": rel, "cab": cab, "path_id": obj.path_id, "type": t, "key": clip_key(sf, 0, obj.path_id)}
        if t == "AnimatorController":
            item["tree"] = {"m_Name": tree.get("m_Name"), "m_TOS": tree.get("m_TOS") or [],
                            "m_Controller": tree.get("m_Controller") or {}}
            if not keep_tos:
                item["tree"]["m_TOS"] = strip_param_names(item["tree"])
            item["clips"] = [r for r in (ref(c) for c in tree.get("m_AnimationClips") or []) if r is not None]
        else:
            item["override"] = decode_override(tree, ref)
        raw.append(item)
    names = Names()
    for it in raw:
        names.add_many((it.get("tree") and tos_map(it["tree"]).values()) or (), "tos_other")
    lits: list[tuple[str, str]] = []
    idents: set[str] = set()
    db = ctx.workspace / "index" / "code.db"
    if db.is_file():
        ctx.use("code.index", db)
        lits, idents = code_strings(db)
        for value, ev in lits:
            names.add(value, "code", ev)
    rea = ctx.workspace / "dumps" / "rea"
    rea_strs: set[str] = rea_strings(rea) if rea.is_dir() else set()
    for s in sorted(rea_strs):
        names.add(s.strip(), "rea")
    clip_names = sorted({c["name"] for it in raw for c in it.get("clips") or [] if c.get("name")})
    names.add_many(clip_names, "clip")
    # what is still unknown after the dictionaries
    targets: set[int] = set()
    for it in raw:
        if "tree" in it:
            d = decode_controller(it["tree"], it["clips"], names)
            targets.update(p["hash"] for p in d["params"] if str(p["name"]).startswith("#"))
    bf_info = {"depth": depth, "targets": len(targets), "vocabulary": 0, "found": 0}
    if targets and depth:
        vocab_src = [s for s in names_texts(raw)] + clip_names + [v for v, _ in lits] + sorted(rea_strs)
        if depth >= 2:
            vocab_src += sorted(idents)
        vocab = words_of(vocab_src)
        bf_info["vocabulary"] = len(vocab)
        ctx.log(f"brute force: {len(targets)} hashes x {len(vocab)} words")
        hits = bruteforce(targets, vocab, seps=("", " ", "_") if depth >= 2 else ())
        for h, cands in hits.items():
            if cands:
                names.add(cands[0], "bruteforce")
                names.alternatives[h] = cands
        bf_info["found"] = len(hits)
    controllers = []
    for it in raw:
        base = {"file": it["file"], **({"cab": it["cab"]} if it.get("cab") else {}), "path_id": it["path_id"],
                "key": it["key"]}
        if "error" in it:
            controllers.append({**base, "name": "?", "kind": it["type"], "error": it["error"]})
        elif "tree" in it:
            controllers.append({**base, **decode_controller(it["tree"], it["clips"], names)})
        else:
            controllers.append({**base, **it["override"]})
    by_key = {c["key"]: c for c in controllers}
    for c in controllers:  # an override plays its base's clips with the replaced ones swapped
        if c.get("kind") == "override":
            b = by_key.get((c.get("base") or {}).get("key"))
            swap = {(p["original"] or {}).get("key"): p["override"] for p in c["overrides"] if p["override"]}
            c["clips"] = [swap.get(x["key"], x) for x in (b or {}).get("clips") or []]
            c["base_name"] = (b or {}).get("name") or (c.get("base") or {}).get("name")
            c["overridden"] = sum(1 for p in c["overrides"] if p["override"])
    controllers.sort(key=lambda c: (c["file"], c.get("cab") or "", c["name"], c["path_id"]))
    return build_report(controllers, names, bf_info, bool(lits), bool(rea_strs))


def strip_param_names(tree: dict) -> list:
    """``m_TOS`` without the parameter names (option ``tos=0``)."""
    values = ((un(tree.get("m_Controller") or {}).get("m_Values") or {}).get("m_ValueArray")) or []
    ids = {int(v["m_ID"]) & 0xFFFFFFFF for v in values}
    return [(h, n) for h, n in tos_map(tree).items() if h not in ids]


def names_texts(raw: list[dict]) -> list[str]:
    out = []
    for it in raw:
        if "tree" in it:
            out += list(tos_map(it["tree"]).values())
    return out


def build_report(controllers: list[dict], names: Names, bf: dict, have_code: bool, have_rea: bool) -> Report:
    ctrls = [c for c in controllers if c.get("kind") == "controller"]
    overrides = [c for c in controllers if c.get("kind") == "override"]
    hashed = [(c["name"], p) for c in ctrls for p in c["params"] if p.get("name_source") or str(p["name"]).startswith("#")]
    src = collections.Counter(p.get("name_source") or "unresolved" for _, p in hashed)
    summary = {"controllers": len(ctrls), "overrides": len(overrides), "errors": sum(1 for c in controllers if "error" in c),
               "params": sum(c["counts"]["params"] for c in ctrls), "layers": sum(c["counts"]["layers"] for c in ctrls),
               "states": sum(c["counts"]["states"] for c in ctrls),
               "params_not_in_tos": len(hashed), "params_unresolved": src.get("unresolved", 0),
               "hash_sources": counted(src), "bruteforce": bf}
    data = {"controllers": controllers,
            "hash_params": [{"controller": cn, **{k: p.get(k) for k in ("name", "hash", "type", "name_source", "confidence",
                                                                        "evidence", "alternatives") if p.get(k) is not None}}
                            for cn, p in hashed]}
    md = [f"{summary['controllers']} AnimatorControllers ({summary['params']} parameters, {summary['layers']} layers, "
          f"{summary['states']} states) and {summary['overrides']} override controllers. Parameters missing from the "
          f"controller's own `m_TOS`: {summary['params_not_in_tos']} ({', '.join(f'{k} {v}' for k, v in src.items()) or '-'}); "
          f"code index {'used' if have_code else 'absent'}, dumps/rea {'used' if have_rea else 'absent'}, brute force "
          f"depth {bf['depth']} over {bf['vocabulary']} words.", "",
          "## Controllers", "",
          md_table(["Controller", "File", "Params", "Layers", "States", "Transitions", "Blend trees", "Clips", "#hash"],
                   [(f"`{c['name']}`", basename(c["file"]) + (f" {c['cab']}" if c.get("cab") else ""), c["counts"]["params"],
                     c["counts"]["layers"], c["counts"]["states"], c["counts"]["transitions"], c["counts"]["blend_trees"],
                     c["counts"]["clips"], c["counts"]["unresolved_params"]) for c in ctrls]), ""]
    if overrides:
        md += ["## Override controllers", "", md_table(["Override", "File", "Base", "Clips replaced"],
                                                       [(f"`{c['name']}`", basename(c["file"]), c.get("base_name"), c.get("overridden"))
                                                        for c in overrides]), ""]
    if hashed:
        md += ["## Parameter names not in the controller's TOS", "",
               "Confidence: code = string literal in the code (exact text), rea = REA evidence, bruteforce = CRC32 match of "
               "one/two words (collisions possible).", "",
               md_table(["Controller", "Hash", "Type", "Name", "Source", "Evidence / alternatives"],
                        [(f"`{cn}`", p["hash"], p["type"], p["name"], p.get("name_source") or "unresolved",
                          p.get("evidence") or ", ".join(p.get("alternatives") or [])) for cn, p in hashed]), ""]
    big = sorted(ctrls, key=lambda c: (-c["counts"]["states"], c["name"]))[:5]
    for c in big:
        md += [f"## `{c['name']}` ({basename(c['file'])}, path id {c['path_id']})", "",
               "Parameters: " + ", ".join(f"{p['name']} ({p['type']})" for p in c["params"]), "",
               md_table(["Layer", "Weight", "Blending", "IK", "Default", "States", "Any State"],
                        [(L["name"], L["weight"], L["blending"], "yes" if L["ik_pass"] else "", L["default_state"],
                          len(L["states"]), len(L["any_state"])) for L in c["layers"]]), ""]
    return Report(data=data, markdown="\n".join(md), summary=summary)
