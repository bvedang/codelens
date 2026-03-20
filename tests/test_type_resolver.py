"""Tests for type_resolver.py."""

from codelens.type_resolver import (
    ImportContext,
    TypeIndex,
    TypeResolver,
    build_project_type_index,
)


def test_import_context_parses_explicit_and_wildcard_imports():
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        [
            "import com.app.payments.PaymentGateway;",
            "import com.app.inventory.*;",
            "import static java.util.Collections.emptyList;",
        ],
    )

    assert ctx.package_name == "com.app.orders"
    assert ctx.explicit_imports == {
        "PaymentGateway": "com.app.payments.PaymentGateway",
    }
    assert ctx.wildcard_imports == ("com.app.inventory",)


def test_resolver_prefers_explicit_import_and_preserves_suffix():
    resolver = TypeResolver()
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        ["import java.util.List;"],
    )

    resolution = resolver.resolve_type_reference("List<String>[]", ctx)

    assert resolution.strategy == "explicit_import"
    assert resolution.best_name() == "java.util.List<String>[]"


def test_resolver_supports_same_package_from_index():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(["com.app.orders.PaymentGateway"])
    )
    ctx = ImportContext.from_declarations("package com.app.orders;", [])

    resolution = resolver.resolve_type_reference("PaymentGateway", ctx)

    assert resolution.strategy == "same_package"
    assert resolution.best_name() == "com.app.orders.PaymentGateway"


def test_resolver_supports_wildcard_imports_when_index_has_match():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(["com.app.payments.PaymentGateway"])
    )
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        ["import com.app.payments.*;"],
    )

    resolution = resolver.resolve_type_reference("PaymentGateway", ctx)

    assert resolution.strategy == "wildcard_import"
    assert resolution.best_name() == "com.app.payments.PaymentGateway"


def test_resolver_leaves_java_lang_unresolved_without_index():
    resolver = TypeResolver()
    ctx = ImportContext.from_declarations("package com.app.orders;", [])

    resolution = resolver.resolve_type_reference("String", ctx)

    assert resolution.strategy == "unresolved"
    assert resolution.best_name() == "String"


def test_resolver_uses_java_lang_from_index():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(["java.lang.String"])
    )
    ctx = ImportContext.from_declarations("package com.app.orders;", [])

    resolution = resolver.resolve_type_reference("String", ctx)

    assert resolution.strategy == "java_lang"
    assert resolution.best_name() == "java.lang.String"


def test_resolver_returns_ambiguous_when_multiple_candidates_match():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(
            [
                "java.awt.List",
                "java.util.List",
            ]
        )
    )
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        ["import java.awt.*;", "import java.util.*;"],
    )

    resolution = resolver.resolve_type_reference("List", ctx)

    assert resolution.strategy == "ambiguous"
    assert resolution.best_name() == "List"
    assert resolution.candidates == ("java.awt.List", "java.util.List")


def test_resolver_returns_ambiguous_for_jdk_vs_dependency_match():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(
            [
                "java.lang.String",
                "com.acme.String",
            ]
        )
    )
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        ["import com.acme.*;"],
    )

    resolution = resolver.resolve_type_reference("String", ctx)

    assert resolution.strategy == "ambiguous"
    assert resolution.best_name() == "String"
    assert resolution.candidates == ("com.acme.String", "java.lang.String")


def test_resolver_returns_ambiguous_for_dependency_vs_dependency_match():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(
            [
                "com.alpha.Client",
                "com.beta.Client",
            ]
        )
    )
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        ["import com.alpha.*;", "import com.beta.*;"],
    )

    resolution = resolver.resolve_type_reference("Client", ctx)

    assert resolution.strategy == "ambiguous"
    assert resolution.best_name() == "Client"
    assert resolution.candidates == ("com.alpha.Client", "com.beta.Client")


def test_resolver_prefers_same_package_over_dependency_with_same_simple_name():
    resolver = TypeResolver(
        type_index=TypeIndex.from_qualified_names(
            [
                "com.app.orders.Client",
                "com.beta.Client",
            ]
        )
    )
    ctx = ImportContext.from_declarations(
        "package com.app.orders;",
        ["import com.beta.*;"],
    )

    resolution = resolver.resolve_type_reference("Client", ctx)

    assert resolution.strategy == "same_package"
    assert resolution.best_name() == "com.app.orders.Client"


def test_build_project_type_index_discovers_top_level_types(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "OrderService.java").write_text(
        "package com.app.orders; class OrderService {}",
        encoding="utf-8",
    )
    (src / "PaymentGateway.java").write_text(
        "package com.app.payments; public interface PaymentGateway {}",
        encoding="utf-8",
    )

    index = build_project_type_index([src])

    assert index.lookup("OrderService") == ("com.app.orders.OrderService",)
    assert index.lookup("PaymentGateway") == ("com.app.payments.PaymentGateway",)
