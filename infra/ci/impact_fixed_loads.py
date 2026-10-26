"""Resolve only immutable, __file__-anchored importlib file-loader chains."""
import ast
from pathlib import Path


def fixed_file_loads(tree, filename, tracked):
    required = {'Path': 'pathlib', 'spec_from_file_location': 'importlib.util',
                'module_from_spec': 'importlib.util'}
    imported = {alias.asname or alias.name: node.module for node in tree.body
                if isinstance(node, ast.ImportFrom) for alias in node.names}
    if any(imported.get(name) != module for name, module in required.items()):
        return {}, set()
    reserved = set(required) | {'ImportError'}
    imports = [(node, alias) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names]
    if any(alias.name == '*' or (alias.asname or alias.name.split('.')[0]) == 'ImportError'
           for node, alias in imports):
        return {}, set()
    for name, module in required.items():
        bindings = [(node, alias) for node, alias in imports
                    if (alias.asname or alias.name.split('.')[0]) == name]
        if len(bindings) != 1 or not isinstance(bindings[0][0], ast.ImportFrom) or bindings[0][0].module != module:
            return {}, set()
    ready_line = max(node.lineno for node, alias in imports
                     if (alias.asname or alias.name) in required)
    if any(isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and node.id in reserved
           or isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.arg))
           and getattr(node, 'name', getattr(node, 'arg', None)) in reserved
           for node in ast.walk(tree)):
        return {}, set()
    locations = {'__file__': Path(filename)}
    specs, modules, dependencies, accepted = {}, {}, {}, set()

    def location(node):
        if isinstance(node, ast.Name):
            return locations.get(node.id)
        if isinstance(node, ast.Attribute) and node.attr == 'parent':
            value = location(node.value)
            return value.parent if value is not None else None
        if isinstance(node, ast.Call) and not node.keywords:
            if isinstance(node.func, ast.Name) and node.func.id == 'Path' and len(node.args) == 1:
                return location(node.args[0])
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'resolve' and not node.args:
                value = location(node.func.value)
                return value.resolve() if value is not None else None
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Attribute)
                and node.value.attr == 'parents' and isinstance(node.slice, ast.Constant)
                and type(node.slice.value) is int and node.slice.value >= 0):
            value = location(node.value.value)
            if value is not None and node.slice.value < len(value.parents):
                return value.parents[node.slice.value]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div) and isinstance(node.right, ast.Constant) and isinstance(node.right.value, str):
            value = location(node.left)
            return value / node.right.value if value is not None else None
        return None

    def loader_guard(node):
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or):
            return all(loader_guard(value) for value in node.values)
        if not (isinstance(node, ast.Compare) and len(node.ops) == len(node.comparators) == 1
                and isinstance(node.ops[0], ast.Is)
                and isinstance(node.comparators[0], ast.Constant) and node.comparators[0].value is None):
            return False
        left = node.left
        return (isinstance(left, ast.Name) and left.id in specs
                or isinstance(left, ast.Attribute) and left.attr == 'loader'
                and isinstance(left.value, ast.Name) and left.value.id in specs)

    assigned = set()
    for node in tree.body:
        if (node.lineno < ready_line and not isinstance(node, (ast.Import, ast.ImportFrom))
                and not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                         and isinstance(node.value.value, str))):
            return {}, set()
        if not isinstance(node, (ast.Import, ast.ImportFrom, ast.Assign, ast.If, ast.Expr)):
            return {}, set()
        if (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and len(node.test.ops) == len(node.test.comparators) == 1
                and isinstance(node.test.ops[0], ast.Eq)
                and isinstance(node.test.left, ast.Attribute) and node.test.left.attr == 'name'
                and isinstance(node.test.comparators[0], ast.Constant)):
            value = location(node.test.left.value)
            if value is not None:
                branch = node.body if value.name == node.test.comparators[0].value else node.orelse
                for statement in branch:
                    if not (isinstance(statement, ast.Assign) and len(statement.targets) == 1
                            and isinstance(statement.targets[0], ast.Name)):
                        return {}, set()
                    resolved = location(statement.value)
                    if resolved is None:
                        return {}, set()
                    locations[statement.targets[0].id] = resolved
                continue
        if isinstance(node, ast.If):
            if (not loader_guard(node.test) or node.orelse
                    or not all(isinstance(statement, ast.Raise) and statement.cause is None
                               and isinstance(statement.exc, ast.Call)
                               and isinstance(statement.exc.func, ast.Name) and statement.exc.func.id == 'ImportError'
                               and not statement.exc.keywords
                               and all(isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                                       for argument in statement.exc.args)
                               for statement in node.body)):
                return {}, set()
            continue
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name, value = node.targets[0].id, node.value
            if name in assigned:
                return {}, set()
            assigned.add(name)
            resolved = location(value)
            locations.pop(name, None)
            if resolved is not None:
                locations[name] = resolved
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and not value.keywords:
                if value.func.id == 'spec_from_file_location' and len(value.args) == 2:
                    target = location(value.args[1])
                    module_name = value.args[0]
                    if (isinstance(module_name, ast.Constant) and isinstance(module_name.value, str)
                            and module_name.value.isidentifier()
                            and target is not None and str(target) in tracked):
                        specs[name] = str(target)
                        dependencies[id(value)] = str(target)
                elif value.func.id == 'module_from_spec' and len(value.args) == 1 and isinstance(value.args[0], ast.Name):
                    if value.args[0].id in specs:
                        modules[name] = value.args[0].id
                        accepted.add(id(value))
                elif resolved is None:
                    return {}, set()
            elif isinstance(value, ast.Call) and resolved is None:
                return {}, set()
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            return {}, set()
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            function = call.func
            if (isinstance(function, ast.Attribute) and function.attr == 'exec_module'
                    and isinstance(function.value, ast.Attribute) and function.value.attr == 'loader'
                    and isinstance(function.value.value, ast.Name) and len(call.args) == 1
                    and not call.keywords and isinstance(call.args[0], ast.Name)):
                target = specs.get(function.value.value.id)
                if target is not None and modules.get(call.args[0].id) == function.value.value.id:
                    accepted.add(id(call))
            if id(call) not in accepted:
                return {}, set()
        elif isinstance(node, ast.Expr) and not (isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
            return {}, set()
    return dependencies, accepted
