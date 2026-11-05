import pytest
from sandbox.cleanup import build_scope, cleanup_intent, deletion_patch, validate_scope, validate_cleanup_patch
from sandbox.container_entry import validate_patch
from app.services.policy import validate_fix
from app.services.publication_patch import validate_publication
from githubapp.patch import apply_diff
from githubapp.client import GitHubClientError

REQUEST="There are four test .py files in the root directory prefixed with test. Remove all 4 .py files safely"
SHA="a"*40
EVIDENCE={"reproduced":True,"regressionPassed":True,"testsRun":4,"newFailures":[]}


def scope(root,lines=40):
    for i in range(4): (root/f"test_old_{i}.py").write_text("# historical test\n"*lines)
    (root/"app.py").write_text("def calculate(): return 42\n")
    result=build_scope(root,REQUEST,SHA)
    return {**result,"approved":True}


def test_cleanup_is_explicit_and_scope_is_bound_to_request_revision_and_content(tmp_path):
    s=scope(tmp_path)
    assert s["maxDiffLines"]==160
    assert len(validate_scope(s,SHA,REQUEST))==4
    with pytest.raises(ValueError):validate_scope(s,"b"*40,REQUEST)
    with pytest.raises(ValueError):validate_scope(s,SHA,"Different request")
    with pytest.raises(ValueError):validate_scope({**s,"approved":False},SHA,REQUEST)
    (tmp_path/"test_old_0.py").write_text("changed\n")
    with pytest.raises(ValueError):deletion_patch(tmp_path,s)


def test_default_deletion_policy_stays_closed_and_exact_approved_cleanup_passes(tmp_path):
    s=scope(tmp_path);patch=deletion_patch(tmp_path,s);names=validate_scope(s,SHA)
    limits={"maxDiffFiles":5,"maxDiffLines":160}
    with pytest.raises(ValueError):validate_fix(patch,limits,EVIDENCE,SHA)
    with pytest.raises(ValueError):validate_patch(patch,160)
    assert validate_patch(patch,160,names)==names
    validate_cleanup_patch(patch,s)
    validate_publication(patch,limits,EVIDENCE,SHA,{"cleanupVerified":True,"allowedDeletions":names},cleanup_scope=s)
    with pytest.raises(ValueError):validate_publication(patch,limits,EVIDENCE,SHA,{},cleanup_scope=s)
    with pytest.raises(ValueError):validate_cleanup_patch(patch.replace("-# historical test","-# changed",1),s)
    with pytest.raises(ValueError):validate_patch(patch+"diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-old\n+new\n",160,names)


@pytest.mark.parametrize("source",["import test_old_0\n","from test_old_0 import helper\n","import importlib\nimportlib.import_module('test_old_0')\n","import importlib\nimportlib.import_module(module_name)\n","from pathlib import Path\np=Path(__file__).parent / 'test_old_0.py'\n","import importlib.util\nimportlib.util.spec_from_file_location('loader',path)\n"])
def test_referenced_modules_are_not_silently_removed(tmp_path,source):
    scope(tmp_path)
    (tmp_path/"app.py").write_text(source)
    with pytest.raises(ValueError,match="references"):build_scope(tmp_path,REQUEST,SHA)


def test_explicit_filename_never_expands_to_all_tests(tmp_path):
    scope(tmp_path)
    s=build_scope(tmp_path,"Remove test_old_0.py from root directory safely",SHA)
    assert [f["path"] for f in s["files"]]==["test_old_0.py"]


@pytest.mark.parametrize("name,content",[("pyproject.toml",'[project.scripts]\ntool = "test_old_0:main"\n'),("setup.py",'entry_points={"console_scripts":["tool = test_old_0:main"]}\n')])
def test_packaging_entry_points_cannot_lose_their_runtime_module(tmp_path,name,content):
    scope(tmp_path)
    (tmp_path/name).write_text(content)
    with pytest.raises(ValueError,match="references"):build_scope(tmp_path,REQUEST,SHA)


def test_ambiguous_and_excessive_cleanup_stops_before_model_retries(tmp_path):
    scope(tmp_path)
    with pytest.raises(ValueError,match="names 3"):build_scope(tmp_path,REQUEST.replace("four","three").replace("4 ","3 "),SHA)
    assert cleanup_intent("Do not remove test files from root Python directory") is None
    for p in tmp_path.glob("test*.py"):p.write_text("# line\n"*101)
    with pytest.raises(ValueError,match="400-line"):build_scope(tmp_path,REQUEST,SHA)


def test_publication_uses_git_tree_deletions_instead_of_empty_files(tmp_path):
    s=scope(tmp_path);patch=deletion_patch(tmp_path,s);names=validate_scope(s,SHA)
    def read(name):return (tmp_path/name).read_bytes().decode("utf-8"),"100644"
    with pytest.raises(GitHubClientError):apply_diff(patch,read)
    files=apply_diff(patch,read,names)
    assert [f["path"] for f in files]==names
    assert all(f["sha"] is None and "content" not in f for f in files)
