import shutil, subprocess
from pathlib import Path

ROOT = Path(__file__).parent / "sample_repo"


def git(*args):
    subprocess.run(
        ["git", "-c", "user.name=dev", "-c", "user.email=dev@example.com", *args],
        cwd=ROOT, check=True,
    )


BASE = '''def bulk_discount(qty):
    """10% off when buying MORE than 10 items."""
    if qty > 10:
        return 0.10
    return 0.0


def apply_discount(price, rate):
    return round(price * (1 - rate), 2)


def total(price, qty):
    rate = bulk_discount(qty)
    return apply_discount(price * qty, rate)
'''

# Bug 1: > became >= (no test covers qty == 10)
# Bug 2: discount applied twice (an existing test catches it)
AI = '''def bulk_discount(qty):
    """10% off for bulk orders."""
    if qty >= 10:
        return 0.10
    return 0.0


def apply_discount(price, rate):
    return round(price * (1 - rate), 2)


def total(price, qty):
    rate = bulk_discount(qty)
    return apply_discount(apply_discount(price * qty, rate), rate)
'''

TESTS = '''from pricing import bulk_discount, total


def test_no_discount_small():
    assert bulk_discount(5) == 0.0


def test_discount_large():
    assert bulk_discount(20) == 0.10


def test_total_small():
    assert total(100, 2) == 200.0


def test_total_large():
    assert total(10, 20) == 180.0
'''

if ROOT.exists():
    shutil.rmtree(ROOT, ignore_errors=True)
ROOT.mkdir()
git("init")
(ROOT / "pricing.py").write_text(BASE)
(ROOT / "test_pricing.py").write_text(TESTS)
git("add", ".")
git("commit", "-m", "initial pricing module")
git("branch", "-M", "main")
git("checkout", "-b", "ai-change")
(ROOT / "pricing.py").write_text(AI)
git("commit", "-am", "AI refactor of pricing")
print("sample_repo ready: compare main -> ai-change")
