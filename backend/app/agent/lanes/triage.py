"""Deterministic pre-patch repository and issue triage. No LLM calls or repo execution."""
from __future__ import annotations

import ast
import builtins
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
import sys
from typing import Any

from app.agent.lanes.types import (
    LaneName,
    RiskFloor,
    TaskType,
    TriageReport,
    max_risk,
    refine_task_type,
)

BUILTIN_NAMES: frozenset[str] = frozenset(dir(builtins)) | {
    "__file__",
    "__name__",
    "__doc__",
    "__package__",
    "__loader__",
    "__spec__",
    "__all__",
    "__annotations__",
    "__builtins__",
    "__debug__",
}

PURE_STDLIB_MODULES: frozenset[str] = frozenset({
    "abc", "argparse", "array", "ast", "base64", "binascii", "bisect", "bz2",
    "calendar", "cmath", "collections", " colorsys", " Compression", "contextlib",
    "contextvars", "copy", "csv", "dataclasses", "datetime", "decimal", "difflib",
    "dis", "enum", "errno", "fnmatch", "fractions", "functools", "getopt", " getpass",
    "gettext", "glob", "graphlib", "gzip", "hashlib", "heapq", "hmac", "html",
    "http", "imaplib", "inspect", "io", "ipaddress", "itertools", "json", "keyword",
    "linecache", "locale", "lzma", "math", "mimetypes", "numbers", "operator",
    "os", "pathlib", "platform", "plistlib", "posixpath", "pprint", "queue",
    "quopri", "random", "re", "reprlib", "sched", "secrets", "selectors", "shlex",
    "shutil", "stat", "statistics", "string", "stringprep", "struct", "sys",
    "tarfile", "tempfile", "textwrap", "time", "timeit", "token", "tokenize",
    "tomllib", "traceback", "types", "typing", "unicodedata", "urllib", "uuid",
    "warnings", "weakref", "xml", "zipfile", "zlib", "zoneinfo",
})

SIDE_EFFECT_STDLIB_MODULES: frozenset[str] = frozenset({
    "atexit", "ctypes", " curses", "importlib", "logging", "multiprocessing",
    " names", "pdb", "pickle", "pty", "readline", "resource", "rlcompleter",
    "runpy", "shelve", "signal", "site", "socket", " socketserver", "sqlite3",
    "ssl", "subprocess", "sysconfig", "syslog", "threading", "tkinter", "trace",
    "tty", "turtle", "unittest", "venv", "webbrowser", "winreg", "winsound", "wsgiref",
})

STDLIB_MODULES: frozenset[str] = (
    (frozenset(getattr(sys, "stdlib_module_names", ())) | PURE_STDLIB_MODULES)
    - {"__main__"}
)

STDLIB_SYMBOL_OWNERS: dict[str, str] = {
    "Path": "pathlib",
    "PurePath": "pathlib",
    "PurePosixPath": "pathlib",
    "PureWindowsPath": "pathlib",
    "dataclass": "dataclasses",
    "field": "dataclasses",
    "asdict": "dataclasses",
    "replace": "dataclasses",
    "Enum": "enum",
    "IntEnum": "enum",
    "StrEnum": "enum",
    "auto": "enum",
    "defaultdict": "collections",
    "Counter": "collections",
    "deque": "collections",
    "OrderedDict": "collections",
    "namedtuple": "collections",
    "ChainMap": "collections",
    "partial": "functools",
    "lru_cache": "functools",
    "cache": "functools",
    "reduce": "functools",
    "wraps": "functools",
    "total_ordering": "functools",
    "chain": "itertools",
    "islice": "itertools",
    "groupby": "itertools",
    "product": "itertools",
    "combinations": "itertools",
    "permutations": "itertools",
    "repeat": "itertools",
    "count": "itertools",
    "cycle": "itertools",
    "StringIO": "io",
    "BytesIO": "io",
    "datetime": "datetime",
    "date": "datetime",
    "time": "datetime",
    "timedelta": "datetime",
    "timezone": "datetime",
    "UUID": "uuid",
    "uuid4": "uuid",
    "uuid1": "uuid",
    "Decimal": "decimal",
    "Fraction": "fractions",
    "contextmanager": "contextlib",
    "asynccontextmanager": "contextlib",
    "suppress": "contextlib",
    "ExitStack": "contextlib",
    "SimpleNamespace": "types",
    "Any": "typing",
    "Optional": "typing",
    "Union": "typing",
    "Literal": "typing",
    "Callable": "typing",
    "Sequence": "typing",
    "Mapping": "typing",
    "Iterable": "typing",
    "Iterator": "typing",
    "Generator": "typing",
    "TypeVar": "typing",
    "Protocol": "typing",
    "TypedDict": "typing",
    "Final": "typing",
    "ClassVar": "typing",
    "cast": "typing",
    "overload": "typing",
    "ABC": "abc",
    "abstractmethod": "abc",
    " deepcopy": "copy",
    "copy": "copy",
    "dedent": "textwrap",
    "indent": "textwrap",
}

_ADVERSARIAL_PATTERNS = (
    re.compile(r"(?i)\b(?:just\s+)?remove\s+the\s+(?:check|validation|guard|auth|verification|permission|security)\b"),
    re.compile(r"(?i)\b(?:disable|bypass|skip)\s+(?:auth|security|validation|check|verification|policy|tests?)\b"),
    re.compile(r"(?i)\bforce\s+fast\s+lane\b"),
)

_RISKY_ISSUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("auth_security_crypto", re.compile(r"(?i)\b(?:auth|authentication|authorization|oauth|jwt|bcrypt|argon2|pbkdf2|cryptography|encrypt|decrypt|csrf|permission|acl|session_token|verify_token|check_password)\b")),
    ("serialization", re.compile(r"(?i)\b(?:pickle|cpickle|dill|shelve|marshal|yaml\.load|unsafe_load|jsonpickle)\b")),
    ("subprocess_eval", re.compile(r"(?i)\b(?:subprocess|os\.system|os\.popen|os\.exec|eval\s*\(|exec\s*\(|shell\s*=\s*True)\b")),
    ("sql_execution", re.compile(r"(?i)\b(?:sql|sqlite3|sqlalchemy|psycopg|cursor\.execute|raw_sql|SELECT\s+.+\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)\b")),
    ("migration_path", re.compile(r"(?i)\b(?:alembic|schema\s+migration|database\s+migration)\b")),
)

_SECRETS_CONTENT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AIza[0-9A-Za-z_\-]{30,}|sk-[A-Za-z0-9_\-]{20,})\b"),
    re.compile(r"(?i)\b(?:api[_-]?key|secret[_-]?key|client[_-]?secret|private[_-]?key|access[_-]?token|password|passwd)\s*=\s*['\"][^'\"]{4,}['\"]"),
)

_CONFIG_PROFILE_NAMES: frozenset[str] = frozenset({
    ".flake8",
    ".gitignore",
    ".editorconfig",
    ".python-version",
    "pytest.ini",
    "ruff.toml",
    ".ruff.toml",
    "mypy.ini",
    ".mypy.ini",
    ".coveragerc",
})

_UNCLEAR_EXPLICIT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"(?is)^\s*(?:\?+|tbd|todo|n/?a|none|help|fix\s+it|broken|doesn'?t\s+work|it\s+is\s+broken|not\s+working|something\s+is\s+wrong|please\s+fix)"
        r"(?:[\s.!?]+(?:\?+|tbd|todo|n/?a|none|help|fix\s+it|broken|doesn'?t\s+work|it\s+is\s+broken|not\s+working|something\s+is\s+wrong|please\s+fix))*\s*[.!?]*\s*$"
    ),
    re.compile(r"(?i)\b(?:need(?:s)?\s+clarification|unclear\s+requirements?|requirements?\s+are\s+ambiguous|not\s+sure\s+what\s+should\s+happen)\b"),
)

_FEATURE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:new\s+feature|feature\s+request)\b"),
    re.compile(r"(?i)^\s*(?:feat(?:\([^)]+\))?:|feature:|enhancement:)"),
    re.compile(r"(?i)\b(?:implement\s+(?:a\s+)?new|introduce\s+(?:a\s+)?new|add\s+support\s+for|support\s+new)\b"),
    re.compile(
        r"(?i)\b(?:add|create|build|implement)\s+(?:a\s+|an\s+|the\s+)?(?:new\s+)?"
        r"(?:endpoint|route|cli\s+flag|command(?:\s+line)?\s+flag|flag|subcommand|option|exporter|webhook\s+endpoint|handler|feature|capability|api\s+method|public\s+function|class|module)\b"
    ),
)

_REFACTOR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*(?:refactor(?:\([^)]+\))?:)"),
    re.compile(r"(?i)\b(?:refactor|refactoring|restructure|reorganize|deduplicate|behavior-preserving|preserve\s+existing\s+behavior)\b"),
    re.compile(r"(?i)\b(?:extract\s+(?:a\s+)?(?:helper|function|method|class)|rename\s+(?:the\s+)?(?:helper|function|method|class|symbol|variable))\b"),
)

_CONFIG_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*(?:config(?:\([^)]+\))?:|chore\(config\):)"),
    re.compile(
        r"(?i)\b(?:update|configure|edit|add|set)\s+(?:the\s+)?"
        r"(?:\.flake8|\.gitignore|\.editorconfig|\.python-version|pytest\.ini|ruff\.toml|\.ruff\.toml|mypy\.ini|\.mypy\.ini|\.coveragerc)\b"
    ),
)

_BUG_SIGNAL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(?:bug|fix|error|exception|traceback|crash|fails?|failing|broken|incorrect|wrong|regression|nameerror|typeerror|valueerror|keyerror|importerror|attributeerror|indexerror|zerodivisionerror|timeout|unhandled|missing\s+import|unused\s+import)\b"),
)


def classify_task_type(
    issue_text: str,
    profile: Any = None,
    originals: dict[str, str] | None = None,
    plan: dict[str, Any] | None = None,
) -> tuple[TaskType, TaskType, tuple[str, ...]]:
    """Deterministically classify task type and apply monotonic LLM caution floor."""
    signals: list[str] = []
    text = (issue_text or "").strip()
    norm = text.replace("\\", "/")
    orig_map = originals or {}

    plan_files: list[str] = []
    if isinstance(plan, dict):
        for key in ("expected_files", "expectedFiles", "likely_files", "likelyFiles", "filesToInspect", "files"):
            val = plan.get(key)
            if isinstance(val, list):
                plan_files.extend(re.sub(r"^(?:\./)+", "", str(item).replace("\\", "/").strip()) for item in val if item)

    # Check if issue or plan targets only config profile files
    mentioned_config_profiles = [
        name for name in _CONFIG_PROFILE_NAMES
        if re.search(rf"(?i)(?:^|[\s`'\"/]){re.escape(name)}(?:$|[\s`'\",.:;)])", norm)
    ]
    plan_only_config = bool(plan_files) and all(
        PurePosixPath(p).name.casefold() in _CONFIG_PROFILE_NAMES for p in plan_files
    )
    mentions_py_file = bool(re.search(r"\b[\w./-]+\.py\b", norm)) or any(p.endswith(".py") for p in plan_files)

    has_bug_signal = any(p.search(norm) for p in _BUG_SIGNAL_PATTERNS)
    is_missing_import_fix = bool(re.search(r"(?i)\b(?:add|fix)\s+(?:missing\s+)?[\w.]+\s+import\b|\b(?:add|fix)\s+import\s+[\w.]+\b", norm))

    if any(p.search(norm) for p in _UNCLEAR_EXPLICIT_PATTERNS):
        candidate = TaskType.UNCLEAR
        signals.append("task_type_deterministic:unclear_explicit")
    elif not norm and not orig_map and not plan_files:
        candidate = TaskType.UNCLEAR
        signals.append("task_type_deterministic:empty_issue")
    elif (
        (any(p.search(norm) for p in _CONFIG_PATTERNS) or plan_only_config or (mentioned_config_profiles and not mentions_py_file))
        and not is_missing_import_fix
    ):
        candidate = TaskType.CONFIG
        signals.append("task_type_deterministic:config")
    elif any(p.search(norm) for p in _FEATURE_PATTERNS) and not is_missing_import_fix:
        candidate = TaskType.FEATURE
        signals.append("task_type_deterministic:feature")
    elif any(p.search(norm) for p in _REFACTOR_PATTERNS) and not is_missing_import_fix:
        candidate = TaskType.REFACTOR
        signals.append("task_type_deterministic:refactor")
    elif norm and len(norm.split()) <= 1 and not mentions_py_file and not has_bug_signal and not plan_files:
        candidate = TaskType.UNCLEAR
        signals.append("task_type_deterministic:under_specified")
    else:
        candidate = TaskType.BUG
        signals.append("task_type_deterministic:bug")

    llm_proposed: str | TaskType | None = None
    if isinstance(plan, dict):
        if plan.get("needs_more_information") is True or plan.get("needsMoreInformation") is True:
            llm_proposed = TaskType.UNCLEAR
        else:
            raw_type = plan.get("task_type") or plan.get("taskType")
            if isinstance(raw_type, (str, TaskType)):
                llm_proposed = raw_type

    floor = refine_task_type(candidate, llm_proposed)
    if llm_proposed is not None:
        try:
            parsed_llm = llm_proposed if isinstance(llm_proposed, TaskType) else TaskType(str(llm_proposed).strip().lower())
        except ValueError:
            parsed_llm = None
        if parsed_llm is not None:
            if floor != candidate:
                signals.append(f"llm_task_type_refined:{candidate.value}->{floor.value}")
            elif parsed_llm != candidate:
                signals.append(f"llm_task_type_downgrade_ignored:{parsed_llm.value}->{candidate.value}")

    return candidate, floor, tuple(dict.fromkeys(signals))


@dataclass(frozen=True)
class ModuleAnalysis:
    path: str
    syntax_valid: bool
    undefined_names: tuple[str, ...] = ()
    unused_imports: tuple[str, ...] = ()
    imported_bindings: dict[str, str] = None  # bound_name -> root_module
    defined_names: tuple[str, ...] = ()
    exported_names: tuple[str, ...] = ()
    has_dynamic_scope: bool = False
    risky_constructs: tuple[str, ...] = ()
    non_import_ast_dump: str = ""

    def __post_init__(self):
        if self.imported_bindings is None:
            object.__setattr__(self, "imported_bindings", {})


def is_override_path(path: str) -> tuple[str, ...]:
    reasons: list[str] = []
    norm = path.replace("\\", "/").lstrip("./")
    p = PurePosixPath(norm)
    parts_lower = [part.casefold() for part in p.parts]
    name_lower = p.name.casefold()
    stem_lower = p.stem.casefold()

    if (
        any(part.startswith((".git", ".github", ".circleci", ".env", "ci", "docker")) for part in parts_lower)
        or name_lower in {"pyproject.toml", "setup.py", "setup.cfg", "dockerfile", "makefile", "jenkinsfile", "pipfile", "poetry.lock", "uv.lock", "pdm.lock"}
        or p.suffix.casefold() in {".lock", ".yml", ".yaml", ".toml", ".cfg", ".ini", ".sh"}
        or name_lower.startswith("requirements")
    ):
        reasons.append("ci_docker_dependency_or_config_path")

    if any("migration" in part or "alembic" in part for part in parts_lower):
        reasons.append("migration_path")

    if name_lower == "__init__.py":
        reasons.append("public_api_or_init_export")

    if (
        name_lower == "conftest.py"
        or name_lower.startswith("test_")
        or name_lower.endswith("_test.py")
        or any(part in {"tests", "test"} for part in parts_lower[:-1])
    ):
        reasons.append("test_or_conftest_edit")

    risky_tokens = {"auth", "security", "crypto", "crypt", "permission", "permissions", "oauth", "jwt", "secret", "secrets", "sql", "database", "serialize", "serializer", "pickle"}
    if any(tok in stem_lower.split("_") or tok in parts_lower for tok in risky_tokens):
        reasons.append("risky_domain_path")

    return tuple(dict.fromkeys(reasons))


def _collect_target_bindings(target: ast.AST, out: set[str]) -> None:
    if isinstance(target, ast.Name):
        out.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target_bindings(elt, out)
    elif isinstance(target, ast.Starred):
        _collect_target_bindings(target.value, out)


def _collect_pattern_bindings(pattern: ast.AST, out: set[str]) -> None:
    for node in ast.walk(pattern):
        if isinstance(node, ast.MatchAs) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchStar) and node.name:
            out.add(node.name)
        elif isinstance(node, ast.MatchMapping) and node.rest:
            out.add(node.rest)


def _collect_scope_bindings(nodes: list[ast.AST], is_module: bool = False) -> tuple[set[str], bool]:
    bound: set[str] = set()
    has_star = False

    def visit_stmt_list(stmts: list[ast.AST]) -> None:
        nonlocal has_star
        for stmt in stmts:
            visit_stmt(stmt)

    def visit_stmt(stmt: ast.AST) -> None:
        nonlocal has_star
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound.add(stmt.name)
            for tp in getattr(stmt, "type_params", ()):
                if hasattr(tp, "name"):
                    bound.add(tp.name)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.name == "*":
                    has_star = True
                else:
                    bound.add(alias.asname or alias.name)
        elif isinstance(stmt, ast.Assign):
            for t in stmt.targets:
                _collect_target_bindings(t, bound)
            visit_expr_for_named(stmt.value)
        elif isinstance(stmt, ast.AnnAssign):
            _collect_target_bindings(stmt.target, bound)
            if stmt.value:
                visit_expr_for_named(stmt.value)
        elif isinstance(stmt, ast.AugAssign):
            _collect_target_bindings(stmt.target, bound)
            visit_expr_for_named(stmt.value)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            _collect_target_bindings(stmt.target, bound)
            visit_expr_for_named(stmt.iter)
            visit_stmt_list(stmt.body)
            visit_stmt_list(stmt.orelse)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                visit_expr_for_named(item.context_expr)
                if item.optional_vars:
                    _collect_target_bindings(item.optional_vars, bound)
            visit_stmt_list(stmt.body)
        elif isinstance(stmt, ast.Try) or ( hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar) ):
            visit_stmt_list(stmt.body)
            for handler in stmt.handlers:
                if handler.name:
                    bound.add(handler.name)
                visit_stmt_list(handler.body)
            visit_stmt_list(stmt.orelse)
            visit_stmt_list(stmt.finalbody)
        elif isinstance(stmt, ast.If):
            visit_expr_for_named(stmt.test)
            visit_stmt_list(stmt.body)
            visit_stmt_list(stmt.orelse)
        elif isinstance(stmt, ast.While):
            visit_expr_for_named(stmt.test)
            visit_stmt_list(stmt.body)
            visit_stmt_list(stmt.orelse)
        elif isinstance(stmt, ast.Match):
            visit_expr_for_named(stmt.subject)
            for case in stmt.cases:
                _collect_pattern_bindings(case.pattern, bound)
                if case.guard:
                    visit_expr_for_named(case.guard)
                visit_stmt_list(case.body)
        elif isinstance(stmt, (ast.Global, ast.Nonlocal)):
            bound.update(stmt.names)
        elif hasattr(ast, "TypeAlias") and isinstance(stmt, ast.TypeAlias):
            if isinstance(stmt.name, ast.Name):
                bound.add(stmt.name.id)
        else:
            for child in ast.iter_child_nodes(stmt):
                if isinstance(child, ast.expr):
                    visit_expr_for_named(child)

    def visit_expr_for_named(expr: ast.AST) -> None:
        if isinstance(expr, (ast.Lambda, ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            return
        if isinstance(expr, ast.NamedExpr):
            _collect_target_bindings(expr.target, bound)
        for child in ast.iter_child_nodes(expr):
            if isinstance(child, ast.expr):
                visit_expr_for_named(child)

    visit_stmt_list(nodes)
    return bound, has_star


def _detect_risky_constructs(source: str, tree: ast.Module) -> tuple[str, ...]:
    risks: list[str] = []
    if any(p.search(source) for p in _SECRETS_CONTENT_PATTERNS):
        risks.append("secrets_like_content")

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for m in mods:
                root_mod = m.split(".")[0].casefold()
                if root_mod in {"jwt", "bcrypt", "argon2", "cryptography", "passlib", "oauthlib", "itsdangerous"}:
                    risks.append("auth_security_crypto")
                elif root_mod in {"pickle", "cpickle", "dill", "shelve", "marshal", "jsonpickle"}:
                    risks.append("serialization")
                elif root_mod in {"subprocess", "pty", "paramiko", "fabric"}:
                    risks.append("subprocess_eval")
                elif root_mod in {"sqlite3", "sqlalchemy", "psycopg", "psycopg2", "asyncpg", "pymysql"}:
                    risks.append("sql_execution")
        elif isinstance(node, ast.Call):
            func_str = ""
            try:
                func_str = ast.unparse(node.func)
            except Exception:
                pass
            fl = func_str.casefold()
            if fl in {"eval", "exec", "compile", "__import__", "os.system", "os.popen", "os.execv", "os.execvp", "os.spawnv"} or fl.startswith("subprocess."):
                risks.append("subprocess_eval")
            elif any(fl.startswith(p) for p in ("pickle.", "marshal.", "shelve.", "dill.")) or fl in {"yaml.load", "yaml.unsafe_load", "loads"}:
                if "yaml" in fl or "pickle" in fl or "marshal" in fl or "dill" in fl:
                    risks.append("serialization")
            elif fl.endswith((".execute", ".executemany", ".executescript", ".raw")) or any(
                isinstance(arg, ast.Constant)
                and isinstance(arg.value, str)
                and bool(re.search(r"(?i)\b(?:SELECT\s+.+\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE)\b", arg.value))
                for arg in node.args
            ):
                risks.append("sql_execution")
            elif any(tok in fl for tok in ("pbkdf2", "scrypt", "hmac.", "check_password", "verify_password", "verify_token", "authenticate", "authorize")):
                risks.append("auth_security_crypto")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn_lower = node.name.casefold()
            if any(tok in fn_lower for tok in ("authenticate", "authorize", "verify_token", "check_password", "check_permission", "encrypt", "decrypt")):
                risks.append("auth_security_crypto")

    return tuple(dict.fromkeys(risks))


def analyze_python_source(source: str, path: str = "<module>") -> ModuleAnalysis:
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return ModuleAnalysis(path=path, syntax_valid=False)

    module_bound, has_star = _collect_scope_bindings(tree.body, is_module=True)
    has_dynamic = has_star

    imported_bindings: dict[str, str] = {}
    defined_names: list[str] = []
    explicit_all: list[str] | None = None

    for stmt in tree.body:
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                bound_name = alias.asname or alias.name.split(".")[0]
                imported_bindings[bound_name] = alias.name.split(".")[0]
        elif isinstance(stmt, ast.ImportFrom):
            mod_root = (stmt.module or "").split(".")[0]
            for alias in stmt.names:
                if alias.name != "*":
                    bound_name = alias.asname or alias.name
                    imported_bindings[bound_name] = mod_root or ("." * stmt.level)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined_names.append(stmt.name)
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                if isinstance(target, ast.Name):
                    defined_names.append(target.id)
                    if target.id == "__all__" and isinstance(stmt.value, (ast.List, ast.Tuple, ast.Set)):
                        explicit_all = [
                            elt.value for elt in stmt.value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        ]

    undefined: list[str] = []
    loaded_names: set[str] = set()

    def walk_node(node: ast.AST, enclosing_scopes: tuple[set[str], ...]) -> None:
        nonlocal has_dynamic
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in {"globals", "locals", "eval", "exec", "vars"}:
            has_dynamic = True

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                walk_node(dec, enclosing_scopes)
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                walk_node(default, enclosing_scopes)
            if node.returns:
                walk_node(node.returns, enclosing_scopes)
            fn_bound, fn_star = _collect_scope_bindings(node.body)
            if fn_star:
                has_dynamic = True
            for arg in node.args.posonlyargs + node.args.args + node.args.kwonlyargs:
                fn_bound.add(arg.arg)
                if arg.annotation:
                    walk_node(arg.annotation, enclosing_scopes)
            if node.args.vararg:
                fn_bound.add(node.args.vararg.arg)
                if node.args.vararg.annotation:
                    walk_node(node.args.vararg.annotation, enclosing_scopes)
            if node.args.kwarg:
                fn_bound.add(node.args.kwarg.arg)
                if node.args.kwarg.annotation:
                    walk_node(node.args.kwarg.annotation, enclosing_scopes)
            for tp in getattr(node, "type_params", ()):
                if hasattr(tp, "name"):
                    fn_bound.add(tp.name)
            new_scopes = enclosing_scopes + (fn_bound,)
            for stmt in node.body:
                walk_node(stmt, new_scopes)
            return

        if isinstance(node, ast.ClassDef):
            for dec in node.decorator_list:
                walk_node(dec, enclosing_scopes)
            for base in node.bases:
                walk_node(base, enclosing_scopes)
            for kw in node.keywords:
                walk_node(kw.value, enclosing_scopes)
            cls_bound, cls_star = _collect_scope_bindings(node.body)
            if cls_star:
                has_dynamic = True
            for tp in getattr(node, "type_params", ()):
                if hasattr(tp, "name"):
                    cls_bound.add(tp.name)
            # Class bodies can resolve class-local bindings in annotations/statements,
            # though methods inside the class do not inherit class scope as enclosing lexical scope.
            for stmt in node.body:
                if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    walk_node(stmt, enclosing_scopes)
                else:
                    walk_node(stmt, enclosing_scopes + (cls_bound,))
            return

        if isinstance(node, ast.Lambda):
            for default in node.args.defaults + [d for d in node.args.kw_defaults if d is not None]:
                walk_node(default, enclosing_scopes)
            lam_bound: set[str] = {
                arg.arg for arg in (node.args.posonlyargs + node.args.args + node.args.kwonlyargs)
            }
            if node.args.vararg:
                lam_bound.add(node.args.vararg.arg)
            if node.args.kwarg:
                lam_bound.add(node.args.kwarg.arg)
            walk_node(node.body, enclosing_scopes + (lam_bound,))
            return

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            comp_bound: set[str] = set()
            for gen in node.generators:
                walk_node(gen.iter, enclosing_scopes + (comp_bound,))
                _collect_target_bindings(gen.target, comp_bound)
                for if_clause in gen.ifs:
                    walk_node(if_clause, enclosing_scopes + (comp_bound,))
            if isinstance(node, ast.DictComp):
                walk_node(node.key, enclosing_scopes + (comp_bound,))
                walk_node(node.value, enclosing_scopes + (comp_bound,))
            else:
                walk_node(node.elt, enclosing_scopes + (comp_bound,))
            return

        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Load, ast.Del)):
            name = node.id
            loaded_names.add(name)
            if (
                name not in BUILTIN_NAMES
                and name not in module_bound
                and not any(name in scope for scope in enclosing_scopes)
            ):
                if name not in undefined:
                    undefined.append(name)
            return

        for child in ast.iter_child_nodes(node):
            walk_node(child, enclosing_scopes)

    for stmt in tree.body:
        walk_node(stmt, ())

    export_set = set(explicit_all) if explicit_all is not None else {
        n for n in defined_names if not n.startswith("_")
    }
    unused_imports: list[str] = []
    is_init = PurePosixPath(path.replace("\\", "/")).name == "__init__.py"
    if not is_init and not has_dynamic:
        for bound_name, mod_root in imported_bindings.items():
            if bound_name == "__future__":
                continue
            if bound_name not in loaded_names and (explicit_all is None or bound_name not in explicit_all):
                unused_imports.append(bound_name)

    non_import_body = [
        stmt for stmt in tree.body
        if not isinstance(stmt, (ast.Import, ast.ImportFrom))
    ]
    non_import_mod = ast.Module(body=non_import_body, type_ignores=[])
    non_import_dump = ast.dump(non_import_mod, include_attributes=False)

    risks = _detect_risky_constructs(source, tree)

    return ModuleAnalysis(
        path=path,
        syntax_valid=True,
        undefined_names=tuple(undefined),
        unused_imports=tuple(unused_imports),
        imported_bindings=imported_bindings,
        defined_names=tuple(defined_names),
        exported_names=tuple(sorted(export_set)),
        has_dynamic_scope=has_dynamic,
        risky_constructs=risks,
        non_import_ast_dump=non_import_dump,
    )


def triage_repository(
    profile: Any,
    originals: dict[str, str],
    issue_text: str = "",
    plan: dict[str, Any] | None = None,
    declared_deps: set[str] | None = None,
) -> TriageReport:
    """Deterministic pre-patch triage immediately after profile_repository."""
    signals: list[str] = []
    override_reasons: list[str] = []
    risk_floor = RiskFloor.LOW
    declared = {d.casefold().replace("-", "_") for d in (declared_deps or set())}

    issue_norm = (issue_text or "").replace("\\", "/")
    plan_files: list[str] = []
    if isinstance(plan, dict):
        for key in ("likely_files", "likelyFiles", "filesToInspect", "files"):
            val = plan.get(key)
            if isinstance(val, list):
                plan_files.extend(str(item).replace("\\", "/").lstrip("./") for item in val if item)

    # 1. Adversarial or risky signals in issue text (can only raise risk, NEVER lower it)
    if any(p.search(issue_norm) for p in _ADVERSARIAL_PATTERNS):
        risk_floor = max_risk(risk_floor, RiskFloor.HIGH)
        override_reasons.append("adversarial_or_unsafe_issue_claim")
        signals.append("adversarial_issue_claim")

    if re.search(r"(?i)\b(?:trivial|simple|easy|one-line|two-line|quick\s+fix)\b", issue_norm):
        signals.append("untrusted_trivial_claim_ignored")

    for reason_code, pattern in _RISKY_ISSUE_PATTERNS:
        if pattern.search(issue_norm):
            risk_floor = max_risk(risk_floor, RiskFloor.HIGH)
            override_reasons.append(reason_code)

    if getattr(profile, "framework", "none") == "streamlit":
        risk_floor = max_risk(risk_floor, RiskFloor.MEDIUM)
        signals.append("streamlit_ui_gates_required")

    # 2. Analyze Python source files statically
    undefined_by_file: dict[str, tuple[str, ...]] = {}
    unused_by_file: dict[str, tuple[str, ...]] = {}
    analyses: dict[str, ModuleAnalysis] = {}

    for name in sorted(originals):
        if not name.endswith(".py"):
            continue
        analysis = analyze_python_source(originals[name], name)
        analyses[name] = analysis
        if analysis.undefined_names and not analysis.has_dynamic_scope:
            undefined_by_file[name] = analysis.undefined_names
        if analysis.unused_imports and not analysis.has_dynamic_scope:
            unused_by_file[name] = analysis.unused_imports

    # 3. Localization
    mentioned_files: list[str] = []
    for name in sorted(originals):
        base = PurePosixPath(name).name
        if name in plan_files or (issue_norm and (name in issue_norm or (len(base) > 3 and base in issue_norm))):
            mentioned_files.append(name)

    witness_files = sorted(set(undefined_by_file) | set(unused_by_file))
    if mentioned_files:
        localized = tuple(dict.fromkeys(mentioned_files))
    elif len(witness_files) == 1:
        localized = (witness_files[0],)
    elif len([f for f in originals if f.endswith(".py") and not is_override_path(f)]) == 1:
        only_py = [f for f in originals if f.endswith(".py") and not is_override_path(f)][0]
        localized = (only_py,)
    else:
        localized = tuple(witness_files[:3])

    uncertain = len(localized) == 0 or len(localized) > 2
    if uncertain:
        risk_floor = max_risk(risk_floor, RiskFloor.MEDIUM)
        signals.append("uncertain_localization")

    # 4. Check localized files for path overrides or risky AST constructs
    eligible_witness = False
    for loc in localized:
        path_overrides = is_override_path(loc)
        if path_overrides:
            override_reasons.extend(path_overrides)
            risk_floor = max_risk(risk_floor, RiskFloor.HIGH)
        mod_analysis = analyses.get(loc)
        if mod_analysis is None:
            override_reasons.append("non_python_target")
            risk_floor = max_risk(risk_floor, RiskFloor.HIGH)
            continue
        if not mod_analysis.syntax_valid or mod_analysis.has_dynamic_scope:
            override_reasons.append("dynamic_or_invalid_syntax")
            risk_floor = max_risk(risk_floor, RiskFloor.MEDIUM)
        if mod_analysis.risky_constructs:
            override_reasons.extend(mod_analysis.risky_constructs)
            risk_floor = max_risk(risk_floor, RiskFloor.HIGH)

        for undef in mod_analysis.undefined_names:
            owner = STDLIB_SYMBOL_OWNERS.get(undef, undef)
            if owner in STDLIB_MODULES or owner.casefold().replace("-", "_") in declared:
                eligible_witness = True
                signals.append(f"undefined_witness:{loc}:{undef}")
        for unused in mod_analysis.unused_imports:
            mod_root = mod_analysis.imported_bindings.get(unused, "")
            if mod_root in PURE_STDLIB_MODULES:
                signals.append(f"unused_pure_import:{loc}:{unused}")
                if re.search(r"(?i)\b(?:unused\s+import|remove\s+unused)\b", issue_norm):
                    eligible_witness = True

    if not eligible_witness:
        signals.append("no_prepatch_static_oracle_witness")
        risk_floor = max_risk(risk_floor, RiskFloor.MEDIUM)

    # 5. Deterministic task-type classification + monotonic LLM caution floor
    task_type_candidate, task_type_floor, type_signals = classify_task_type(
        issue_text=issue_text,
        profile=profile,
        originals=originals,
        plan=plan,
    )
    signals.extend(type_signals)
    if task_type_candidate != TaskType.BUG or task_type_floor != TaskType.BUG:
        override_reasons.append(f"non_bug_task_type:{task_type_floor.value}")
        risk_floor = max_risk(
            risk_floor,
            RiskFloor.HIGH if task_type_floor == TaskType.UNCLEAR else RiskFloor.MEDIUM,
        )

    if isinstance(plan, dict) and plan.get("dependency_changes") or (isinstance(plan, dict) and plan.get("dependencyChanges")):
        override_reasons.append("new_third_party_dependency")
        risk_floor = max_risk(risk_floor, RiskFloor.HIGH)

    dedup_overrides = tuple(dict.fromkeys(override_reasons))
    dedup_signals = tuple(dict.fromkeys(signals))

    candidate_lane = (
        LaneName.FAST
        if (
            task_type_candidate == TaskType.BUG
            and task_type_floor == TaskType.BUG
            and risk_floor == RiskFloor.LOW
            and not dedup_overrides
            and not uncertain
            and eligible_witness
        )
        else LaneName.FULL
    )

    return TriageReport(
        candidate_lane=candidate_lane,
        risk_floor=risk_floor,
        localized_files=localized,
        undefined_names_by_file=undefined_by_file,
        unused_imports_by_file=unused_by_file,
        signals=dedup_signals,
        override_reasons=dedup_overrides,
        uncertain_localization=uncertain,
        task_type_candidate=task_type_candidate,
        task_type_floor=task_type_floor,
    )
