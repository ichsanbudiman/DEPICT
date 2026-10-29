from symbolic_extractors.base_extractor import format_signature
from symbolic_extractors.method_extractor import method_extractor


class constructor_extractor(method_extractor):
    """Extract ``constructor_declaration`` nodes as first-class Constructor facts."""

    def extract(self, node) -> dict:
        name_node = node.child_by_field_name("name")
        params_node = node.child_by_field_name("parameters")
        body_node = node.child_by_field_name("body")
        name = self.text(name_node) if name_node else None
        qn = self.qualify(
            self.package_name,
            f"{self.owner_name}.{name}" if self.owner_name and name else name,
        )
        # Distinguish overloads by parameter arity in the stable key.
        arity = 0
        if params_node is not None:
            arity = sum(
                1 for c in params_node.named_children
                if c.type in ("formal_parameter", "spread_parameter")
            )
        self._scope_key = f"{qn}#{arity}" if qn else (name or "ctor")

        result = {
            "id": self.ids.stable("CT", self._scope_key),
            "name": name,
            "qualified_name": qn,
            "modifiers": self.modifiers(node),
            "annotations": self.annotations(node),
            "generic_parameters": self.generic_parameters(node),
            "parameters": self.extract_parameters(params_node, owner_qn=self._scope_key),
            "statements": [],
            "calls": [],
            "variables": [],
            "creations": [],
            "span": self.member_span(node),
            "synthetic": False,
        }
        result["signature"] = format_signature(name, result["parameters"])
        result["source"] = self.line_text(result["span"])

        if body_node is not None:
            result["statements"] = self.extract_statements(body_node)
            result["calls"] = self.extract_calls(body_node)
            variables, creations = self.extract_variables_and_creations(body_node)
            result["variables"] = variables
            result["creations"] = creations

        return result
