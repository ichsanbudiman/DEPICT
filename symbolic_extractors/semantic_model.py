"""Semantic model: plain Python objects between AST extraction and graph building.

Pipeline:
  Java source → tree-sitter AST → nested dicts / Semantic Model → Graph (nodes/edges)
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SourceSpan:
    line_start: int | None = None
    line_end: int | None = None


@dataclass
class TypeRef:
    """Unresolved or symbolic type name before graph identity assignment."""

    name: str


@dataclass
class CallInfo:
    target_name: str | None = None
    receiver_expression: str | None = None


@dataclass
class CreationInfo:
    variable_id: str | None = None
    type_name: str | None = None


@dataclass
class Parameter:
    id: str
    name: str | None = None
    datatype: str | None = None
    annotations: list[str] = field(default_factory=list)
    span: SourceSpan = field(default_factory=SourceSpan)


@dataclass
class Variable:
    id: str
    name: str | None = None
    type_name: str | None = None
    span: SourceSpan = field(default_factory=SourceSpan)


@dataclass
class Expression:
    """Expression node (MethodInvocation, ObjectCreation, Assignment, ...)."""

    id: str
    kind: str
    span: SourceSpan = field(default_factory=SourceSpan)
    # MethodInvocation
    call: CallInfo | None = None
    receiver: Expression | None = None
    arguments: list[Expression] = field(default_factory=list)
    # ObjectCreation
    type_name: str | None = None
    # FieldAccess / Identifier
    name: str | None = None
    object: Expression | None = None
    # Assignment / Binary
    lhs: Expression | None = None
    rhs: Expression | None = None
    operator: str | None = None
    # Nested / misc
    children: list[Expression] = field(default_factory=list)


@dataclass
class Statement:
    """Statement node (IfStatement, ExpressionStatement, ...)."""

    id: str
    kind: str
    order: int = 0
    span: SourceSpan = field(default_factory=SourceSpan)
    expression: Expression | None = None
    then_branch: list[Statement] = field(default_factory=list)
    else_branch: list[Statement] = field(default_factory=list)
    body: list[Statement] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    creations: list[CreationInfo] = field(default_factory=list)


@dataclass
class Method:
    id: str
    name: str | None = None
    qualified_name: str | None = None
    return_type: str | None = None
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    generic_parameters: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    creations: list[CreationInfo] = field(default_factory=list)
    span: SourceSpan = field(default_factory=SourceSpan)


@dataclass
class Constructor:
    id: str
    name: str | None = None
    qualified_name: str | None = None
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    generic_parameters: list[str] = field(default_factory=list)
    parameters: list[Parameter] = field(default_factory=list)
    statements: list[Statement] = field(default_factory=list)
    variables: list[Variable] = field(default_factory=list)
    creations: list[CreationInfo] = field(default_factory=list)
    span: SourceSpan = field(default_factory=SourceSpan)
    synthetic: bool = False
    implicit: bool = False


@dataclass
class Field:
    id: str
    name: str | None = None
    qualified_name: str | None = None
    type_name: str | None = None
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    span: SourceSpan = field(default_factory=SourceSpan)


@dataclass
class TypeDeclaration:
    id: str
    kind: str
    name: str | None = None
    qualified_name: str | None = None
    modifiers: list[str] = field(default_factory=list)
    annotations: list[str] = field(default_factory=list)
    generic_parameters: list[str] = field(default_factory=list)
    extends: TypeRef | None = None
    implements: list[TypeRef] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    constructors: list[Constructor] = field(default_factory=list)
    methods: list[Method] = field(default_factory=list)
    span: SourceSpan = field(default_factory=SourceSpan)


@dataclass
class PackageInfo:
    id: str
    name: str | None = None
    path: str | None = None
    project_path: str | None = None


@dataclass
class FileInfo:
    id: str
    name: str | None = None
    path: str | None = None
    project_path: str | None = None
    imports: list[str] = field(default_factory=list)


@dataclass
class CompilationUnit:
    """File-level semantic model (one .java compilation unit)."""

    package: PackageInfo | None = None
    file: FileInfo | None = None
    types: list[TypeDeclaration] = field(default_factory=list)


def span_from_dict(d: dict | None) -> SourceSpan:
    if not d:
        return SourceSpan()
    return SourceSpan(d.get("line_start"), d.get("line_end"))


def compilation_unit_from_nested(nested: dict) -> CompilationUnit:
    """Adapt extractor nested dicts into the semantic model."""
    package = None
    if nested.get("package"):
        p = nested["package"]
        package = PackageInfo(
            id=p["id"],
            name=p.get("name"),
            path=p.get("path"),
            project_path=p.get("project_path"),
        )

    file_info = None
    if nested.get("file"):
        f = nested["file"]
        file_info = FileInfo(
            id=f["id"],
            name=f.get("name"),
            path=f.get("path"),
            project_path=f.get("project_path"),
            imports=f.get("imports") or nested.get("imports") or [],
        )

    types = [_type_from_dict(t) for t in nested.get("types", [])]
    return CompilationUnit(package=package, file=file_info, types=types)


def _type_ref_from(value) -> TypeRef | None:
    if value is None:
        return None
    if isinstance(value, dict):
        name = value.get("type_ref")
        return TypeRef(name) if name else None
    return TypeRef(str(value))


def _param_from(d: dict) -> Parameter:
    return Parameter(
        id=d["id"],
        name=d.get("name"),
        datatype=d.get("datatype"),
        annotations=d.get("annotations") or [],
        span=span_from_dict(d.get("span")),
    )


def _var_from(d: dict) -> Variable:
    return Variable(
        id=d["id"],
        name=d.get("name"),
        type_name=d.get("type_ref") or d.get("type_name"),
        span=span_from_dict(d.get("span")),
    )


def _creation_from(d: dict) -> CreationInfo:
    creates = d.get("creates") or {}
    type_name = creates.get("type_ref") if isinstance(creates, dict) else None
    return CreationInfo(variable_id=d.get("variable"), type_name=type_name)


def _call_from(d: dict | None) -> CallInfo | None:
    if not d:
        return None
    receiver = d.get("receiver") or {}
    expr = receiver.get("expression") if isinstance(receiver, dict) else None
    return CallInfo(target_name=d.get("target_name"), receiver_expression=expr)


def _expr_from(d: dict | None) -> Expression | None:
    if not d:
        return None
    return Expression(
        id=d["id"],
        kind=d.get("kind") or "Expression",
        span=span_from_dict(d.get("span")),
        call=_call_from(d.get("call")),
        receiver=_expr_from(d.get("receiver_expr")),
        arguments=[e for e in (_expr_from(a) for a in d.get("arguments") or []) if e],
        type_name=d.get("type_name"),
        name=d.get("name"),
        object=_expr_from(d.get("object")),
        lhs=_expr_from(d.get("lhs")),
        rhs=_expr_from(d.get("rhs")),
        operator=d.get("operator"),
        children=[e for e in (_expr_from(c) for c in d.get("children") or []) if e],
    )


def _stmt_from(d: dict) -> Statement:
    return Statement(
        id=d["id"],
        kind=d.get("kind") or "Statement",
        order=d.get("order", 0),
        span=span_from_dict(d.get("span")),
        expression=_expr_from(d.get("expression")),
        then_branch=[_stmt_from(s) for s in d.get("then") or []],
        else_branch=[_stmt_from(s) for s in d.get("else") or []],
        body=[_stmt_from(s) for s in d.get("body") or []],
        variables=[_var_from(v) for v in d.get("variables") or []],
        creations=[_creation_from(c) for c in d.get("creations") or []],
    )


def _method_from(d: dict) -> Method:
    return Method(
        id=d["id"],
        name=d.get("name"),
        qualified_name=d.get("qualified_name"),
        return_type=d.get("return_type"),
        modifiers=d.get("modifiers") or [],
        annotations=d.get("annotations") or [],
        generic_parameters=d.get("generic_parameters") or [],
        parameters=[_param_from(p) for p in d.get("parameters") or []],
        statements=[_stmt_from(s) for s in d.get("statements") or []],
        variables=[_var_from(v) for v in d.get("variables") or []],
        creations=[_creation_from(c) for c in d.get("creations") or []],
        span=span_from_dict(d.get("span")),
    )


def _ctor_from(d: dict) -> Constructor:
    return Constructor(
        id=d["id"],
        name=d.get("name"),
        qualified_name=d.get("qualified_name"),
        modifiers=d.get("modifiers") or [],
        annotations=d.get("annotations") or [],
        generic_parameters=d.get("generic_parameters") or [],
        parameters=[_param_from(p) for p in d.get("parameters") or []],
        statements=[_stmt_from(s) for s in d.get("statements") or []],
        variables=[_var_from(v) for v in d.get("variables") or []],
        creations=[_creation_from(c) for c in d.get("creations") or []],
        span=span_from_dict(d.get("span")),
        synthetic=bool(d.get("synthetic")),
        implicit=bool(d.get("implicit")),
    )


def _field_from(d: dict) -> Field:
    return Field(
        id=d["id"],
        name=d.get("name"),
        qualified_name=d.get("qualified_name"),
        type_name=d.get("type"),
        modifiers=d.get("modifiers") or [],
        annotations=d.get("annotations") or [],
        span=span_from_dict(d.get("span")),
    )


def _type_from_dict(d: dict) -> TypeDeclaration:
    return TypeDeclaration(
        id=d["id"],
        kind=d.get("kind") or "class",
        name=d.get("name"),
        qualified_name=d.get("qualified_name"),
        modifiers=d.get("modifiers") or [],
        annotations=d.get("annotations") or [],
        generic_parameters=d.get("generic_parameters") or [],
        extends=_type_ref_from(d.get("extends")),
        implements=[r for r in (_type_ref_from(x) for x in d.get("implements") or []) if r],
        fields=[_field_from(f) for f in d.get("fields") or []],
        constructors=[_ctor_from(c) for c in d.get("constructors") or []],
        methods=[_method_from(m) for m in d.get("methods") or []],
        span=span_from_dict(d.get("span")),
    )
