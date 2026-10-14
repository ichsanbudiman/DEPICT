JAVA_PRIMITIVES = frozenset({
    "byte", "short", "int", "long", "float", "double", "boolean", "char", "void",
})

KEYWORD_MODIFIERS = frozenset({
    "public", "protected", "private", "abstract", "static", "final", "strictfp",
    "native", "synchronized", "transient", "volatile", "default", "sealed", "non-sealed",
})


class id_allocator:
    """Content-addressed IDs for declarations; positional IDs for AST noise nodes.

    Declaration IDs are stable across files and re-extractions::

        C:demo.Foo
        M:demo.Foo.run
        F:demo.Foo.strategy

    Statement/expression IDs use file-local byte spans so a single-file
    re-extract does not shift other files' declaration IDs.
    """

    def __init__(self):
        self._counts: dict[str, int] = {}
        self._stable_seen: set[str] = set()

    def next(self, prefix: str) -> str:
        """Legacy monotonic ID (prefer ``stable`` / ``span`` for new code)."""
        self._counts[prefix] = self._counts.get(prefix, 0) + 1
        return f"{prefix}{self._counts[prefix]}"

    def stable(self, prefix: str, key: str) -> str:
        """Deterministic ID from kind prefix + stable key (usually a FQN)."""
        safe = (key or "<anon>").strip().replace(" ", "")
        nid = f"{prefix}:{safe}"
        self._stable_seen.add(nid)
        return nid

    def span(self, prefix: str, owner_key: str, start_byte: int, end_byte: int) -> str:
        """Positional ID unique within an owner (method / file)."""
        return self.stable(prefix, f"{owner_key}@{start_byte}-{end_byte}")


class base_extractor:
    def __init__(self, source_bytes: bytes, ids: id_allocator | None = None):
        self.source_bytes = source_bytes
        self.ids = ids or id_allocator()

    def text(self, node) -> str | None:
        if node is None:
            return None
        return self.source_bytes[node.start_byte:node.end_byte].decode("utf-8")

    def span(self, node) -> dict:
        if node is None:
            return {"line_start": None, "line_end": None}
        return {
            "line_start": node.start_point[0] + 1,
            "line_end": node.end_point[0] + 1,
        }

    def modifiers(self, node) -> list[str]:
        """Keyword modifiers only (annotations collected separately)."""
        result = []
        for child in node.named_children:
            if child.type != "modifiers":
                continue
            for m in child.named_children:
                text = self.text(m)
                if text and text in KEYWORD_MODIFIERS:
                    result.append(text)
                elif m.type in ("public", "private", "protected", "abstract", "static",
                                "final", "native", "synchronized", "transient", "volatile",
                                "default", "strictfp"):
                    result.append(m.type)
        if not result:
            for child in node.named_children:
                if child.type == "modifiers":
                    for tok in (self.text(child) or "").split():
                        clean = tok.lstrip("@")
                        if tok in KEYWORD_MODIFIERS or clean in KEYWORD_MODIFIERS:
                            if not tok.startswith("@"):
                                result.append(tok)
        return result

    def annotations(self, node) -> list[str]:
        anns = []
        for child in node.named_children:
            if child.type != "modifiers":
                continue
            for m in child.named_children:
                if "annotation" in m.type:
                    text = self.text(m) or ""
                    anns.append(text.lstrip("@"))
        return anns

    def generic_parameters(self, node) -> list[str]:
        params_node = node.child_by_field_name("type_parameters")
        if params_node is None:
            return []
        names = []
        for child in params_node.named_children:
            if child.type == "type_parameter":
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    names.append(self.text(name_node))
                else:
                    for c in child.named_children:
                        if c.type == "type_identifier":
                            names.append(self.text(c))
                            break
        return [n for n in names if n]

    def type_ref(self, name: str | None) -> dict | None:
        if name is None:
            return None
        return {"type_ref": name}

    def qualify(self, package_name: str | None, simple_name: str | None) -> str | None:
        if not simple_name:
            return None
        if package_name:
            return f"{package_name}.{simple_name}"
        return simple_name

    def parse_type_reference(self, type_text: str | None) -> dict:
        """
        Parse a Java type string into raw name + type arguments.

        Examples::
            List<Listener>      → raw=List, args=[Listener], is_array=False
            Listener[]          → raw=Listener, args=[], is_array=True
            Map<String, Listener> → raw=Map, args=[String, Listener]
        """
        if not type_text:
            return {
                "raw": None,
                "args": [],
                "is_array": False,
                "text": type_text,
            }
        text = type_text.strip()
        is_array = "[]" in text
        no_array = text.replace("[]", "").strip()
        args: list[str] = []
        raw = no_array
        if "<" in no_array and no_array.endswith(">"):
            lt = no_array.find("<")
            raw = no_array[:lt].strip()
            inner = no_array[lt + 1 : -1]
            args = [p.strip() for p in self._split_type_args(inner) if p.strip()]
        return {
            "raw": raw,
            "args": args,
            "is_array": is_array,
            "text": type_text,
        }

    def _split_type_args(self, inner: str) -> list[str]:
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
