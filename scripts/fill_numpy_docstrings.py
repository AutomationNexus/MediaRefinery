"""Insert minimal NumPy docstrings for missing public API symbols (task007 helper)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

INDENT = "    "
MAGIC = frozenset({"__init__", "__enter__", "__exit__", "__repr__", "__str__"})


def _human(name: str) -> str:
    return name.lstrip("_").replace("_", " ")


def _summary(name: str, kind: str, module_stem: str) -> str:
    if kind == "module":
        label = re.sub(r"_+", " ", module_stem).strip()
        return f"{label.capitalize()} for MediaRefinery."
    if kind == "class":
        return f"Represent {_human(name)}."
    if name in MAGIC:
        return {
            "__init__": "Initialize the instance.",
            "__enter__": "Enter the context manager.",
            "__exit__": "Exit the context manager.",
            "__repr__": "Return the debug representation.",
            "__str__": "Return the string representation.",
        }[name]
    if name.startswith("get_"):
        return f"Return {_human(name[4:])}."
    if name.startswith("set_"):
        return f"Set {_human(name[4:])}."
    if name.startswith("is_") or name.startswith("has_"):
        return f"Indicate whether {_human(name[3:])}."
    if name.startswith("create_"):
        return f"Create {_human(name[7:])}."
    if name.startswith("load_"):
        return f"Load {_human(name[5:])}."
    if name.startswith("run_"):
        return f"Run {_human(name[4:])}."
    if name.startswith("build_"):
        return f"Build {_human(name[6:])}."
    if name.startswith("validate_"):
        return f"Validate {_human(name[9:])}."
    if name.startswith("list_"):
        return f"List {_human(name[5:])}."
    if name.startswith("record_"):
        return f"Record {_human(name[7:])}."
    if name.startswith("write_"):
        return f"Write {_human(name[6:])}."
    if name.startswith("fetch_"):
        return f"Fetch {_human(name[6:])}."
    return f"{_human(name).capitalize()}."


def _type_name(node: ast.expr | None) -> str:
    if node is None:
        return "Any"
    try:
        return ast.unparse(node)
    except Exception:
        return "Any"


def _is_public(name: str) -> bool:
    return not name.startswith("_") or name in MAGIC


def _is_dataclass(node: ast.ClassDef) -> bool:
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name) and target.id == "dataclass":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "dataclass":
            return True
    return False


def _field_lines(node: ast.ClassDef) -> list[str]:
    lines: list[str] = []
    for stmt in node.body:
        if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
            lines.append(f"{stmt.target.id} : {_type_name(stmt.annotation)}")
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    lines.append(f"{target.id} : Any")
    return lines


def _params(args: ast.arguments) -> list[str]:
    out: list[str] = []
    posonly = list(args.posonlyargs)
    regular = list(args.args)
    defaults = [None] * (len(posonly) + len(regular) - len(args.defaults)) + list(args.defaults)
    idx = 0
    for arg in posonly + regular:
        if arg.arg in {"self", "cls"}:
            idx += 1
            continue
        default = defaults[idx] if idx < len(defaults) else None
        suffix = ", optional" if default is not None else ""
        out.append(f"{arg.arg} : {_type_name(arg.annotation)}{suffix}")
        idx += 1
    for arg, default in zip(args.kwonlyargs, args.kw_defaults, strict=False):
        suffix = ", optional" if default is not None else ""
        out.append(f"{arg.arg} : {_type_name(arg.annotation)}{suffix}")
    if args.vararg:
        out.append(f"{args.vararg.arg} : tuple")
    if args.kwarg:
        out.append(f"{args.kwarg.arg} : Any, optional")
    return out


def _build_docstring(*, name: str, kind: str, module_stem: str, node: ast.AST | None) -> str:
    parts: list[str] = [_summary(name, kind, module_stem), ""]
    if kind == "class" and isinstance(node, ast.ClassDef) and _is_dataclass(node):
        fields = _field_lines(node)
        if fields:
            parts.extend(["Attributes", "----------", *fields, ""])
    if kind in {"function", "method"} and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        params = _params(node.args)
        if params:
            parts.extend(["Parameters", "----------", *params, ""])
        if node.returns is not None:
            parts.extend(["Returns", "-------", _type_name(node.returns), ""])
    while parts and parts[-1] == "":
        parts.pop()
    return "\n".join(parts)


def _has_docstring(node: ast.AST) -> bool:
    body = getattr(node, "body", None)
    if not body:
        return False
    first = body[0]
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def _quote(text: str, indent: str) -> list[str]:
    lines = text.split("\n")
    if len(lines) == 1:
        return [f'{indent}"""{lines[0]}"""\n']
    out = [f'{indent}"""{lines[0]}\n']
    out.extend(f"{indent}{line}\n" for line in lines[1:])
    out.append(f'{indent}"""\n')
    return out


def _module_insert_index(tree: ast.Module) -> int | None:
    if _has_docstring(tree):
        return None
    return 0


def _class_insert_index(node: ast.ClassDef) -> int:
    """Insert immediately after the class header line (inside the class body)."""
    return node.lineno


class Collector(ast.NodeVisitor):
    def __init__(self, source_lines: list[str], module_stem: str) -> None:
        self.lines = source_lines
        self.module_stem = module_stem
        self.pending: list[tuple[int, list[str]]] = []
        self.in_class = 0

    def visit_Module(self, node: ast.Module) -> None:
        idx = _module_insert_index(node)
        if idx is not None:
            doc = _build_docstring(
                name=self.module_stem,
                kind="module",
                module_stem=self.module_stem,
                node=None,
            )
            self.pending.append((idx, _quote(doc, "")))
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if _is_public(node.name) and not _has_docstring(node) and node.body:
            doc = _build_docstring(
                name=node.name,
                kind="class",
                module_stem=self.module_stem,
                node=node,
            )
            line_no = _class_insert_index(node)
            indent = INDENT
            if node.body:
                body_line = self.lines[node.body[0].lineno - 1]
                if body_line.strip():
                    indent = body_line[: len(body_line) - len(body_line.lstrip())]
            self.pending.append((line_no, _quote(doc, indent)))
        self.in_class += 1
        self.generic_visit(node)
        self.in_class -= 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_fn(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_fn(node)

    def _visit_fn(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not _is_public(node.name) or _has_docstring(node) or not node.body:
            return
        kind = "method" if self.in_class else "function"
        doc = _build_docstring(
            name=node.name,
            kind=kind,
            module_stem=self.module_stem,
            node=node,
        )
        indent = self._indent_at(node.body[0].lineno - 1)
        self.pending.append((node.body[0].lineno - 1, _quote(doc, indent)))
        self.generic_visit(node)

    def _indent_at(self, line_idx: int) -> str:
        line = self.lines[line_idx]
        return line[: len(line) - len(line.lstrip())]


def process_file(path: Path) -> bool:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        print(f"skip syntax error: {path}", file=sys.stderr)
        return False
    lines = source.splitlines(keepends=True)
    collector = Collector(lines, path.stem)
    collector.visit(tree)
    if not collector.pending:
        return False
    for line_no, doc_lines in sorted(collector.pending, key=lambda x: x[0], reverse=True):
        lines[line_no:line_no] = doc_lines
    new_source = "".join(lines)
    if new_source == source:
        return False
    path.write_text(new_source, encoding="utf-8")
    return True


def main(argv: list[str]) -> int:
    roots = [Path(p) for p in argv[1:]] or [Path("src")]
    changed = 0
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if process_file(path):
                changed += 1
                print(f"updated {path}")
    print(f"done ({changed} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
