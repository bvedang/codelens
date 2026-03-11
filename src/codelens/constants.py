TYPE_NODES = {
    "class_declaration",
    "interface_declaration",
    "enum_declaration",
    "record_declaration",
    "annotation_type_declaration",
}

MEMBER_NODES = {
    "method_declaration",
    "constructor_declaration",
    "compact_constructor_declaration",
    "field_declaration",
    "constant_declaration",
    "enum_constant",
    "annotation_type_element_declaration",
}

BEHAVIOR_NODES = {
    "lambda_expression",
    "method_reference",
    "switch_expression",
    "throw_statement",
    "try_statement",
    "try_with_resources_statement",
    "object_creation_expression",
}

INITIALIZER_NODES = {
    "static_initializer",
    "block",
}

SMALL_TYPE_CHAR_LIMIT = 1500
COMMENT_TYPES = {"line_comment", "block_comment"}
