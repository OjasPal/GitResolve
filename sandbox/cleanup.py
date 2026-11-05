"""Narrow, deterministic root-test cleanup. No repository code is executed here."""
import ast
import hashlib
import json
import re
from pathlib import Path


def digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def cleanup_intent(text):
    original_request = text
    text = text.lower()
    if (len(text) > 4000 or not re.search(r"\b(remove|delete)\b", text)
            or not re.search(r"\broot\b", text) or "test" not in text
            or not re.search(r"\.py\b|\bpython\b", text)
            or re.search(r"(?:do not|don't|never|avoid)\s+(?:remove|delete)|\b(add|implement|rename|replace|refactor)\b", text)):
        return None
    numbers = {"one":1,"two":2,"three":3,"four":4,"1":1,"2":2,"3":3,"4":4}
    counts = {numbers[n] for n in re.findall(r"\b(one|two|three|four|[1-4])\b", text)}
    if len(counts) > 1:
        return None
    return {"count": next(iter(counts)) if counts else None,
            "names": sorted(set(re.findall(r"\btest[A-Za-z0-9_]*\.py\b",original_request)))}


def safe_names(names):
    if not isinstance(names, (list, tuple)) or not 1 <= len(names) <= 4 or not all(isinstance(n,str) for n in names) or len(set(names)) != len(names):
        raise ValueError("Cleanup must name one to four distinct root test files.")
    if any(not re.fullmatch(r"test[A-Za-z0-9_]*\.py", name) for name in names):
        raise ValueError("Only root test*.py files can be removed by this operation.")
    return list(names)


def references(root, names):
    """Conservatively reject local imports, dynamic loaders and config references."""
    modules = {n[:-3] for n in names}
    module_reference=re.compile(r"(?<![\w])(?:"+"|".join(re.escape(n) for n in modules)+r")[.:][A-Za-z_]\w*")
    found = []
    for path in Path(root).rglob("*"):
        rel = path.relative_to(root)
        if path.name == ".git" or any(p in {".git",".venv","venv","node_modules","__pycache__"} for p in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError("Repository links require manual review before cleanup.")
        if not path.is_file() or rel.as_posix() in names:
            continue
        if path.stat().st_size > 100000:
            if path.suffix == ".py": raise ValueError("A Python source file exceeds the cleanup inspection limit.")
            continue
        if path.suffix == ".py":
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(rel))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value,str) and node.value.replace("\\","/").split("/")[-1] in names:
                    found.append(str(rel))
                if isinstance(node, ast.Constant) and isinstance(node.value,str) and module_reference.search(node.value):
                    found.append(str(rel))
                if isinstance(node, ast.Import) and any(a.name.split(".")[0] in modules for a in node.names):
                    found.append(str(rel))
                if isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] in modules:
                    found.append(str(rel))
                if isinstance(node, ast.ImportFrom) and not node.module and any(a.name in modules for a in node.names):
                    found.append(str(rel))
                if isinstance(node, ast.Call):
                    name = getattr(node.func,"attr",getattr(node.func,"id",""))
                    loaders={"__import__","import_module","find_spec","run_path","run_module","exec_module","spec_from_file_location","SourceFileLoader","exec","eval"}
                    if name in loaders | {"open","Path"}:
                        value = node.args[0] if node.args else None
                        if name in loaders and (not isinstance(value, ast.Constant) or name in {"spec_from_file_location","SourceFileLoader","exec_module"}):
                            found.append(str(rel) + " (dynamic loader)")
                        if isinstance(value, ast.Constant) and isinstance(value.value,str) and (value.value.split(".")[0] in modules or value.value in names):
                            found.append(str(rel))
        elif path.suffix in {".toml",".ini",".cfg",".yaml",".yml",".json",".sh",".bat",".ps1"} or path.name in {"Makefile","Dockerfile","Jenkinsfile"}:
            text = path.read_text(encoding="utf-8",errors="replace")
            if module_reference.search(text) or any(re.search(r"(?<![\w])"+re.escape(n)+r"(?![\w])",text) for n in names): found.append(str(rel))
    return sorted(set(found))


def build_scope(root, request, base_sha):
    intent = cleanup_intent(request)
    if intent is None: return None
    root = Path(root)
    paths = [root/n for n in intent["names"]] if intent["names"] else sorted(p for p in root.iterdir() if re.fullmatch(r"test[A-Za-z0-9_]*\.py",p.name))
    names = safe_names([p.name for p in paths])
    if intent["count"] is not None and intent["count"] != len(names):
        raise ValueError(f"The request names {intent['count']} files, but the pinned repository contains {len(names)} matching root files. Clarify the exact targets.")
    files = []
    for path in paths:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 100000:
            raise ValueError("Cleanup targets must be small regular files.")
        text = path.read_bytes().decode("utf-8")
        if not text: raise ValueError("Empty cleanup targets require explicit manual review.")
        files.append({"path":path.name,"sha256":digest(text),"lines":len(text.splitlines())})
    lines = sum(f["lines"] for f in files)
    if lines > 400: raise ValueError("The requested cleanup exceeds the 400-line cleanup ceiling. Split the request.")
    refs = references(root,names)
    if refs: raise ValueError("Application/configuration references require review before deletion: " + ", ".join(refs)[:1000])
    return {"baseSha":base_sha,"requestHash":digest(request),"files":files,"maxDiffLines":max(150,lines),"approved":False}


def validate_scope(scope, base_sha, request=None, approved=True):
    if not isinstance(scope,dict) or scope.get("baseSha") != base_sha or not re.fullmatch(r"[a-f0-9]{40}",base_sha or ""):
        raise ValueError("Cleanup scope does not match the pinned revision.")
    files = scope.get("files",[])
    names = safe_names([f["path"] for f in files])
    if approved and scope.get("approved") is not True: raise ValueError("The cleanup plan requires explicit approval.")
    if request is not None and scope.get("requestHash") != digest(request): raise ValueError("The cleanup request changed; investigate it again.")
    if any(not re.fullmatch(r"[a-f0-9]{64}",f.get("sha256","")) or type(f.get("lines")) is not int or f["lines"] < 1 for f in files):
        raise ValueError("Invalid cleanup file fingerprint.")
    if scope.get("maxDiffLines") != max(150,sum(f["lines"] for f in files)) or scope["maxDiffLines"] > 400:
        raise ValueError("Invalid task-scoped cleanup budget.")
    return names


def deletion_patch(root, scope):
    sections = []
    for item in scope["files"]:
        text = (Path(root)/item["path"]).read_bytes().decode("utf-8")
        if digest(text) != item["sha256"]: raise ValueError("Cleanup source changed since investigation.")
        lines = text.splitlines(keepends=True)
        body = "".join("-"+line if line.endswith("\n") else "-"+line+"\n\\ No newline at end of file\n" for line in lines)
        name = item["path"]
        sections.append(f"diff --git a/{name} b/{name}\ndeleted file mode 100644\n--- a/{name}\n+++ /dev/null\n@@ -1,{len(lines)} +0,0 @@\n"+body)
    return "".join(sections)


def validate_cleanup_patch(patch, scope):
    names = validate_scope(scope,scope.get("baseSha"))
    sections = [s for s in re.split(r"(?=^diff --git )",patch,flags=re.M) if s.strip()]
    if len(sections)!=len(names): raise ValueError("Cleanup must remove exactly the approved files.")
    for section,item in zip(sections,scope["files"]):
        name=item["path"]
        prefix=f"diff --git a/{name} b/{name}\ndeleted file mode 100644\n--- a/{name}\n+++ /dev/null\n@@ -1,{item['lines']} +0,0 @@\n"
        if not section.startswith(prefix): raise ValueError("Cleanup patch differs from the approved operation.")
        rows=section[len(prefix):].splitlines(keepends=True);removed=[]
        for line in rows:
            if line.startswith("-"): removed.append(line[1:])
            elif line=="\\ No newline at end of file\n" and removed: removed[-1]=removed[-1].removesuffix("\n")
            else: raise ValueError("Cleanup may contain only complete-file deletions.")
        if len(removed)!=item["lines"] or digest("".join(removed))!=item["sha256"]:
            raise ValueError("Cleanup deletion does not match the approved source fingerprint.")
    return names
