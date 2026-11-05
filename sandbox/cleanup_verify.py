"""Image-owned parent verifies cleanup before/after untrusted pytest child execution."""
import ast
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from gitresolve_cleanup import references, safe_names


def sources(root, excluded=()):
    result = {}
    for path in root.rglob("*"):
        rel=path.relative_to(root)
        if any(p in {".git","__pycache__",".pytest_cache"} for p in rel.parts): continue
        if path.is_symlink(): raise ValueError("Repository links require manual review.")
        if not path.is_file() or rel.as_posix() in excluded: continue
        if path.suffix in {".py",".js",".ts",".tsx",".html",".css",".json",".toml",".yaml",".yml",".ini",".cfg"}:
            result[rel.as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def verify(root,names,expected):
    if any((root/name).exists() or (root/name).is_symlink() for name in names):
        raise ValueError("Approved cleanup targets still exist.")
    if sources(root)!=expected: raise ValueError("Application source changed outside the approved cleanup.")
    if references(root,names): raise ValueError("Application references a removed module.")
    for name in expected:
        if name.endswith(".py"): ast.parse((root/name).read_bytes(),filename=name)


def main():
    names=safe_names(json.loads(os.environ["GITRESOLVE_DELETE_PATHS"]))
    root=Path("/workspace")
    expected=sources(Path("/input"),names)
    verify(root,names,expected)
    tests=["/input/"+name for name in names if name.startswith("test_") or name.endswith("_test.py")]
    if not tests: raise ValueError("No existing automated tests are available for compatibility verification.")
    # Include surviving tests too; the original deleted tests remain read-only.
    code=subprocess.run(["python","-I","/usr/local/lib/gitresolve_cleanup_pytest.py","-q","--tb=short","-o","addopts=","--import-mode=importlib","--",*tests,"/workspace"],cwd=root,timeout=240).returncode
    if code: return code
    verify(root,names,expected)
    print("CLEANUP GATE PASSED: exact files removed; application source unchanged; original tests passed against the cleaned workspace.",flush=True)
    return 0


if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception:
        print("Cleanup compatibility verification could not be completed.",file=sys.stderr)
        raise SystemExit(125)
