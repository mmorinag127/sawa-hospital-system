# Hospital Order Output Isolation Binding

Base: `9495fee9cc2f6a55ae860f9512ef44e7355612a1`.
Branch: `codex/modernization-hospital-output-isolation-20261008`.
Scope: output_builder.py, new test_order_output_isolation.py, this document.

Failure class: fixed import-time /tmp/orders-outputs prevents owned-WT technical isolation. Shared configuration boundary is OUTPUT_DIR. Production change is exactly import os and Path(os.getenv("ORDERS_OUTPUT_DIR", "/tmp/orders-outputs")); unchanged mkdir follows. Unset default is the exact original Path; explicit environment changes only path. Empty values receive no additional fallback. All generation methods, file content, quantities, sources, master references and receipts remain unchanged.

Separate historical harness error: the five-module parent batch failed collection because existing SHIPPING_EXPORT_DIR was unspecified, causing shipping_status_store to attempt /tmp/shipping-exports. That is not fixed by this binding and must not be reclassified as an output_builder product failure. Historical exit 2 / zero executed is not test acceptance.

Unexecuted selectors under backend/tests/unit/test_order_output_isolation.py:
- test_unset_default_binding_without_service_import
- test_explicit_owned_binding_without_import_or_write
- test_empty_value_is_not_replaced_with_default
- test_exact_source_and_entire_other_ast_parity
- test_output_consumers_and_explicit_output_dir_priority_unchanged

Definitions evaluate only the extracted assignment/expression with explicit Path and getenv bindings; they do not import the service or perform mkdir. Exact source equality allows only the two approved edits. Entire AST equality after reversing those edits preserves every other node. Complete OUTPUT_DIR-consuming function ASTs remain identical to base; explicit non-None output_dir still wins and None selects OUTPUT_DIR. These checks are defined, not executed. Parent technical regression should also retain request-directory isolation and frozen receipt behavior.

Applicable AGENTS and existing test_shipping_output_isolation.py source were read. Manual edits, diff/hash/staging only; no tests/runtime/imports/build/DB/browser/commit/integration/push/deploy. Source packet is not full system acceptance. Parent monitor, commit and tests remain pending.

SHA256:
- output_builder.py: `12c07eab88d2465aef1dbeee992318b5b21471168bee8189901a970820ea893c`
- test_order_output_isolation.py: `c03080536a5cb178731cbf29b7cfbb1568bc47e3edc73287cb54730c12008dfe`

Cached binary patch hash is reported externally.
