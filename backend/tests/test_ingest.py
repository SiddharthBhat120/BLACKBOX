import io
import json
import zipfile

from app.ingest import AlertIngester, ChatIngester, DeployIngester, LogIngester
from tests.conftest import FIX


def test_json_logs_with_bad_lines_counted():
    b = LogIngester().parse((FIX / "app.jsonl").read_bytes(), "inc", name="checkout.jsonl")
    assert len(b.events) == 4
    assert b.bad == 3 and len(b.errors) == 3
    svc = [e.service for e in b.events]
    assert svc == ["orders", "orders", "payments", "checkout"]  # last one falls back to the file name
    assert [e.severity for e in b.events] == ["info", "warning", "error", "info"]
    assert b.events[1].ts.hour == 9  # +05:30 converted


def test_plain_text_formats():
    b = LogIngester().parse((FIX / "plain.log").read_text(), "inc", name="plain.log")
    assert [(e.service, e.severity) for e in b.events] == [
        ("gateway", "error"), ("orders", "warning"), ("payments", "info"), ("orders", "critical")]
    assert b.bad == 1


def test_ingesting_twice_gives_same_ids():
    a = LogIngester().parse((FIX / "plain.log").read_text(), "inc", name="plain.log")
    b = LogIngester().parse((FIX / "plain.log").read_text(), "inc", name="plain.log")
    assert [e.id for e in a.events] == [e.id for e in b.events]


def test_alertmanager_webhook():
    b = AlertIngester().parse((FIX / "alertmanager.json").read_bytes(), "inc")
    assert len(b.events) == 2 and b.bad == 1
    fire, res = b.events
    assert fire.tags[0] == "firing" and fire.severity == "critical" and fire.service == "orders"
    assert res.tags[0] == "resolved" and res.ts.minute == 10  # resolved events use endsAt
    assert AlertIngester().parse(b"{not json", "inc").bad == 1


def test_deploys_manual_and_github():
    d = DeployIngester()
    manual = d.parse((FIX / "deploys.json").read_bytes(), "inc")
    assert len(manual.events) == 2 and manual.bad == 1
    assert manual.events[1].tags == ["config"] and manual.events[1].ts.hour == 9
    gh = d.parse((FIX / "github_deployment_status.json").read_bytes(), "inc", github_event="deployment_status")
    assert gh.events[0].service == "orders" and "v1.8.0" in gh.events[0].message
    push = d.parse((FIX / "github_push.json").read_bytes(), "inc", github_event="push")
    assert len(push.events) == 1 and push.bad == 1
    assert push.events[0].message.endswith("Lower provider timeout")


def test_slack_export_folder_and_zip():
    folder = ChatIngester().parse(FIX / "slack", "inc")
    assert len(folder.events) == 3 and folder.bad == 1  # empty text skipped, missing ts counted
    bot = [e for e in folder.events if "bot" in e.tags]
    assert len(bot) == 1
    human = [e for e in folder.events if "human" in e.tags][0]
    assert human.raw["user"] == "Asha Rao" and human.raw["role"] == "SRE on call"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for p in (FIX / "slack").rglob("*.json"):
            z.write(p, f"export/{p.relative_to(FIX / 'slack')}")
    zipped = ChatIngester().parse(buf.getvalue(), "inc")
    assert [e.id for e in zipped.events] == [e.id for e in folder.events]
    assert ChatIngester().parse(b"not a zip", "inc").bad == 1
