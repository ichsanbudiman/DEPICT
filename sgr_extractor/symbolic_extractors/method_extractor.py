import re

from symbolic_extractors.base_extractor import (
    base_extractor,
    format_signature,
    id_allocator,
)


STATEMENT_KIND = {
    "if_statement": "IfStatement",
    "for_statement": "ForStatement",
    "enhanced_for_statement": "ForEachStatement",
    "while_statement": "WhileStatement",
    "do_statement": "DoStatement",
    "return_statement": "ReturnStatement",
    "expression_statement": "ExpressionStatement",
    "local_variable_declaration": "LocalVariableDeclaration",
    "try_statement": "TryStatement",
    "switch_expression": "SwitchStatement",
    "synchronized_statement": "SynchronizedStatement",
    "throw_statement": "ThrowStatement",
    "break_statement": "BreakStatement",
    "continue_statement": "ContinueStatement",
    "labeled_statement": "LabeledStatement",
    "block": "Block",
}


class method_extractor(base_extractor):
    def __init__(
        self,
        source_bytes: bytes,
        ids: id_allocator | None = None,
        package_name: str | None = None,
        owner_name: str | None = None,
    ):
        super().__init__(source_bytes, ids)
        self.package_name = package_name
        self.owner_name = owner_name

    def extract(self, node) -> dict:
        name_node = node.child_by_field_name("name")
        type_node = node.child_by_field_name("type")
        params_node = node.child_by_field_name("parameters")
        body_node = node.child_by_field_name("body")
        name = self.text(name_node) if name_node else None
        qn = self.qualify(
            self.package_name,
            f"{self.owner_name}.{name}" if self.owner_name and name else name,
        )
        self._scope_key = qn or name or "method"

        result = {
            "id": self.ids.stable("M", qn or name or "method"),
            "name": name,
            "qualified_name": qn,
            "return_type": None,
            "modifiers": self.modifiers(node),
            "annotations": self.annotations(node),
            "generic_parameters": self.generic_parameters(node),
            "parameters": [],
            "statements": [],
            "calls": [],
            "variables": [],
            "creations": [],
            "span": self.member_span(node),
        }

        if type_node is not None:
            result["return_type"] = self.text(type_node)
        else:
            for child in node.named_children:
                if child.type == "void_type":
                    result["return_type"] = "void"
                    break

        result["parameters"] = self.extract_parameters(params_node, owner_qn=qn)
        result["signature"] = format_signature(name, result["parameters"])
        result["source"] = self.line_text(result["span"])
        if body_node is not None:
            result["statements"] = self.extract_statements(body_node)
            result["calls"] = self.extract_calls(body_node)
            variables, creations = self.extract_variables_and_creations(body_node)
            result["variables"] = variables
            result["creations"] = creations

        return result

    def _node_id(self, prefix: str, node) -> str:
        owner = getattr(self, "_scope_key", None) or self.owner_name or "local"
        return self.ids.span(prefix, owner, node.start_byte, node.end_byte)

    def extract_parameters(self, params_node, owner_qn: str | None = None) -> list[dict]:
        if params_node is None:
            return []

        parameters = []
        for child in params_node.named_children:
            if child.type not in ("formal_parameter", "spread_parameter"):
                continue

            param_type = None
            param_name = None
            type_node = child.child_by_field_name("type")
            name_node = child.child_by_field_name("name")

            if type_node is not None:
                param_type = self.text(type_node)
            # ``String argv[]`` stores the brackets on a dimensions node, not on the type.
            for dim in child.children:
                if dim.type != "dimensions":
                    continue
                brackets = re.sub(r"\s+", "", self.text(dim) or "[]")
                if param_type and brackets and not param_type.endswith(brackets):
                    param_type += brackets
            if name_node is not None:
                param_name = self.text(name_node)
            else:
                for c in child.named_children:
                    if c.type == "identifier":
                        param_name = self.text(c)
                    elif c.type != "modifiers" and "annotation" not in c.type:
                        param_type = self.text(c)

            param_key = (
                f"{owner_qn}.{param_name}" if owner_qn and param_name
                else (param_name or self._node_id("PR", child))
            )
            parameters.append({
                "id": self.ids.stable("PR", param_key),
                "name": param_name,
                "datatype": param_type,
                "annotations": self.annotations(child),
                "span": self.span(child),
            })

        return parameters

    def extract_statements(self, body_node) -> list[dict]:
        if body_node is None:
            return []
        # Methods use ``block``; constructors use ``constructor_body``.
        if body_node.type in ("block", "constructor_body"):
            children = list(body_node.named_children)
        else:
            children = [body_node]
        statements = []
        for order, child in enumerate(children):
            stmt = self._extract_statement(child)
            stmt["order"] = order
            statements.append(stmt)
        return statements

    def _extract_statement(self, node) -> dict:
        kind = STATEMENT_KIND.get(node.type, node.type)
        stmt = {
            "id": self._node_id("ST", node),
            "kind": kind,
            "order": 0,
            "span": self.span(node),
            "expression": None,
            "then": [],
            "else": [],
            "body": [],
            "variables": [],
            "creations": [],
        }

        if node.type == "if_statement":
            consequence = node.child_by_field_name("consequence")
            alternative = node.child_by_field_name("alternative")
            condition = node.child_by_field_name("condition")
            if condition is not None:
                stmt["expression"] = self.extract_expression(condition)
            if consequence is not None:
                stmt["then"] = self._ordered_statements(consequence)
            if alternative is not None:
                stmt["else"] = self._ordered_statements(alternative)

        elif node.type in (
            "for_statement",
            "enhanced_for_statement",
            "while_statement",
            "do_statement",
        ):
            body = node.child_by_field_name("body")
            if body is not None:
                stmt["body"] = self._ordered_statements(body)

        elif node.type == "expression_statement":
            stmt["kind"] = "ExpressionStatement"
            for child in node.named_children:
                stmt["expression"] = self.extract_expression(child)
                break

        elif node.type == "return_statement":
            for child in node.named_children:
                stmt["expression"] = self.extract_expression(child)
                break

        elif node.type == "local_variable_declaration":
            variables, creations = self.extract_variables_and_creations(node)
            stmt["variables"] = variables
            stmt["creations"] = creations
            # Initializer expression (first declarator value), if any.
            for child in node.named_children:
                if child.type == "variable_declarator":
                    value = child.child_by_field_name("value")
                    if value is not None:
                        stmt["expression"] = self.extract_expression(value)
                    break

        elif node.type == "block":
            stmt["body"] = self._ordered_statements(node)

        elif node.type == "try_statement":
            body = node.child_by_field_name("body")
            if body is not None:
                stmt["body"] = self._ordered_statements(body)

        elif node.type == "throw_statement":
            for child in node.named_children:
                stmt["expression"] = self.extract_expression(child)
                break

        return stmt

    def _ordered_statements(self, node) -> list[dict]:
        if node.type in ("block", "constructor_body"):
            children = list(node.named_children)
        else:
            children = [node]
        out = []
        for order, child in enumerate(children):
            stmt = self._extract_statement(child)
            stmt["order"] = order
            out.append(stmt)
        return out

    def _expr_fallback(self, node) -> dict:
        return {
            "id": self._node_id("EX", node),
            "kind": "Expression",
            "span": self.span(node),
            "name": self.text(node),
        }

    def extract_expression(
        self,
        node,
        *,
        _depth: int = 0,
        _seen: set[tuple] | None = None,
    ) -> dict | None:
        if node is None:
            return None
        # Guard against malformed / ERROR trees (e.g. CDO) that cycle on
        # binary_expression.left and blow the Python stack.
        if _depth > 256 or node.type == "ERROR":
            return self._expr_fallback(node)
        if _seen is None:
            _seen = set()
        key = (node.start_byte, node.end_byte, node.type)
        if key in _seen:
            return self._expr_fallback(node)
        _seen.add(key)

        def _child(child):
            if child is None:
                return None
            child_key = (child.start_byte, child.end_byte, child.type)
            if child_key == key:
                return self._expr_fallback(child)
            return self.extract_expression(child, _depth=_depth + 1, _seen=_seen)

        try:
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                object_node = node.child_by_field_name("object")
                args_node = node.child_by_field_name("arguments")
                target_name = self.text(name_node) if name_node else None
                receiver_expr = _child(object_node)
                receiver_text = self.text(object_node) if object_node is not None else None
                arguments = []
                if args_node is not None:
                    for arg in args_node.named_children:
                        expr = _child(arg)
                        if expr is not None:
                            arguments.append(expr)
                return {
                    "id": self._node_id("EX", node),
                    "kind": "MethodInvocation",
                    "span": self.span(node),
                    "call": {
                        "target_name": target_name,
                        "receiver": {"expression": receiver_text} if receiver_text else None,
                    },
                    "receiver_expr": receiver_expr,
                    "name": target_name,
                    "arguments": arguments,
                }

            if node.type == "object_creation_expression":
                args_node = node.child_by_field_name("arguments")
                arguments = []
                if args_node is not None:
                    for arg in args_node.named_children:
                        expr = _child(arg)
                        if expr is not None:
                            arguments.append(expr)
                return {
                    "id": self._node_id("EX", node),
                    "kind": "ObjectCreation",
                    "span": self.span(node),
                    "type_name": self._created_type_name(node),
                    "arguments": arguments,
                }

            if node.type == "assignment_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                operator = "="
                for child in node.children:
                    if child.type not in ("left", "right") and (self.text(child) or "").strip() in {
                        "=", "+=", "-=", "*=", "/=", "&=", "|=", "^=", "%=",
                        "<<=", ">>=", ">>>=",
                    }:
                        operator = (self.text(child) or "=").strip()
                        break
                return {
                    "id": self._node_id("EX", node),
                    "kind": "Assignment",
                    "span": self.span(node),
                    "operator": operator,
                    "lhs": _child(left),
                    "rhs": _child(right),
                }

            if node.type == "update_expression":
                operand = next(iter(node.named_children), None)
                operator = None
                for child in node.children:
                    if child.type in ("++", "--"):
                        operator = child.type
                        break
                return {
                    "id": self._node_id("EX", node),
                    "kind": "Update",
                    "span": self.span(node),
                    "operator": operator,
                    "lhs": _child(operand),
                }

            if node.type == "field_access":
                obj = node.child_by_field_name("object")
                field = node.child_by_field_name("field")
                return {
                    "id": self._node_id("EX", node),
                    "kind": "FieldAccess",
                    "span": self.span(node),
                    "name": self.text(field) if field is not None else None,
                    "object": _child(obj),
                }

            if node.type == "binary_expression":
                left = node.child_by_field_name("left")
                right = node.child_by_field_name("right")
                op = node.child_by_field_name("operator")
                return {
                    "id": self._node_id("EX", node),
                    "kind": "BinaryExpression",
                    "span": self.span(node),
                    "operator": self.text(op) if op is not None else None,
                    "lhs": _child(left),
                    "rhs": _child(right),
                }

            if node.type in ("identifier", "this", "super"):
                return {
                    "id": self._node_id("EX", node),
                    "kind": "ThisExpression" if node.type == "this" else (
                        "SuperExpression" if node.type == "super" else "Identifier"
                    ),
                    "span": self.span(node),
                    "name": self.text(node),
                }

            if node.type in (
                "decimal_integer_literal",
                "hex_integer_literal",
                "octal_integer_literal",
                "binary_integer_literal",
                "decimal_floating_point_literal",
                "string_literal",
                "character_literal",
                "true",
                "false",
                "null_literal",
            ):
                return {
                    "id": self._node_id("EX", node),
                    "kind": "Literal",
                    "span": self.span(node),
                    "name": self.text(node),
                }

            if node.type == "parenthesized_expression":
                for child in node.named_children:
                    return _child(child)
                return None

            return self._expr_fallback(node)
        finally:
            _seen.discard(key)

    def extract_calls(
        self,
        node,
        *,
        _depth: int = 0,
        _seen: set[tuple] | None = None,
    ) -> list[dict]:
        if node is None:
            return []
        if _depth > 256 or node.type == "ERROR":
            return []
        if _seen is None:
            _seen = set()
        key = (node.start_byte, node.end_byte, node.type)
        if key in _seen:
            return []
        _seen.add(key)
        try:
            calls = []
            if node.type == "method_invocation":
                name_node = node.child_by_field_name("name")
                object_node = node.child_by_field_name("object")
                call = {
                    "target_name": self.text(name_node) if name_node else None,
                    "receiver": None,
                }
                if object_node is not None:
                    call["receiver"] = {"expression": self.text(object_node)}
                calls.append(call)
            for child in node.named_children:
                child_key = (child.start_byte, child.end_byte, child.type)
                if child_key == key:
                    continue
                calls.extend(
                    self.extract_calls(child, _depth=_depth + 1, _seen=_seen)
                )
            return calls
        finally:
            _seen.discard(key)

    def extract_variables_and_creations(
        self,
        node,
        *,
        _depth: int = 0,
        _seen: set[tuple] | None = None,
    ) -> tuple[list[dict], list[dict]]:
        if node is None:
            return [], []
        if _depth > 256 or node.type == "ERROR":
            return [], []
        if _seen is None:
            _seen = set()
        key = (node.start_byte, node.end_byte, node.type)
        if key in _seen:
            return [], []
        _seen.add(key)

        def _walk(child):
            child_key = (child.start_byte, child.end_byte, child.type)
            if child_key == key:
                return [], []
            return self.extract_variables_and_creations(
                child, _depth=_depth + 1, _seen=_seen
            )

        try:
            variables = []
            creations = []

            if node.type == "local_variable_declaration":
                declared_type = None
                for child in node.named_children:
                    if child.type == "modifiers":
                        continue
                    if child.type == "variable_declarator":
                        name_node = child.child_by_field_name("name")
                        value_node = child.child_by_field_name("value")
                        var_name = self.text(name_node) if name_node else None
                        owner = getattr(self, "_scope_key", None) or self.owner_name or "local"
                        if var_name:
                            variable_id = self.ids.stable(
                                "V",
                                f"{owner}.{var_name}@{child.start_byte}",
                            )
                        else:
                            variable_id = self._node_id("V", child)
                        variables.append({
                            "id": variable_id,
                            "name": var_name,
                            "type_ref": declared_type,
                            "span": self.span(child),
                        })

                        if value_node is not None and value_node.type == "object_creation_expression":
                            creations.append({
                                "variable": variable_id,
                                "creates": self.type_ref(self._created_type_name(value_node)),
                            })
                            for c in value_node.named_children:
                                if c.type == "argument_list":
                                    nested_vars, nested_creates = _walk(c)
                                    variables.extend(nested_vars)
                                    creations.extend(nested_creates)
                        elif value_node is not None:
                            nested_vars, nested_creates = _walk(value_node)
                            variables.extend(nested_vars)
                            creations.extend(nested_creates)
                    elif declared_type is None and "annotation" not in child.type:
                        declared_type = self.text(child)
                return variables, creations

            if node.type == "object_creation_expression":
                creations.append({
                    "variable": None,
                    "creates": self.type_ref(self._created_type_name(node)),
                })
                for child in node.named_children:
                    if child.type == "argument_list":
                        nested_vars, nested_creates = _walk(child)
                        variables.extend(nested_vars)
                        creations.extend(nested_creates)
                return variables, creations

            for child in node.named_children:
                nested_vars, nested_creates = _walk(child)
                variables.extend(nested_vars)
                creations.extend(nested_creates)

            return variables, creations
        finally:
            _seen.discard(key)

    def _created_type_name(self, creation_node) -> str | None:
        type_node = creation_node.child_by_field_name("type")
        if type_node is not None:
            return self.text(type_node)
        for child in creation_node.named_children:
            if child.type in (
                "type_identifier",
                "scoped_type_identifier",
                "generic_type",
            ):
                return self.text(child)
        return None
