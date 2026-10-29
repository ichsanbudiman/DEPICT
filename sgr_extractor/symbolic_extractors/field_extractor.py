import re

from symbolic_extractors.base_extractor import base_extractor, id_allocator


class field_extractor(base_extractor):
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

    def extract(self, node) -> list[dict]:
        modifiers = self.modifiers(node)
        annotations = self.annotations(node)
        field_type = None
        fields = []
        decl_span = self.member_span(node)

        for child in node.named_children:
            if child.type == "modifiers":
                continue
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    for c in child.named_children:
                        if c.type == "identifier":
                            name_node = c
                            break
                name = self.text(name_node) if name_node else None
                dtype = field_type
                for dim in child.children:
                    if dim.type != "dimensions":
                        continue
                    brackets = re.sub(r"\s+", "", self.text(dim) or "[]")
                    if dtype and brackets and not str(dtype).endswith(brackets):
                        dtype = f"{dtype}{brackets}"
                qn = self.qualify(
                    self.package_name,
                    f"{self.owner_name}.{name}" if self.owner_name and name else name,
                )
                var_span = self.span(child)
                span = {
                    "line_start": decl_span.get("line_start") or var_span.get("line_start"),
                    "line_end": var_span.get("line_end") or decl_span.get("line_end"),
                }
                fields.append({
                    "id": self.ids.stable("F", qn or name or "field"),
                    "name": name,
                    "qualified_name": qn,
                    "type": dtype,
                    "modifiers": modifiers,
                    "annotations": annotations,
                    "span": span,
                    "source": self.line_text(span),
                })
            elif "annotation" not in child.type:
                field_type = self.text(child)

        return fields
