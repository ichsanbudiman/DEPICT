"""Graph builder: Semantic Model → CPG nodes/edges (SGR v1).

Statements and expressions are separate node kinds.
Every relationship edge uses ``source`` / ``target`` / ``type``.
"""

from __future__ import annotations

from symbolic_extractors.base_extractor import JAVA_PRIMITIVES, id_allocator
from symbolic_extractors.semantic_model import (
    CallInfo,
    CompilationUnit,
    Constructor,
    CreationInfo,
    Expression,
    Method,
    Statement,
    TypeDeclaration,
    Variable,
    compilation_unit_from_nested,
)

KIND_TO_NODE_TYPE = {
    "class": "Class",
    "interface": "Interface",
    "enum": "Enum",
    "record": "Record",
}

MODIFIER_FLAGS = (
    "abstract",
    "final",
    "static",
    "public",
    "private",
    "protected",
)

# Common JDK receivers → (declaring type FQN, library package)
JDK_RECEIVER_TYPES = {
    "System.out": ("java.io.PrintStream", "java.io"),
    "System.err": ("java.io.PrintStream", "java.io"),
    "System.in": ("java.io.InputStream", "java.io"),
}

# java.lang simple names we normalize to FQNs when unresolved.
JAVA_LANG_TYPES = frozenset({
    "String", "Object", "Class", "Integer", "Long", "Double", "Float",
    "Boolean", "Byte", "Short", "Character", "Void", "Throwable",
    "Exception", "RuntimeException", "Error", "Iterable", "Comparable",
})

COLLECTION_RAW_TYPES = frozenset({
    "List", "ArrayList", "LinkedList", "CopyOnWriteArrayList",
    "Set", "HashSet", "LinkedHashSet", "TreeSet", "EnumSet",
    "Collection", "Queue", "Deque", "ArrayDeque", "PriorityQueue",
    "Map", "HashMap", "LinkedHashMap", "TreeMap", "EnumMap", "Hashtable",
    "ConcurrentHashMap", "Vector", "Stack",
})

# Node kinds whose ``name`` / ``qualified_name`` are identifier labels (strip when anonymizing).
IDENTIFIER_NAME_KINDS = frozenset({
    "Identifier",
    "Variable",
    "Parameter",
    "Field",
    "FieldAccess",
    "Method",
    "Constructor",
    "Class",
    "Interface",
    "Enum",
    "Record",
    "Package",
    "File",
    "ExternalType",
    "ThisExpression",
    "SuperExpression",
    "Literal",
})


def normalize_modifiers(modifiers: list[str] | None) -> dict[str, bool]:
    present = set(modifiers or [])
    return {flag: flag in present for flag in MODIFIER_FLAGS}


def parse_type_reference(type_text: str | None) -> dict:
    """Parse ``List<Listener>`` / ``Listener[]`` into raw + type args."""
    if not type_text:
        return {"raw": None, "args": [], "is_array": False, "text": type_text}
    text = type_text.strip()
    is_array = "[]" in text
    no_array = text.replace("[]", "").strip()
    args: list[str] = []
    raw = no_array
    if "<" in no_array and ">" in no_array:
        lt = no_array.find("<")
        gt = no_array.rfind(">")
        if lt >= 0 and gt > lt:
            raw = no_array[:lt].strip()
            inner = no_array[lt + 1 : gt]
            args = [p.strip() for p in _split_type_args(inner) if p.strip()]
    return {"raw": raw, "args": args, "is_array": is_array, "text": type_text}


def _split_type_args(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in inner:
        if ch == "<":
            depth += 1
            buf.append(ch)
        elif ch == ">":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts



def nested_ast_to_graph(
    nested: dict,
    ids: id_allocator | None = None,
    anonymize: bool = False,
) -> dict:
    unit = compilation_unit_from_nested(nested)
    return GraphBuilder(ids or id_allocator(), anonymize=anonymize).build(unit)


def semantic_to_graph(
    unit: CompilationUnit,
    ids: id_allocator | None = None,
    anonymize: bool = False,
) -> dict:
    return GraphBuilder(ids or id_allocator(), anonymize=anonymize).build(unit)


class GraphBuilder:
    def __init__(self, ids: id_allocator, anonymize: bool = False):
        self.ids = ids
        self.anonymize = anonymize
        self.nodes: list[dict] = []
        self.edges: list[dict] = []
        self.type_name_to_id: dict[str, str] = {}
        self.method_name_to_ids: dict[str, list[str]] = {}
        self.field_name_to_ids: dict[str, list[str]] = {}
        self.type_methods: dict[str, dict[str, str]] = {}  # type_id -> {method_name: method_id}
        self.field_type: dict[str, str] = {}  # field_id -> type_id
        self.var_type: dict[str, str] = {}
        self.param_type: dict[str, str] = {}
        self.extends_of: dict[str, str] = {}  # type_id -> superclass type_id
        self.implements_of: dict[str, list[str]] = {}  # type_id -> [interface ids]
        self.primitive_ids: dict[str, str] = {}
        self.external_type_ids: dict[str, str] = {}
        self.external_method_ids: dict[str, str] = {}
        self._scope_names: dict[str, str] = {}
        self._scope_kinds: dict[str, str] = {}  # name -> Field|Parameter|Variable
        self._current_type_id: str | None = None
        self._current_callable_id: str | None = None
        self._unit: CompilationUnit | None = None
        self._access_mode: str = "read"  # read | write
        self._node_by_id: dict[str, dict] = {}
        self._stmt_order: dict[str, int] = {}
        self._current_stmt_order: int | None = None

    def build(self, unit: CompilationUnit) -> dict:
        self._unit = unit
        self._index_types(unit)

        if unit.package is not None:
            self.nodes.append({
                "id": unit.package.id,
                "type": "Package",
                "name": unit.package.name,
                "path": unit.package.path,
                "project_path": unit.package.project_path,
            })

        file_id = None
        if unit.file is not None:
            file_id = unit.file.id
            self.nodes.append({
                "id": unit.file.id,
                "type": "File",
                "name": unit.file.name,
                "path": unit.file.path,
                "project_path": unit.file.project_path,
                "imports": unit.file.imports,
            })
            if unit.package is not None:
                self._edge(unit.file.id, unit.package.id, "IN_PACKAGE")

        for typ in unit.types:
            self._emit_type(typ, file_id)

        self._emit_override_and_implements_method_edges(unit)
        self._emit_delegation_edges()
        self._annotate_write_provenance()
        self._annotate_class_pattern_features()

        if self.anonymize:
            self._anonymize_nodes()

        return {"nodes": self.nodes, "edges": self.edges}

    def _index_types(self, unit: CompilationUnit) -> None:
        for typ in unit.types:
            if typ.name:
                self.type_name_to_id[typ.name] = typ.id
            self.type_methods[typ.id] = {}
            for method in typ.methods:
                if method.name:
                    self.method_name_to_ids.setdefault(method.name, []).append(method.id)
                    self.type_methods[typ.id][method.name] = method.id
            for field in typ.fields:
                if field.name:
                    self.field_name_to_ids.setdefault(field.name, []).append(field.id)

    def _emit_type(self, typ: TypeDeclaration, file_id: str | None) -> None:
        self._current_type_id = typ.id
        self.nodes.append({
            "id": typ.id,
            "type": KIND_TO_NODE_TYPE.get(typ.kind, "Type"),
            "name": typ.name,
            "qualified_name": typ.qualified_name,
            "modifiers": normalize_modifiers(typ.modifiers),
            "annotations": typ.annotations,
            "generic_parameters": typ.generic_parameters,
            "line_start": typ.span.line_start,
            "line_end": typ.span.line_end,
        })
        if file_id is not None:
            self._edge(file_id, typ.id, "DEFINES")

        if typ.extends is not None:
            parent_id = self._resolve_type_name(typ.extends.name)
            self.extends_of[typ.id] = parent_id
            self._edge(typ.id, parent_id, "EXTENDS")
        for iface in typ.implements:
            iface_id = self._resolve_type_name(iface.name)
            self.implements_of.setdefault(typ.id, []).append(iface_id)
            self._edge(typ.id, iface_id, "IMPLEMENTS")

        for field in typ.fields:
            parsed = parse_type_reference(field.type_name)
            field_node = {
                "id": field.id,
                "type": "Field",
                "name": field.name,
                "qualified_name": field.qualified_name,
                "modifiers": normalize_modifiers(field.modifiers),
                "annotations": field.annotations,
                "generic_parameters": list(parsed["args"]),
                "datatype": field.type_name,
                "line_start": field.span.line_start,
                "line_end": field.span.line_end,
            }
            raw = parsed["raw"]
            if raw and (raw.split(".")[-1] in COLLECTION_RAW_TYPES or parsed["is_array"]):
                field_node["is_collection"] = True
            if parsed["is_array"]:
                field_node["is_array"] = True
            self.nodes.append(field_node)
            self._edge(typ.id, field.id, "HAS_FIELD")
            if raw:
                type_id = self._resolve_type_name(raw)
                self.field_type[field.id] = type_id
                self._edge(field.id, type_id, "HAS_TYPE")
                for arg in parsed["args"]:
                    elem_id = self._resolve_type_name(arg)
                    self._edge(field.id, elem_id, "HAS_ELEMENT_TYPE")
            elif field.type_name:
                type_id = self._resolve_type_name(field.type_name)
                self.field_type[field.id] = type_id
                self._edge(field.id, type_id, "HAS_TYPE")

        for ctor in typ.constructors:
            self._emit_callable(typ, ctor, is_constructor=True)
        for method in typ.methods:
            self._emit_callable(typ, method, is_constructor=False)

    def _emit_callable(
        self,
        typ: TypeDeclaration,
        callable_node: Method | Constructor,
        is_constructor: bool,
    ) -> None:
        self._current_type_id = typ.id
        self._current_callable_id = callable_node.id
        self.nodes.append({
            "id": callable_node.id,
            "type": "Constructor" if is_constructor else "Method",
            "name": callable_node.name,
            "qualified_name": callable_node.qualified_name,
            "modifiers": normalize_modifiers(callable_node.modifiers),
            "annotations": callable_node.annotations,
            "generic_parameters": callable_node.generic_parameters,
            "line_start": callable_node.span.line_start,
            "line_end": callable_node.span.line_end,
        })
        if is_constructor:
            ctor = callable_node  # type: Constructor
            self.nodes[-1]["synthetic"] = bool(ctor.synthetic)
            self.nodes[-1]["implicit"] = bool(ctor.implicit)
        self._edge(
            typ.id,
            callable_node.id,
            "HAS_CONSTRUCTOR" if is_constructor else "HAS_METHOD",
        )

        self._scope_names = {}
        self._scope_kinds = {}
        for field_name, field_ids in self.field_name_to_ids.items():
            if len(field_ids) == 1:
                self._scope_names[field_name] = field_ids[0]
                self._scope_kinds[field_name] = "Field"

        if not is_constructor:
            method = callable_node  # type: Method
            if method.return_type:
                parsed = parse_type_reference(method.return_type)
                self.nodes[-1]["return_type"] = method.return_type
                if parsed["args"]:
                    self.nodes[-1]["return_type_args"] = list(parsed["args"])
                # Always emit RETURNS, including void/primitives/arrays.
                # Ground truth (sgr-testbed) requires exactly one RETURNS per method.
                raw = parsed["raw"] or method.return_type
                self._edge(
                    method.id,
                    self._resolve_type_name(raw),
                    "RETURNS",
                )
                for arg in parsed["args"]:
                    self._edge(
                        method.id,
                        self._resolve_type_name(arg),
                        "RETURNS_ELEMENT",
                    )

        for param in callable_node.parameters:
            parsed = parse_type_reference(param.datatype)
            self.nodes.append({
                "id": param.id,
                "type": "Parameter",
                "name": param.name,
                "datatype": param.datatype,
                "generic_parameters": list(parsed["args"]),
                "annotations": param.annotations,
                "line_start": param.span.line_start,
                "line_end": param.span.line_end,
            })
            if parsed["raw"] and (
                parsed["raw"].split(".")[-1] in COLLECTION_RAW_TYPES or parsed["is_array"]
            ):
                self.nodes[-1]["is_collection"] = True
            self._edge(callable_node.id, param.id, "HAS_PARAMETER")
            self._edge(param.id, callable_node.id, "DECLARED_IN")
            if param.name:
                self._scope_names[param.name] = param.id
                self._scope_kinds[param.name] = "Parameter"
            if parsed["raw"] or param.datatype:
                type_id = self._resolve_type_name(parsed["raw"] or param.datatype or "")
                self.param_type[param.id] = type_id
                self._edge(param.id, type_id, "HAS_TYPE")
                for arg in parsed["args"]:
                    self._edge(param.id, self._resolve_type_name(arg), "HAS_ELEMENT_TYPE")

        if callable_node.statements:
            self._emit_statement_list(
                callable_node.id, callable_node.statements, parent_edge="HAS_STATEMENT"
            )
        else:
            for variable in callable_node.variables:
                self._emit_variable(callable_node.id, variable)
            for creation in callable_node.creations:
                self._emit_creation(callable_node.id, creation)

    def _emit_statement_list(
        self, owner_id: str, stmts: list[Statement], parent_edge: str
    ) -> None:
        prev: Statement | None = None
        for stmt in stmts:
            self._emit_statement(owner_id, stmt, parent_edge=parent_edge)
            if prev is not None:
                self._edge(prev.id, stmt.id, "NEXT_STATEMENT")
            prev = stmt

    def _emit_statement(
        self, owner_id: str, stmt: Statement, parent_edge: str
    ) -> None:
        self.nodes.append({
            "id": stmt.id,
            "type": stmt.kind,
            "order": stmt.order,
            "line_start": stmt.span.line_start,
            "line_end": stmt.span.line_end,
        })
        self._stmt_order[stmt.id] = stmt.order if stmt.order is not None else 0
        prev_order = self._current_stmt_order
        self._current_stmt_order = stmt.order if stmt.order is not None else 0
        self._edge(owner_id, stmt.id, parent_edge)

        for variable in stmt.variables:
            self._emit_variable(stmt.id, variable)

        if stmt.expression is not None:
            expr_id = stmt.expression.id
            self._emit_expression(stmt.id, stmt.expression, parent_edge="HAS_EXPRESSION")
            if stmt.kind == "LocalVariableDeclaration":
                for variable in stmt.variables:
                    self._edge(variable.id, expr_id, "INITIALIZED_BY")
            if not (
                stmt.kind == "LocalVariableDeclaration"
                and stmt.expression.kind == "ObjectCreation"
            ):
                for creation in stmt.creations:
                    self._emit_creation(stmt.id, creation)
        else:
            for creation in stmt.creations:
                self._emit_creation(stmt.id, creation)

        if stmt.then_branch:
            self._emit_statement_list(stmt.id, stmt.then_branch, parent_edge="THEN")
        if stmt.else_branch:
            self._emit_statement_list(stmt.id, stmt.else_branch, parent_edge="ELSE")
        if stmt.body:
            self._emit_statement_list(stmt.id, stmt.body, parent_edge="HAS_STATEMENT")

        self._current_stmt_order = prev_order

    def _emit_expression(
        self, owner_id: str, expr: Expression, parent_edge: str
    ) -> None:
        node = {
            "id": expr.id,
            "type": expr.kind,
            "line_start": expr.span.line_start,
            "line_end": expr.span.line_end,
        }
        if expr.name is not None and expr.kind in (
            "Identifier", "Literal", "FieldAccess", "ThisExpression", "SuperExpression"
        ):
            node["name"] = expr.name
        if expr.operator is not None:
            node["operator"] = expr.operator
        if expr.type_name is not None:
            node["type_name"] = expr.type_name
        self.nodes.append(node)
        self._edge(owner_id, expr.id, parent_edge)

        if expr.kind == "MethodInvocation":
            if expr.call is not None:
                target = self._resolve_call(expr.call, receiver_expr=expr.receiver)
                self._edge(expr.id, target, "CALLS", order=self._order_for(owner_id))
            if expr.receiver is not None:
                self._emit_expression(expr.id, expr.receiver, parent_edge="RECEIVER")
            elif expr.call and expr.call.receiver_expression:
                recv_id = self._resolve_name(expr.call.receiver_expression)
                if recv_id:
                    self._edge(expr.id, recv_id, "RECEIVER")
            for arg in expr.arguments:
                self._emit_expression(expr.id, arg, parent_edge="ARGUMENT")

        elif expr.kind == "ObjectCreation":
            if expr.type_name:
                self._edge(
                    expr.id,
                    self._resolve_type_name(expr.type_name),
                    "CREATES",
                    order=self._order_for(owner_id),
                )
            for arg in expr.arguments:
                self._emit_expression(expr.id, arg, parent_edge="ARGUMENT")

        elif expr.kind == "Assignment":
            if expr.lhs is not None:
                prev = self._access_mode
                self._access_mode = "write"
                self._emit_expression(expr.id, expr.lhs, parent_edge="LHS")
                self._access_mode = prev
            if expr.rhs is not None:
                prev = self._access_mode
                self._access_mode = "read"
                self._emit_expression(expr.id, expr.rhs, parent_edge="RHS")
                self._access_mode = prev

        elif expr.kind == "FieldAccess":
            if expr.object is not None:
                self._emit_expression(expr.id, expr.object, parent_edge="RECEIVER")
            if expr.name:
                field_ids = self.field_name_to_ids.get(expr.name, [])
                if len(field_ids) == 1:
                    self._emit_field_access(expr.id, field_ids[0], owner_id)

        elif expr.kind == "BinaryExpression":
            if expr.lhs is not None:
                self._emit_expression(expr.id, expr.lhs, parent_edge="LHS")
            if expr.rhs is not None:
                self._emit_expression(expr.id, expr.rhs, parent_edge="RHS")

        elif expr.kind == "Identifier":
            target = self._resolve_name(expr.name)
            if target:
                kind = self._scope_kinds.get(expr.name or "", "")
                if kind == "Field" or target in self.field_type:
                    self._emit_field_access(expr.id, target, owner_id)
                else:
                    edge = "WRITES_LOCAL" if self._access_mode == "write" else "REFERS_TO"
                    self._edge(expr.id, target, edge)

        for child in expr.children:
            self._emit_expression(expr.id, child, parent_edge="HAS_EXPRESSION")

    def _emit_field_access(self, expr_id: str, field_id: str, owner_id: str) -> None:
        edge = "WRITES_FIELD" if self._access_mode == "write" else "READS_FIELD"
        self._edge(expr_id, field_id, edge, order=self._current_stmt_order)

    def _order_for(self, node_id: str) -> int | None:
        if self._current_stmt_order is not None:
            return self._current_stmt_order
        if node_id in self._stmt_order:
            return self._stmt_order[node_id]
        return None

    def _emit_variable(self, owner_id: str, variable: Variable) -> None:
        if any(n.get("id") == variable.id for n in self.nodes):
            if not any(
                e.get("source") == owner_id
                and e.get("target") == variable.id
                and e.get("type") == "HAS_VARIABLE"
                for e in self.edges
            ):
                self._edge(owner_id, variable.id, "HAS_VARIABLE")
            if variable.name:
                self._scope_names[variable.name] = variable.id
                self._scope_kinds[variable.name] = "Variable"
            return

        self.nodes.append({
            "id": variable.id,
            "type": "Variable",
            "name": variable.name,
            "line_start": variable.span.line_start,
            "line_end": variable.span.line_end,
        })
        self._edge(owner_id, variable.id, "HAS_VARIABLE")
        if self._current_callable_id is not None:
            self._edge(variable.id, self._current_callable_id, "DECLARED_IN")
        if variable.name:
            self._scope_names[variable.name] = variable.id
            self._scope_kinds[variable.name] = "Variable"
        if variable.type_name:
            type_id = self._resolve_type_name(variable.type_name)
            self.var_type[variable.id] = type_id
            self._edge(variable.id, type_id, "HAS_TYPE")

    def _emit_creation(self, owner_id: str, creation: CreationInfo) -> None:
        source = creation.variable_id or owner_id
        if not creation.type_name:
            return
        self._edge(source, self._resolve_type_name(creation.type_name), "CREATES")

    def _emit_override_and_implements_method_edges(self, unit: CompilationUnit) -> None:
        """Add OVERRIDES (inheritance) and IMPLEMENTS_METHOD (interface) edges."""
        for typ in unit.types:
            # Interface method implementation
            for iface_id in self.implements_of.get(typ.id, []):
                iface_methods = self.type_methods.get(iface_id, {})
                class_methods = self.type_methods.get(typ.id, {})
                for name, iface_mid in iface_methods.items():
                    if name in class_methods:
                        self._edge(class_methods[name], iface_mid, "IMPLEMENTS_METHOD")

            # Inheritance overrides (walk superclass chain)
            parent_id = self.extends_of.get(typ.id)
            seen = set()
            while parent_id and parent_id not in seen:
                seen.add(parent_id)
                parent_methods = self.type_methods.get(parent_id, {})
                class_methods = self.type_methods.get(typ.id, {})
                for name, parent_mid in parent_methods.items():
                    if name in class_methods and class_methods[name] != parent_mid:
                        self._edge(class_methods[name], parent_mid, "OVERRIDES")
                parent_id = self.extends_of.get(parent_id)

    def _infer_expr_type(self, expr: Expression | None) -> str | None:
        if expr is None:
            return None
        if expr.kind == "ThisExpression":
            return self._current_type_id
        if expr.kind == "FieldAccess" and expr.name:
            field_ids = self.field_name_to_ids.get(expr.name, [])
            if len(field_ids) == 1:
                return self.field_type.get(field_ids[0])
        if expr.kind == "Identifier" and expr.name:
            nid = self._resolve_name(expr.name)
            if not nid:
                return None
            return (
                self.var_type.get(nid)
                or self.param_type.get(nid)
                or self.field_type.get(nid)
            )
        if expr.kind == "MethodInvocation" and expr.call and expr.call.target_name:
            # Return type of callee if uniquely resolvable on receiver type
            recv_type = self._infer_expr_type(expr.receiver)
            if recv_type:
                mid = self.type_methods.get(recv_type, {}).get(expr.call.target_name)
                # Without return-type index, stop here
                _ = mid
            return None
        return None

    def _lookup_method_on_type(self, type_id: str | None, method_name: str | None) -> str | None:
        if not type_id or not method_name:
            return None
        # Direct
        mid = self.type_methods.get(type_id, {}).get(method_name)
        if mid:
            return mid
        # Interfaces implemented by this type
        for iface_id in self.implements_of.get(type_id, []):
            mid = self.type_methods.get(iface_id, {}).get(method_name)
            if mid:
                return mid
        # Superclass chain
        parent = self.extends_of.get(type_id)
        seen = set()
        while parent and parent not in seen:
            seen.add(parent)
            mid = self.type_methods.get(parent, {}).get(method_name)
            if mid:
                return mid
            for iface_id in self.implements_of.get(parent, []):
                mid = self.type_methods.get(iface_id, {}).get(method_name)
                if mid:
                    return mid
            parent = self.extends_of.get(parent)
        return None

    def _resolve_name(self, name: str | None) -> str | None:
        if not name:
            return None
        simple = name.split(".")[-1]
        return self._scope_names.get(name) or self._scope_names.get(simple)

    def _resolve_type_name(self, type_name: str) -> str:
        parsed = parse_type_reference(type_name)
        base = (parsed["raw"] or type_name).replace("[]", "").split("<", 1)[0].strip()
        simple = base.split(".")[-1]

        if base in JAVA_PRIMITIVES or simple in JAVA_PRIMITIVES:
            key = base if base in JAVA_PRIMITIVES else simple
            return self._primitive_id(key)

        if simple in self.type_name_to_id:
            return self.type_name_to_id[simple]
        if base in self.type_name_to_id:
            return self.type_name_to_id[base]

        # Normalize well-known JDK types so they don't become demo.String / demo.List.
        if "." not in base:
            if simple in JAVA_LANG_TYPES:
                base = f"java.lang.{simple}"
            elif simple in COLLECTION_RAW_TYPES:
                base = f"java.util.{simple}"

        return self._external_type_id(base)

    def _primitive_id(self, name: str) -> str:
        if name not in self.primitive_ids:
            pid = self.ids.stable("T", name)
            self.primitive_ids[name] = pid
            self.nodes.append({"id": pid, "type": "PrimitiveType", "name": name})
        return self.primitive_ids[name]

    def _external_type_id(self, name: str) -> str:
        """Key ExternalTypes by package-qualified name when possible.

        Same-package Java resolution assumes ``demo.Strategy`` rather than a
        bare ``Strategy`` key, so two unrelated ``Logger`` types in different
        packages do not collapse into one node.
        """
        key = name
        if "." not in name and self._unit and self._unit.package and self._unit.package.name:
            key = f"{self._unit.package.name}.{name}"
        if key not in self.external_type_ids:
            eid = self.ids.stable("ET", key)
            self.external_type_ids[key] = eid
            # Also index simple name for in-file lookups of the same key.
            if name not in self.external_type_ids:
                self.external_type_ids[name] = eid
            self.nodes.append({
                "id": eid,
                "type": "ExternalType",
                "name": name.split(".")[-1],
                "qualified_name": key,
                "resolved": False,
            })
        return self.external_type_ids[key]

    def _resolve_call(
        self,
        call: CallInfo,
        receiver_expr: Expression | None = None,
    ) -> str:
        name = call.target_name

        # 1) Type-aware resolution via receiver expression (Strategy/Observer key path)
        recv_type = self._infer_expr_type(receiver_expr)
        typed = self._lookup_method_on_type(recv_type, name)
        if typed:
            return typed

        # 1b) Receiver typed as ExternalType / unknown project type → external stub
        #     (do NOT fall through to same-class method name; that breaks Decorator).
        if recv_type and name:
            owner = next((n for n in self.nodes if n["id"] == recv_type), None)
            if owner and (
                owner.get("type") == "ExternalType"
                or (
                    owner.get("type") in ("Class", "Interface", "Enum", "Record")
                    and name not in self.type_methods.get(recv_type, {})
                )
            ):
                owner_name = owner.get("qualified_name") or owner.get("name") or ""
                return self._external_method_id(owner_name, name)

        # 2) Textual receiver like "strategy" / "observer" → variable/field type
        if call.receiver_expression and name:
            recv_name = call.receiver_expression.split(".")[-1]
            nid = self._resolve_name(recv_name)
            if nid:
                type_id = (
                    self.var_type.get(nid)
                    or self.param_type.get(nid)
                    or self.field_type.get(nid)
                )
                typed = self._lookup_method_on_type(type_id, name)
                if typed:
                    return typed
                if type_id:
                    owner = next((n for n in self.nodes if n["id"] == type_id), None)
                    if owner and owner.get("type") == "ExternalType":
                        owner_name = owner.get("qualified_name") or owner.get("name") or ""
                        return self._external_method_id(owner_name, name)

        # 3) Unique in-file method name — only for unqualified calls (no receiver).
        has_receiver = bool(receiver_expr or call.receiver_expression)
        if not has_receiver:
            candidates = self.method_name_to_ids.get(name, []) if name else []
            if len(candidates) == 1:
                return candidates[0]

        # 4) External / unresolved library or unknown call
        receiver = call.receiver_expression or ""
        return self._external_method_id(receiver, name)

    def _external_method_id(self, receiver: str, simple_name: str | None) -> str:
        qualified_name, library = self._normalize_external_method(receiver, simple_name)
        if qualified_name not in self.external_method_ids:
            emid = self.ids.stable("EM", qualified_name)
            self.external_method_ids[qualified_name] = emid
            self.nodes.append({
                "id": emid,
                "type": "Method",
                "name": simple_name,
                "qualified_name": qualified_name,
                "external": True,
                "library": library,
            })
        return self.external_method_ids[qualified_name]

    def _normalize_external_method(
        self, receiver: str, simple_name: str | None
    ) -> tuple[str, str | None]:
        """Map receivers like System.out.println to JDK FQNs when known."""
        method = simple_name or "<unknown>"
        recv = (receiver or "").strip()

        if recv in JDK_RECEIVER_TYPES:
            type_fqn, library = JDK_RECEIVER_TYPES[recv]
            return f"{type_fqn}.{method}", library

        # Already looks like a Java package FQN: java.util.List.add
        if recv.startswith("java.") or recv.startswith("javax."):
            parts = recv.split(".")
            library = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
            if method and not recv.endswith(f".{method}"):
                return f"{recv}.{method}", library
            return recv, library

        # Fallback: keep expression-style qualifier (unresolved project/external).
        if recv and method:
            return f"{recv}.{method}", None
        return method, None

    def _anonymize_nodes(self) -> None:
        """Strip identifier labels; keep topology and semantic type edges."""
        for node in self.nodes:
            if node.get("type") in IDENTIFIER_NAME_KINDS:
                node.pop("name", None)
                node.pop("qualified_name", None)
                node.pop("datatype", None)
                node.pop("type_name", None)
            # Keep PrimitiveType.name (int/boolean) — semantic, not a program identifier label.
            if node.get("type") == "File":
                node.pop("imports", None)

    def _edge(
        self,
        source: str,
        target: str,
        edge_type: str,
        order: int | None = None,
        **attrs,
    ) -> None:
        edge = {
            "source": source,
            "target": target,
            "type": edge_type,
        }
        if order is not None:
            edge["order"] = order
        for key, value in attrs.items():
            if value is not None:
                edge[key] = value
        self.edges.append(edge)

    def _emit_delegation_edges(self) -> None:
        """
        Method --DELEGATES_VIA(field|parameter)--> callee.

        Derived from MethodInvocation RECEIVER chains that terminate at a
        Field (Decorator/Adapter/Facade) or Parameter (Visitor accept).
        """
        node_by_id = {n["id"]: n for n in self.nodes}
        parent: dict[str, str] = {}
        receivers: dict[str, list[str]] = {}
        field_reads: dict[str, list[str]] = {}
        refers: dict[str, list[str]] = {}
        calls: list[tuple[str, str]] = []

        for e in self.edges:
            et = e["type"]
            if et in (
                "HAS_EXPRESSION", "HAS_STATEMENT", "THEN", "ELSE",
                "LHS", "RHS", "ARGUMENT", "INITIALIZED_BY",
            ):
                parent[e["target"]] = e["source"]
            elif et == "RECEIVER":
                receivers.setdefault(e["source"], []).append(e["target"])
            elif et in ("READS_FIELD", "ACCESSES_FIELD"):
                field_reads.setdefault(e["source"], []).append(e["target"])
            elif et in ("REFERS_TO", "WRITES_LOCAL"):
                refers.setdefault(e["source"], []).append(e["target"])
            elif et == "CALLS":
                calls.append((e["source"], e["target"]))

        def enclosing_callable(nid: str) -> str | None:
            cur: str | None = nid
            seen: set[str] = set()
            while cur and cur not in seen:
                n = node_by_id.get(cur)
                if n and n.get("type") in ("Method", "Constructor"):
                    return cur
                seen.add(cur)
                cur = parent.get(cur)
            return None

        def resolve_via(recv_id: str) -> tuple[str | None, str | None]:
            """Return (via_id, via_kind) for a receiver expression."""
            for fid in field_reads.get(recv_id, []):
                return fid, "field"
            for tid in refers.get(recv_id, []):
                t = node_by_id.get(tid) or {}
                if t.get("type") == "Field":
                    return tid, "field"
                if t.get("type") == "Parameter":
                    return tid, "parameter"
            # Nested FieldAccess: this.x → RECEIVER(this) + READS_FIELD(x) on same node
            n = node_by_id.get(recv_id) or {}
            if n.get("type") == "FieldAccess":
                for fid in field_reads.get(recv_id, []):
                    return fid, "field"
            return None, None

        seen_keys: set[tuple[str, str, str, str]] = set()
        for call_expr, callee in calls:
            owner = enclosing_callable(call_expr)
            if not owner:
                continue
            for recv in receivers.get(call_expr, []):
                via_id, via_kind = resolve_via(recv)
                if not via_id or not via_kind:
                    continue
                key = (owner, callee, via_kind, via_id)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                self._edge(
                    owner,
                    callee,
                    "DELEGATES_VIA",
                    via=via_id,
                    via_kind=via_kind,
                )

    def _annotate_write_provenance(self) -> None:
        """Mark WRITES_FIELD with provenance: parameter | creation | other."""
        node_by_id = {n["id"]: n for n in self.nodes}
        lhs_of: dict[str, str] = {}  # lhs expr -> assignment
        rhs_of: dict[str, str] = {}  # assignment -> rhs expr
        refers: dict[str, list[str]] = {}
        creates: dict[str, list[str]] = {}
        children: dict[str, list[str]] = {}

        for e in self.edges:
            if e["type"] == "LHS":
                lhs_of[e["target"]] = e["source"]
            elif e["type"] == "RHS":
                rhs_of[e["source"]] = e["target"]
            elif e["type"] == "REFERS_TO":
                refers.setdefault(e["source"], []).append(e["target"])
            elif e["type"] == "CREATES":
                creates.setdefault(e["source"], []).append(e["target"])
            elif e["type"] in ("HAS_EXPRESSION", "ARGUMENT", "RECEIVER"):
                children.setdefault(e["source"], []).append(e["target"])

        def walk(nid: str) -> list[str]:
            out = [nid]
            for c in children.get(nid, []):
                out.extend(walk(c))
            return out

        for e in self.edges:
            if e["type"] != "WRITES_FIELD":
                continue
            write_expr = e["source"]
            assign = lhs_of.get(write_expr)
            # FieldAccess may be nested under Assignment LHS directly
            if assign is None:
                # write_expr itself might be under another expr that is LHS
                for cand_expr, assign_id in lhs_of.items():
                    if write_expr == cand_expr or write_expr in walk(cand_expr):
                        assign = assign_id
                        break
            provenance = "other"
            if assign and assign in rhs_of:
                rhs = rhs_of[assign]
                rhs_nodes = walk(rhs)
                for rid in rhs_nodes:
                    for tid in refers.get(rid, []):
                        if (node_by_id.get(tid) or {}).get("type") == "Parameter":
                            provenance = "parameter"
                            break
                    if provenance == "parameter":
                        break
                    if creates.get(rid) or (node_by_id.get(rid) or {}).get("type") == "ObjectCreation":
                        provenance = "creation"
                        break
            e["provenance"] = provenance

    def _annotate_class_pattern_features(self) -> None:
        """Adapter/Decorator/Facade cues on Class/Interface nodes."""
        node_by_id = {n["id"]: n for n in self.nodes}
        methods_of: dict[str, list[str]] = {}
        field_type: dict[str, str] = {}
        implements_of: dict[str, list[str]] = {}

        for e in self.edges:
            if e["type"] == "HAS_METHOD":
                methods_of.setdefault(e["source"], []).append(e["target"])
            elif e["type"] == "HAS_TYPE":
                src = node_by_id.get(e["source"]) or {}
                if src.get("type") == "Field":
                    field_type[e["source"]] = e["target"]
            elif e["type"] == "IMPLEMENTS":
                implements_of.setdefault(e["source"], []).append(e["target"])

        delegates = [e for e in self.edges if e["type"] == "DELEGATES_VIA"]
        for node in self.nodes:
            if node.get("type") not in ("Class", "Interface", "Enum", "Record"):
                continue
            mids = set(methods_of.get(node["id"], []))
            class_dels = [e for e in delegates if e["source"] in mids]
            targets = {e["target"] for e in class_dels}
            fields = {
                e["via"] for e in class_dels
                if e.get("via_kind") == "field" and e.get("via")
            }
            params = {
                e["via"] for e in class_dels
                if e.get("via_kind") == "parameter" and e.get("via")
            }
            same: bool | None = None
            implemented = set(implements_of.get(node["id"], []))
            for fid in fields:
                ft = field_type.get(fid)
                if ft is None:
                    continue
                if ft in implemented or ft == node["id"]:
                    same = True
                    break
                if same is None:
                    same = False
            node["pattern_features"] = {
                "distinct_delegate_targets": len(targets),
                "distinct_delegate_fields": len(fields),
                "distinct_delegate_params": len(params),
                "delegates_same_interface": same,
            }

