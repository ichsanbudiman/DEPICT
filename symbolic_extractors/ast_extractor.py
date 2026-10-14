import sys
from pathlib import Path

# Allow: python symbolic_extractors/ast_extractor.py
# and:   python -m symbolic_extractors.ast_extractor
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tree_sitter import Language, Parser
import tree_sitter_java as tsjava
import yaml

from symbolic_extractors.base_extractor import base_extractor, id_allocator
from symbolic_extractors.type_extractor import type_extractor
from symbolic_extractors.semantic_model import compilation_unit_from_nested
from symbolic_extractors.graph_builder import GraphBuilder


TYPE_DECLARATIONS = (
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
)


class ast_extractor(base_extractor):
    """
    Pipeline:
      Java bytes → tree-sitter AST → nested extract → Semantic Model → Graph
    """

    def __init__(
        self,
        source_bytes: bytes,
        ids: id_allocator | None = None,
        file_path: str | Path | None = None,
        project_path: str | Path | None = None,
        anonymize: bool = False,
    ):
        super().__init__(source_bytes, ids)
        self.file_path = Path(file_path).resolve() if file_path else None
        if project_path is not None:
            self.project_path = Path(project_path).resolve()
        elif self.file_path is not None:
            self.project_path = self.file_path.parent
        else:
            self.project_path = None
        self.anonymize = anonymize
        self._package_name: str | None = None
        self.type_extractor = type_extractor(source_bytes, self.ids)

    def extract(self, root) -> dict:
        """Extract a CPG graph (nodes + edges)."""
        nested = self.extract_nested(root)
        unit = compilation_unit_from_nested(nested)
        return GraphBuilder(self.ids, anonymize=self.anonymize).build(unit)

    def extract_semantic(self, root):
        """Extract the semantic model only (no graph)."""
        return compilation_unit_from_nested(self.extract_nested(root))

    def extract_nested(self, root) -> dict:
        package_name = None
        imports = []
        type_nodes = []

        for child in root.named_children:
            if child.type == "package_declaration":
                package_name = self._extract_package(child)
            elif child.type == "import_declaration":
                imports.append(self._extract_import(child))
            elif child.type in TYPE_DECLARATIONS:
                type_nodes.append(child)

        self._package_name = package_name
        self.type_extractor = type_extractor(
            self.source_bytes, self.ids, package_name=package_name
        )

        package = self._build_package_node(package_name)
        file_node = self._build_file_node(imports)
        types = [self.type_extractor.extract(node) for node in type_nodes]

        return {
            "package": package,
            "file": file_node,
            "imports": imports,
            "types": types,
        }

    def _build_package_node(self, package_name: str | None) -> dict | None:
        if package_name is None and self.file_path is None:
            return None

        if self.file_path is not None:
            package_fs_path = str(self.file_path.parent)
        elif self.project_path is not None and package_name:
            package_fs_path = str(self.project_path / Path(*package_name.split(".")))
        else:
            package_fs_path = None

        return {
            "id": self.ids.stable("P", package_name or "<default>"),
            "type": "Package",
            "name": package_name,
            "path": package_fs_path,
            "project_path": str(self.project_path) if self.project_path else None,
        }

    def _build_file_node(self, imports: list[str]) -> dict | None:
        if self.file_path is None and self.project_path is None:
            return {
                "id": self.ids.stable("S", "<memory>"),
                "type": "File",
                "name": "<memory>",
                "path": None,
                "project_path": None,
                "imports": imports,
            }

        file_path = self.file_path
        if file_path is not None and self.project_path is not None:
            try:
                file_key = str(file_path.relative_to(self.project_path))
            except ValueError:
                file_key = file_path.name
        else:
            file_key = file_path.name if file_path else "<file>"
        return {
            "id": self.ids.stable("S", file_key),
            "type": "File",
            "name": file_path.name if file_path else None,
            "path": str(file_path) if file_path else None,
            "project_path": str(self.project_path) if self.project_path else None,
            "imports": imports,
        }

    def _extract_package(self, node) -> str | None:
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return self.text(name_node)
        for child in node.named_children:
            if child.type in ("identifier", "scoped_identifier"):
                return self.text(child)
        return None

    def _extract_import(self, node) -> str:
        for child in node.named_children:
            if child.type in ("scoped_identifier", "identifier", "asterisk"):
                return self.text(child)
        return self.text(node) or ""


def parse_java(source: str):
    language = Language(tsjava.language())
    parser = Parser(language)
    source_bytes = source.encode("utf-8")
    tree = parser.parse(source_bytes)
    return tree.root_node, source_bytes


def _read_java_source(path: Path) -> str:
    """Read Java source with encoding fallbacks (many DPD trees are not UTF-8)."""
    raw = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # latin-1 never fails; keep a last-resort replace for completeness
    return raw.decode("utf-8", errors="replace")


def extract_java_file(
    file_path: str | Path,
    project_path: str | Path | None = None,
    ids: id_allocator | None = None,
    anonymize: bool = False,
) -> dict:
    path = Path(file_path).resolve()
    source = _read_java_source(path)
    root, source_bytes = parse_java(source)
    return ast_extractor(
        source_bytes,
        ids=ids,
        file_path=path,
        project_path=project_path,
        anonymize=anonymize,
    ).extract(root)


if __name__ == "__main__":
    sample = """
package demo;

import java.util.List;

class Bar {}

interface Runnable {}

interface Observer {
    void update();
}

interface Strategy {
    void execute();
}

class Database {}

class ConcreteStrategy implements Strategy {
    public void execute() {}
}

public class Foo extends Bar implements Runnable {
    private int x;
    private Strategy strategy;

    private Foo() {}

    public Foo(int x) {
        this.x = x;
    }

    public void setStrategy(Strategy strategy) {
        this.strategy = strategy;
    }

    public final void run() {
        System.out.println(x);
        helper();
        Observer observer;
        if (observer != null) {
            observer.update();
        }
        this.strategy.execute();
        Database db = new Database();
        strategy = new ConcreteStrategy();
        new Foo();
    }

    private void helper() {}
}
"""
    root, source_bytes = parse_java(sample)
    project = Path("/projects/demo-app")
    file_path = project / "src/main/java/demo/Foo.java"
    graph = ast_extractor(
        source_bytes,
        file_path=file_path,
        project_path=project,
    ).extract(root)
    print(yaml.dump(graph, sort_keys=False, allow_unicode=True))
