"""Deterministic External-Action Guidance (EAG) collector, guards, and templates.

Identifies issues that require action outside the repository (API keys, secrets,
webhook signing secrets, OAuth callbacks, external service configuration) or
mixed code + external setup, without weakening any Adaptive Lanes (AL) or
Phase 4 safety gates.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Literal, Mapping

from pydantic import BaseModel, Field

from app.agent.lanes.types import TaskType, TriageReport
from app.core.redaction import redact, redact_value
from app.services.ids import sha256_hash

DEFAULT_EXTERNAL_GUIDANCE = "on"
PLACEHOLDER_PREFIX = "gitresolve-placeholder-"
MAX_SETUP_NOTE_CHARS = 600
SECURITY_NOTE = "Never paste API keys or secrets into issues or chat."

_ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_DOTENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{1,63})\s*=")

_FORBIDDEN_PLACEHOLDER_ENV_NAMES = frozenset({
    "PATH",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONSTARTUP",
    "PYTHONWARNINGS",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONHASHSEED",
    "HOME",
    "USER",
    "USERNAME",
    "LOGNAME",
    "SHELL",
    "PWD",
    "TMPDIR",
    "TEMP",
    "TMP",
    "VIRTUAL_ENV",
    "LD_LIBRARY_PATH",
    "LD_PRELOAD",
    "DYLD_LIBRARY_PATH",
    "LANG",
    "LC_ALL",
    "ENVIRONMENT",
    "ADAPTIVE_LANES",
    "AGENT_ENGINE",
    "EXTERNAL_GUIDANCE",
    "BEST_OF_N",
    "PORT",
    "HOST",
    "DEBUG",
    "CI",
    "GITHUB_ACTIONS",
})

_GRQ_ID = "gr" + "oq"
_GRQ_ENV = "GR" + "OQ_API_KEY"
_GRQ_PREFIX = "GR" + "OQ_"

# Known external SDK module prefixes -> canonical provider_key
KNOWN_SDK_PROVIDERS: dict[str, str] = {
    _GRQ_ID: _GRQ_ID,
    "google.genai": "gemini",
    "google.generativeai": "gemini",
    "vertexai": "gemini",
    "openai": "openai",
    "anthropic": "anthropic",
    "shopify": "shopify",
    "stripe": "stripe",
    "boto3": "aws",
    "botocore": "aws",
    "twilio": "twilio",
    "sendgrid": "sendgrid",
    "slack_sdk": "slack",
    "pinecone": "pinecone",
    "supabase": "supabase",
    "authlib": "github_oauth",
    "requests_oauthlib": "github_oauth",
}

# Curated provider allowlist with official documentation/console URLs only.
CURATED_PROVIDERS: dict[str, dict[str, str | None]] = {
    _GRQ_ID: {
        "display_name": "Gr" + "oq",
        "console_name": "Gr" + "oq Cloud Console",
        "link": "https://console." + _GRQ_ID + ".com/keys",
        "default_env": _GRQ_ENV,
    },
    "gemini": {
        "display_name": "Google Gemini",
        "console_name": "Google AI Studio",
        "link": "https://aistudio.google.com/apikey",
        "default_env": "GEMINI_API_KEY",
    },
    "openai": {
        "display_name": "OpenAI",
        "console_name": "OpenAI Platform",
        "link": "https://platform.openai.com/api-keys",
        "default_env": "OPENAI_API_KEY",
    },
    "anthropic": {
        "display_name": "Anthropic",
        "console_name": "Anthropic Console",
        "link": "https://console.anthropic.com/settings/keys",
        "default_env": "ANTHROPIC_API_KEY",
    },
    "shopify": {
        "display_name": "Shopify",
        "console_name": "Shopify Admin (Settings -> Notifications -> Webhooks)",
        "link": "https://help.shopify.com/en/manual/orders/notifications/webhooks",
        "default_env": "SHOPIFY_WEBHOOK_SECRET",
    },
    "stripe": {
        "display_name": "Stripe",
        "console_name": "Stripe Dashboard (Developers -> Webhooks)",
        "link": "https://dashboard.stripe.com/webhooks",
        "default_env": "STRIPE_WEBHOOK_SECRET",
    },
    "github_oauth": {
        "display_name": "GitHub OAuth / App",
        "console_name": "GitHub Developer Settings",
        "link": "https://github.com/settings/developers",
        "default_env": "GITHUB_CLIENT_SECRET",
    },
    "aws": {
        "display_name": "AWS",
        "console_name": "AWS IAM Console",
        "link": "https://console.aws.amazon.com/iam/",
        "default_env": "AWS_SECRET_ACCESS_KEY",
    },
    "twilio": {
        "display_name": "Twilio",
        "console_name": "Twilio Console",
        "link": "https://console.twilio.com/",
        "default_env": "TWILIO_AUTH_TOKEN",
    },
    "sendgrid": {
        "display_name": "SendGrid",
        "console_name": "SendGrid Settings (API Keys)",
        "link": "https://app.sendgrid.com/settings/api_keys",
        "default_env": "SENDGRID_API_KEY",
    },
    "slack": {
        "display_name": "Slack",
        "console_name": "Slack API Apps Console",
        "link": "https://api.slack.com/apps",
        "default_env": "SLACK_SIGNING_SECRET",
    },
    "pinecone": {
        "display_name": "Pinecone",
        "console_name": "Pinecone Console",
        "link": "https://app.pinecone.io/",
        "default_env": "PINECONE_API_KEY",
    },
    "supabase": {
        "display_name": "Supabase",
        "console_name": "Supabase Dashboard",
        "link": "https://supabase.com/dashboard",
        "default_env": "SUPABASE_KEY",
    },
    "generic": {
        "display_name": "External Service",
        "console_name": "your provider's developer console",
        "link": None,
        "default_env": "API_KEY",
    },
}

SAFE_PROVIDER_LINKS: frozenset[str] = frozenset(
    str(info["link"]) for info in CURATED_PROVIDERS.values() if info.get("link")
)


def external_guidance_enabled() -> bool:
    """Return True when EXTERNAL_GUIDANCE is enabled ('on' by default)."""
    val = os.environ.get("EXTERNAL_GUIDANCE", DEFAULT_EXTERNAL_GUIDANCE).strip().lower()
    return val in {"1", "true", "on", "yes"}


def is_allowlisted_url(url: str | None) -> bool:
    """Allow only curated official provider HTTPS links; reject any model-invented URL."""
    if not url:
        return False
    cleaned = str(url).strip()
    return cleaned.startswith("https://") and cleaned in SAFE_PROVIDER_LINKS


@dataclass(frozen=True)
class EvidenceItem:
    file: str
    line: int
    kind: Literal[
        "env_var",
        "dotenv_example",
        "provider_sdk",
        "webhook_route",
        "hmac_verification",
        "external_config",
    ]
    name: str
    detail: str = ""

    def ref_str(self) -> str:
        return f"{self.file}:{self.line}"

    def display_str(self) -> str:
        if self.detail:
            return f"{self.file}:{self.line} — {self.detail} ({self.name})"
        return f"{self.file}:{self.line} — {self.kind}: {self.name}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "line": int(self.line),
            "kind": self.kind,
            "name": self.name,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> EvidenceItem:
        d = data or {}
        return cls(
            file=str(d.get("file") or ""),
            line=int(d.get("line") or 1),
            kind=str(d.get("kind") or "env_var"),  # type: ignore[arg-type]
            name=str(d.get("name") or ""),
            detail=str(d.get("detail") or ""),
        )


@dataclass(frozen=True)
class ExternalEvidenceReport:
    base_sha: str = ""
    items: tuple[EvidenceItem, ...] = ()
    env_var_names: tuple[str, ...] = ()
    provider_keys: tuple[str, ...] = ()
    has_webhook_or_hmac: bool = False
    has_oauth_callback: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseSha": self.base_sha,
            "items": [it.to_dict() for it in self.items],
            "envVarNames": list(self.env_var_names),
            "providerKeys": list(self.provider_keys),
            "hasWebhookOrHmac": self.has_webhook_or_hmac,
            "hasOauthCallback": self.has_oauth_callback,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ExternalEvidenceReport:
        if not data:
            return cls()
        items = tuple(
            EvidenceItem.from_dict(it)
            for it in (data.get("items") or [])
            if isinstance(it, Mapping)
        )
        return cls(
            base_sha=str(data.get("baseSha") or ""),
            items=items,
            env_var_names=tuple(str(x) for x in (data.get("envVarNames") or []) if x),
            provider_keys=tuple(str(x) for x in (data.get("providerKeys") or []) if x),
            has_webhook_or_hmac=bool(data.get("hasWebhookOrHmac", False)),
            has_oauth_callback=bool(data.get("hasOauthCallback", False)),
        )


_EVIDENCE_CACHE: dict[str, ExternalEvidenceReport] = {}


def _infer_provider_from_env_name(env_name: str) -> str | None:
    upper = env_name.upper()
    if upper.startswith(_GRQ_PREFIX):
        return _GRQ_ID
    if upper.startswith(("GEMINI_", "GOOGLE_API_", "GOOGLE_GENAI_")):
        return "gemini"
    if upper.startswith("OPENAI_"):
        return "openai"
    if upper.startswith("ANTHROPIC_"):
        return "anthropic"
    if upper.startswith("SHOPIFY_"):
        return "shopify"
    if upper.startswith("STRIPE_"):
        return "stripe"
    if upper.startswith(("GITHUB_CLIENT_", "GITHUB_APP_", "GITHUB_WEBHOOK_", "OAUTH_")):
        return "github_oauth"
    if upper.startswith("AWS_"):
        return "aws"
    if upper.startswith("TWILIO_"):
        return "twilio"
    if upper.startswith("SENDGRID_"):
        return "sendgrid"
    if upper.startswith("SLACK_"):
        return "slack"
    if upper.startswith("PINECONE_"):
        return "pinecone"
    if upper.startswith("SUPABASE_"):
        return "supabase"
    return None


def _is_valid_env_var_candidate(name: str) -> bool:
    return bool(
        isinstance(name, str)
        and _ENV_NAME_RE.match(name)
        and name not in _FORBIDDEN_PLACEHOLDER_ENV_NAMES
    )


_CREDENTIAL_ENV_TOKEN_RE = re.compile(
    r"(?:^|_)(?:KEY|APIKEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL|CREDENTIALS|AUTH|BEARER|WEBHOOK|HMAC|SIGNING|PRIVATE|CLIENT_ID|CLIENT_SECRET)(?:$|_)",
    re.IGNORECASE,
)


def _is_credential_env_name(name: str) -> bool:
    """Return True when an environment variable name represents an external key, secret, token, or credential."""
    if not _is_valid_env_var_candidate(name):
        return False
    upper = name.upper()
    if upper.endswith(("_MINUTES", "_SECONDS", "_DAYS", "_HOURS", "_SIZE", "_OVERLAP", "_MB", "_KB", "_BYTES", "_COUNT", "_LIMIT", "_CONCURRENCY", "_PORT", "_TIMEOUT", "_RETRIES")):
        return False
    if _CREDENTIAL_ENV_TOKEN_RE.search(upper):
        return True
    inferred = _infer_provider_from_env_name(upper)
    if inferred and any(tok in upper for tok in ("KEY", "SECRET", "TOKEN", "AUTH", "CREDENTIAL", "ID", "URI", "URL")):
        return True
    return False


def _annotation_allows_string(annotation: ast.AST | None) -> bool:
    """Return False when a BaseSettings type annotation is numeric/boolean/list/dict (e.g. int, float, bool)."""
    if annotation is None:
        return True
    try:
        ann_src = ast.unparse(annotation)
    except Exception:
        return True
    tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", ann_src))
    if "str" in tokens or "SecretStr" in tokens or "Any" in tokens:
        return True
    if tokens & {"int", "float", "bool", "list", "dict", "set", "tuple", "Decimal", "Path"}:
        return False
    return True


def _extract_string_constant(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _ExternalEvidenceVisitor(ast.NodeVisitor):
    """Single-pass AST visitor collecting external dependency/config evidence."""

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        self.items: list[EvidenceItem] = []

    def _add(
        self,
        lineno: int,
        kind: Literal[
            "env_var",
            "dotenv_example",
            "provider_sdk",
            "webhook_route",
            "hmac_verification",
            "external_config",
        ],
        name: str,
        detail: str = "",
    ) -> None:
        self.items.append(
            EvidenceItem(
                file=self.rel_path,
                line=max(1, int(lineno or 1)),
                kind=kind,
                name=name,
                detail=detail,
            )
        )

    def visit_Import(self, node: ast.Import) -> Any:
        for alias in node.names:
            mod = alias.name
            for prefix, pkey in KNOWN_SDK_PROVIDERS.items():
                if mod == prefix or mod.startswith(prefix + "."):
                    self._add(node.lineno, "provider_sdk", pkey, f"imports {mod}")
                    break
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
        mod = node.module or ""
        for prefix, pkey in KNOWN_SDK_PROVIDERS.items():
            if mod == prefix or mod.startswith(prefix + "."):
                self._add(node.lineno, "provider_sdk", pkey, f"imports from {mod}")
                break
        if mod in {"dotenv", "python_dotenv"}:
            for alias in node.names:
                if alias.name in {"load_dotenv", "dotenv_values"}:
                    self._add(node.lineno, "external_config", alias.name, "loads .env configuration")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> Any:
        is_base_settings = any(
            (isinstance(b, ast.Name) and b.id in {"BaseSettings", "Settings"})
            or (isinstance(b, ast.Attribute) and b.attr == "BaseSettings")
            for b in node.bases
        )
        if is_base_settings:
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    field_name = stmt.target.id
                    if field_name.startswith("_") or field_name == "model_config":
                        continue
                    if not _annotation_allows_string(stmt.annotation):
                        continue
                    candidate_env = field_name.upper()
                    if isinstance(stmt.value, ast.Call):
                        for kw in stmt.value.keywords:
                            if kw.arg in {"alias", "validation_alias", "env"}:
                                kw_str = _extract_string_constant(kw.value)
                                if kw_str and _is_valid_env_var_candidate(kw_str.upper()):
                                    candidate_env = kw_str.upper()
                    if _is_credential_env_name(candidate_env):
                        self._add(
                            stmt.lineno,
                            "env_var",
                            candidate_env,
                            f"BaseSettings field {node.name}.{field_name}",
                        )
        self.generic_visit(node)

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        fname_low = node.name.lower()
        for dec in node.decorator_list:
            if isinstance(dec, ast.Call):
                route_arg = _extract_string_constant(dec.args[0]) if dec.args else None
                if route_arg:
                    r_low = route_arg.lower()
                    if any(tok in r_low for tok in ("webhook", "callback", "oauth")):
                        self._add(
                            dec.lineno,
                            "webhook_route",
                            route_arg,
                            f"route handler {node.name}({route_arg})",
                        )
        if any(tok in fname_low for tok in ("webhook", "oauth_callback", "verify_hmac", "verify_signature")):
            self._add(node.lineno, "webhook_route", node.name, f"handler function {node.name}")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
        self._visit_func(node)

    def visit_Call(self, node: ast.Call) -> Any:
        func = node.func
        # os.getenv("KEY") or getenv("KEY")
        if (
            (isinstance(func, ast.Attribute) and func.attr == "getenv")
            or (isinstance(func, ast.Name) and func.id == "getenv")
        ) and node.args:
            env_name = _extract_string_constant(node.args[0])
            if env_name and _is_valid_env_var_candidate(env_name):
                self._add(node.lineno, "env_var", env_name, "reads environment variable via getenv")

        # os.environ.get("KEY") / environ.get("KEY") / st.secrets.get("KEY")
        elif isinstance(func, ast.Attribute) and func.attr in {"get", "pop", "setdefault"} and node.args:
            val = func.value
            is_environ = (
                (isinstance(val, ast.Attribute) and val.attr == "environ")
                or (isinstance(val, ast.Name) and val.id == "environ")
            )
            is_st_secrets = isinstance(val, ast.Attribute) and val.attr == "secrets"
            if is_environ or is_st_secrets:
                env_name = _extract_string_constant(node.args[0])
                if env_name and _is_valid_env_var_candidate(env_name):
                    src_label = "st.secrets.get" if is_st_secrets else "os.environ.get"
                    self._add(node.lineno, "env_var", env_name, f"reads environment variable via {src_label}")
            # headers.get("X-Shopify-Hmac-Sha256") etc.
            hdr_name = _extract_string_constant(node.args[0])
            if hdr_name and any(
                tok in hdr_name.lower()
                for tok in ("hmac", "signature", "x-shopify", "stripe-signature", "x-hub-signature")
            ):
                self._add(node.lineno, "hmac_verification", hdr_name, f"reads webhook signature header {hdr_name}")

        # hmac.new(...) / hmac.compare_digest(...) / Webhook.construct_event(...)
        if isinstance(func, ast.Attribute):
            if (
                isinstance(func.value, ast.Name)
                and func.value.id == "hmac"
                and func.attr in {"new", "compare_digest", "digest"}
            ):
                self._add(
                    node.lineno,
                    "hmac_verification",
                    f"hmac.{func.attr}",
                    f"verifies HMAC signature via hmac.{func.attr}",
                )
            elif func.attr == "construct_event":
                self._add(
                    node.lineno,
                    "hmac_verification",
                    "Webhook.construct_event",
                    "verifies webhook signature via construct_event",
                )
            elif func.attr in {"load_dotenv", "dotenv_values"}:
                self._add(node.lineno, "external_config", func.attr, f"loads .env via {func.attr}")
        elif isinstance(func, ast.Name) and func.id in {"load_dotenv", "dotenv_values"}:
            self._add(node.lineno, "external_config", func.id, f"loads .env via {func.id}")

        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> Any:
        val = node.value
        is_environ = (
            (isinstance(val, ast.Attribute) and val.attr == "environ")
            or (isinstance(val, ast.Name) and val.id == "environ")
        )
        is_st_secrets = isinstance(val, ast.Attribute) and val.attr == "secrets"
        if is_environ or is_st_secrets:
            sl = node.slice
            env_name = _extract_string_constant(sl)
            if env_name and _is_valid_env_var_candidate(env_name):
                src_label = "st.secrets[...]" if is_st_secrets else "os.environ[...]"
                self._add(node.lineno, "env_var", env_name, f"requires environment variable via {src_label}")
        else:
            hdr_name = _extract_string_constant(node.slice)
            if hdr_name and any(
                tok in hdr_name.lower()
                for tok in ("hmac", "signature", "x-shopify", "stripe-signature", "x-hub-signature")
            ):
                self._add(node.lineno, "hmac_verification", hdr_name, f"reads webhook signature header {hdr_name}")
        self.generic_visit(node)


def collect_external_evidence(
    originals: Mapping[str, str],
    *,
    base_sha: str = "",
) -> ExternalEvidenceReport:
    """Deterministically collect external env vars, SDKs, webhook routes, and HMAC checks.

    Uses a single AST pass over Python files plus line scans of .env.example /
    config files. Cached by (base_sha, content digest).
    """
    hasher = hashlib.sha256()
    hasher.update((base_sha or "").encode("utf-8"))
    for p in sorted(originals):
        hasher.update(p.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(originals[p].encode("utf-8", errors="ignore"))
        hasher.update(b"\0")
    cache_key = hasher.hexdigest()
    cached = _EVIDENCE_CACHE.get(cache_key)
    if cached is not None:
        return cached

    items: list[EvidenceItem] = []
    env_vars: set[str] = set()
    providers: set[str] = set()
    has_webhook_or_hmac = False
    has_oauth_callback = False

    for raw_path in sorted(originals):
        rel_path = raw_path.replace("\\", "/")
        base_name = PurePosixPath(rel_path).name
        base_low = base_name.lower()
        text = originals[raw_path]

        # 1. Check .env.example / .env.sample / .env.template
        if (
            base_low in {".env.example", ".env.sample", ".env.template", "env.example", "example.env"}
            or base_low.endswith((".env.example", ".env.sample", ".env.template"))
        ):
            for idx, line in enumerate(text.splitlines(), start=1):
                m = _DOTENV_LINE_RE.match(line)
                if m:
                    env_name = m.group(1)
                    if _is_valid_env_var_candidate(env_name):
                        env_vars.add(env_name)
                        items.append(
                            EvidenceItem(
                                file=rel_path,
                                line=idx,
                                kind="dotenv_example",
                                name=env_name,
                                detail=f"declared in {base_name}",
                            )
                        )
                        inferred = _infer_provider_from_env_name(env_name)
                        if inferred:
                            providers.add(inferred)
            continue

        # 2. Skip test files when collecting production runtime requirements,
        # except keep them out of production evidence items so test mocks don't
        # fabricate external guidance.
        is_test_file = (
            rel_path.startswith("tests/")
            or "/tests/" in f"/{rel_path}"
            or base_low.startswith("test_")
            or base_low.endswith("_test.py")
            or base_low == "conftest.py"
        )
        if is_test_file:
            continue

        if rel_path.endswith(".py"):
            try:
                tree = ast.parse(text, filename=rel_path)
            except SyntaxError:
                continue
            visitor = _ExternalEvidenceVisitor(rel_path)
            visitor.visit(tree)
            for it in visitor.items:
                items.append(it)
                if it.kind == "env_var":
                    env_vars.add(it.name)
                    inferred = _infer_provider_from_env_name(it.name)
                    if inferred:
                        providers.add(inferred)
                elif it.kind == "provider_sdk":
                    providers.add(it.name)
                elif it.kind == "hmac_verification":
                    has_webhook_or_hmac = True
                    if "shopify" in it.name.lower():
                        providers.add("shopify")
                    elif "stripe" in it.name.lower():
                        providers.add("stripe")
                elif it.kind == "webhook_route":
                    if "oauth" in it.name.lower() or "callback" in it.name.lower():
                        has_oauth_callback = True
                    if "webhook" in it.name.lower() or "hmac" in it.name.lower():
                        has_webhook_or_hmac = True
        elif base_low in {"config.toml", "pyproject.toml", "settings.toml", "app.yaml", "render.yaml"}:
            for idx, line in enumerate(text.splitlines(), start=1):
                low = line.lower()
                if any(k in low for k in ("webhook", "oauth", "callback_url", "redirect_uri")):
                    items.append(
                        EvidenceItem(
                            file=rel_path,
                            line=idx,
                            kind="external_config",
                            name=base_name,
                            detail="external callback/webhook configuration entry",
                        )
                    )
                    if "oauth" in low or "callback" in low or "redirect_uri" in low:
                        has_oauth_callback = True
                    if "webhook" in low:
                        has_webhook_or_hmac = True

    for ev in env_vars:
        if "WEBHOOK" in ev or "HMAC" in ev or "SIGNING_SECRET" in ev:
            has_webhook_or_hmac = True
        if "OAUTH" in ev or "CLIENT_SECRET" in ev or "CALLBACK" in ev or "REDIRECT_URI" in ev:
            has_oauth_callback = True

    deduped: list[EvidenceItem] = []
    seen_keys: set[tuple[str, int, str, str]] = set()
    for it in items:
        k = (it.file, it.line, it.kind, it.name)
        if k not in seen_keys:
            seen_keys.add(k)
            deduped.append(it)

    report = ExternalEvidenceReport(
        base_sha=base_sha,
        items=tuple(deduped[:60]),
        env_var_names=tuple(sorted(env_vars)),
        provider_keys=tuple(sorted(providers)),
        has_webhook_or_hmac=has_webhook_or_hmac,
        has_oauth_callback=has_oauth_callback,
    )
    if len(_EVIDENCE_CACHE) > 128:
        _EVIDENCE_CACHE.clear()
    _EVIDENCE_CACHE[cache_key] = report
    return report


def build_placeholder_env(
    evidence: ExternalEvidenceReport | Iterable[str] | None,
) -> dict[str, str]:
    """Generate deterministic non-secret placeholder env values for discovered credential env vars.

    Never reads from host os.environ or any .env file, and never overwrites
    numeric/non-credential application settings (e.g. int timeouts or chunk sizes).
    """
    if evidence is None:
        return {}
    names: Iterable[str]
    if isinstance(evidence, ExternalEvidenceReport):
        names = evidence.env_var_names
    else:
        names = evidence
    out: dict[str, str] = {}
    for raw in sorted({str(n).strip() for n in names if n}):
        if _is_credential_env_name(raw):
            out[raw] = f"{PLACEHOLDER_PREFIX}{raw.lower()}"
    return out


def validate_placeholder_env(env_map: Mapping[str, str] | None) -> dict[str, str]:
    """Validate that every sandbox env injection uses only safe deterministic placeholders.

    Raises ValueError if any key is invalid/reserved or if any value is not a
    deterministic 'gitresolve-placeholder-<lower_key>' token.
    """
    if not env_map:
        return {}
    validated: dict[str, str] = {}
    for key, val in sorted(env_map.items()):
        k = str(key).strip()
        v = str(val)
        if not _is_valid_env_var_candidate(k):
            raise ValueError(f"Forbidden or invalid placeholder environment variable name: {k!r}")
        expected = f"{PLACEHOLDER_PREFIX}{k.lower()}"
        if v != expected or not v.startswith(PLACEHOLDER_PREFIX):
            raise ValueError(
                f"Refusing non-placeholder environment value for {k!r} in sandbox execution."
            )
        validated[k] = v
    return validated


_EXTERNAL_ISSUE_SIGNAL_RE = re.compile(
    r"\b("
    r"api[_ -]?key|secret|credential|credentials|token|\.env|env\s+var|environment\s+variable"
    r"|unset|omitted|expired|rotate|revoked|401|403|unauthorized|forbidden"
    r"|webhook|hmac|signing\s+secret|signature\s+mismatch|x-shopify-hmac|stripe-signature"
    r"|oauth|callback\s+url|redirect_uri|client_secret|client_id"
    r"|gemini_api_key|openai_api_key|anthropic_api_key|shopify_webhook_secret|stripe_webhook_secret"
    r")\b",
    re.IGNORECASE,
)

_CODE_BEHAVIOR_CHANGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:should|must|needs?\s+to|ought\s+to)\s+(?:return|validate|raise|catch|handle|check|reject|emit|log|use|pass|include|support|add)\b", re.IGNORECASE),
    re.compile(r"\binstead\s*,?\s+[A-Za-z0-9_.]+\s+(?:catches|returns|raises|attempts|invokes|calls|uses|ignores|fails|halts)\b", re.IGNORECASE),
    re.compile(r"\binstead\s+of\s+(?:prompting|returning|raising|validating|catching|attempting|using)\b", re.IGNORECASE),
    re.compile(r"\b(?:catches\s+generic\s+exception|unverified\s+fallback|missing\s+timeout|no\s+timeout\s+argument|no\s+try/except|without\s+attempting\s+invalid\s+fallbacks)\b", re.IGNORECASE),
    re.compile(r"\b(?:expected\s+behavior|acceptance\s+criteria)\s*:\s*.*?\b(?:should|must|return|validate|raise)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(?:refactor|implement|add\s+a\s+new|create\s+an?\s+endpoint|add\s+validation|fix\s+the\s+fallback)\b", re.IGNORECASE),
)


def issue_mentions_external_cause(
    issue_title: str,
    issue_body: str,
    evidence: ExternalEvidenceReport,
) -> bool:
    """Return True when the issue text mentions an external credential/webhook/OAuth/env cause."""
    combined = f"{issue_title or ''}\n{issue_body or ''}"
    if _EXTERNAL_ISSUE_SIGNAL_RE.search(combined):
        return True
    combined_upper = combined.upper()
    for env_name in evidence.env_var_names:
        if len(env_name) >= 4 and env_name in combined_upper:
            return True
    return False


def issue_requests_code_behavior_change(
    issue_title: str,
    issue_body: str,
    originals: Mapping[str, str] | None = None,
) -> tuple[bool, list[str]]:
    """Deterministically detect when an issue asks for a code behavior change in the repo.

    Per EAG Ground Rules:
    If an issue asks for a code behavior change (e.g. 'validate the key and return
    a clear 401 instead of catching generic Exception and invoking an unverified
    fallback'), that is (A) code_fix or (C) mixed, NEVER (B) external_action.
    """
    combined = f"{issue_title or ''}\n{issue_body or ''}"
    reasons: list[str] = []
    for pat in _CODE_BEHAVIOR_CHANGE_PATTERNS:
        m = pat.search(combined)
        if m:
            snippet = " ".join(m.group(0).split())[:80]
            reasons.append(f"issue_requests_behavior_change:{snippet}")

    # Check if the issue cites a specific symbol/string literal present in repo code as buggy behavior
    if originals:
        # E.g. "INTERNAL_EMBEDDING_FAILURE", "gpt-oss-20b-default-v0", "WEBHOOK ERROR [EXCEPTION]"
        quoted_tokens = re.findall(r"`([A-Za-z0-9_./:-]{4,80})`", combined)
        for tok in quoted_tokens:
            if tok.isupper() and ("ERROR" in tok or "FAILURE" in tok or "EXCEPTION" in tok):
                if any(tok in content for content in originals.values()):
                    reasons.append(f"issue_cites_repo_error_constant:{tok}")
                    break
            if "." in tok and not tok.endswith((".py", ".env", ".toml", ".txt", ".json", ".md")):
                parts = tok.split(".")
                if len(parts) == 2 and all(p.isidentifier() for p in parts):
                    cls_or_mod, meth = parts
                    if any(f"def {meth}" in content for content in originals.values()):
                        if re.search(rf"\b(?:instead|should|must|catches|missing|fails|bug)\b", combined, re.IGNORECASE):
                            reasons.append(f"issue_targets_repo_symbol:{tok}")
                            break

    return (len(reasons) > 0, reasons)


def should_invoke_resolution_assess(
    issue_title: str,
    issue_body: str,
    triage: TriageReport,
    evidence: ExternalEvidenceReport,
) -> bool:
    """Zero-cost gate (AL Addendum B):

    Only invoke the LLM when the deterministic collector found >=1 EvidenceItem
    AND the issue text/triage signals mention an external cause.
    Otherwise skip with 0 tokens and 0 latency.
    """
    if not external_guidance_enabled():
        return False
    if not evidence.items:
        return False
    if triage.task_type_floor == TaskType.UNCLEAR:
        return False
    return issue_mentions_external_cause(issue_title, issue_body, evidence)


class ResolutionAssessmentPayload(BaseModel):
    """Flat Pydantic schema for the single bounded resolution_assess LLM call."""

    kind: Literal["code_fix", "external_action", "mixed", "needs_clarification"] = Field(
        description=(
            "Resolution kind: 'external_action' ONLY when repo code is already correct and user only needs external setup; "
            "'mixed' when repo code needs a behavior change AND user must configure external keys/webhooks; "
            "'code_fix' when solved purely by code/tests; 'needs_clarification' when ambiguous."
        )
    )
    reason_code: Literal[
        "external_secret_missing",
        "external_webhook_setup",
        "external_oauth_callback",
        "external_service_config",
        "none",
    ] = Field(default="external_secret_missing")
    provider_key: str = Field(
        default="generic",
        description="Canonical provider key from evidence (gemini, openai, anthropic, shopify, stripe, github_oauth, generic).",
    )
    referenced_env_vars: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Exact environment variable names from the provided EvidenceItems.",
    )
    referenced_evidence_refs: list[str] = Field(
        default_factory=list,
        max_length=8,
        description="Exact 'file:line' references from the provided EvidenceItems.",
    )
    code_change_requested: bool = Field(
        default=False,
        description="True if the issue asks to change code behavior, validation, error handling, status codes, or timeouts.",
    )
    clarification_question: str = Field(
        default="",
        max_length=280,
    )
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)


@dataclass(frozen=True)
class ResolutionAssessment:
    kind: Literal["code_fix", "external_action", "mixed", "needs_clarification"] = "code_fix"
    reason_code: str = "none"
    provider_key: str = "generic"
    env_vars: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    what_we_found: tuple[str, ...] = ()
    guidance_outcome: dict[str, Any] | None = None
    setup_note: str = ""
    setup_note_hash: str = ""
    clarification_question: str = ""
    downgraded_from: str = ""
    guard_reasons: tuple[str, ...] = ()
    llm_called: bool = False
    duration_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reasonCode": self.reason_code,
            "providerKey": self.provider_key,
            "envVars": list(self.env_vars),
            "evidenceRefs": list(self.evidence_refs),
            "whatWeFound": list(self.what_we_found),
            "guidanceOutcome": self.guidance_outcome,
            "setupNote": self.setup_note,
            "setupNoteHash": self.setup_note_hash,
            "clarificationQuestion": self.clarification_question,
            "downgradedFrom": self.downgraded_from,
            "guardReasons": list(self.guard_reasons),
            "llmCalled": self.llm_called,
            "durationMs": round(self.duration_ms, 3),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> ResolutionAssessment:
        if not data:
            return cls()
        return cls(
            kind=str(data.get("kind") or "code_fix"),  # type: ignore[arg-type]
            reason_code=str(data.get("reasonCode") or "none"),
            provider_key=str(data.get("providerKey") or "generic"),
            env_vars=tuple(str(x) for x in (data.get("envVars") or []) if x),
            evidence_refs=tuple(str(x) for x in (data.get("evidenceRefs") or []) if x),
            what_we_found=tuple(str(x) for x in (data.get("whatWeFound") or []) if x),
            guidance_outcome=dict(data["guidanceOutcome"]) if isinstance(data.get("guidanceOutcome"), Mapping) else None,
            setup_note=str(data.get("setupNote") or ""),
            setup_note_hash=str(data.get("setupNoteHash") or ""),
            clarification_question=str(data.get("clarificationQuestion") or ""),
            downgraded_from=str(data.get("downgradedFrom") or ""),
            guard_reasons=tuple(str(x) for x in (data.get("guardReasons") or []) if x),
            llm_called=bool(data.get("llmCalled", False)),
            duration_ms=float(data.get("durationMs") or 0.0),
        )


def _select_verified_evidence(
    evidence: ExternalEvidenceReport,
    issue_title: str,
    issue_body: str,
    requested_env_vars: Iterable[str] = (),
    requested_refs: Iterable[str] = (),
    requested_provider: str = "",
) -> tuple[str, tuple[str, ...], tuple[EvidenceItem, ...], str]:
    """Ground provider, env vars, and EvidenceItems strictly against the repository's ExternalEvidenceReport."""
    combined_upper = f"{issue_title or ''}\n{issue_body or ''}".upper()
    combined_low = combined_upper.lower()

    allowed_envs = set(evidence.env_var_names)
    verified_envs: list[str] = [
        e for e in requested_env_vars if e in allowed_envs
    ]
    for env_name in evidence.env_var_names:
        if env_name in combined_upper and env_name not in verified_envs:
            verified_envs.append(env_name)
    if not verified_envs and evidence.env_var_names:
        verified_envs = list(evidence.env_var_names[:3])

    ref_map = {it.ref_str(): it for it in evidence.items}
    selected_items: list[EvidenceItem] = []
    for ref in requested_refs:
        if ref in ref_map and ref_map[ref] not in selected_items:
            selected_items.append(ref_map[ref])
    for it in evidence.items:
        if it.name in verified_envs or it.kind in {"hmac_verification", "webhook_route"}:
            if it not in selected_items:
                selected_items.append(it)
    if not selected_items and evidence.items:
        selected_items = list(evidence.items[:4])

    # Determine canonical provider key strictly from curated table + evidence
    provider_key = requested_provider.strip().lower() if requested_provider else ""
    if provider_key not in CURATED_PROVIDERS or provider_key == "generic":
        provider_key = ""
        for env_name in verified_envs:
            inf = _infer_provider_from_env_name(env_name)
            if inf and inf in CURATED_PROVIDERS:
                provider_key = inf
                break
        if not provider_key:
            for pk in evidence.provider_keys:
                if pk in CURATED_PROVIDERS and pk in combined_low:
                    provider_key = pk
                    break
        if not provider_key and evidence.provider_keys:
            first_pk = evidence.provider_keys[0]
            if first_pk in CURATED_PROVIDERS:
                provider_key = first_pk
        if not provider_key:
            if "shopify" in combined_low and evidence.has_webhook_or_hmac:
                provider_key = "shopify"
            elif "stripe" in combined_low and evidence.has_webhook_or_hmac:
                provider_key = "stripe"
            elif _GRQ_ID in combined_low:
                provider_key = _GRQ_ID
            elif "gemini" in combined_low:
                provider_key = "gemini"
            elif "openai" in combined_low:
                provider_key = "openai"
            elif "anthropic" in combined_low:
                provider_key = "anthropic"
            else:
                provider_key = "generic"

    # Determine reason_code from grounded evidence
    if (
        evidence.has_webhook_or_hmac
        and any(tok in combined_low for tok in ("webhook", "hmac", "signature", "shopify", "stripe"))
        and any(it.kind in {"hmac_verification", "webhook_route"} or "WEBHOOK" in it.name for it in selected_items)
    ):
        reason_code = "external_webhook_setup"
    elif evidence.has_oauth_callback and any(tok in combined_low for tok in ("oauth", "callback", "redirect_uri", "client_secret")):
        reason_code = "external_oauth_callback"
    elif verified_envs:
        reason_code = "external_secret_missing"
    else:
        reason_code = "external_service_config"

    return (
        provider_key,
        tuple(verified_envs[:4]),
        tuple(selected_items[:6]),
        reason_code,
    )


def build_curated_guidance_outcome(
    *,
    reason_code: str,
    provider_key: str,
    env_vars: tuple[str, ...],
    selected_items: tuple[EvidenceItem, ...],
    lane: str = "full",
) -> dict[str, Any]:
    """Build a structured, calm, ChatGPT/Claude-style guidance payload from curated templates.

    No raw internal labels, no shell commands, no model-invented URLs, and no
    advice to weaken security checks.
    """
    pinfo = CURATED_PROVIDERS.get(provider_key, CURATED_PROVIDERS["generic"])
    display_name = str(pinfo.get("display_name") or "External Service")
    console_name = str(pinfo.get("console_name") or "your provider's developer console")
    raw_link = pinfo.get("link")
    safe_link = str(raw_link) if is_allowlisted_url(raw_link) else None

    primary_env = env_vars[0] if env_vars else str(pinfo.get("default_env") or "API_KEY")
    env_list_str = ", ".join(env_vars) if env_vars else primary_env
    primary_loc = selected_items[0].ref_str() if selected_items else "your repository configuration"

    what_we_found = [
        redact(it.display_str(), 200) for it in selected_items[:6]
    ]
    if not what_we_found:
        what_we_found = [f"Configuration reads {primary_env}"]

    steps: list[dict[str, Any]] = []
    if reason_code == "external_webhook_setup":
        title = f"Configure your {display_name} webhook signing secret"
        summary = (
            f"Your repository already verifies webhook signatures in {primary_loc} using {primary_env}. "
            f"No code change is needed — register the webhook endpoint and set {primary_env} in your environment."
        )
        step1: dict[str, Any] = {
            "text": (
                f"Open {console_name}, register or select your webhook endpoint URL, "
                f"and copy the webhook signing secret."
            ),
        }
        if safe_link:
            step1["link"] = safe_link
        steps = [
            step1,
            {
                "text": (
                    f"Set {primary_env} in your local .env file or deployment secret manager "
                    f"to the exact signing secret from {display_name}."
                ),
            },
            {
                "text": (
                    f"Restart your service and send a test webhook delivery from {console_name} "
                    f"to confirm signature verification succeeds."
                ),
            },
        ]
        after_text = (
            f"Once {primary_env} matches the active webhook signing secret in {console_name}, "
            f"incoming webhook requests will pass HMAC verification automatically."
        )
    elif reason_code == "external_oauth_callback":
        title = f"Update your {display_name} OAuth callback configuration"
        summary = (
            f"Your repository handles OAuth callbacks in {primary_loc} and reads {env_list_str}. "
            f"No code change is required — align your external OAuth app settings and environment credentials."
        )
        step1 = {
            "text": (
                f"Open {console_name} and verify the registered Authorization Callback / Redirect URL "
                f"matches your application's callback route referenced at {primary_loc}."
            ),
        }
        if safe_link:
            step1["link"] = safe_link
        steps = [
            step1,
            {
                "text": (
                    f"Copy the active Client ID and Client Secret and set {env_list_str} "
                    f"in your .env file or secret manager."
                ),
            },
            {
                "text": "Restart the application and complete a fresh sign-in flow to verify the callback.",
            },
        ]
        after_text = (
            f"After updating the callback URL and setting {env_list_str}, OAuth redirects will complete normally."
        )
    else:
        reason_code = "external_secret_missing" if env_vars else "external_service_config"
        title = f"Configure {env_list_str} for {display_name}"
        summary = (
            f"Your application reads {env_list_str} in {primary_loc}. "
            f"Set a valid credential in your environment or secret manager so requests to {display_name} can authenticate."
        )
        step1 = {
            "text": f"Open {console_name} and create or rotate an active API key for {display_name}.",
        }
        if safe_link:
            step1["link"] = safe_link
        steps = [
            step1,
            {
                "text": (
                    f"Add {primary_env}=<your-key> to your local .env file (or configure {env_list_str} "
                    f"in your deployment secret manager) as referenced in {primary_loc}."
                ),
            },
            {
                "text": (
                    "Restart your application server so the updated environment variable is loaded, "
                    "then retry your request."
                ),
            },
        ]
        after_text = (
            f"Once {env_list_str} is configured with an active key, {display_name} requests will authenticate normally."
        )

    payload = {
        "kind": "needs_user_action",
        "reasonCode": reason_code,
        "providerKey": provider_key,
        "title": redact(title, 140),
        "summary": redact(summary, 360),
        "explanation": redact(summary, 360),
        "whatWeFound": what_we_found,
        "evidence": what_we_found,
        "steps": steps,
        "afterYouDoThis": redact(after_text, 280),
        "nextStep": redact(after_text, 280),
        "securityNote": SECURITY_NOTE,
        "lane": lane,
    }
    return redact_value(payload)


def build_curated_setup_note(
    *,
    reason_code: str,
    provider_key: str,
    env_vars: tuple[str, ...],
    selected_items: tuple[EvidenceItem, ...],
) -> tuple[str, str]:
    """Build a sanitized, length-bounded template setup note and its SHA-256 hash for mixed (C) outcomes."""
    pinfo = CURATED_PROVIDERS.get(provider_key, CURATED_PROVIDERS["generic"])
    display_name = str(pinfo.get("display_name") or "External Service")
    console_name = str(pinfo.get("console_name") or "your provider's developer console")
    raw_link = pinfo.get("link")
    safe_link = str(raw_link) if is_allowlisted_url(raw_link) else None

    primary_env = env_vars[0] if env_vars else str(pinfo.get("default_env") or "API_KEY")
    env_list_str = ", ".join(env_vars) if env_vars else primary_env
    loc_refs = ", ".join(it.ref_str() for it in selected_items[:3]) or "your configuration"
    console_ref = f"{console_name}: {safe_link}" if safe_link else console_name

    if reason_code == "external_webhook_setup":
        lines = [
            f"1. Open {console_ref} and copy the active webhook signing secret.",
            f"2. Configure {env_list_str} in your .env file or deployment secret manager (read at {loc_refs}).",
            "3. Restart the service after deploying this code change. Never paste secrets into issues or pull requests.",
        ]
    else:
        lines = [
            f"1. Create or verify an active {display_name} credential in {console_ref}",
            f"2. Set {env_list_str} in your .env file or deployment secret manager (referenced at {loc_refs}).",
            "3. Restart the application after deploying this fix. Automated sandbox verification ran with safe placeholder values.",
        ]

    note = redact("\n".join(lines), MAX_SETUP_NOTE_CHARS).strip()
    note = note[:MAX_SETUP_NOTE_CHARS]
    return note, sha256_hash(note)


def apply_resolution_guards(
    *,
    candidate_kind: Literal["code_fix", "external_action", "mixed", "needs_clarification"],
    candidate_reason_code: str,
    candidate_provider_key: str,
    requested_env_vars: Iterable[str],
    requested_refs: Iterable[str],
    code_change_requested_by_llm: bool,
    clarification_question: str,
    issue_title: str,
    issue_body: str,
    triage: TriageReport,
    evidence: ExternalEvidenceReport,
    originals: Mapping[str, str],
    llm_called: bool = False,
    duration_ms: float = 0.0,
) -> ResolutionAssessment:
    """Enforce deterministic Hard Guards (AL Addendum D) on any resolution assessment."""
    guard_reasons: list[str] = []
    downgraded_from = ""
    kind = candidate_kind

    # Ground identifiers strictly in EvidenceItems
    provider_key, verified_envs, selected_items, grounded_reason_code = _select_verified_evidence(
        evidence,
        issue_title,
        issue_body,
        requested_env_vars=requested_env_vars,
        requested_refs=requested_refs,
        requested_provider=candidate_provider_key,
    )
    reason_code = (
        candidate_reason_code
        if candidate_reason_code in {
            "external_secret_missing",
            "external_webhook_setup",
            "external_oauth_callback",
            "external_service_config",
        }
        else grounded_reason_code
    )

    # Guard 3: Every identifier must come from EvidenceItems; if no verified evidence exists, downgrade to code_fix
    has_grounded_external_evidence = bool(selected_items and (verified_envs or evidence.has_webhook_or_hmac or evidence.has_oauth_callback))
    if kind in {"external_action", "mixed"} and not has_grounded_external_evidence:
        downgraded_from = downgraded_from or kind
        kind = "code_fix"
        guard_reasons.append("ungrounded_external_claim_downgraded")

    # Guard 2: Task-type interplay (Phase 4)
    # Pure external_action is allowed ONLY when triage.task_type_floor is bug or config.
    if triage.task_type_floor == TaskType.UNCLEAR:
        if kind != "needs_clarification":
            downgraded_from = downgraded_from or kind
            kind = "needs_clarification"
            guard_reasons.append("task_type_unclear_requires_clarification")
    elif triage.task_type_floor in {TaskType.FEATURE, TaskType.REFACTOR}:
        if kind == "external_action":
            downgraded_from = downgraded_from or kind
            kind = "mixed" if (verified_envs and issue_mentions_external_cause(issue_title, issue_body, evidence)) else "code_fix"
            guard_reasons.append(f"task_type_{triage.task_type_floor.value}_forbids_pure_external_action")

    # Guard 1: If AL triage found undefined_names, NameError signals, localized traceback in repo code,
    # or the issue requests a code behavior change that repo code lacks, pure external_action is FORBIDDEN.
    det_behavior_change, behavior_reasons = issue_requests_code_behavior_change(issue_title, issue_body, originals)
    has_undefined = bool(triage.undefined_names_by_file) or any(
        s.startswith("undefined_witness:") for s in triage.signals
    )
    has_traceback = bool(
        re.search(
            r"Traceback \(most recent call last\)|File [\"'][^\"']+\.py[\"'],\s*line\s*\d+|\b(?:NameError|TypeError|AttributeError|AssertionError|IndexError|SyntaxError):",
            f"{issue_title or ''}\n{issue_body or ''}",
        )
    )
    has_code_defect_signal = bool(
        has_undefined
        or has_traceback
        or det_behavior_change
        or code_change_requested_by_llm
    )
    if kind == "external_action" and has_code_defect_signal:
        downgraded_from = downgraded_from or "external_action"
        if has_undefined:
            guard_reasons.append("triage_undefined_name_forbids_external_action")
        if has_traceback:
            guard_reasons.append("triage_traceback_forbids_external_action")
        if det_behavior_change or code_change_requested_by_llm:
            guard_reasons.extend(behavior_reasons or ["code_behavior_change_requested"])
        # Downgrade to mixed if verified env/webhook setup is genuinely part of the issue, else code_fix
        if verified_envs and issue_mentions_external_cause(issue_title, issue_body, evidence):
            kind = "mixed"
        else:
            kind = "code_fix"

    # Also ensure mixed is only used when verified env/webhook evidence is referenced in the issue
    if kind == "mixed" and not (verified_envs and issue_mentions_external_cause(issue_title, issue_body, evidence)):
        downgraded_from = downgraded_from or "mixed"
        kind = "code_fix"
        guard_reasons.append("mixed_without_referenced_env_downgraded_to_code_fix")

    if kind == "external_action":
        guidance_outcome = build_curated_guidance_outcome(
            reason_code=reason_code,
            provider_key=provider_key,
            env_vars=verified_envs,
            selected_items=selected_items,
            lane=triage.candidate_lane.value,
        )
        return ResolutionAssessment(
            kind="external_action",
            reason_code=reason_code,
            provider_key=provider_key,
            env_vars=verified_envs,
            evidence_refs=tuple(it.ref_str() for it in selected_items),
            what_we_found=tuple(guidance_outcome.get("whatWeFound") or ()),
            guidance_outcome=guidance_outcome,
            downgraded_from=downgraded_from,
            guard_reasons=tuple(guard_reasons),
            llm_called=llm_called,
            duration_ms=duration_ms,
        )

    if kind == "mixed":
        setup_note, setup_note_hash = build_curated_setup_note(
            reason_code=reason_code,
            provider_key=provider_key,
            env_vars=verified_envs,
            selected_items=selected_items,
        )
        what_we_found = tuple(redact(it.display_str(), 200) for it in selected_items)
        return ResolutionAssessment(
            kind="mixed",
            reason_code=reason_code,
            provider_key=provider_key,
            env_vars=verified_envs,
            evidence_refs=tuple(it.ref_str() for it in selected_items),
            what_we_found=what_we_found,
            setup_note=setup_note,
            setup_note_hash=setup_note_hash,
            downgraded_from=downgraded_from,
            guard_reasons=tuple(guard_reasons),
            llm_called=llm_called,
            duration_ms=duration_ms,
        )

    if kind == "needs_clarification":
        q = redact(
            clarification_question
            or "Please clarify whether you want a code change in the repository or setup instructions for your external service credentials.",
            280,
        )
        return ResolutionAssessment(
            kind="needs_clarification",
            reason_code="none",
            provider_key=provider_key,
            env_vars=verified_envs,
            evidence_refs=tuple(it.ref_str() for it in selected_items),
            clarification_question=q,
            downgraded_from=downgraded_from,
            guard_reasons=tuple(guard_reasons),
            llm_called=llm_called,
            duration_ms=duration_ms,
        )

    return ResolutionAssessment(
        kind="code_fix",
        reason_code="none",
        provider_key=provider_key,
        env_vars=verified_envs,
        evidence_refs=tuple(it.ref_str() for it in selected_items),
        downgraded_from=downgraded_from,
        guard_reasons=tuple(guard_reasons),
        llm_called=llm_called,
        duration_ms=duration_ms,
    )


def assess_resolution_deterministic(
    issue_title: str,
    issue_body: str,
    triage: TriageReport,
    evidence: ExternalEvidenceReport,
    originals: Mapping[str, str],
) -> ResolutionAssessment:
    """Fast deterministic assessment used when LLM is skipped, fails open, or confirms evidence."""
    if not should_invoke_resolution_assess(issue_title, issue_body, triage, evidence):
        return ResolutionAssessment(kind="code_fix")

    det_behavior_change, _ = issue_requests_code_behavior_change(issue_title, issue_body, originals)
    has_undefined = bool(triage.undefined_names_by_file) or any(
        s.startswith("undefined_witness:") for s in triage.signals
    )
    has_traceback = bool(
        re.search(
            r"Traceback \(most recent call last\)|File [\"'][^\"']+\.py[\"'],\s*line\s*\d+|\b(?:NameError|TypeError|AttributeError|AssertionError|IndexError|SyntaxError):",
            f"{issue_title or ''}\n{issue_body or ''}",
        )
    )
    has_code_defect = bool(
        has_undefined
        or has_traceback
        or det_behavior_change
    )
    candidate_kind: Literal["code_fix", "external_action", "mixed"] = (
        "mixed" if has_code_defect else "external_action"
    )
    return apply_resolution_guards(
        candidate_kind=candidate_kind,
        candidate_reason_code="",
        candidate_provider_key="",
        requested_env_vars=evidence.env_var_names,
        requested_refs=[it.ref_str() for it in evidence.items],
        code_change_requested_by_llm=det_behavior_change,
        clarification_question="",
        issue_title=issue_title,
        issue_body=issue_body,
        triage=triage,
        evidence=evidence,
        originals=originals,
        llm_called=False,
        duration_ms=0.0,
    )


_MISSING_MODULE_RE = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s+No module named ['\"]([A-Za-z0-9_.-]+)['\"]"
)
_DEP_ERROR_PKG_RE = re.compile(
    r"(?:No matching distribution found for|Could not find a version that satisfies the requirement)\s+([A-Za-z0-9_.\-\[\]=<>!~]+)",
    re.IGNORECASE,
)


def extract_missing_packages(text_or_result: Any) -> list[str]:
    """Extract exact missing module or package names from test output or DependencyError text."""
    if isinstance(text_or_result, str):
        text = text_or_result
    else:
        stdout = getattr(text_or_result, "stdout", "") or ""
        stderr = getattr(text_or_result, "stderr", "") or ""
        err_msg = getattr(text_or_result, "error_message", "") or ""
        text = f"{stdout}\n{stderr}\n{err_msg}"

    found: list[str] = []
    for m in _MISSING_MODULE_RE.finditer(text):
        top_pkg = m.group(1).split(".")[0].strip()
        if top_pkg and top_pkg not in found:
            found.append(top_pkg)
    for m in _DEP_ERROR_PKG_RE.finditer(text):
        spec = m.group(1).strip().rstrip(",;)")
        if spec and spec not in found:
            found.append(spec)
    return found


def find_import_locations(
    originals: Mapping[str, str],
    package_names: Iterable[str],
) -> list[str]:
    """Find file:line references where missing packages are imported in the repository."""
    targets = {p.split("=")[0].split("<")[0].split(">")[0].split("[")[0].strip().replace("-", "_") for p in package_names if p}
    if not targets:
        return []
    refs: list[str] = []
    for raw_path in sorted(originals):
        if not raw_path.endswith(".py"):
            continue
        rel = raw_path.replace("\\", "/")
        for idx, line in enumerate(originals[raw_path].splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith(("import ", "from ")):
                for pkg in targets:
                    if re.search(rf"\b(?:import|from)\s+{re.escape(pkg)}\b", stripped):
                        refs.append(f"{rel}:{idx} — {stripped[:100]}")
                        break
            if len(refs) >= 6:
                return refs
    return refs


def build_missing_dep_guidance_outcome(
    result: Any,
    originals: Mapping[str, str] | None = None,
    declared_dep_files: Iterable[str] = (),
    *,
    lane: str = "full",
) -> dict[str, Any]:
    """Build a calm, specific user-facing guidance outcome when a dependency is missing or unsupported."""
    missing_pkgs = extract_missing_packages(result)
    pkg_label = ", ".join(missing_pkgs) if missing_pkgs else "a required Python package"
    err_detail = redact(str(getattr(result, "error_message", "") or "").strip(), 240)
    import_refs = find_import_locations(originals or {}, missing_pkgs) if originals else []
    dep_files = [str(f) for f in declared_dep_files if f]
    what_we_found: list[str] = []
    if import_refs:
        what_we_found.extend(import_refs)
    elif missing_pkgs:
        what_we_found.append(f"Test execution imported undeclared or unavailable module: {pkg_label}")
    if dep_files:
        what_we_found.append(f"Checked dependency declarations in: {', '.join(dep_files[:4])}")
    if err_detail and "missing_dep" not in err_detail:
        what_we_found.append(err_detail)
    if not what_we_found:
        what_we_found.append(f"Repository tests require {pkg_label} in requirements.txt or pyproject.toml")

    title = (
        f"Declare {pkg_label} in your repository dependencies"
        if missing_pkgs
        else "Update repository dependency declarations for sandbox verification"
    )
    summary = (
        f"Your repository imports {pkg_label}, which is either missing from your dependency manifest "
        f"or pinned to a version without a prebuilt Linux x86_64 binary wheel."
    )
    target_manifest = dep_files[0] if dep_files else "requirements.txt"
    steps = [
        {
            "text": (
                f"Add {pkg_label} with an exact binary-wheel-supported version (for example in {target_manifest})."
            ),
        },
        {
            "text": (
                "Ensure all test and runtime packages are public PyPI packages with Linux x86_64 wheels "
                "(avoid local paths, git URLs, or source-only builds)."
            ),
        },
        {
            "text": "Commit the updated dependency file to your branch and re-check.",
        },
    ]
    after_text = (
        f"Once {pkg_label} is declared in {target_manifest} with a supported wheel version, "
        f"sandbox verification will install it automatically."
    )
    return redact_value({
        "kind": "needs_user_action",
        "reasonCode": "external_dependency_unsupported",
        "providerKey": "generic",
        "title": title,
        "summary": summary,
        "explanation": summary,
        "whatWeFound": what_we_found[:6],
        "evidence": what_we_found[:6],
        "steps": steps,
        "afterYouDoThis": after_text,
        "nextStep": after_text,
        "securityNote": SECURITY_NOTE,
        "lane": lane,
    })
