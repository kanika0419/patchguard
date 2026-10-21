import ast, io, json, re, subprocess, sys, tarfile, tempfile
from pathlib import Path

RUNNER = Path(__file__).with_name("runner.py")


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


def run_tests(folder):
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "--tb=no", "-rA", "-p", "no:cacheprovider"],
            cwd=folder, capture_output=True, text=True, timeout=60)
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


def analyze(repo, base, head):
    findings = []
    with tempfile.TemporaryDirectory() as tmp:
        old, new = Path(tmp, "old"), Path(tmp, "new")
        checkout(repo, base, old)
        checkout(repo, head, new)
        funcs = changed_functions(old, new, changed_files(repo, base, head))

        t_old, t_new = run_tests(old), run_tests(new)
        if t_old and t_new:
            for test in sorted(t_old[0] & t_new[1]):  # passed before, fails now
                findings.append({"tier": "Tool-confirmed",
                                 "title": f"{test} passed before and fails now"})

        for f, name in funcs:
            a, b = run_func(old, f, name), run_func(new, f, name)
            diffs = {k: (a[k], b.get(k)) for k in a if a[k] != b.get(k)}
            if diffs:
                findings.append({"tier": "Hypothesis", "file": f,
                                 "title": f"{name}() behaves differently", "diffs": diffs})
    return findings


if __name__ == "__main__":
    repo, base, head = sys.argv[1:4]
    for item in analyze(repo, base, head):
        print(f"[{item['tier']}] {item['title']}")
        for inp, (old_out, new_out) in list(item.get("diffs", {}).items())[:6]:
            print(f"      input {inp}: old={old_out}  new={new_out}")
            