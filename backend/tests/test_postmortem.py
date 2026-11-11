import json

from app.pipeline import analyze
from app.postmortem import blameless, build_postmortem, to_markdown


class FakeLLM:
    def __init__(self, reply):
        self.reply, self.prompts = reply, []

    def complete(self, system, user):
        self.prompts.append(user)
        return self.reply(user)


def test_template_postmortem_every_sentence_has_evidence(demo_runs):
    st, inc, _ = demo_runs["bad_deploy"]
    pm = build_postmortem(st, analyze(st, inc), "Bad deploy")
    assert pm.engine == "template" and pm.unverified == 0
    assert all(pm.sections[k] for k in ("summary", "impact", "timeline", "trigger_candidates", "follow_ups"))
    md = to_markdown(pm)
    assert md.startswith("# Postmortem: Bad deploy") and "evidence:" in md


def test_llm_ids_are_verified_and_bad_ones_marked(demo_runs):
    st, inc, _ = demo_runs["bad_deploy"]
    a = analyze(st, inc)
    real = a.key_moments[0].event_ids[0]

    def reply(user):
        assert "fetched order" not in user  # only key moments are sent, never raw logs
        return json.dumps({"sections": {
            "summary": [{"text": "Orders failed after a release.", "evidence": [real]}],
            "impact": [{"text": "Made up claim.", "evidence": ["l_does_not_exist"]}],
            "timeline": [{"text": "No evidence at all.", "evidence": []}],
        }})

    llm = FakeLLM(reply)
    pm = build_postmortem(st, a, "x", client=llm)
    assert pm.engine == "llm"
    assert pm.sections["summary"][0].verified
    assert not pm.sections["impact"][0].verified and pm.sections["impact"][0].evidence == []
    assert pm.unverified == 2
    assert "(unverified: no evidence)" in to_markdown(pm)


def test_bad_llm_reply_falls_back_to_template(demo_runs):
    st, inc, _ = demo_runs["disk_full"]
    pm = build_postmortem(st, analyze(st, inc), "x", client=FakeLLM(lambda u: "I cannot do that"))
    assert pm.engine == "template" and pm.unverified == 0


def test_blameless_wording():
    out = blameless("Rahul Nair pushed the bad config. It was Rahul's mistake.", {"Rahul Nair", "Rahul"})
    assert "Rahul" not in out and "an engineer" in out and "mistake" not in out


def test_pinned_events_appear_in_timeline(demo_runs):
    from app.models import Annotation

    st, inc, _ = demo_runs["slow_dependency"]
    chat = st.query(inc, source="chat")[-1]
    st.annotate(inc, Annotation(event_id=chat.id, pinned=True, note="confirmed with provider"))
    pm = build_postmortem(st, analyze(st, inc), "x")
    pinned = [s for s in pm.sections["timeline"] if chat.id in s.evidence]
    assert pinned and "confirmed with provider" in pinned[0].text
