from codelens.chunker import parse_java
from codelens.retrieval.documents import build_retrieval_documents


def _get_document(documents, kind, name):
    for document in documents:
        if document.kind == kind and document.name == name:
            return document
    raise AssertionError(f"Missing document kind={kind!r} name={name!r}")


def test_build_retrieval_documents_for_method_and_field():
    code = b"""package com.app.orders;

import com.app.payments.PaymentGateway;

class OrderService {
    private final PaymentGateway paymentGateway;

    void placeOrder() {
        paymentGateway.charge();
    }
}
"""
    chunks = parse_java(code, filepath="src/main/java/com/app/orders/OrderService.java")
    documents = build_retrieval_documents(
        chunks,
        repo_root="/repo",
        source_set=":orders:main",
    )

    field_document = _get_document(documents, "field", "paymentGateway")
    method_document = _get_document(documents, "method", "placeOrder")

    assert field_document.package_name == "com.app.orders"
    assert field_document.repo_root == "/repo"
    assert field_document.field_type == "PaymentGateway"
    assert (
        "resolved_symbols com.app.payments.PaymentGateway.charge"
        in method_document.retrieval_text
    )
    assert method_document.signature == "void placeOrder()"
    assert method_document.source_set == ":orders:main"


def test_build_retrieval_document_for_skeleton_includes_inheritance():
    code = b"""package com.app.orders;

class OrderService extends BaseOrderService implements Auditable, Runnable {}
"""
    chunks = parse_java(code, filepath="src/main/java/com/app/orders/OrderService.java")
    documents = build_retrieval_documents(chunks, repo_root="/repo")
    skeleton = _get_document(documents, "skeleton", "OrderService")

    assert skeleton.extends_name == "extends BaseOrderService"
    assert skeleton.implements == ["Auditable", "Runnable"]
    assert "extends extends BaseOrderService" in skeleton.retrieval_text
