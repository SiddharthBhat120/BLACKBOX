# BlackBox: demo script, resume bullets, interview prep

## 3-minute demo script

1. **Problem (20 s).** "After an outage, someone spends hours scrolling logs and Slack to rebuild what happened. BlackBox does that first pass in seconds and shows its evidence."
2. **Break something (20 s).** Pick *Disk fills up*, press **Inject the fault and record**. "This runs a small shop for 70 minutes, injects a real fault, and adds two harmless decoy deploys so the answer is not obvious."
3. **Readouts (20 s).** "Started 12:48 in payments, detected two minutes later by an alert, mitigated after ten minutes, recovered after thirteen. And 37,000 log lines became 22 timeline items."
4. **Timeline (40 s).** Point at the error chart and the lanes. Click the *write failed: No space left on device* pattern: "463 lines, zero in the baseline, so it is new." Toggle *Show unchanged patterns* to show the noise that was hidden.
5. **Trigger (40 s).** "The top candidate is a payments deploy 20 minutes earlier that turned on DEBUG logging. It wins over a gateway deploy that was closer in time, because errors started in payments and payments logged new debug lines right after that deploy. The gateway change left no trace of its own." Open the answer key: correct.
6. **Postmortem (30 s).** Open the draft. Click an evidence chip: it jumps back to the event on the timeline. "Every sentence cites event ids that are checked. Names from chat are replaced: blameless by design."
7. **Numbers (10 s).** "Across 50 injected incidents it ranked the real trigger first 49 times, and the one miss is documented."

## Resume bullets

- Built **BlackBox**, an incident-timeline tool (FastAPI, SQLite, Drain3, React) that ingests logs, Alertmanager alerts, deploy/GitHub events and Slack exports into one UTC event model and drafts an evidence-linked, blameless postmortem.
- Reduced **~34,000 log lines to ~23 timeline items** per incident with Drain template mining and z-score anomaly detection; identified the triggering change first in **49 of 50 injected incidents** (29 of 30 held-out) with two decoy deploys per run.
- Built a chaos simulator (bad deploy, DB pool exhaustion, slow provider, bad config, disk full) with recorded ground truth and an evaluation harness measuring trigger rank, time-to-detect/recover error (under 1 min) and postmortem evidence coverage (100%).

## 10 interview questions with answers

**1. What is a postmortem, and why "blameless"?**
A written review after an incident: what happened, the impact, the timeline, why it happened, and what we will change. Blameless means we describe systems and decisions, not people's mistakes. If people fear blame, they hide details, and the organisation stops learning. BlackBox writes in that style and replaces names from chat with roles.

**2. How does log template mining (Drain) work?**
Drain groups log lines by shape. It first splits by number of words, then walks a shallow tree keyed on the first words, then compares the line with existing groups word by word. If enough words match, the line joins the group and the differing words become wildcards. I also mask ids, numbers and hex values first, so `fetched order o-123 in 31ms` and `fetched order o-987 in 29ms` become `fetched order <ID> in <NUM>ms`.

**3. Correlation vs causation: how does your tool avoid claiming too much?**
It never says "root cause". It ranks candidates and shows why: minutes before the first error, whether the change touched the service that failed first, and whether that service behaved differently afterwards. An "outside cause" candidate is always present. The postmortem says the top candidate is confirmed only if reverting it stopped the errors.

**4. Why did the disk-full deploy win even though a decoy was closer in time?**
Time is only one factor. The real trigger changed payments, where errors started, and payments logged new DEBUG lines right after it. The decoy changed gateway, whose only new behaviour was errors that are explained by payments failing below it. I give a change behaviour credit only for patterns that are not just echoes of a failing dependency.

**5. What problems do timestamps and time zones cause across services?**
Different formats (ISO, epoch seconds, epoch milliseconds, `09:00:01,123`), missing zones, local times like +05:30, clock skew between hosts, and events arriving out of order. I parse everything to timezone-aware UTC at ingestion, read zone-less times in a configured zone, store epoch seconds so sorting is exact, and always query sorted by time. Tests cover +05:30, +0530, epoch ms and late-arriving events.

**6. How do you find the first failing service?**
For each service I take the earliest event of a new or increased error pattern (exact timestamps), falling back to minute-level spikes. Callers often fail milliseconds after their callee, so if two services fail within 5 seconds and one calls the other, I pick the callee. A bug where latency spikes used the first log line of the minute made gateway look first; fixing it raised accuracy on that scenario.

**7. How do you detect spikes without a trained model?**
Per service per minute I compute error rate and p95 latency. The baseline is the quiet period before the incident. A spike needs a z-score above 4, a minimum real size (2% errors, or 1.5x baseline latency), and two minutes in a row. It is simple, explainable, and every moment has a reason string such as "35% errors, z-score 82".

**8. How do you stop the LLM from inventing facts in the postmortem?**
It only sees key moments and pinned events, each with its event ids, never raw logs. It must return strict JSON where each sentence lists its evidence ids. My code checks every id exists in the incident; sentences with none are marked unverified, and invalid ids are removed. If the JSON is broken, the template draft is used.

**9. What are time-to-detect and time-to-recover, and why track them?**
TTD is from the first errors to the first alert or human report; TTR is from the first errors to normal service. They measure how good monitoring and response are. Teams try to push both down over time, and they are good follow-up targets: here, "errors ran 2 minutes before an alert" becomes a concrete action.

**10. How would this scale to millions of log lines per minute?**
Mine templates at the edge or per shard so only template ids and counts travel; pre-aggregate per-minute metrics in a stream processor; store events in a column store like ClickHouse partitioned by time; keep raw lines in cheap object storage and fetch samples on demand. The analysis itself works on aggregates and key moments, so it stays fast.
