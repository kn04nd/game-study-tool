"""Split large REA Evidence into per-namespace JSON and Markdown a person (or an agent) can read.

Port of the study scripts ``notes/formats/tools/rea_modelos.py`` (REPO/Safari-TV): a bundle is first cut per
artifact (one file per SHA-256, same envelope); a managed-members record becomes, per assembly and namespace, the
types with their methods, fields, the engine APIs and game types each one calls (counted) and the string literals
its methods load (``ldstr`` anchors joined to ``user_strings``); an IL2CPP metadata record becomes, per image and
namespace, the types with their methods (RVA and address when the binary was linked) and fields (with types).

Output (``out_dir`` = ``<ws>/dumps/rea/split`` by default)::

    <assembly>/INDEX.md                 namespaces, types, call/field counts
    <assembly>/<namespace>.json         {type: {...}} for that namespace ("_global" = no namespace)
    <assembly>/<namespace>.md           one section per type
"""

from __future__ import annotations

import collections
import json
import os
import re
from pathlib import Path

from gst.common import long_path, write_json, write_text
from gst.rea import evidence

SPLIT_DIR = Path("dumps") / "rea" / "split"

# Engine APIs that matter to model, rig, animation, material and body physics (Unity and Godot), from rea_modelos.py.
ENGINE = frozenset((
    "Animator", "AnimatorStateInfo", "RuntimeAnimatorController", "AnimatorOverrideController", "AnimationClip",
    "Animation", "AnimationCurve", "Avatar", "HumanPoseHandler",
    "SkinnedMeshRenderer", "MeshRenderer", "Renderer", "MeshFilter", "Mesh", "Material", "MaterialPropertyBlock",
    "Shader", "Texture", "Texture2D", "Rigidbody", "ConfigurableJoint", "CharacterJoint", "Joint", "HingeJoint",
    "Collider", "CapsuleCollider", "SphereCollider", "BoxCollider", "Physics",
    "RigBuilder", "Rig", "TwoBoneIKConstraint", "MultiAimConstraint", "RigLayer",
    "Skeleton3D", "AnimationTree", "AnimationPlayer", "AnimationMixer", "AnimationNodeStateMachinePlayback",
    "PhysicalBone3D", "PhysicalBoneSimulator3D", "BoneAttachment3D", "SkeletonModifier3D", "LookAtModifier3D",
    "MeshInstance3D", "GeometryInstance3D", "StandardMaterial3D", "BaseMaterial3D", "ShaderMaterial",
    "RigidBody3D", "CollisionShape3D", "Generic6DofJoint3D",
))
# Framework/helper types left out of "calls game types" (they are everywhere and say nothing about the design).
NOISE_PREFIXES = ("System", "Mathf", "Vector", "Quaternion", "Object", "Debug", "Component", "GameObject",
                  "MonoBehaviour", "List", "Dictionary", "GD", "Node", "Variant", "StringName", "Math", "Time", "Color",
                  "Transform", "Basis", "Godot", "<", "Enumerable", "String", "Array", "Span", "Nullable", "HashSet",
                  "Single", "Int32")


def _safe(name: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")
    return s or "_"


def split_bundle(bundle_path: str | os.PathLike, out_dir: str | os.PathLike) -> list[Path]:
    """One bundle file per artifact SHA-256 (``<artifact name>-<sha12>.json``, same envelope, only its records)."""
    doc = evidence.load(bundle_path)
    if not isinstance(doc, dict) or not isinstance(doc.get("records"), list):
        return []
    groups: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for rec in doc["records"]:
        art = evidence.artifact(rec)
        groups[(art["sha256"], art["name"])].append(rec)
    written = []
    for (sha, name), recs in sorted(groups.items()):
        sub = dict(doc)
        sub["records"] = recs
        target = Path(out_dir) / f"{_safe(name or 'artifact')}-{(sha or 'nosha')[:12]}.json"
        os.makedirs(long_path(target.parent), exist_ok=True)
        with open(long_path(target), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(sub, fh, ensure_ascii=False)
        written.append(target)
    return written


# --- managed members ------------------------------------------------------------------------------

def members_by_type(rec: dict, engine_types=ENGINE) -> dict[str, dict]:
    """``{full type name: {...}}`` from one ``inspect_managed_members`` record (pure; used by split and tests)."""
    nr = evidence.result(rec)
    types: dict[str, dict] = {}

    def entry(full: str) -> dict:
        if full not in types:
            ns, _, name = full.rpartition(".") if "/" not in full else (full.split("/", 1)[0].rpartition(".")[0], "", full)
            types[full] = {"type": full, "namespace": ns, "name": name.split("/")[-1] if name else full,
                           "methods": [], "fields": [], "engine_calls": collections.Counter(),
                           "game_calls": collections.Counter(), "calls": 0, "field_accesses": 0, "strings": []}
        return types[full]

    for t in nr.get("types") or []:
        full = str(t.get("full_name") or t.get("name") or "")
        if full and full != "<Module>":
            entry(full)
    by_token: dict[str, dict] = {}
    for m in nr.get("methods") or []:
        owner = str(m.get("declaring_type") or "")
        if not owner:
            continue
        sig = m.get("signature") or {}
        row = {"name": m.get("name"), "token": m.get("token"), "rva": m.get("rva"),
               "returns": sig.get("return_type"), "params": sig.get("parameter_types") or []}
        entry(owner)["methods"].append(row)
        if m.get("token"):
            by_token[str(m["token"])] = m
    for f in nr.get("fields") or []:
        owner = str(f.get("declaring_type") or "")
        if owner:
            entry(owner)["fields"].append({"name": f.get("name"), "type": (f.get("signature") or {}).get("field_type"),
                                           "flags": f.get("flags")})
    for e in nr.get("call_edges") or []:
        caller = by_token.get(str(e.get("caller_token")))
        owner = str(caller.get("declaring_type")) if caller else evidence.split_member(str(e.get("caller") or ""))[0]
        if not owner:
            continue
        ent = entry(owner)
        ent["calls"] += 1
        target = str(e.get("target_declaring_type") or "")
        tshort = evidence.short_type(target)
        name = str(e.get("target_name") or "?")
        if tshort in engine_types:
            ent["engine_calls"][f"{tshort}.{name}"] += 1
        elif tshort and tshort != evidence.short_type(owner) and not tshort.startswith(NOISE_PREFIXES):
            ent["game_calls"][tshort] += 1
    for a in nr.get("field_accesses") or []:
        method = by_token.get(str(a.get("method_token")))
        owner = str(method.get("declaring_type")) if method else evidence.split_member(str(a.get("method") or ""))[0]
        if owner:
            entry(owner)["field_accesses"] += 1
    literals = {str(lit.get("token")): lit.get("value") for lit in (nr.get("user_strings") or {}).get("literals") or []}
    if literals:
        for m in nr.get("methods") or []:
            owner = str(m.get("declaring_type") or "")
            for anchor in (m.get("body") or {}).get("anchors") or []:
                if anchor.get("opcode") == "ldstr":
                    value = literals.get(str(anchor.get("operand")))
                    strs = entry(owner)["strings"] if owner else None
                    if value and strs is not None and value not in strs:
                        strs.append(value)
    for ent in types.values():
        ent["engine_calls"] = dict(ent["engine_calls"].most_common())
        ent["game_calls"] = dict(ent["game_calls"].most_common())
    return types


def _members_md(assembly: str, namespace: str, types: list[dict], art: dict, evidence_id: str) -> str:
    out = [f"# {assembly} - {namespace or '(global namespace)'}", "",
           f"Source: REA `inspect_managed_members` (static, nothing executed), artifact `{art['path']}`, sha256 "
           f"`{art['sha256'][:16]}...`, evidence `{evidence_id}`.", ""]
    for t in types:
        out.append(f"## {t['name']}")
        out.append(f"- {len(t['methods'])} methods, {len(t['fields'])} fields, {t['calls']} calls, "
                   f"{t['field_accesses']} field accesses")
        if t["engine_calls"]:
            out.append("- Engine APIs (model/animation/physics): "
                       + ", ".join(f"`{k}` {n}" for k, n in t["engine_calls"].items()))
        if t["game_calls"]:
            out.append("- Calls game types: " + ", ".join(f"`{k}` {n}" for k, n in t["game_calls"].items()))
        if t["strings"]:
            out.append("- String literals: " + ", ".join(f"`{v}`" for v in t["strings"]))
        if t["fields"]:
            out.append("- Fields: " + ", ".join(f"`{f['name']}`: {f['type']}" for f in t["fields"]))
        out.append("")
    return "\n".join(out)


# --- IL2CPP metadata ------------------------------------------------------------------------------

def il2cpp_by_image(rec: dict) -> dict[str, dict[str, dict]]:
    """``{image: {full type: {...}}}`` from one ``inspect_il2cpp_metadata`` record (pure)."""
    nr = evidence.result(rec)
    types = nr.get("types") or []
    methods = nr.get("methods") or []
    addresses = nr.get("method_addresses") or []
    fields = nr.get("fields") or []
    field_types = nr.get("field_types") or []
    image_of: dict[int, str] = {}
    for img in nr.get("images") or []:
        start, count = img.get("type_start") or 0, img.get("type_count") or 0
        for i in range(start, start + count):
            image_of[i] = str(img.get("name") or "?")
    names = [il2cpp_type_name(types, i) for i in range(len(types))]
    out: dict[str, dict[str, dict]] = collections.defaultdict(dict)
    by_index: dict[int, dict] = {}
    for i, t in enumerate(types):
        full = names[i]
        if not full or full == "<Module>":
            continue
        root = i
        seen = set()
        while 0 <= (types[root].get("declaring_type") if isinstance(types[root].get("declaring_type"), int) else -1) \
                and root not in seen:
            seen.add(root)
            root = types[root]["declaring_type"]
        ent = {"type": full, "namespace": str(types[root].get("namespace") or ""), "index": i, "methods": [], "fields": []}
        start, count = t.get("method_start"), t.get("method_count") or 0
        if isinstance(start, int) and start >= 0:
            for mi in range(start, start + count):
                if mi >= len(methods):
                    break
                row = {"name": methods[mi].get("name"), "index": mi}
                if mi < len(addresses) and isinstance(addresses[mi], dict):
                    row.update({k: addresses[mi].get(k) for k in ("state", "rva", "address") if k in addresses[mi]})
                ent["methods"].append(row)
        out[image_of.get(i, "?")][full] = ent
        by_index[i] = ent
    for fi, f in enumerate(fields):
        owner = by_index.get(f.get("declaring_type"))
        if owner is not None:
            ftype = field_types[fi].get("name") if fi < len(field_types) and isinstance(field_types[fi], dict) else None
            owner["fields"].append({"name": f.get("name"), "type": ftype, "index": fi})
    return out


def il2cpp_type_name(types: list[dict], i: int) -> str:
    """``Ns.Outer/Inner`` for type row ``i`` (nesting from ``declaring_type``; cycles stop)."""
    parts, seen = [], set()
    cur = i
    while isinstance(cur, int) and 0 <= cur < len(types) and cur not in seen:
        seen.add(cur)
        t = types[cur]
        parts.append(str(t.get("name") or ""))
        parent = t.get("declaring_type")
        if not isinstance(parent, int) or parent < 0:
            ns = str(t.get("namespace") or "")
            name = "/".join(reversed(parts))
            return f"{ns}.{name}" if ns else name
        cur = parent
    return "/".join(reversed(parts))


def _il2cpp_md(image: str, namespace: str, types: list[dict], art: dict, binary: dict | None) -> str:
    out = [f"# {image} - {namespace or '(global namespace)'}", "",
           f"Source: REA `inspect_il2cpp_metadata`, metadata `{art['path']}` (sha256 `{art['sha256'][:16]}...`)"
           + (f", binary `{binary.get('path')}` (sha256 `{str(binary.get('sha256'))[:16]}...`)" if binary else
              ", no binary (no addresses)") + ".", ""]
    for t in types:
        out.append(f"## {t['type']}")
        if t["methods"]:
            out.append("| method | state | RVA |")
            out.append("| --- | --- | --- |")
            out += [f"| `{m['name']}` | {m.get('state', '-')} | {m.get('rva') or '-'} |" for m in t["methods"]]
        if t["fields"]:
            out.append("")
            out.append("Fields: " + ", ".join(f"`{f['name']}`" + (f": {f['type']}" if f.get("type") else "")
                                             for f in t["fields"]))
        out.append("")
    return "\n".join(out)


# --- entry point ----------------------------------------------------------------------------------

def split_file(path: str | os.PathLike, out_dir: str | os.PathLike) -> dict:
    """Split every managed-members / IL2CPP record of one Evidence file (record or bundle). Returns a summary."""
    doc = evidence.load(path)
    summary = {"evidence": str(path), "assemblies": [], "namespaces": 0, "types": 0, "files": 0}
    for rec in evidence.records(doc):
        op = evidence.operation(rec)
        art = evidence.artifact(rec)
        if op == "inspect_managed_members":
            assembly = evidence.assembly(rec)
            groups = {assembly: members_by_type(rec)}
            render = lambda asm, ns, ts: _members_md(asm, ns, ts, art, str(rec.get("evidence_id") or ""))  # noqa: E731
        elif op == "inspect_il2cpp_metadata":
            groups = il2cpp_by_image(rec)
            binary = (evidence.result(rec).get("binary") or {}).get("artifact")
            render = lambda asm, ns, ts: _il2cpp_md(asm, ns, ts, art, binary)  # noqa: E731
        else:
            continue
        for asm, types in groups.items():
            asm_dir = Path(out_dir) / _safe(asm[:-4] if asm.lower().endswith(".dll") else asm)
            by_ns: dict[str, list[dict]] = collections.defaultdict(list)
            for t in types.values():
                by_ns[t["namespace"]].append(t)
            index = [f"# {asm}", "", f"Source: REA `{op}`, `{art['path']}`.", "",
                     "| namespace | types | methods | file |", "| --- | --- | --- | --- |"]
            for ns in sorted(by_ns):
                ts = sorted(by_ns[ns], key=lambda t: t["type"])
                stem = _safe(ns or "_global")
                write_json(asm_dir / f"{stem}.json", {t["type"]: t for t in ts})
                write_text(asm_dir / f"{stem}.md", render(asm, ns, ts))
                index.append(f"| {ns or '(global)'} | {len(ts)} | {sum(len(t['methods']) for t in ts)} | `{stem}.md` |")
                summary["files"] += 2
                summary["namespaces"] += 1
                summary["types"] += len(ts)
            write_text(asm_dir / "INDEX.md", "\n".join(index) + "\n")
            summary["assemblies"].append(asm)
    return summary
