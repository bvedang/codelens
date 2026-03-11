from __future__ import annotations

import argparse
import sys
from pathlib import Path

from codelens.chunker import annotation_texts, file_context, parse_java
from codelens.indexing import (
    ColbertEncoder,
    FaissIndexRepository,
    FaissIndexingService,
)
from codelens.logging_config import configure_logging, get_logger
from codelens.parser import JAVA_PARSER
from codelens.symbol_index import build_jdk_symbol_index
from codelens.type_resolver import TypeResolver
from codelens.workspace_runtime import build_workspace_resolver_context

logger = get_logger(__name__)

DEMO_CODE = b'''
package com.app.orders;

import com.app.models.Order;
import com.app.models.Customer;
import com.app.payments.PaymentGateway;
import com.app.inventory.StockService;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;
import java.util.Optional;
import java.util.List;

/**
 * Handles order lifecycle: placement, lookup, and cancellation.
 * Coordinates between payment, inventory, and persistence layers.
 */
@Service
public class OrderService {

    private final PaymentGateway paymentGateway;
    private final StockService stockService;
    private final OrderRepository orderRepo;

    public OrderService(PaymentGateway paymentGateway,
                        StockService stockService,
                        OrderRepository orderRepo) {
        this.paymentGateway = paymentGateway;
        this.stockService = stockService;
        this.orderRepo = orderRepo;
    }

    /**
     * Places a new order after validating stock availability.
     * Charges the customer and reserves inventory.
     *
     * @param customer the customer placing the order
     * @param items    line items to include
     * @return the persisted Order
     * @throws OutOfStockException if any item is unavailable
     */
    @Transactional
    public Order placeOrder(Customer customer, List<LineItem> items) {
        for (LineItem item : items) {
            if (!stockService.isAvailable(item.getSku(), item.getQty())) {
                throw new OutOfStockException(item.getSku());
            }
        }

        Order order = Order.create(customer, items);
        paymentGateway.charge(customer.getPaymentMethod(), order.getTotal());
        stockService.reserve(items);
        return orderRepo.save(order);
    }

    /** Finds an order by ID, returning empty if not found. */
    public Optional<Order> findOrder(Long orderId) {
        return orderRepo.findById(orderId);
    }

    /**
     * Cancels an existing order: refunds payment and releases inventory.
     * @throws OrderNotFoundException if the order doesn't exist
     */
    @Transactional(readOnly = false)
    public void cancelOrder(Long orderId) {
        Order order = orderRepo.findById(orderId)
            .orElseThrow(() -> new OrderNotFoundException(orderId));
        paymentGateway.refund(order.getPaymentId(), order.getTotal());
        stockService.release(order.getItems());
        order.setStatus(OrderStatus.CANCELLED);
        orderRepo.save(order);
    }
}
'''


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "index":
        _run_index_cli(args[1:])
        return
    if args and args[0] == "parse":
        _run_parse_cli(args[1:])
        return
    _run_parse_cli(args)


def _run_index_cli(argv: list[str]) -> None:
    cli = argparse.ArgumentParser(description="Build and inspect the local CodeLens index.")
    cli.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity. Use -v for info, -vv for debug.",
    )
    subparsers = cli.add_subparsers(dest="index_command", required=True)

    workspace_parser = subparsers.add_parser("workspace", help="Rebuild the workspace index.")
    workspace_parser.add_argument("--repo-root", required=True)
    workspace_parser.add_argument("--workspace-json", required=True)
    workspace_parser.add_argument("--resolve-binaries", action="store_true")
    workspace_parser.add_argument("--jdk-home")
    workspace_parser.add_argument("--batch-size", type=int, default=32)
    workspace_parser.add_argument("--device")

    file_parser = subparsers.add_parser("file", help="Refresh a single file in the local index.")
    file_parser.add_argument("--repo-root", required=True)
    file_parser.add_argument("--file", required=True)
    file_parser.add_argument("--workspace-json", required=True)
    file_parser.add_argument("--resolve-binaries", action="store_true")
    file_parser.add_argument("--jdk-home")
    file_parser.add_argument("--batch-size", type=int, default=32)
    file_parser.add_argument("--device")

    status_parser = subparsers.add_parser("status", help="Show local index status.")
    status_parser.add_argument("--repo-root", required=True)

    args = cli.parse_args(argv)
    configure_logging(args.verbose)

    repo_root = Path(args.repo_root).resolve()
    repository = FaissIndexRepository(repo_root)

    if args.index_command == "status":
        status = repository.status()
        if status is None:
            print("chunk_count: 0")
            print("indexed_at : -")
            print("model      : -")
            return
        print(f"chunk_count: {status.chunk_count}")
        print(f"indexed_at : {status.indexed_at or '-'}")
        print(f"model      : {status.model_name or '-'}")
        return

    service = FaissIndexingService(
        repository,
        ColbertEncoder(device=args.device),
        batch_size=args.batch_size,
    )
    if args.index_command == "workspace":
        result = service.index_workspace(
            repo_root=repo_root,
            workspace_json=args.workspace_json,
            resolve_binaries=args.resolve_binaries,
            jdk_home=args.jdk_home,
        )
    else:
        result = service.index_file(
            args.file,
            repo_root=repo_root,
            workspace_json=args.workspace_json,
            resolve_binaries=args.resolve_binaries,
            jdk_home=args.jdk_home,
        )

    print(f"repo_root        : {result.repo_root}")
    print(f"files_indexed    : {result.files_indexed}")
    print(f"documents_indexed: {result.documents_indexed}")
    print(f"failures         : {result.failures}")


def _run_parse_cli(argv: list[str]) -> None:
    cli = argparse.ArgumentParser(description="Parse Java and print chunk/debug output.")
    cli.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase log verbosity. Use -v for info, -vv for debug.",
    )
    cli.add_argument(
        "--file",
        help="Path to a real Java file to parse. Defaults to the embedded demo sample.",
    )
    cli.add_argument(
        "--workspace-json",
        help=(
            "Path to exported Gradle workspace metadata. "
            "When provided with --file, the demo builds a workspace-aware resolver."
        ),
    )
    cli.add_argument(
        "--resolve-jars",
        action="store_true",
        help="Also build a binary index from visible external jars/class directories. Slower, but needed for external dependency resolution demos.",
    )
    cli.add_argument(
        "--jdk-home",
        help=(
            "Path to a JDK home. When provided, the demo indexes JDK classes "
            "so implicit java.lang types resolve without fallback."
        ),
    )
    args = cli.parse_args(argv)
    configure_logging(args.verbose)

    demo_code = DEMO_CODE
    demo_filepath = "src/main/java/com/app/orders/OrderService.java"
    demo_resolver = None
    demo_workspace = None
    demo_jdk_index = None

    if args.workspace_json and not args.file:
        cli.error("--workspace-json requires --file")

    if args.file:
        file_path = Path(args.file).resolve()
        demo_code = file_path.read_bytes()
        demo_filepath = str(file_path)
        logger.info("Using input file %s", demo_filepath)

        if args.workspace_json:
            demo_context = build_workspace_resolver_context(
                file_path,
                workspace_json=args.workspace_json,
                resolve_binaries=args.resolve_jars,
                jdk_home=args.jdk_home,
            )
            demo_workspace = demo_context.workspace
            demo_jdk_index = demo_context.jdk_index
            demo_resolver = demo_context.resolver
        elif args.jdk_home:
            demo_jdk_index = build_jdk_symbol_index(args.jdk_home)
            demo_resolver = TypeResolver(
                type_index=demo_jdk_index.to_type_index(origin_kind="jdk")
            )
    elif args.jdk_home:
        demo_jdk_index = build_jdk_symbol_index(args.jdk_home)
        demo_resolver = TypeResolver(
            type_index=demo_jdk_index.to_type_index(origin_kind="jdk")
        )

    logger.info("Parsing %s", demo_filepath)
    chunks = parse_java(demo_code, filepath=demo_filepath, resolver=demo_resolver)

    if demo_workspace is not None and demo_resolver is not None:
        tree = JAVA_PARSER.parse(demo_code)
        root = tree.root_node
        demo_file_ctx = file_context(demo_code, root)
        import_context = demo_resolver.build_import_context(
            demo_file_ctx.get("package"),
            demo_file_ctx.get("imports", []),
        )
        source_set_id = demo_workspace.source_set_for_file(demo_filepath)

        print("=" * 70)
        print("Workspace Resolution")
        print("=" * 70)
        print(f"file           : {demo_filepath}")
        print(f"source_set     : {source_set_id.key if source_set_id else '—'}")
        print(f"workspace_jdk  : {demo_workspace.jdk_home or '—'}")
        if source_set_id is not None:
            visible_source_sets = [item.key for item in demo_workspace.visible_source_sets(source_set_id)]
            print(f"visible_sets   : {len(visible_source_sets)}")
            for item in visible_source_sets[:10]:
                print(f"  - {item}")
            visible_roots = demo_workspace.visible_source_roots(source_set_id)
            print(f"visible_roots  : {len(visible_roots)}")
            visible_jars = demo_workspace.visible_external_jars(source_set_id)
            print(f"visible_jars   : {len(visible_jars)}")
        if demo_jdk_index is not None:
            print(f"jdk_types      : {len(demo_jdk_index.qualified_names(origin_kind='jdk'))}")

        imported_types = []
        for import_decl in demo_file_ctx.get("imports", []):
            clean = import_decl.replace("import ", "", 1).rstrip(";").strip()
            if clean.startswith("static ") or clean.endswith(".*"):
                continue
            imported_types.append(clean.rsplit(".", 1)[-1])

        if imported_types:
            print("resolved_imports:")
            for type_name in imported_types[:12]:
                resolution = demo_resolver.resolve_type_reference(type_name, import_context)
                print(f"  - {type_name} -> {resolution.best_name()} [{resolution.strategy}]")
        print()

    print("=" * 70)
    print(f"Total chunks: {len(chunks)}")
    print("=" * 70)

    for i, chunk in enumerate(chunks):
        kind = chunk["kind"]
        name = chunk.get("name", "")
        owner = ".".join(chunk.get("owner_chain", []))

        print(f"\n{'─' * 70}")
        print(f"[{i}] {kind}: {name}  (owner: {owner or '—'})")
        print(f"     file: {chunk.get('filepath', '—')}")

        if chunk.get("javadoc"):
            print(f"     javadoc: {chunk['javadoc']}")

        if kind == "skeleton":
            print(chunk["text"])
        elif kind in ("method", "constructor"):
            print(f"  return_type    : {chunk.get('return_type')}")
            print(f"  parameters     : {chunk.get('parameters')}")
            for annotation in chunk.get("annotations", []):
                print(f"  annotation     : {annotation['text']}  attrs={annotation.get('attributes', {})}")
            print(f"  modifiers      : {chunk.get('modifiers')}")
            print(f"  throws         : {chunk.get('throws')}")
            print(f"  calls          : {chunk.get('calls')}")
            print(f"  fields_accessed: {chunk.get('fields_accessed')}")
            if chunk.get("embed_text"):
                print("  ── embed_text ──")
                print(chunk["embed_text"])
        elif kind == "type":
            print(f"  type_kind   : {chunk.get('type_kind')}")
            print(f"  extends     : {chunk.get('extends')}")
            print(f"  implements  : {chunk.get('implements')}")
            for annotation in chunk.get("annotations", []):
                print(f"  annotation  : {annotation['text']}  attrs={annotation.get('attributes', {})}")
            print(f"  is_exception: {chunk.get('is_exception')}")
        elif kind == "field":
            print(f"  field_type  : {chunk.get('field_type')}")
            print(f"  annotations : {annotation_texts(chunk.get('annotations', []))}")
            print(f"  modifiers   : {chunk.get('modifiers')}")
            print(f"  text        : {chunk['text'].strip()}")
            print("  ── embed_text ──")
            print(f"  {chunk.get('embed_text', '')}")
        elif kind == "record_component":
            print(f"  component_type: {chunk.get('component_type')}")
            print(f"  text          : {chunk['text'].strip()}")
        elif kind == "file":
            print(f"  package     : {chunk.get('package')}")
            print(f"  imports     : {len(chunk.get('imports', []))} imports")
        else:
            print(chunk.get("text", "").strip())


if __name__ == "__main__":
    main()
