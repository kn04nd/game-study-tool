"""gst analyze code_architecture and the arquitetura-codigo note template, on synthetic C# sources (no game data)."""

from __future__ import annotations

import json

import pytest

from gst import analysis
from gst.analysis import code_architecture as ca

GAME_MANAGER = """using System;
using UnityEngine;
using Photon.Pun;
using DG.Tweening;

[DefaultExecutionOrder(-100)]
public class GameManager : MonoBehaviour
{
    public static GameManager Instance;

    public event Action OnLevelLoaded;

    private Rigidbody body;

    private void Awake()
    {
        Instance = this;
        body = GetComponent<Rigidbody>();
        DontDestroyOnLoad(gameObject);
    }

    private void Update()
    {
        var other = GetComponent<Collider>();
        string label = "hp " + 3;
        timer -= Time.deltaTime;
        if ((transform.position - Vector3.zero).sqrMagnitude < 4f) { }
    }

    [PunRPC]
    private void RPC_Load()
    {
        OnLevelLoaded?.Invoke();
    }
}
"""

ENEMY = """using UnityEngine;

public class Enemy : MonoBehaviour
{
    private enum EnemyState { Idle, Chase }

    private static readonly int SpeedId = Animator.StringToHash("Speed");

    private void Start()
    {
        GameManager.Instance.OnLevelLoaded += Reset;
        StartCoroutine(Think());
    }

    private void Reset() { }

    private System.Collections.IEnumerator Think()
    {
        yield return null;
    }
}
"""

ITEM_DATA = """using UnityEngine;

namespace Game.Data
{
    [CreateAssetMenu]
    public class ItemData : ScriptableObject
    {
        [SerializeField] private int weight;
    }
}
"""

PHOTON_VIEW = """namespace Photon.Pun
{
    public class PhotonView
    {
        public void RPC(string name) { }
    }
}
"""


def test_library_of_and_namespace_of():
    assert ca.library_of("PhotonUnityNetworking")[0] == "Photon PUN/Realtime"
    assert ca.library_of("DG.Tweening") == ("DOTween", "tweening", "third-party")
    assert ca.library_of("UnityEngine.UI")[2] == "engine"
    assert ca.library_of("System.Linq")[2] == "system"
    assert ca.library_of("Zorro.Core") is None
    assert ca.namespace_of("decomp/cs/Mod/Game/Data/ItemData.cs", "Mod") == "Game.Data"
    assert ca.namespace_of("decomp/cs/Mod/Root.cs", "Mod") == "(global)"


def test_scan_source_tags_hits_with_their_method():
    ranges = ca.method_ranges([("Awake", 15, 20), ("Update", 22, 28), ("RPC_Load", 31, 34)])
    hits = list(ca.scan_source(GAME_MANAGER, ranges))
    by_key = {}
    for key, line, tag, _text in hits:
        by_key.setdefault(key, []).append((line, tag))
    assert [ns for key, _l, ns, _t in hits if key == "using"] == ["System", "UnityEngine", "Photon.Pun", "DG.Tweening"]
    assert by_key["cached_component"] == [(18, "init")]          # GetComponent in Awake
    assert by_key["get_component_hot"] == [(24, "hot")]          # GetComponent in Update
    assert by_key["string_hot"] == [(25, "hot")]
    assert by_key["interval_update"] == [(26, "hot")]
    assert by_key["sqr_magnitude"] == [(27, "hot")]
    assert by_key["singleton"] == [(9, ""), (17, "init")]
    assert by_key["csharp_event"] == [(11, "")] and by_key["rpc"] == [(30, "")]
    assert by_key["execution_order"] == [(6, "")] and by_key["dont_destroy"] == [(19, "init")]


@pytest.fixture
def code_ws(tmp_path):
    from gst import index

    ws = tmp_path / "gs-home" / "g"
    game_code = ws / "decomp" / "cs" / "Assembly-CSharp"
    (game_code / "Game" / "Data").mkdir(parents=True)
    (game_code / "GameManager.cs").write_text(GAME_MANAGER, encoding="utf-8")
    (game_code / "Enemy.cs").write_text(ENEMY, encoding="utf-8")
    (game_code / "Game" / "Data" / "ItemData.cs").write_text(ITEM_DATA, encoding="utf-8")
    lib = ws / "decomp" / "cs" / "PhotonUnityNetworking" / "Photon" / "Pun"
    lib.mkdir(parents=True)
    (lib / "PhotonView.cs").write_text(PHOTON_VIEW, encoding="utf-8")
    install = tmp_path / "install"
    managed = install / "G_Data" / "Managed"
    managed.mkdir(parents=True)
    for name in ("Assembly-CSharp", "PhotonUnityNetworking", "DOTween", "UnityEngine.CoreModule", "Zorro.Core"):
        (managed / f"{name}.dll").write_bytes(b"MZ")
    game = {"gs_workspace": 1, "gst_workspace": 2, "slug": "g", "name": "G", "buildid": "1", "install_dir": str(install),
            "engine": {"key": "unity-mono", "version": "6000"}, "verdict": "offline-ok"}
    (ws / "game.json").write_text(json.dumps(game), encoding="utf-8")
    index.build(ws, jobs=1)
    return ws, game


def test_code_architecture_on_a_small_project(code_ws):
    ws, game = code_ws
    res = analysis.run(ws, game, "code_architecture")
    assert res["status"] == "done", res["reason"]
    doc = json.loads((ws / "analysis" / "code_architecture.json").read_text(encoding="utf-8"))
    d = doc["data"]
    assert d["game_modules"] == ["Assembly-CSharp"]
    mods = {m["name"]: m for m in d["modules"]}
    assert mods["Assembly-CSharp"]["kind"] == "game" and mods["Assembly-CSharp"]["files"] == 3
    assert {n["name"] for n in mods["Assembly-CSharp"]["namespaces"]} == {"(global)", "Game.Data"}
    assert mods["PhotonUnityNetworking"]["library"] == "Photon PUN/Realtime"
    shipped = {a["assembly"]: a for a in d["shipped_assemblies"]}
    assert shipped["DOTween"]["library"] == "DOTween" and not shipped["DOTween"]["decompiled"]
    assert shipped["Zorro.Core"]["kind"] == "other" and shipped["PhotonUnityNetworking"]["decompiled"]
    libs = {r["library"]: r for r in d["libraries"]}
    assert libs["Photon PUN/Realtime"]["game_files"] == 1 and libs["DOTween"]["kind"] == "third-party"
    assert libs["Unity engine"]["game_files"] == 3
    assert d["libraries"][0]["kind"] == "third-party"  # third parties first
    pats = {p["key"]: p for p in d["patterns"]}
    assert pats["scriptable_object"]["count"] == 2 and pats["coroutine"]["count"] == 2
    assert pats["event_subscribe"]["examples"][0]["path"].endswith("Enemy.cs")
    assert pats["get_component_hot"]["hot"] == 1 and pats["cached_component"]["init"] == 1
    assert pats["hash_ids"]["count"] == 1 and pats["state_machine"]["count"] >= 1
    e = d["entry"]
    assert e["lifecycle"]["Awake"] == 1 and e["lifecycle"]["Update"] == 1 and e["per_frame_methods"] == 1
    assert e["execution_order"] == [{"type": "GameManager", "order": -100,
                                     "path": "decomp/cs/Assembly-CSharp/GameManager.cs", "line": 6}]
    managers = {m["type"]: m for m in e["managers"]}
    assert managers["GameManager"]["lifecycle"] == ["Awake", "Update"]
    assert {"base": "MonoBehaviour", "types": 2} in e["bases"]
    md = (ws / "analysis" / "code_architecture.md").read_text(encoding="utf-8")
    assert "## Performance techniques" in md and "| GameManager |" in md
    # deterministic: a second run gives the same data
    analysis.run(ws, game, "code_architecture")
    again = json.loads((ws / "analysis" / "code_architecture.json").read_text(encoding="utf-8"))
    assert again["data"] == d


def test_modules_option_picks_the_game_modules(code_ws):
    ws, game = code_ws
    res = analysis.run(ws, game, "code_architecture", {"modules": "^PhotonUnityNetworking$"})
    assert res["status"] == "done", res["reason"]
    d = json.loads((ws / "analysis" / "code_architecture.json").read_text(encoding="utf-8"))["data"]
    assert d["game_modules"] == ["PhotonUnityNetworking"]
    assert sum(p["count"] for p in d["patterns"]) == 0  # no pattern in that tiny module


def test_arquitetura_codigo_template(code_ws):
    from gst import notetemplates

    ws, game = code_ws
    missing = notetemplates.render(ws, game, "arquitetura-codigo", title="Arquitetura", note="notes/code/a.md",
                                   date="2026-01-01")
    assert "gst analyze code_architecture" in missing  # no analysis yet: says what to run
    analysis.run(ws, game, "code_architecture")
    text = notetemplates.render(ws, game, "arquitetura-codigo", title="Arquitetura", note="notes/code/a.md",
                                date="2026-01-01")
    assert "| Assembly-CSharp | jogo |" in text
    assert "| Photon PUN/Realtime | terceiros | networking | 1 |" in text
    assert "**Técnicas de desempenho**" in text and "**Custos nos métodos por frame**" in text
    assert "GameManager.cs:6" in text  # execution order row
    assert "## 8. Relevância para o meu jogo" in text and "## Não verificado" in text
    assert notetemplates.unfilled(text) >= 7
    assert notetemplates.TEMPLATES["arquitetura-codigo"] == "code"
