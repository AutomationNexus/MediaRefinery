"""Insert one-line test docstrings where missing (task007 helper)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path


def _summary(name: str) -> str:
    text = name.removeprefix("test_").replace("_", " ")
    if not text:
        return "Run the test."
    return f"Test {text}."


def _quote(text: str, indent: str) -> list[str]:
    return [f'{indent}"""{text}"""\n']


class Collector(ast.NodeVisitor):
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines
        self.pending: list[tuple[int, list[str]]] = []
        self.in_class = False

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        if node.name.startswith("Test") and not _has_docstring(node) and node.body:
            indent = self._indent(node.body[0].lineno - 1)
            self.pending.append(
                (node.body[0].lineno - 1, _quote(f"Tests for {_human_class(node.name)}.", indent))
            )
        self.in_class = True
        self.generic_visit(node)
        self.in_class = False

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        if not node.name.startswith("test_") or _has_docstring(node) or not node.body:
            return
        indent = self._indent(node.body[0].lineno - 1)
        self.pending.append((node.body[0].lineno - 1, _quote(_summary(node.name), indent)))
        self.generic_visit(node)

    def _indent(self, line_idx: int) -> str:
        line = self.lines[line_idx]
        return line[: len(line) - len(line.lstrip())]


def _human_class(name: str) -> str:
    base = name.removeprefix("Test")
    spaced = re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", base)
    return spaced.strip().lower() or name


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


def process_file(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    lines = source.splitlines(keepends=True)
    collector = Collector(lines)
    collector.visit(tree)
    if not collector.pending:
        return 0
    for line_no, doc_lines in sorted(collector.pending, key=lambda x: x[0], reverse=True):
        lines[line_no:line_no] = doc_lines
    path.write_text("".join(lines), encoding="utf-8")
    return len(collector.pending)


def main(argv: list[str]) -> int:
    root = Path(argv[1] if len(argv) > 1 else "tests")
    total = 0
    for path in sorted(root.rglob("test_*.py")):
        count = process_file(path)
        if count:
            print(f"updated {path} (+{count})")
            total += count
    print(f"done ({total} docstrings)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
