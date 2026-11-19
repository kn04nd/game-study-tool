"""Code index on synthetic C# sources (written for the tests, not taken from any game)."""

import os
import time

import pytest

pytest.importorskip("tree_sitter_c_sharp")

from gst import index  # noqa: E402
from gst.index import csharp, query  # noqa: E402

ATTRIBUTE = """using System;
namespace Klei.AI
{
    public class Attribute : Resource, IComparable<Attribute>
    {
        public float BaseValue;
        private Calculator calc;
        private static int count = 3, other = 4;
        public string Name { get; set; }
        public event Action<int> OnChanged;
        public Attribute(string id) : base(id) { Name = "attr:" + id; }

        public float GetTotal(AttributeInstance inst)
        {
            var mods = inst.Modifiers;
            float total = BaseValue + Helper.Sum(mods, 2);
            Debug.Log("total value " + total);
            new SaveLoader().Save(total);
            var c = gameObject.GetComponent<Some.Thing>();
            calc.Run();
            Registry.Instance.Run();
            Singleton<Calculator>.Instance.Run();
            return total;
        }

        public enum Kind { Flat, Percent }
    }
}
"""
HELPER = """namespace Klei.AI;

public static class Helper
{
    public static float Sum(object mods, int n) { return Mul(n, 1f); }
    private static float Mul(int a, float b) => a * b;
}

public class Resource { public Resource(string id) { } }
public class Calculator : CalcBase { }
public class CalcBase { public void Run() { } }
public class Other { public void Run() { } }
public class Registry { public static Other Instance; }
public class Singleton<T> { public static T Instance { get; } }
public class Amount : Resource { public Amount() : base("x") { } }
public class Stress : Amount { }
"""


@pytest.fixture
def ws(tmp_path):
    root = tmp_path / "ws"
    src = root / "decomp" / "cs" / "Assembly-CSharp" / "Klei" / "AI"
    src.mkdir(parents=True)
    (src / "Attribute.cs").write_text(ATTRIBUTE, encoding="utf-8")
    (src / "Helper.cs").write_text(HELPER, encoding="utf-8")
    summary = index.build(root, jobs=2)
    assert summary["files"] == 2 and summary["failed"] == 0
    return root


@pytest.fixture
def con(ws):
    c = index.connect(ws)
    yield c
    c.close()


def test_extract_symbols_and_kinds():
    data = csharp.extract("x.cs", ATTRIBUTE.encode())
    by_q = {s[4]: s[2] for s in data["symbols"]}
    assert by_q["Klei.AI.Attribute"] == "class"
    assert by_q["Klei.AI.Attribute.GetTotal"] == "method"
    assert by_q["Klei.AI.Attribute.Attribute"] == "ctor"
    assert by_q["Klei.AI.Attribute.Name"] == "property"
    assert by_q["Klei.AI.Attribute.OnChanged"] == "event"
    assert by_q["Klei.AI.Attribute.count"] == by_q["Klei.AI.Attribute.other"] == "field"
    assert by_q["Klei.AI.Attribute.Kind.Percent"] == "enum_member"
    calls = {(r[0], r[2]) for r in data["refs"]}
    assert {("call", "Sum"), ("call", "Log"), ("call", "Save"), ("call", "GetComponent"), ("new", "SaveLoader")} <= calls


def test_file_scoped_namespace():
    data = csharp.extract("h.cs", HELPER.encode())
    assert "Klei.AI.Helper.Sum" in {s[4] for s in data["symbols"]}


def test_def_and_sym(con):
    hits = query.defs(con, "Attribute")
    assert hits[0]["kind"] == "class" and hits[0]["qname"] == "Klei.AI.Attribute"
    assert query.defs(con, "Attribute.GetTotal")[0]["kind"] == "method"
    assert query.defs(con, "Klei.AI.Attribute.GetTotal(AttributeInstance)")[0]["line"] == 13
    assert query.defs(con, "Nope.GetTotal") == []
    assert any(r["qname"] == "Klei.AI.Attribute.GetTotal" for r in query.symbols(con, "tTot"))


def test_refs_callers_callees(con):
    refs = query.references(con, "Helper.Sum")
    assert refs[0]["owner"] == "Klei.AI.Attribute.GetTotal" and refs[0]["path"].endswith("Attribute.cs")
    assert [c["caller"] for c in query.callers(con, "Helper.Sum")] == ["Klei.AI.Attribute.GetTotal"]
    assert [c["caller"] for c in query.callers(con, "SaveLoader")] == ["Klei.AI.Attribute.GetTotal"]  # type -> `new`
    out = query.callees(con, "Attribute.GetTotal")
    sums = [c for c in out if c["target"] == "Helper.Sum"]
    assert sums and sums[0]["defined_as"] == ["Klei.AI.Helper.Sum"] and sums[0]["resolved_by"] == "type"
    runs = [c for c in out if c["target"] == "calc.Run"]
    assert runs[0]["defined_as"] == ["Klei.AI.CalcBase.Run"] and runs[0]["resolved_by"] == "field-type"  # via base class
    chained = {c["target"]: c["defined_as"] for c in out}
    assert chained["Registry.Instance.Run"] == ["Klei.AI.Other.Run"]
    assert chained["Singleton<Calculator>.Instance.Run"] == ["Klei.AI.CalcBase.Run"]
    mul = query.callees(con, "Helper.Sum")
    assert [(c["target"], c["defined_as"], c["resolved_by"]) for c in mul] == [("Mul", ["Klei.AI.Helper.Mul"], "self")]


def test_show_outline_hier_strings(con, ws):
    shown = query.show(con, ws, "Attribute.GetTotal")
    assert shown["start"] == 13 and "Helper.Sum(mods, 2)" in shown["text"]
    around = query.show(con, ws, "decomp/cs/Assembly-CSharp/Klei/AI/Attribute.cs:15")
    assert around["qname"] == "Klei.AI.Attribute.GetTotal"
    members = [m["name"] for m in query.outline(con, "Attribute")]
    assert members[0] == "Attribute" and {"BaseValue", "GetTotal", "Kind"} <= set(members)
    tree = query.hierarchy(con, "Resource")
    assert [(d["qname"], d["level"]) for d in tree["derived"]] == [
        ("Klei.AI.Attribute", 1), ("Klei.AI.Amount", 1), ("Klei.AI.Stress", 2)]
    assert query.hierarchy(con, "Attribute")["bases"] == ["Resource", "IComparable<Attribute>"]
    found = query.strings(con, "total value")
    assert found and found[0]["owner"] == "Klei.AI.Attribute.GetTotal"


def test_incremental_update(ws):
    helper = ws / "decomp" / "cs" / "Assembly-CSharp" / "Klei" / "AI" / "Helper.cs"
    helper.write_text(HELPER + "\npublic class Extra { }\n", encoding="utf-8")
    later = time.time() + 5
    os.utime(helper, (later, later))
    summary = index.build(ws)
    assert summary["parsed"] == 1 and summary["removed"] == 0
    (ws / "decomp" / "cs" / "Assembly-CSharp" / "Klei" / "AI" / "Attribute.cs").unlink()
    summary = index.build(ws)
    assert summary["parsed"] == 0 and summary["removed"] == 1 and summary["files"] == 1
    con = index.connect(ws)
    assert query.defs(con, "Extra") and not query.defs(con, "GetTotal")
    con.close()


def test_grep_names_the_enclosing_symbol(con, ws, real_tool, monkeypatch):
    from gst import tools

    rg = real_tool("ripgrep")
    if rg is None:
        pytest.skip("ripgrep not installed")
    monkeypatch.setattr(tools, "bin_path", lambda tool, name=None: rg)
    hits = query.grep(con, ws, r"Helper\.Sum")
    assert hits and hits[0]["owner"] == "Klei.AI.Attribute.GetTotal"


def test_query_without_index_explains(tmp_path):
    from gst.common import GsError

    with pytest.raises(GsError, match="gst index"):
        index.connect(tmp_path)
