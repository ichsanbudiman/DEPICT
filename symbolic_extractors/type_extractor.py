from symbolic_extractors.base_extractor import base_extractor, id_allocator
from symbolic_extractors.field_extractor import field_extractor
from symbolic_extractors.method_extractor import method_extractor
from symbolic_extractors.constructor_extractor import constructor_extractor


class type_extractor(base_extractor):
    def __init__(
        self,
        source_bytes: bytes,
        ids: id_allocator | None = None,
        package_name: str | None = None,
    ):
        super().__init__(source_bytes, ids)
        self.package_name = package_name

    def extract(self, node) -> dict:
        kind_map = {
            "class_declaration": "class",
            "interface_declaration": "interface",
            "enum_declaration": "enum",
            "record_declaration": "record",
        }
        kind = kind_map.get(node.type, node.type)
        id_prefix = {
            "class": "C",
            "interface": "I",
            "enum": "E",
            "record": "R",
        }.get(kind, "C")

        name = self.text(node.child_by_field_name("name"))
        qn = self.qualify(self.package_name, name)
        fields = field_extractor(
            self.source_bytes, self.ids, self.package_name, name
        )
        methods = method_extractor(
            self.source_bytes, self.ids, self.package_name, name
        )
        constructors = constructor_extractor(
            self.source_bytes, self.ids, self.package_name, name
        )

        result = {
            "id": self.ids.stable(id_prefix, qn or name or kind),
            "kind": kind,
            "name": name,
            "qualified_name": qn,
            "modifiers": self.modifiers(node),
            "annotations": self.annotations(node),
            "generic_parameters": self.generic_parameters(node),
            "extends": None,
            "implements": [],
            "fields": [],
            "constructors": [],
            "methods": [],
            "span": self.span(node),
        }

        for child in node.named_children:
            if child.type == "modifiers":
                continue
            elif child.type == "superclass":
                for t in child.named_children:
                    if t.type in (
                        "type_identifier",
                        "scoped_type_identifier",
                        "generic_type",
                    ):
                        result["extends"] = self.type_ref(self.text(t))
            elif child.type == "super_interfaces":
                for t in child.named_children:
                    if t.type == "type_list":
                        for iface in t.named_children:
                            if iface.type in (
                                "type_identifier",
                                "scoped_type_identifier",
                                "generic_type",
                            ):
                                result["implements"].append(self.type_ref(self.text(iface)))
                    elif t.type in (
                        "type_identifier",
                        "scoped_type_identifier",
                        "generic_type",
                    ):
                        result["implements"].append(self.type_ref(self.text(t)))
            elif child.type in ("class_body", "interface_body", "enum_body"):
                for member in child.named_children:
                    if member.type == "field_declaration":
                        result["fields"].extend(fields.extract(member))
                    elif member.type == "constructor_declaration":
                        result["constructors"].append(constructors.extract(member))
                    elif member.type == "method_declaration":
                        result["methods"].append(methods.extract(member))

        # Implicit default constructor: absence is a Singleton/Factory signal.
        if kind in ("class", "enum", "record") and not result["constructors"] and name:
            ctor_qn = self.qualify(self.package_name, f"{name}.{name}")
            access = []
            for m in result["modifiers"]:
                if m in ("public", "protected", "private"):
                    access.append(m)
            result["constructors"].append({
                "id": self.ids.stable("CT", f"{ctor_qn}#default"),
                "name": name,
                "qualified_name": ctor_qn,
                "modifiers": access,
                "annotations": [],
                "generic_parameters": [],
                "parameters": [],
                "statements": [],
                "calls": [],
                "variables": [],
                "creations": [],
                "span": result["span"],
                "synthetic": True,
                "implicit": True,
            })

        return result
