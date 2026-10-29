from __future__ import annotations

SCHEMA = "dpd_graph_v5"
ANNOTATION_SCHEMA = "depict_annotation_v1"
GOLD_CSV_NAME = "dpd_graph_v3_bplus_balanced_labels_gt100.csv"

SIX_CLASS = ("Adapter", "Factory", "Facade", "Observer", "Visitor", "None")
GOLD_TO_SIX = {
    "AbstractFactory": "Factory",
    "FactoryMethod": "Factory",
}

JREFACTORY_FOLDER = "JRefactory_v2.6.24"

TYPE_SGR = frozenset({"Class", "Interface", "Enum", "Record"})
METHOD_SGR = frozenset({"Method", "Constructor"})
FIELD_SGR = frozenset({"Field"})
PARAM_SGR = "Parameter"

KIND_TYPE = "type"
KIND_METHOD = "method"
KIND_FIELD = "field"

SGR_TO_V5 = {
    "HAS_METHOD": "has_method",
    "HAS_CONSTRUCTOR": "has_constructor",
    "HAS_FIELD": "has_field",
    "EXTENDS": "inherits",
    "IMPLEMENTS": "implements",
    "HAS_ELEMENT_TYPE": "has_element",
    "RETURNS": "uses",
    "RETURNS_ELEMENT": "uses_element",
    "CREATES": "creates",
    "CALLS": "calls",
    "OVERRIDES": "overrides",
    "IMPLEMENTS_METHOD": "implements_method",
    "READS_FIELD": "reads_field",
    "WRITES_FIELD": "writes_field",
}


MEMBER_MEMBER = frozenset({"CALLS", "DELEGATES_VIA", "OVERRIDES", "IMPLEMENTS_METHOD"})

IGNORE_SGR = frozenset({
    "REFERS_TO",
    "IN_PACKAGE",
    "DEFINES",
    "WRITES_LOCAL",
    "HAS_VARIABLE",
    "HAS_PARAMETER",
    "HAS_STATEMENT",
    "HAS_EXPRESSION",
    "ARGUMENT",
})

PRIMITIVE_SIMPLE = frozenset({
    "void",
    "int",
    "long",
    "boolean",
    "byte",
    "char",
    "short",
    "float",
    "double",
})

TYPE_FEAT_NAMES = [
    "class_kind",
    "is_abstract",
    "is_final",
    "is_public",
    "n_fields_total",
    "n_fields_static",
    "n_methods_total",
    "n_methods_public",
    "n_ctors_total",
    "has_private_ctor",
    "n_direct_supertypes",
    "n_direct_subtypes",
    "fan_in_calls",
    "fan_out_calls",
    "is_root",
    "hop_distance",
    "has_bidirectional_call",
]

METHOD_FEAT_NAMES = [
    "method_visibility_public",
    "method_visibility_protected",
    "method_visibility_private",
    "is_abstract",
    "is_constructor",
    "n_params",
    "return_kind_void",
    "return_kind_self",
    "return_kind_super",
    "return_kind_interface",
    "return_kind_other",
    "overrides_base_method",
    "calls_sibling_that_creates",
    "forwarding_ratio",
    "calls_on_held_field",
    "total_calls",
    "allocates_types_count",
    "allocates_self_type",
    "allocates_subtype_of_return",
    "loops_over_collection_and_calls_iface",
    "synchronization_used",
    "has_dispatch_to_caller",
    "calls_method_using_owner",
    "is_root",
    "hop_distance",
    "has_bidirectional_call",
]

FIELD_FEAT_NAMES = [
    "field_visibility_public",
    "field_visibility_private",
    "field_visibility_protected",
    "field_visibility_package",
    "field_is_static",
    "field_is_final",
    "field_is_volatile",
    "field_is_transient",
    "field_type_primitive",
    "field_type_generic",
    "type_relation_self",
    "type_relation_super",
    "type_relation_interface_of_owner",
    "type_relation_unrelated",
    "field_initialized",
    "field_initialized_literal",
    "field_initialized_new",
    "is_collection",
    "has_project_element_type",
    "is_root",
    "hop_distance",
    "has_bidirectional_call",
]

COLLECTION_SIMPLE = frozenset({
    "list",
    "set",
    "map",
    "arraylist",
    "hashmap",
    "hashset",
    "linkedlist",
    "vector",
    "hashtable",
    "collection",
    "queue",
    "deque",
    "arraydeque",
    "concurrenthashmap",
    "copyonwritearraylist",
    "enumeration",
})
