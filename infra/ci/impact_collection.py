"""Attribute collection reads to their test modules and imported test helpers."""
import ast
import sys


class CollectionInputs:
    def __init__(self, root, tracked):
        self.root = str(root) + '/'
        self.paths = {}
        for name in tracked:
            if name.startswith('backend/tests/') and name.endswith('.py'):
                path = root / name
                parts = name.removeprefix('backend/').removesuffix('.py').split('/')
                if parts[-1] == '__init__':
                    parts.pop()
                self.paths[str(path)] = '.'.join(parts)
        modules = {}
        for path, name in self.paths.items():
            # Tests run with several test directories on sys.path. A bare
            # helper import can therefore name more than one tracked module.
            parts = name.split('.')
            for offset in range(len(parts)):
                modules.setdefault('.'.join(parts[offset:]), set()).add(path)
        self.edges = {path: set() for path in self.paths}
        self.unknown = set()
        self.closures = {}
        self.inputs = {}
        self.unsafe = {}
        for path, name in self.paths.items():
            try:
                with open(path, encoding='utf-8') as source:
                    tree = ast.parse(source.read(), filename=path)
            except (SyntaxError, UnicodeError):
                self.unknown.add(path)
                continue
            package = name.split('.') if path.endswith('/__init__.py') else name.split('.')[:-1]

            def add(module):
                parts = module.split('.')
                for length in range(1, len(parts) + 1):
                    self.edges[path].update(modules.get('.'.join(parts[:length]), ()))

            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        add(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    prefix = package[:len(package) - node.level + 1] if node.level else []
                    module = '.'.join([*prefix, *(node.module or '').split('.')]).strip('.')
                    add(module)
                    for alias in node.names:
                        add(module + '.' + alias.name)
                elif isinstance(node, ast.Call):
                    function = node.func
                    function_name = function.id if isinstance(function, ast.Name) else function.attr if isinstance(function, ast.Attribute) else ''
                    if function_name in {'import_module', '__import__'}:
                        if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str) and not node.args[0].value.startswith('.'):
                            add(node.args[0].value)
                        else:
                            self.unknown.add(path)
                    elif function_name in {'spec_from_file_location', 'exec_module'}:
                        self.unknown.add(path)

    def owner(self):
        frame = sys._getframe(1)
        while frame:
            name = frame.f_code.co_filename
            if name.startswith(self.root):
                if name in {self.root + 'infra/ci/impact_collection.py',
                            self.root + 'infra/ci/pytest_impact.py'}:
                    frame = frame.f_back
                    continue
                if name in self.paths and not name.endswith('/conftest.py'):
                    return name
                # A production/conftest read may affect every test, not only
                # the first importing collector. Keep it shared and conservative.
                return None
            frame = frame.f_back
        return None

    def dependencies(self, path):
        path = str(path)
        if path not in self.closures:
            pending, found = [path], set()
            while pending:
                current = pending.pop()
                if current in found:
                    continue
                found.add(current)
                if current in self.unknown:
                    found.update(self.paths)
                    break
                pending.extend(self.edges.get(current, ()))
            self.closures[path] = found
        return self.closures[path]
