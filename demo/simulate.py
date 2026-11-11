"""Chaos simulator: runs a small shop (gateway -> orders -> payments -> database) on a simulated
clock, injects a real incident, and exports what an on-call engineer would have:

  logs.jsonl      JSON logs from orders and payments
  gateway.log     plain-text access logs from the gateway
  alerts.json     Alertmanager-style webhook payloads (firing and resolved)
  deploys.json    deploys, config changes and rollbacks (simple manual format)
  chat/           a Slack export folder (users.json, channels.json, incidents/<date>.json)
  ground_truth.json   what really happened (trigger, root service, start/detect/mitigate/recover)

Every request is simulated: the fault changes how a service behaves (errors, latency, extra
logs), and alerts fire from the resulting error rates, exactly as a rule in Prometheus would.
Decoy deploys to unrelated services are added so that trigger ranking is not trivial.

    python demo/simulate.py --scenario bad_deploy --seed 7 --out demo/output/bad_deploy
"""
from __future__ import annotations

import argparse
import json
import math
import random
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCENARIOS = {
    "bad_deploy": "A new orders release turns on a feature flag with a bug in the promotions code.",
    "db_pool_exhausted": "A config change shrinks the orders database connection pool; requests queue and fail.",
    "slow_dependency": "The external payment provider slows down. Nothing inside the company changed.",
    "config_change": "A payments config change lowers the provider timeout from 3000 ms to 250 ms.",
    "disk_full": "A payments release turns on DEBUG logging; twenty minutes later the disk fills up.",
}
SERVICES = ["gateway", "orders", "payments"]
DURATION = 70 * 60
USERS = {
    "U01ASHA": ("asha", "Asha", "SRE on call"),
    "U02RAHUL": ("rahul", "Rahul", "Backend engineer"),
    "U03MEERA": ("meera", "Meera", "Payments engineer"),
    "U04KIRAN": ("kiran", "Kiran", "Support lead"),
}
DECOYS = [
    ("gateway", "deploy", "v4.2.{n}", "Update API docs and response headers"),
    ("frontend", "deploy", "v12.{n}.0", "New banner on the home page"),
    ("search", "deploy", "v2.{n}.1", "Tune search ranking weights"),
    ("notifications", "config", "EMAIL_BATCH_SIZE", "Raise email batch size 100 -> 200"),
    ("gateway", "config", "RATE_LIMIT_RPS", "Raise per-user rate limit 20 -> 25"),
    ("orders", "deploy", "v1.7.{n}", "Refactor order listing query (no behaviour change)"),
]


@dataclass
class Fault:
    kind: str
    start: int
    mitigate: int
    recover: int
    detect: int = 0

    def level(self, t: int) -> float:
        """0 before the fault, 1 at full strength, fading to 0 between mitigation and recovery."""
        if t < self.start:
            return 0.0
        if t < self.start + 60:
            return (t - self.start + 1) / 60  # ramps up over a minute
        if t < self.mitigate:
            return 1.0
        if t < self.recover:
            return 1.0 - (t - self.mitigate) / max(1, self.recover - self.mitigate)
        return 0.0


@dataclass
class Sim:
    scenario: str
    seed: int
    t0: datetime
    rng: random.Random
    logs: list[dict] = field(default_factory=list)
    gateway_lines: list[str] = field(default_factory=list)
    alerts: list[dict] = field(default_factory=list)
    deploys: list[dict] = field(default_factory=list)
    chat: list[dict] = field(default_factory=list)
    minute_stats: dict[tuple[str, int], list[float]] = field(default_factory=dict)  # (service, minute) -> [reqs, errors, lat...]
    order_no: int = 41000

    def ts(self, t: float) -> datetime:
        return self.t0 + timedelta(seconds=t)

    def iso(self, t: float) -> str:
        return self.ts(t).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    def log(self, t: float, service: str, level: str, msg: str, **extra) -> None:
        self.logs.append({"ts": self.iso(t), "level": level, "service": service, "msg": msg, **extra})

    def record(self, service: str, t: float, ok: bool, ms: float) -> None:
        s = self.minute_stats.setdefault((service, int(t // 60)), [0, 0])
        s[0] += 1
        s[1] += 0 if ok else 1
        s.append(ms)


def _lat(rng: random.Random, median: float, spread: float = 0.35) -> float:
    return max(2.0, rng.lognormvariate(math.log(median), spread))


def build_plan(scenario: str, rng: random.Random) -> tuple[Fault, dict | None, str]:
    """Pick fault timing and the change that triggers it. Returns (fault, trigger change, root service)."""
    start = 1500 + rng.randint(-150, 150)
    mitigate = start + rng.randint(420, 780)
    recover = mitigate + rng.randint(90, 240)
    fault = Fault(scenario, start, mitigate, recover)
    if scenario == "bad_deploy":
        trig = {"service": "orders", "kind": "deploy", "version": "v1.8.0",
                "description": "Enable promo_engine_v2 flag; new discount code handling", "t": start - rng.randint(30, 100)}
        return fault, trig, "orders"
    if scenario == "db_pool_exhausted":
        trig = {"service": "orders", "kind": "config", "version": "DB_POOL_MAX",
                "description": "Lower DB_POOL_MAX 50 -> 8 to save database memory", "t": start - rng.randint(40, 140)}
        return fault, trig, "orders"
    if scenario == "config_change":
        trig = {"service": "payments", "kind": "config", "version": "PROVIDER_TIMEOUT_MS",
                "description": "Lower PROVIDER_TIMEOUT_MS 3000 -> 250", "t": start - rng.randint(10, 60)}
        return fault, trig, "payments"
    if scenario == "disk_full":
        trig = {"service": "payments", "kind": "deploy", "version": "v3.2.0",
                "description": "Set LOG_LEVEL=DEBUG to investigate a refund bug", "t": start - rng.randint(1100, 1300)}
        return fault, trig, "payments"
    if scenario == "slow_dependency":
        return fault, None, "payments"
    raise ValueError(f"unknown scenario {scenario}")


def simulate(scenario: str, seed: int, out: Path) -> dict:
    rng = random.Random(f"{scenario}-{seed}")
    t0 = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc) + timedelta(minutes=rng.randint(0, 600))
    sim = Sim(scenario, seed, t0, rng)
    fault, trigger, root = build_plan(scenario, rng)

    # ---- changes: decoys first, then the real trigger, then the mitigation ----
    changes: list[dict] = []
    for i, (svc, kind, ver, desc) in enumerate(rng.sample(DECOYS, 2)):
        lo, hi = (fault.start - 1500, fault.start - 200) if i == 0 else (fault.start - 600, fault.start - 20)
        changes.append({"service": svc, "kind": kind, "version": ver.format(n=rng.randint(1, 9)),
                        "description": desc, "t": rng.randint(max(60, lo), hi), "decoy": True})
    if trigger:
        changes.append({**trigger, "decoy": False})
    mitigation_change = {
        "bad_deploy": {"service": "orders", "kind": "rollback", "version": "v1.7.3", "description": "Roll back orders to v1.7.3"},
        "db_pool_exhausted": {"service": "orders", "kind": "config", "version": "DB_POOL_MAX", "description": "Restore DB_POOL_MAX 8 -> 50"},
        "config_change": {"service": "payments", "kind": "config", "version": "PROVIDER_TIMEOUT_MS", "description": "Restore PROVIDER_TIMEOUT_MS 250 -> 3000"},
        "disk_full": {"service": "payments", "kind": "config", "version": "LOG_LEVEL", "description": "Set LOG_LEVEL=INFO and clear old debug logs"},
        "slow_dependency": {"service": "payments", "kind": "config", "version": "PAYMENT_PROVIDER", "description": "Fail over PAYMENT_PROVIDER razorpay-sim -> paybackup-sim"},
    }[scenario]
    changes.append({**mitigation_change, "t": fault.mitigate - rng.randint(5, 25), "decoy": False, "mitigation": True})
    authors = ["deploy-bot", "ci-pipeline", "rahul", "meera", "terraform"]
    for c in sorted(changes, key=lambda c: c["t"]):
        sim.deploys.append({
            "timestamp": sim.iso(c["t"]), "service": c["service"], "kind": c["kind"], "version": c["version"],
            "description": c["description"], "author": rng.choice(authors),
            "commit": f"{rng.getrandbits(28):07x}", "environment": "production",
        })

    debug_on = trigger["t"] if scenario == "disk_full" else None

    # ---- traffic, one simulated second at a time ----
    for t in range(DURATION):
        f = fault.level(t)
        # health checks and background jobs
        if t % 15 == 0:
            for svc in SERVICES[1:]:
                sim.log(t + rng.random(), svc, "INFO", f"health check ok uptime={t + 86400}s")
        if t % 60 == 30:
            sim.log(t, "orders", "INFO", f"cache stats hits={rng.randint(800, 1200)} misses={rng.randint(20, 90)}")
            sim.log(t, "payments", "INFO", f"settlement batch {rng.randint(1000, 9999)} queued items={rng.randint(5, 40)}")
        n_view = _poisson(rng, 2.2)
        n_checkout = _poisson(rng, 1.1)
        for _ in range(n_view):
            _view(sim, t + rng.random(), f, scenario)
        for _ in range(n_checkout):
            _checkout(sim, t + rng.random(), f, scenario, debug_on is not None and t >= debug_on and t < fault.mitigate)

    # ---- alert rules, evaluated every minute like Prometheus ----
    _alerts(sim, fault)
    detect_candidates = [a for a in sim.alerts if a["status"] == "firing"]
    fault.detect = min((_parse(a["alerts"][0]["startsAt"]) - t0).total_seconds() for a in detect_candidates) if detect_candidates else fault.start + 600

    # ---- chat ----
    _chat(sim, fault, scenario, rng)

    # ---- export ----
    out.mkdir(parents=True, exist_ok=True)
    sim.logs.sort(key=lambda r: r["ts"])
    with (out / "logs.jsonl").open("w", encoding="utf-8") as fh:
        for r in sim.logs:
            fh.write(json.dumps(r) + "\n")
        fh.write("{this line is broken on purpose\n")  # ingesters must skip and count bad lines
    (out / "gateway.log").write_text("\n".join(sorted(sim.gateway_lines)) + "\n", encoding="utf-8")
    (out / "alerts.json").write_text(json.dumps(sim.alerts, indent=1), encoding="utf-8")
    (out / "deploys.json").write_text(json.dumps(sim.deploys, indent=1), encoding="utf-8")
    chat_dir = out / "chat"
    (chat_dir / "incidents").mkdir(parents=True, exist_ok=True)
    (chat_dir / "users.json").write_text(json.dumps([
        {"id": uid, "name": n, "real_name": rn, "profile": {"title": title}} for uid, (n, rn, title) in USERS.items()
    ], indent=1), encoding="utf-8")
    (chat_dir / "channels.json").write_text(json.dumps([{"id": "C0INCIDENT", "name": "incidents"}]), encoding="utf-8")
    by_day: dict[str, list[dict]] = {}
    for m in sorted(sim.chat, key=lambda m: float(m["ts"])):
        day = datetime.fromtimestamp(float(m["ts"]), timezone.utc).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(m)
    for day, msgs in by_day.items():
        (chat_dir / "incidents" / f"{day}.json").write_text(json.dumps(msgs, indent=1), encoding="utf-8")

    trigger_rec = None
    if trigger:
        trigger_rec = {"service": trigger["service"], "kind": trigger["kind"], "version": trigger["version"],
                       "timestamp": sim.iso(trigger["t"])}
    truth = {
        "scenario": scenario, "seed": seed, "description": SCENARIOS[scenario],
        "trigger": trigger_rec, "root_service": root,
        "start": sim.iso(fault.start), "detect": sim.iso(fault.detect),
        "mitigation": sim.iso(fault.mitigate), "recovery": sim.iso(fault.recover),
        "decoys": [{"service": c["service"], "version": c["version"], "timestamp": sim.iso(c["t"])} for c in changes if c.get("decoy")],
        "log_lines": len(sim.logs) + len(sim.gateway_lines),
    }
    (out / "ground_truth.json").write_text(json.dumps(truth, indent=1), encoding="utf-8")
    return truth


def _poisson(rng: random.Random, lam: float) -> int:
    k, p, limit = 0, 1.0, math.exp(-lam)
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _gateway(sim: Sim, t: float, method: str, path: str, status: int, ms: float, extra: str = "") -> None:
    level = "ERROR" if status >= 500 else "INFO"
    sim.gateway_lines.append(f"{sim.iso(t)} {level} gateway {method} {path} {status} in {ms:.0f}ms{extra}")
    sim.record("gateway", t, status < 500, ms)


def _view(sim: Sim, t: float, f: float, scenario: str) -> None:
    rng = sim.rng
    oid = f"o-{rng.randint(10000, 99999)}"
    ms = _lat(rng, 28)
    ok = True
    if scenario == "db_pool_exhausted" and rng.random() < 0.45 * f:
        ms = 5000 + rng.random() * 40
        ok = False
        sim.log(t + ms / 1000, "orders", "ERROR", f"could not acquire db connection after {ms:.0f}ms pool_size=8 in_use=8",
                order_id=oid)
    elif scenario == "db_pool_exhausted" and f > 0:
        ms = _lat(rng, 28 + 900 * f)
    if ok:
        sim.log(t + ms / 1000, "orders", "INFO", f"fetched order {oid} in {ms:.0f}ms")
    sim.record("orders", t, ok, ms)
    _gateway(sim, t + ms / 1000 + 0.004, "GET", f"/api/orders/{oid}", 200 if ok else 504, ms + _lat(rng, 4),
             "" if ok else " upstream=orders")


def _checkout(sim: Sim, t: float, f: float, scenario: str, debug: bool) -> None:
    rng = sim.rng
    sim.order_no += 1
    oid = f"o-{sim.order_no}"
    user = f"u-{rng.randint(100, 999)}"
    amount = rng.choice([199, 349, 499, 899, 1299, 2499])
    status, upstream = 200, ""
    # orders
    o_ms = _lat(rng, 45)
    o_ok = True
    if scenario == "bad_deploy" and rng.random() < 0.38 * f:
        o_ok = False
        sim.log(t + o_ms / 1000, "orders", "ERROR", f"KeyError: 'discount_code' in apply_promotions order={oid}",
                order_id=oid, exc="KeyError")
        status, upstream = 500, " upstream=orders"
    elif scenario == "db_pool_exhausted" and rng.random() < 0.45 * f:
        o_ms = 5000 + rng.random() * 40
        o_ok = False
        sim.log(t + o_ms / 1000, "orders", "ERROR", f"could not acquire db connection after {o_ms:.0f}ms pool_size=8 in_use=8",
                order_id=oid)
        status, upstream = 504, " upstream=orders"
    elif scenario == "db_pool_exhausted" and f > 0:
        o_ms = _lat(rng, 45 + 900 * f)
    if o_ok:
        sim.log(t + o_ms / 1000, "orders", "INFO", f"created order {oid} for user {user} total={amount}", order_id=oid)
        # payments
        p_ms = _lat(rng, 180)
        p_ok = True
        if debug:
            sim.log(t + 0.01, "payments", "DEBUG", f"charge request payload order={oid} amount={amount} currency=INR")
            sim.log(t + 0.02, "payments", "DEBUG", f"provider handshake razorpay-sim session=s{rng.getrandbits(24):06x}")
        if scenario == "slow_dependency" and f > 0:
            p_ms = _lat(rng, 180 + 2600 * f)
            if p_ms > 3000:
                p_ok = False
                sim.log(t + 3.0, "payments", "ERROR", "provider timeout after 3000ms provider=razorpay-sim", order_id=oid)
                p_ms = 3000
            elif p_ms > 1200:
                sim.log(t + p_ms / 1000, "payments", "WARNING", f"slow provider response {p_ms:.0f}ms provider=razorpay-sim")
        elif scenario == "config_change" and rng.random() < 0.55 * f:
            p_ok = False
            p_ms = 250
            sim.log(t + 0.25, "payments", "ERROR", "provider timeout after 250ms provider=razorpay-sim", order_id=oid)
        elif scenario == "disk_full" and rng.random() < 0.6 * f:
            p_ok = False
            sim.log(t + p_ms / 1000, "payments", "ERROR",
                    "write failed: [Errno 28] No space left on device path=/var/lib/payments/ledger", order_id=oid)
        if p_ok:
            sim.log(t + p_ms / 1000, "payments", "INFO", f"charged {amount} INR for order {oid} via razorpay-sim in {p_ms:.0f}ms",
                    order_id=oid)
        else:
            sim.log(t + p_ms / 1000 + 0.01, "orders", "ERROR", f"payment call failed status=504 order={oid}", order_id=oid)
            status, upstream = 502, " upstream=payments"
        sim.record("payments", t, p_ok, p_ms)
        o_ms += p_ms
    sim.record("orders", t, o_ok and status == 200, o_ms)
    _gateway(sim, t + o_ms / 1000 + 0.005, "POST", "/api/checkout", status, o_ms + _lat(rng, 6), upstream)


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _alerts(sim: Sim, fault: Fault) -> None:
    minutes = DURATION // 60
    state: dict[tuple[str, str], dict] = {}
    for m in range(minutes):
        for svc in SERVICES:
            s = sim.minute_stats.get((svc, m))
            if not s or s[0] == 0:
                continue
            reqs, errs, lats = s[0], s[1], sorted(s[2:])
            p95 = lats[int(0.95 * (len(lats) - 1))] if lats else 0
            checks = {"HighErrorRate": errs / reqs > 0.05, "HighLatency": p95 > 1500}
            for name, bad in checks.items():
                st = state.setdefault((svc, name), {"bad": 0, "good": 0, "firing": False, "since": None})
                st["bad"], st["good"] = (st["bad"] + 1, 0) if bad else (0, st["good"] + 1)
                t_eval = (m + 1) * 60 + 15
                if not st["firing"] and st["bad"] >= 2:
                    st["firing"], st["since"] = True, t_eval
                    sim.alerts.append(_am(sim, "firing", svc, name, t_eval, None, errs / reqs, p95))
                elif st["firing"] and st["good"] >= 3:
                    st["firing"] = False
                    sim.alerts.append(_am(sim, "resolved", svc, name, st["since"], t_eval, errs / reqs, p95))


def _am(sim: Sim, status: str, svc: str, name: str, start: float, end: float | None, rate: float, p95: float) -> dict:
    summary = (f"{svc} error rate above 5% (now {rate:.0%})" if name == "HighErrorRate"
               else f"{svc} p95 latency above 1500ms (now {p95:.0f}ms)")
    return {
        "version": "4", "status": status, "receiver": "blackbox",
        "groupLabels": {"alertname": name},
        "alerts": [{
            "status": status,
            "labels": {"alertname": name, "service": svc, "severity": "critical" if name == "HighErrorRate" else "warning"},
            "annotations": {"summary": summary},
            "startsAt": sim.iso(start),
            "endsAt": sim.iso(end) if end else "0001-01-01T00:00:00Z",
            "fingerprint": f"{zlib.crc32(f'{svc}/{name}'.encode()):08x}",
        }],
    }


CHAT_SCRIPTS = {
    "bad_deploy": [
        ("U04KIRAN", 0, "customers are reporting checkout errors, anyone looking?"),
        ("U01ASHA", 60, "on it. gateway shows 500s on POST /api/checkout, upstream is orders"),
        ("U02RAHUL", 150, "orders logs have KeyError discount_code in apply_promotions, that is new code"),
        ("U01ASHA", 210, "orders shipped v1.8.0 a little before this started. rolling back to v1.7.3"),
        ("M", 0, "rolled back orders to v1.7.3"),
        ("R", 30, "errors are dropping, checkout looks healthy again"),
        ("R", 240, "all clear. will write the postmortem tomorrow"),
    ],
    "db_pool_exhausted": [
        ("U04KIRAN", 0, "order pages are timing out for many users"),
        ("U01ASHA", 50, "orders p95 is over 5 seconds, looks like it cannot get db connections"),
        ("U02RAHUL", 160, "pool_size=8 in the errors?? it should be 50"),
        ("U01ASHA", 220, "found a config change to DB_POOL_MAX earlier today, restoring it"),
        ("M", 0, "restored DB_POOL_MAX to 50 and restarted orders"),
        ("R", 40, "latency back to normal"),
        ("R", 200, "resolved. we need a check on pool size changes"),
    ],
    "slow_dependency": [
        ("U04KIRAN", 0, "payments are failing for some customers"),
        ("U03MEERA", 80, "payments to razorpay-sim are slow, many 3s timeouts"),
        ("U01ASHA", 170, "nothing deployed to payments recently that matches. their status page says degraded"),
        ("U03MEERA", 260, "switching PAYMENT_PROVIDER to paybackup-sim"),
        ("M", 0, "failed over to the backup provider"),
        ("R", 50, "success rate is recovering"),
        ("R", 220, "resolved. provider incident on their side"),
    ],
    "config_change": [
        ("U04KIRAN", 0, "checkout failing with payment errors"),
        ("U03MEERA", 70, "payments logs: provider timeout after 250ms. our timeout is 3000ms normally"),
        ("U01ASHA", 160, "someone changed PROVIDER_TIMEOUT_MS just before this, reverting"),
        ("M", 0, "restored PROVIDER_TIMEOUT_MS to 3000"),
        ("R", 30, "errors gone"),
        ("R", 200, "resolved. adding validation for timeout values"),
    ],
    "disk_full": [
        ("U04KIRAN", 0, "payments failing, users charged twice?"),
        ("U03MEERA", 90, "payments says No space left on device on the ledger volume"),
        ("U01ASHA", 170, "disk is full of debug logs. LOG_LEVEL was set to DEBUG in v3.2.0"),
        ("M", 0, "set LOG_LEVEL=INFO and cleared old debug logs"),
        ("R", 60, "writes are succeeding again"),
        ("R", 230, "resolved. need disk usage alerts on payments"),
    ],
}


def _chat(sim: Sim, fault: Fault, scenario: str, rng: random.Random) -> None:
    def add(uid: str, t: float, text: str) -> None:
        ts = sim.ts(t).timestamp()
        sim.chat.append({"type": "message", "user": uid, "text": text, "ts": f"{ts:.6f}"})

    # some ordinary chatter before the incident
    add("U02RAHUL", max(30, fault.start - rng.randint(900, 1300)), "deploying the small docs change for gateway after lunch")
    add("U04KIRAN", max(40, fault.start - rng.randint(300, 700)), "quiet morning on support so far")
    # alert bot message
    sim.chat.append({"type": "message", "subtype": "bot_message", "username": "alertmanager", "bot_id": "B0ALERTS",
                     "text": "[FIRING] alert in production", "ts": f"{sim.ts(fault.detect + 5).timestamp():.6f}"})
    t = fault.detect + rng.randint(40, 120)
    for uid, gap, text in CHAT_SCRIPTS[scenario]:
        if uid == "M":
            add("U01ASHA" if scenario not in ("slow_dependency", "config_change", "disk_full") else "U03MEERA",
                fault.mitigate + rng.randint(0, 20), text)
            t = fault.mitigate
            continue
        if uid == "R":
            t = max(t, fault.mitigate) + gap + rng.randint(0, 30)
            add("U01ASHA", t, text)
            continue
        t += gap + rng.randint(0, 20)
        t = min(t, fault.mitigate - 30)
        add(uid, t, text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--scenario", choices=sorted(SCENARIOS), default="bad_deploy")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = args.out or Path(__file__).parent / "output" / f"{args.scenario}-{args.seed}"
    truth = simulate(args.scenario, args.seed, out)
    print(json.dumps(truth, indent=1))
    print(f"\nWrote incident data to {out}")


if __name__ == "__main__":
    main()
