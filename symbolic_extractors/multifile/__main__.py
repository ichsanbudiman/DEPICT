"""Demo: multifile Strategy-pattern project → local SGRs + resolved graph."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from symbolic_extractors.multifile import process_project_from_sources


SOURCES = {
    "demo/Strategy.java": """
package demo;
public interface Strategy {
    void execute();
}
""",
    "demo/ConcreteStrategy.java": """
package demo;
public class ConcreteStrategy implements Strategy {
    public void execute() {}
}
""",
    "demo/Database.java": """
package demo;
public class Database {}
""",
    "demo/Foo.java": """
package demo;
public class Foo {
    private Strategy strategy;

    public void setStrategy(Strategy strategy) {
        this.strategy = strategy;
    }

    public void run() {
        this.strategy.execute();
        strategy = new ConcreteStrategy();
        Database db = new Database();
    }
}
""",
}


if __name__ == "__main__":
    out = ROOT / "dataset" / "project_001"
    paths = process_project_from_sources(SOURCES, out, project_name="demo-app")

    meta_path = paths["metadata"]
    meta = yaml.safe_load(meta_path.read_text(encoding="utf-8"))
    meta["labels"] = {
        "pattern": "Strategy",
        "violation": True,
        "commit": None,
        "roles": {
            meta["symbols"]["classes"]["demo.Strategy"]["id"]: "strategy",
            meta["symbols"]["classes"]["demo.ConcreteStrategy"]["id"]: "concrete_strategy",
            meta["symbols"]["classes"]["demo.Foo"]["id"]: "context",
            meta["symbols"]["fields"]["demo.Foo.strategy"]["id"]: "strategy_field",
        },
        "notes": (
            "Foo.run() WRITES_FIELD strategy instead of only using setStrategy — "
            "classic Strategy context violation."
        ),
    }
    meta_path.write_text(
        yaml.dump(meta, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    for key in ("graph", "model_graph"):
        gpath = paths[key]
        g = yaml.safe_load(gpath.read_text(encoding="utf-8"))
        g["labels"] = meta["labels"]
        gpath.write_text(
            yaml.dump(g, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )

    print("Wrote multifile SGR artifacts:")
    for key, path in paths.items():
        print(f"  {key}: {path}")
