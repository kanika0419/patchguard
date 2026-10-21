import ast, difflib, io, json, re, shutil, subprocess, sys, tarfile, tempfile
from pathlib import Path

RUNNER = Path(__file__).with_name("runner.py")
PROOF_FILE = "test_patchguard_proof.py"
MAX_PROOFS = 3

# Simple, visible rules (these show up in the UI too)
PENALTY = {"Proven": 25, "Tool-confirmed": 15, "Hypothesis": 5}
SEVERITY = {"Proven": "High", "Tool-confirmed": "High", "Hypothesis": "Low"}
CONFIDENCE = {"Proven": "High", "Tool-confirmed": "High", "Hypothesis": "Low"}
TIER_ORDER = {"Proven": 0, "Tool-confirmed": 1, "Hypothesis": 2}


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True)
    if r.returncode:
        raise RuntimeError(r.stderr.decode())
    return r.stdout


def checkout(repo, ref, dest):
    """Copy a branch into a plain folder. Your real branch is never touched."""
    data = git(repo, "archive", ref)
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest)


def changed_files(repo, base, head):
    names = git(repo, "diff", "--name-only", base, head).decode().split()
    return [f for f in names if f.endswith(".py") and not Path(f).name.startswith("test_")]


def functions(path):
    if not path.exists():
        return {}
    src = path.read_text()
    tree = ast.parse(src)
    return {n.name: ast.get_source_segment(src, n)
            for n in tree.body if isinstance(n, ast.FunctionDef)}


def changed_functions(old, new, files):
    out = []
    for f in files:
        a, b = functions(old / f), functions(new / f)
        out += [(f, name) for name, src in b.items() if name in a and a[name] != src]
    return out


def run_tests(folder, target=None):
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=no", "-rA", "-p", "no:cacheprovider"]
    if target:
        cmd.append(target)
    try:
        r = subprocess.run(cmd, cwd=folder, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None
    passed = set(re.findall(r"^PASSED (\S+)", r.stdout, re.M))
    failed = set(re.findall(r"^FAILED (\S+)", r.stdout, re.M))
    return passed, failed


def run_func(folder, file, func):
    try:
        r = subprocess.run(
            [sys.executable, str(RUNNER), str(folder), file, func],
            capture_output=True, text=True, timeout=30)
        return json.loads(r.stdout)
    except Exception:
        return {}


def make_proof_test(file, func, diffs):
    """Write a small test that pins down what the OLD code returned."""
    cases = []
    for key, (old_out, new_out) in diffs.items():
        try:
            args = ast.literal_eval(key)
            expected = ast.literal_eval(old_out)
        except Exception:
            continue  # the old version raised an error here, so skip this input
        cases.append((args, expected, key, old_out, new_out))
        if len(cases) == MAX_PROOFS:
            break
    module = file[:-3].replace("/", ".")
    lines = [f"from {module} import {func} as fn", ""]
    for i, (args, expected, *_rest) in enumerate(cases):
        lines += [f"def test_case_{i}():", f"    assert fn(*{args!r}) == {expected!r}", ""]
    return "\n".join(lines), cases


def prove(old, new, file, func, diffs):
    """A drift is PROVEN if the generated test passes on old code and fails on new."""
    code, cases = make_proof_test(file, func, diffs)
    if not cases:
        return [], code
    (old / PROOF_FILE).write_text(code)
    (new / PROOF_FILE).write_text(code)
    r_old, r_new = run_tests(old, PROOF_FILE), run_tests(new, PROOF_FILE)
    if not r_old or not r_new:
        return [], code
    ok = r_old[0] & r_new[1]
    rows = []
    for i, (args, expected, key, old_out, new_out) in enumerate(cases):
        if f"{PROOF_FILE}::test_case_{i}" in ok:
            rows.append({"input": key, "old": old_out, "new": new_out})
    return rows, code


def analyze(repo, base, head):
    findings = []
    with tempfile.TemporaryDirectory() as tmp:
        old, new = Path(tmp, "old"), Path(tmp, "new")
        checkout(repo, base, old)
        checkout(repo, head, new)
        funcs = changed_functions(old, new, changed_files(repo, base, head))

        # Rung 2: tests that passed before and fail now
        t_old, t_new = run_tests(old), run_tests(new)
        if t_old and t_new:
            for test in sorted(t_old[0] & t_new[1]):
                findings.append({
                    "tier": "Tool-confirmed",
                    "title": f"{test} passed before and fails now",
                    "file": test.split("::")[0],
                    "rows": [], "test_code": ""})

        # Rung 1 or 3: behavior differences between old and new functions
        for f, name in funcs:
            a, b = run_func(old, f, name), run_func(new, f, name)
            diffs = {k: (a[k], b.get(k)) for k in a if a[k] != b.get(k)}
            if not diffs:
                continue
            rows, code = prove(old, new, f, name, diffs)
            if rows:
                findings.append({"tier": "Proven", "file": f, "func": name,
                                "title": f"{name}() changed behavior",
                                "rows": rows, "test_code": code})
            else:
                rows = [{"input": k, "old": o, "new": str(n)}
                        for k, (o, n) in list(diffs.items())[:MAX_PROOFS]]
                findings.append({"tier": "Hypothesis", "file": f,
                                 "title": f"{name}() behaves differently",
                                 "rows": rows, "test_code": ""})

    findings.sort(key=lambda x: TIER_ORDER[x["tier"]])
    for i, item in enumerate(findings):
        item["id"] = f"f{i}"
        item["severity"] = SEVERITY[item["tier"]]
        item["confidence"] = CONFIDENCE[item["tier"]]
    return findings


def trust_score(findings, dismissed=()):
    score = 100
    for item in findings:
        if item["id"] not in dismissed:
            score -= PENALTY[item["tier"]]
    return max(score, 0)

def revert_text(text, old_srcs):
    """Put the old version of the named functions back into a file's text."""
    tree = ast.parse(text)
    lines = text.split("\n")
    spans = sorted(
        ((n.lineno, n.end_lineno, n.name) for n in tree.body
         if isinstance(n, ast.FunctionDef) and n.name in old_srcs),
        reverse=True)
    for start, end, name in spans:
        lines[start - 1:end] = old_srcs[name].split("\n")
    return "\n".join(lines)


def judge(before, after):
    """Compare test results before and after a fix."""
    if before is None or after is None:
        return {"status": "Could not verify", "note": "The tests timed out."}
    bp, bf = before
    ap, af = after
    broke = sorted(bp & af)
    healed = sorted(bf & ap)
    proof_left = [t for t in af if t.startswith(PROOF_FILE)]
    others = sorted(t for t in af if not t.startswith(PROOF_FILE))
    out = {"before": (len(bp), len(bf)), "after": (len(ap), len(af))}
    if broke:
        out.update(status="Failed", note="The fix broke: " + ", ".join(broke))
    elif proof_left:
        out.update(status="Failed", note="The generated test still fails after this fix.")
    elif healed:
        note = "These tests now pass: " + ", ".join(healed) + "."
        if others:
            note += f" {len(others)} other test(s) still fail because of other findings."
        out.update(status="Passed", note=note)
    else:
        out.update(status="Could not verify",
                   note="No test changed result, so nothing was proven either way.")
    return out


def verify_fix(repo, base, head, file, func, proof_code=""):
    """Try a fix in a throwaway copy and re-run the tests. Your real branch is never touched.
    The fix is simple: put the old version of the function back."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp, "old"), Path(tmp, "new")
            checkout(repo, base, old)
            checkout(repo, head, new)
            old_funcs = functions(old / file)
            new_text = (new / file).read_text()
            changed = [n for _, n in changed_functions(old, new, [file])]
            if func not in changed:
                return {"status": "Could not verify",
                        "note": "Couldn't find that function in both versions."}
            if proof_code:
                (new / PROOF_FILE).write_text(proof_code)
            before = run_tests(new)

            attempts = [(f"{func}() only", [func])]
            if len(changed) > 1:
                attempts.append((f"all {len(changed)} changed functions in {file}", changed))
            result, first_failed = None, False
            for label, names in attempts:
                fixed = revert_text(new_text, {n: old_funcs[n] for n in names})
                (new / file).write_text(fixed)
                for c in list(new.rglob("__pycache__")):
                    shutil.rmtree(c, ignore_errors=True)
                result = judge(before, run_tests(new))
                result["scope"] = label
                result["diff"] = "".join(difflib.unified_diff(
                    new_text.splitlines(True), fixed.splitlines(True),
                    fromfile=f"a/{file}", tofile=f"b/{file}"))
                if result["status"] == "Passed":
                    if first_failed:
                        result["note"] = (f"Reverting {func}() alone wasn't enough, so this fix "
                                          "puts back every changed function in the file. "
                                          + result["note"])
                    break
                first_failed = True
            return result
    except Exception as e:
        return {"status": "Could not verify", "note": f"Something went wrong: {e}"}
if __name__ == "__main__":
    repo, base, head = sys.argv[1:4]
    results = analyze(repo, base, head)
    for item in results:
        print(f"[{item['tier']}] {item['title']}")
        for r in item["rows"]:
            print(f"      input {r['input']}: old={r['old']}  new={r['new']}")
    print("Trust score:", trust_score(results), "/ 100")