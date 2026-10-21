import os, subprocess, sys
from pathlib import Path

import streamlit as st

from patchguard.engine import analyze, trust_score, verify_fix, PENALTY

HERE = Path(__file__).parent.resolve()
COLORS = {"Proven": "#0E8F66", "Tool-confirmed": "#2F6FDE", "Hypothesis": "#6B7685"}
FIX_COLORS = {"Passed": "#0E8F66", "Failed": "#D63B40", "Could not verify": "#C98410"}
WHY = {
    "Proven": "A generated test passes on the old code and fails on the new code. "
              "This is a real change in behavior, not a guess.",
    "Tool-confirmed": "Your own test suite caught this: the test passed before the change and fails now.",
    "Hypothesis": "The behavior differs, but nothing proved it is wrong. A person should take a look.",
}

st.set_page_config(page_title="PatchGuard", page_icon="🛡️", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 2rem; max-width: 1100px;}
.hero h1 {font-size: 2.6rem; margin-bottom: 0;}
.hero p {font-size: 1.15rem; color: #C98410; font-style: italic; margin-top: .2rem;}
.pill {display:inline-block; color:white; padding:3px 12px; border-radius:999px;
       font-weight:600; font-size:.85rem;}
.stat, .step {border-radius:12px; padding:16px 18px; background: rgba(128,128,128,.12);}
.stat .label {font-size:.85rem; opacity:.7;}
.stat .value {font-size:2.2rem; font-weight:700; line-height:1.1;}
</style>
""", unsafe_allow_html=True)


def pill(text, color):
    return f'<span class="pill" style="background:{color}">{text}</span>'


def stat(label, value, color=None):
    style = f' style="color:{color}"' if color else ""
    return (f'<div class="stat"><div class="label">{label}</div>'
            f'<div class="value"{style}>{value}</div></div>')


def ensure_sample():
    if not (HERE / "sample_repo").exists():
        subprocess.run([sys.executable, str(HERE / "make_sample.py")],
                       check=True, capture_output=True)


def resolve_repo(text):
    p = (HERE / text).resolve()
    if not os.environ.get("PATCHGUARD_ALLOW_ANY_PATH") and HERE not in [p, *p.parents]:
        raise ValueError("For safety, only repos inside this project folder can be analyzed.")
    return str(p)


st.markdown('<div class="hero"><h1>🛡️ PatchGuard</h1>'
            "<p>Don't trust the patch. Make it prove itself.</p></div>", unsafe_allow_html=True)

with st.sidebar:
    st.header("Review a change")
    st.caption("Compares two branches of a Python repo.")
    with st.expander("Settings"):
        repo_text = st.text_input("Repo folder", "sample_repo")
        base = st.text_input("Before (branch)", "main")
        head = st.text_input("After (branch)", "ai-change")
    go = st.button("Analyze", type="primary")

if go:
    for k in [k for k in st.session_state if k.startswith(("dismiss_", "result_"))]:
        del st.session_state[k]
    try:
        if repo_text.strip() == "sample_repo":
            ensure_sample()
        repo = resolve_repo(repo_text)
        with st.spinner("Running tests and comparing old vs new code..."):
            st.session_state["findings"] = analyze(repo, base, head)
            st.session_state["ctx"] = (repo, base, head)
    except Exception as e:
        st.session_state.pop("findings", None)
        st.error(f"Couldn't analyze that repo. Check the folder and branch names. Details: {e}")

findings = st.session_state.get("findings")
if findings is None:
    c1, c2, c3 = st.columns(3)
    c1.markdown('<div class="step"><b>1. Read the change</b><br>Finds the Python functions '
                'that an AI change touched.</div>', unsafe_allow_html=True)
    c2.markdown('<div class="step"><b>2. Make it prove itself</b><br>Runs your tests and '
                'compares old vs new behavior.</div>', unsafe_allow_html=True)
    c3.markdown('<div class="step"><b>3. Test the fix</b><br>Tries a fix in a throwaway copy '
                'and re-runs the tests.</div>', unsafe_allow_html=True)
    st.info("Press **Analyze** in the sidebar to review the sample pull request.")
    st.stop()
if not findings:
    st.success("No behavior differences found between these two branches.")
    st.stop()

dismissed = {f["id"] for f in findings if st.session_state.get("dismiss_" + f["id"], False)}
score = trust_score(findings, dismissed)
score_color = "#0E8F66" if score >= 80 else "#C98410" if score >= 50 else "#D63B40"
counts = {t: sum(1 for f in findings if f["tier"] == t) for t in COLORS}

cols = st.columns(4)
cols[0].markdown(stat("Trust score", f"{score} / 100", score_color), unsafe_allow_html=True)
for col, tier in zip(cols[1:], COLORS):
    col.markdown(stat(tier, counts[tier], COLORS[tier]), unsafe_allow_html=True)

with st.expander("How is the trust score calculated?"):
    st.write("Start at 100, then subtract points for every finding you haven't dismissed:")
    for tier, pts in PENALTY.items():
        st.write(f"- {tier}: -{pts}")
    st.caption("A simple rule of thumb, not a certified measure of code quality.")

st.write("")
shown = st.multiselect("Show findings of type", list(COLORS), default=list(COLORS))

for f in findings:
    if f["tier"] not in shown:
        continue
    with st.container(border=True):
        st.markdown(f'{pill(f["tier"], COLORS[f["tier"]])} &nbsp; **{f["title"]}**',
                    unsafe_allow_html=True)
        st.caption(f'{f["file"]}  ·  Severity: {f["severity"]}  ·  Confidence: {f["confidence"]}')
        st.write(WHY[f["tier"]])
        if f["rows"]:
            st.table([{"Input": r["input"], "Old code": r["old"], "New code": r["new"]}
                      for r in f["rows"]])
        if f["test_code"]:
            with st.expander("Show the generated test (passes on old code, fails on new)"):
                st.code(f["test_code"], language="python")

        left, right = st.columns([1, 2])
        left.checkbox("Dismiss as false positive", key="dismiss_" + f["id"])
        if f["id"] in dismissed:
            right.caption("Dismissed. Not counted in the score.")

        if f.get("func"):
            if right.button("Verify a fix", key="btn_" + f["id"]):
                with st.spinner("Trying a fix in a throwaway copy..."):
                    st.session_state["result_" + f["id"]] = verify_fix(
                        *st.session_state["ctx"], f["file"], f["func"], f["test_code"])
            res = st.session_state.get("result_" + f["id"])
            if res:
                st.markdown(pill("Fix: " + res["status"], FIX_COLORS[res["status"]]),
                            unsafe_allow_html=True)
                if res.get("scope"):
                    st.caption(f'Tried: put back the old version of {res["scope"]}')
                if res.get("before"):
                    st.write(f'Tests before the fix: **{res["before"][0]} passed, '
                             f'{res["before"][1]} failed**  →  after: '
                             f'**{res["after"][0]} passed, {res["after"][1]} failed**')
                st.write(res["note"])
                if res.get("diff"):
                    st.code(res["diff"], language="diff")
                    st.download_button("Download patch", res["diff"],
                                       file_name=f'{f["func"]}_fix.patch',
                                       key="dl_" + f["id"])

st.caption("Your real branch is never touched: fixes run in a throwaway copy and come back as a patch. "
           "Results come from running the code, not from a language model. Python projects only.")