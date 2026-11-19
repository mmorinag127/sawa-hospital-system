"""Keep historical AST fingerprints stable across Python syntax versions."""
import ast
from copy import deepcopy
import hashlib


def pinned_ast_hash(node):
    normalized = deepcopy(node)
    for child in ast.walk(normalized):
        # Python 3.12 added this field. Keep nonempty syntax in the fingerprint.
        if getattr(child, "type_params", None) == []:
            del child.type_params
    return hashlib.sha256(ast.dump(normalized).encode()).hexdigest()
