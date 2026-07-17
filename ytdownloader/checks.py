"""
Self-audit utilities to catch false successes and broken code.

This module provides static-analysis audits that scan the ytdownloader
package to surface suspicious patterns: bad regexes, circular/missing
imports, over-broad exception swallowing, missing docstrings, and
constants inconsistencies.  It is fully self-contained and safe to run
even when other modules in the package have not yet been created.
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Package root resolution
# ---------------------------------------------------------------------------

# Resolve the package directory from this file's location so the module
# works correctly even when imported as ``ytdownloader.checks`` or run
# directly.
_PACKAGE_DIR: Path = Path(__file__).resolve().parent

# Module-level flag set after first successful load.
_LOADED: bool = False

# Patterns that a YouTube HTML regex should ideally contain so the extractor
# can find the data it needs.
_REQUIRED_YT_PATTERNS: list[str] = [
    "ytInitialPlayerResponse",
    "ytcfg",
    "sts",
]

# itag IDs that every production-ready map is expected to provide.
_REQUIRED_ITAGS: frozenset[int] = frozenset({18, 22, 137, 140})

# Keys that each ITAG_MAP value dict must carry.
_REQUIRED_ITAG_KEYS: frozenset[str] = frozenset({"ext", "vcodec", "acodec", "height"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body_has_raise_or_log(body: list[ast.stmt]) -> bool:
    """Return True if the except body contains a raise or a logging call."""
    for stmt in body:
        if isinstance(stmt, ast.Raise):
            return True
        for child in ast.walk(stmt):
            if not isinstance(child, ast.Call):
                continue
            func = child.func
            if isinstance(func, ast.Attribute) and func.attr in (
                "log",
                "debug",
                "info",
                "warning",
                "error",
                "critical",
                "exception",
            ):
                return True
            if isinstance(func, ast.Name) and func.id in ("log", "print"):
                return True
    return False


def _iter_package_py_files(package_dir: Path | None = None) -> list[Path]:
    """Return sorted list of .py files under the package directory."""
    package_dir = package_dir or _PACKAGE_DIR
    return sorted(
        p for p in package_dir.rglob("*.py")
        if p.is_file()
    )


def _source(path: Path) -> str:
    """Read a file, returning an empty string on read errors."""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# audit_regex_patterns
# ---------------------------------------------------------------------------

def audit_regex_patterns(package_dir: Path | None = None) -> list[tuple[str, int, str, str]]:
    """Scan .py files for ``re.compile(...)`` calls and flag suspicious patterns.

    Args:
        package_dir: Root directory of the package. Defaults to the directory
            containing this module.

    Returns:
        A list of ``(file, line_number, pattern_string, issue_description)``
        tuples.  ``file`` is a path relative to ``package_dir``.

    Issues flagged:
        * Empty patterns (``re.compile("")``).
        * Patterns that contain none of the known YouTube HTML markers.
        * Unescaped literal dots (``.``) outside character classes.
        * Missing ``re.DOTALL`` / ``re.IGNORECASE`` flags when the pattern
          spans multiple lines or matches case-insensitive HTML.
    """
    package_dir = package_dir or _PACKAGE_DIR
    findings: list[tuple[str, int, str, str]] = []

    for py_file in _iter_package_py_files(package_dir):
        rel = str(py_file.relative_to(package_dir))
        source = _source(py_file)
        if not source:
            continue

        for lineno, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if not stripped.startswith("re.compile("):
                continue

            # Extract the pattern string between the outermost parentheses.
            # This is a best-effort textual extraction; complex calls are
            # handled gracefully.
            open_paren = line.index("(", stripped.index("re.compile"))
            try:
                # Find matching close paren — skip over nested parens
                # inside the string literal.
                depth = 1
                i = open_paren + 1
                pattern_str: str | None = None
                while i < len(line) and depth > 0:
                    ch = line[i]
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            break
                    elif ch in ('"', "'"):
                        quote = ch
                        i += 1
                        while i < len(line):
                            if line[i] == "\\":
                                i += 2
                                continue
                            if line[i] == quote:
                                break
                            i += 1
                    i += 1

                # Extract the first string-literal token between open_paren+1
                # and close position.
                content = line[open_paren + 1 : i]
                m = re.search(r"""(['"])(.*?)\1""", content, re.DOTALL)
                if not m:
                    continue
                pattern_str = m.group(2)
            except (ValueError, IndexError):
                continue

            if pattern_str is None:
                continue

            # --- Issue 1: empty pattern ---
            if pattern_str == "":
                findings.append((rel, lineno, pattern_str, "Empty regex pattern"))
                continue

            # --- Issue 2: no YouTube HTML marker ---
            if not any(marker in pattern_str for marker in _REQUIRED_YT_PATTERNS):
                findings.append((
                    rel,
                    lineno,
                    pattern_str,
                    "Pattern does not reference any known YouTube HTML marker "
                    "(ytInitialPlayerResponse, ytcfg, sts)",
                ))

            # --- Issue 3: unescaped dots outside character classes ---
            in_char_class = False
            for idx, ch in enumerate(pattern_str):
                if ch == "[":
                    in_char_class = True
                elif ch == "]":
                    in_char_class = False
                elif ch == "." and not in_char_class:
                    # Check it is not already escaped
                    if idx == 0 or pattern_str[idx - 1] != "\\":
                        findings.append((
                            rel,
                            lineno,
                            pattern_str,
                            f"Unescaped dot at position {idx} (matches any character)",
                        ))
                        break  # report once per pattern

            # --- Issue 4: missing DOTALL/IGNORECASE ---
            flags_part = line[i + 1 :] if i < len(line) else ""
            flags_str = flags_part.strip().rstrip(")").strip()
            has_dotall = "DOTALL" in flags_str
            has_ignorecase = "IGNORECASE" in flags_str
            if "\\n" in pattern_str or "\\s" in pattern_str:
                if not has_dotall:
                    findings.append((
                        rel,
                        lineno,
                        pattern_str,
                        "Pattern spans multiple lines or uses \\s but is missing "
                        "re.DOTALL flag",
                    ))
            if re.search(r"[A-Za-z]", pattern_str) and "yt" in pattern_str.lower():
                if not has_ignorecase:
                    findings.append((
                        rel,
                        lineno,
                        pattern_str,
                        "Pattern contains alphabetic characters (possibly HTML tags) "
                        "but is missing re.IGNORECASE flag",
                    ))

    return findings


# ---------------------------------------------------------------------------
# audit_imports
# ---------------------------------------------------------------------------

def audit_imports(package_dir: Path | None = None) -> list[dict[str, Any]]:
    """Scan .py files for imports and flag anomalies.

    Args:
        package_dir: Root directory of the package.

    Returns:
        A list of dicts, each with keys ``file``, ``line``, ``issue``,
        and optionally ``detail``.
    """
    package_dir = package_dir or _PACKAGE_DIR
    findings: list[dict[str, Any]] = []

    # import_graph: imported_module_name -> set of file paths that import it
    import_graph: dict[str, set[str]] = {}

    # module_deps: source_module_name -> set of module names it imports
    module_deps: dict[str, set[str]] = {}

    # Collect all known module names in the package so we can detect imports
    # of modules that do not exist yet.
    known_modules: set[str] = set()
    for py_file in _iter_package_py_files(package_dir):
        stem = py_file.stem
        known_modules.add(stem)
        rel = py_file.relative_to(package_dir)
        if len(rel.parts) > 1:
            pkg_prefix = ".".join(rel.parts[:-1])
            known_modules.add(f"{pkg_prefix}.{stem}")

    def _module_name_for_file(file_rel: str) -> str:
        rel_path = Path(file_rel)
        if len(rel_path.parts) > 1 and rel_path.parts[-1] == "__init__.py":
            return ".".join(rel_path.parts[:-1])
        return rel_path.stem

    for py_file in _iter_package_py_files(package_dir):
        rel = str(py_file.relative_to(package_dir))
        source = _source(py_file)
        if not source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        source_module = _module_name_for_file(rel)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top_level = alias.name.split(".")[0]
                    import_graph.setdefault(top_level, set()).add(rel)
                    module_deps.setdefault(source_module, set()).add(top_level)
            elif isinstance(node, ast.ImportFrom):
                module_name = node.module or ""
                base = module_name.split(".")[0] if module_name else ""
                imported = [a.name for a in node.names]
                if base:
                    import_graph.setdefault(base, set()).add(rel)
                    module_deps.setdefault(source_module, set()).add(base)

                if node.module and node.module.startswith(".") and node.level > 0:
                    parts = node.module.lstrip(".").split(".")
                    target_stem = parts[-1] if parts else ""
                    if target_stem and target_stem not in known_modules:
                        findings.append({
                            "file": rel,
                            "line": node.lineno,
                            "issue": "Import from module that does not exist in the package",
                            "detail": f"from {node.module} (resolved stem: {target_stem})",
                        })
                elif (
                    node.module
                    and not node.module.startswith(".")
                    and node.module.split(".")[0] not in known_modules
                    and node.module.split(".")[0] not in sys.stdlib_module_names
                ):
                    pass  # Absolute import of a package-internal module; skip for now

    # Detect circular import risks (A imports B AND B imports A).
    for module_a, deps_a in module_deps.items():
        for module_b in deps_a:
            if module_b in module_deps and module_a in module_deps[module_b]:
                for fa in import_graph.get(module_a, []):
                    findings.append({
                        "file": fa,
                        "line": 0,
                        "issue": (
                            f"Potential circular import: '{module_a}' imports "
                            f"'{module_b}' and '{module_b}' imports '{module_a}'"
                        ),
                    })
                break  # report once per module_a

    # Detect unused imports: find ``import X`` or ``from X import Y`` where
    # the imported name is never referenced elsewhere in the file.
    for py_file in _iter_package_py_files(package_dir):
        rel = str(py_file.relative_to(package_dir))
        source = _source(py_file)
        if not source:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        # Collect imported names
        imported_names: dict[str, str] = {}  # name -> module
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for alias in node.names:
                    imported_names[alias.asname or alias.name] = node.module

        if not imported_names:
            continue

        # Collect all names used in the file
        used_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used_names.add(node.id)
            elif isinstance(node, ast.Attribute):
                # Walk value chain
                val = node
                while isinstance(val, ast.Attribute):
                    used_names.add(val.attr)
                    val = val.value
                if isinstance(val, ast.Name):
                    used_names.add(val.id)

        for name, module in imported_names.items():
            if name not in used_names:
                findings.append({
                    "file": rel,
                    "line": 0,
                    "issue": f"Unused import: '{name}' from '{module}'",
                })

    return findings


# ---------------------------------------------------------------------------
# audit_exception_handling
# ---------------------------------------------------------------------------

def audit_exception_handling(package_dir: Path | None = None) -> list[dict[str, Any]]:
    """Scan for bare ``except:`` and silent ``except Exception:`` clauses.

    Args:
        package_dir: Root directory of the package.

    Returns:
        A list of dicts with keys ``file``, ``line``, and ``issue``.
    """
    package_dir = package_dir or _PACKAGE_DIR
    findings: list[dict[str, Any]] = []

    for py_file in _iter_package_py_files(package_dir):
        rel = str(py_file.relative_to(package_dir))
        source = _source(py_file)
        if not source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            if node.type is None:
                findings.append({
                    "file": rel,
                    "line": node.lineno,
                    "issue": "Bare 'except:' clause swallows all exceptions silently",
                })
            elif isinstance(node.type, ast.Name) and node.type.id == "Exception":
                if len(node.body) == 0:
                    findings.append({
                        "file": rel,
                        "line": node.lineno,
                        "issue": "Bare 'except Exception:' with empty body swallows errors silently",
                    })
                elif not _body_has_raise_or_log(node.body):
                    findings.append({
                        "file": rel,
                        "line": node.lineno,
                        "issue": "'except Exception:' clause swallows errors silently without logging or re-raising",
                    })

    return findings


# ---------------------------------------------------------------------------
# audit_return_values
# ---------------------------------------------------------------------------

def audit_return_values(package_dir: Path | None = None) -> list[dict[str, Any]]:
    """Find functions that return ``None`` after a bare ``except:``.

    These are "false success" risks: the function signals success by
    returning a value but silently returns ``None`` when an unexpected
    error occurs, hiding the failure from the caller.

    Args:
        package_dir: Root directory of the package.

    Returns:
        A list of dicts with keys ``file``, ``line``, ``function``, and
        ``issue``.
    """
    package_dir = package_dir or _PACKAGE_DIR
    findings: list[dict[str, Any]] = []

    def _is_bare_except(handler: ast.ExceptHandler) -> bool:
        return handler.type is None

    def _scan_try(node: ast.AST, func_name: str, rel: str) -> None:
        """Recursively scan Try nodes within a function body."""
        if not isinstance(node, ast.Try):
            return

        has_bare_except = any(_is_bare_except(h) for h in node.handlers)
        if not has_bare_except:
            return

        # Walk the siblings of the Try node inside the parent body to see
        # if the next statement is a ``return None``.
        return_none_in_tail = False

        def _check_returns_none(n: ast.AST) -> bool:
            if isinstance(n, ast.Return):
                val = n.value
                if val is None or (isinstance(val, ast.Constant) and val.value is None):
                    return True
            elif isinstance(n, ast.If):
                if _check_returns_none(n.body[-1]) if n.body else False:
                    return True
                if _check_returns_none(n.orelse[-1]) if n.orelse else False:
                    return True
            elif isinstance(n, ast.Try):
                return any(_check_returns_none(stmt) for stmt in n.body)
            elif isinstance(n, list):
                return any(_check_returns_none(stmt) for stmt in n)
            return False

        # The AST walker gives us the Try node directly; we need the
        # enclosing body list.  We handle this at the parent level below.
        _node_for_parent = node  # sentinel; actual check done in _scan_function

    # Re-scan at function level so we can inspect siblings of Try nodes.
    for py_file in _iter_package_py_files(package_dir):
        rel = str(py_file.relative_to(package_dir))
        source = _source(py_file)
        if not source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue

            func_name = node.name
            body_stmts = node.body

            for idx, stmt in enumerate(body_stmts):
                if not isinstance(stmt, ast.Try):
                    continue
                has_bare = any(h.type is None for h in stmt.handlers)
                if not has_bare:
                    continue

                # Check the remaining statements after this Try node
                following = body_stmts[idx + 1 :]

                def _stmt_returns_none(s: ast.AST) -> bool:
                    if isinstance(s, ast.Return):
                        val = s.value
                        return val is None or (
                            isinstance(val, ast.Constant) and val.value is None
                        )
                    if isinstance(s, ast.If):
                        if _stmt_returns_none(s.body[-1]) if s.body else False:
                            return True
                        if _stmt_returns_none(s.orelse[-1]) if s.orelse else False:
                            return True
                    if isinstance(s, ast.Try):
                        return any(_stmt_returns_none(st2) for st2 in s.body)
                    if isinstance(s, list):
                        return any(_stmt_returns_none(st2) for st2 in s)
                    return False

                if any(_stmt_returns_none(st2) for st2 in following):
                    findings.append({
                        "file": rel,
                        "line": stmt.lineno,
                        "function": func_name,
                        "issue": (
                            f"Function '{func_name}' has a bare except and "
                            f"returns None afterward — false success risk"
                        ),
                    })

    return findings


# ---------------------------------------------------------------------------
# audit_docstrings
# ---------------------------------------------------------------------------

def audit_docstrings(package_dir: Path | None = None) -> list[dict[str, Any]]:
    """Flag public functions and classes missing docstrings.

    "Public" means names that do not start with an underscore.

    Args:
        package_dir: Root directory of the package.

    Returns:
        A list of dicts with keys ``file``, ``line``, ``name``, and
        ``kind`` (``function`` or ``class``).
    """
    package_dir = package_dir or _PACKAGE_DIR
    findings: list[dict[str, Any]] = []

    for py_file in _iter_package_py_files(package_dir):
        rel = str(py_file.relative_to(package_dir))
        source = _source(py_file)
        if not source:
            continue

        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_") and ast.get_docstring(node) is None:
                    findings.append({
                        "file": rel,
                        "line": node.lineno,
                        "name": node.name,
                        "kind": "function",
                    })
            elif isinstance(node, ast.ClassDef):
                if not node.name.startswith("_") and ast.get_docstring(node) is None:
                    findings.append({
                        "file": rel,
                        "line": node.lineno,
                        "name": node.name,
                        "kind": "class",
                    })

    return findings


# ---------------------------------------------------------------------------
# audit_constants
# ---------------------------------------------------------------------------

def audit_constants(package_dir: Path | None = None) -> list[dict[str, Any]]:
    """Validate ``constants.py`` for itag map consistency.

    Checks performed:
        * Duplicate itag entries in ``ITAG_MAP``.
        * Missing required itags (18, 22, 137, 140).
        * Dict values missing required keys (``ext``, ``vcodec``, ``acodec``,
          ``height``).

    Args:
        package_dir: Root directory of the package.

    Returns:
        A list of dicts with keys ``file``, ``line``, and ``issue``.
    """
    package_dir = package_dir or _PACKAGE_DIR
    findings: list[dict[str, Any]] = []

    constants_file = package_dir / "constants.py"
    if not constants_file.exists():
        findings.append({
            "file": "constants.py",
            "line": 0,
            "issue": "constants.py does not exist — cannot perform constants audit",
        })
        return findings

    source = _source(constants_file)
    if not source:
        return findings

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        findings.append({
            "file": "constants.py",
            "line": 0,
            "issue": f"constants.py has a syntax error: {exc}",
        })
        return findings

    # Locate ITAG_MAP assignment
    itag_map_assign: ast.Assign | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "ITAG_MAP":
                    itag_map_assign = node
                    break
        if itag_map_assign is not None:
            break

    if itag_map_assign is None:
        findings.append({
            "file": "constants.py",
            "line": 0,
            "issue": "ITAG_MAP not found in constants.py",
        })
        return findings

    # Try to evaluate the dict statically (works for literal dicts).
    try:
        itag_map_value = ast.literal_eval(itag_map_assign.value)
    except Exception:
        findings.append({
            "file": "constants.py",
            "line": itag_map_assign.lineno,
            "issue": "ITAG_MAP could not be statically evaluated",
        })
        return findings

    if not isinstance(itag_map_value, dict):
        findings.append({
            "file": "constants.py",
            "line": itag_map_assign.lineno,
            "issue": "ITAG_MAP is not a dict",
        })
        return findings

    # --- Duplicate itags ---
    seen_itags: dict[int, int] = {}
    for itag_str, value in itag_map_value.items():
        try:
            itag = int(itag_str)
        except (ValueError, TypeError):
            continue
        if itag in seen_itags:
            findings.append({
                "file": "constants.py",
                "line": seen_itags[itag],
                "issue": f"Duplicate itag entry: {itag} (appears multiple times in ITAG_MAP)",
            })
        else:
            seen_itags[itag] = itag_map_assign.lineno  # best-effort line

    # --- Missing required itags ---
    present_itags: set[int] = set()
    for k in itag_map_value.keys():
        try:
            present_itags.add(int(k))
        except (ValueError, TypeError):
            continue
    missing = _REQUIRED_ITAGS - present_itags
    for itag in sorted(missing):
        findings.append({
            "file": "constants.py",
            "line": 0,
            "issue": f"Required itag {itag} is missing from ITAG_MAP",
        })

    # --- Missing required keys in dict values ---
    for itag_str, value in itag_map_value.items():
        if not isinstance(value, dict):
            findings.append({
                "file": "constants.py",
                "line": 0,
                "issue": (
                    f"ITAG_MAP[{itag_str!r}] value is not a dict "
                    f"(got {type(value).__name__})"
                ),
            })
            continue
        missing_keys = _REQUIRED_ITAG_KEYS - set(value.keys())
        if missing_keys:
            findings.append({
                "file": "constants.py",
                "line": 0,
                "issue": (
                    f"ITAG_MAP[{itag_str!r}] is missing required keys: "
                    f"{sorted(missing_keys)}"
                ),
            })

    return findings


# ---------------------------------------------------------------------------
# audit_all
# ---------------------------------------------------------------------------

def audit_all(package_dir: Path | None = None) -> dict[str, Any]:
    """Run all audits and return a structured summary.

    Args:
        package_dir: Root directory of the package.  Defaults to the
            directory containing this module.

    Returns:
        A dict with keys:

        * ``"passed"`` — list of audit names that produced zero findings.
        * ``"failed"`` — list of audit names that produced one or more
          findings.
        * ``"results"`` — mapping of audit name → list of finding dicts /
          tuples.
        * ``"summary"`` — human-readable summary string.
    """
    package_dir = package_dir or _PACKAGE_DIR

    audits: dict[str, list] = {
        "regex_patterns": audit_regex_patterns(package_dir),
        "imports": audit_imports(package_dir),
        "exception_handling": audit_exception_handling(package_dir),
        "return_values": audit_return_values(package_dir),
        "docstrings": audit_docstrings(package_dir),
        "constants": audit_constants(package_dir),
    }

    passed: list[str] = []
    failed: list[str] = []
    for name, results in audits.items():
        if results:
            failed.append(name)
        else:
            passed.append(name)

    total_findings = sum(len(v) for v in audits.values())

    summary_lines = [
        "=" * 60,
        "  ytdownloader self-audit report",
        "=" * 60,
        f"  Audits passed : {len(passed)} / {len(audits)}",
        f"  Audits failed : {len(failed)}",
        f"  Total findings: {total_findings}",
        "-" * 60,
    ]
    for name in passed:
        summary_lines.append(f"  [PASS] {name}")
    for name in failed:
        count = len(audits[name])
        summary_lines.append(f"  [FAIL] {name} ({count} finding{'s' if count != 1 else ''})")
    summary_lines.append("=" * 60)

    summary = "\n".join(summary_lines)
    print(summary)

    return {
        "passed": passed,
        "failed": failed,
        "results": audits,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Standalone entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    audit_all()
