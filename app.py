import streamlit as st
from patchguard.engine import analyze, trust_score, PENALTY

st.set_page_config(page_title="PatchGuard", page_icon="🛡️", layout="wide")
st.title("PatchGuard")
st.caption("Don't trust the patch. Make it prove itself.")

COLORS = {"Proven": "#0E8F66", "Tool-confirmed": "#2F6FDE", "Hypothesis": "#6B7685"}

with st.sidebar:
    st.header("What to review")
    repo = st.text_input("Repo folder", "sample_repo")
    base = st.text_input("Base branch (before)", "main")
    head = st.text_input("Changed branch (after)", "ai-change")
    go = st.button("Analyze", type="primary")

if go:
    for key in [k for k in st.session_state if k.startswith("dismiss_")]:
        del st.session_state[key]
    try:
        with st.spinner("Running tests and comparing old vs new..."):
            st.session_state["findings"] = analyze(repo, base, head)
    except Exception as e:
        st.session_state.pop("findings", None)
        st.error(f"Couldn't analyze that repo. Check the folder and branch names. Details: {e}")

findings = st.session_state.get("findings")
if findings is None:
    st.info("Pick a repo and two branches on the left, then press Analyze.")
    st.stop()
if not findings:
    st.success("No behavior differences found between these two branches.")
    st.stop()

dismissed = {f["id"] for f in findings if st.session_state.get("dismiss_" + f["id"], False)}
score = trust_score(findings, dismissed)

left, right = st.columns([1, 2])
left.metric("Trust score", f"{score} / 100")
with right:
    with st.expander("How is the score calculated?"):
        st.write("Start at 100. Subtract points for every finding you haven't dismissed:")
        for tier, pts in PENALTY.items():
            st.write(f"- {tier}: -{pts}")
        st.caption("A simple rule of thumb, not a certified measure of code quality.")

st.divider()

for f in findings:
    with st.container(border=True):
        color = COLORS[f["tier"]]
        st.markdown(
            f'<span style="background:{color};color:white;padding:3px 12px;'
            f'border-radius:12px;font-weight:600">{f["tier"]}</span> &nbsp; **{f["title"]}**',
            unsafe_allow_html=True)
        st.caption(f'File: {f["file"]}  |  Severity: {f["severity"]}  |  Confidence: {f["confidence"]}')

        if f["rows"]:
            st.table([{"Input": r["input"], "Old code": r["old"], "New code": r["new"]}
                      for r in f["rows"]])
        if f["test_code"]:
            with st.expander("Show the generated test (passes on old code, fails on new)"):
                st.code(f["test_code"], language="python")

        if f["tier"] == "Hypothesis":
            st.caption("Unverified suspicion. A person should check this.")
        st.checkbox("Dismiss as false positive", key="dismiss_" + f["id"])

st.caption("Results come from actually running the code, not from a language model. "
           "Python projects only.")