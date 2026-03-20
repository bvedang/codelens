from hashlib import sha1

from codelens.chunker import parse_java
from codelens.indexing.documents import (
    SKELETON_SEGMENT_MAX_CHARS,
    build_index_documents,
)


def _get_document(documents, kind, name):
    for document in documents:
        if document.chunk_kind == kind and document.name == name:
            return document
    raise AssertionError(f"Missing document kind={kind!r} name={name!r}")


def test_build_index_documents_matches_spec_shape(tmp_path):
    repo_root = tmp_path
    java_file = (
        repo_root / "src" / "main" / "java" / "com" / "app" / "OrderService.java"
    )
    java_file.parent.mkdir(parents=True)
    code = b"""package com.app;

class OrderService {
    private PaymentGateway paymentGateway;

    void placeOrder(String orderId) {
        paymentGateway.charge();
    }
}
"""
    chunks = parse_java(code, filepath=str(java_file))

    documents = build_index_documents(
        chunks,
        repo_root=repo_root,
        indexed_at="2026-03-10T00:00:00+00:00",
        source_set=":app:main",
    )

    repo_hash = sha1(str(repo_root.resolve()).encode("utf-8")).hexdigest()[:12]
    method = _get_document(documents, "method", "placeOrder")
    field = _get_document(documents, "field", "paymentGateway")
    skeleton = _get_document(documents, "skeleton", "OrderService")

    assert method.chunk_id == (
        f"{repo_hash}:src/main/java/com/app/OrderService.java:"
        "method:com.app.OrderService.placeOrder(StringorderId)"
    )
    assert field.file_path == "src/main/java/com/app/OrderService.java"
    assert field.field_type == "PaymentGateway"
    assert skeleton.chunk_id == (
        f"{repo_hash}:src/main/java/com/app/OrderService.java:skeleton:0"
    )
    assert method.retrieval_text.startswith(
        "[method] com.app.OrderService.placeOrder\nvoid placeOrder(String orderId)"
    )
    assert method.source_set == ":app:main"


def test_build_index_documents_filters_non_indexable_and_small_chunks(tmp_path):
    repo_root = tmp_path
    java_file = repo_root / "src" / "X.java"
    java_file.parent.mkdir(parents=True)
    code = b"""class X {
    int a;
    void x() {}

    void largeMethod() {
        System.out.println("x");
    }
}
"""
    chunks = parse_java(code, filepath=str(java_file))

    documents = build_index_documents(
        chunks,
        repo_root=repo_root,
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    kinds_and_names = {(document.chunk_kind, document.name) for document in documents}
    assert ("field", "a") not in kinds_and_names
    assert ("method", "x") not in kinds_and_names
    assert ("method", "largeMethod") in kinds_and_names


def test_build_index_documents_disambiguates_overloaded_methods(tmp_path):
    repo_root = tmp_path
    java_file = repo_root / "src" / "Overload.java"
    java_file.parent.mkdir(parents=True)
    code = b"""package com.app;

class Overload {
    void doesProduce(String value) {
        System.out.println(value);
    }

    void doesProduce(int value) {
        System.out.println(value);
    }
}
"""
    chunks = parse_java(code, filepath=str(java_file))

    documents = build_index_documents(
        chunks,
        repo_root=repo_root,
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    chunk_ids = [
        document.chunk_id for document in documents if document.name == "doesProduce"
    ]
    assert len(chunk_ids) == 2
    assert len(set(chunk_ids)) == 2


def test_build_index_documents_splits_large_skeletons_without_losing_late_members(
    tmp_path,
):
    repo_root = tmp_path
    java_file = repo_root / "src" / "HugeConstants.java"
    java_file.parent.mkdir(parents=True)
    fields = "\n".join(
        f'    public static final String VALUE_{index} = "{index}";'
        for index in range(200)
    )
    code = f"""class HugeConstants {{
{fields}
}}
""".encode()
    chunks = parse_java(code, filepath=str(java_file))

    documents = build_index_documents(
        chunks,
        repo_root=repo_root,
        indexed_at="2026-03-10T00:00:00+00:00",
    )

    skeletons = [
        document
        for document in documents
        if document.chunk_kind == "skeleton" and document.name == "HugeConstants"
    ]

    assert len(skeletons) > 1
    assert all(
        len(document.source_text) <= SKELETON_SEGMENT_MAX_CHARS + 128
        for document in skeletons
    )
    assert any("segment 1/" in document.retrieval_text for document in skeletons)
    assert any("segment 2/" in document.retrieval_text for document in skeletons)
    assert any("VALUE_199" in document.source_text for document in skeletons)
