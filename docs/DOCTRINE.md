# How kill chains actually work

A practical primer, written to go with this simulator. It sticks to unclassified,
published doctrine and to the parts that matter if you want to build software.

---

## 1. The thing itself

A **kill chain** is the ordered set of things that must all succeed before a
target is struck. The US joint targeting construct is **F2T2EA**:

| Phase | Question it answers | Typical owner |
|---|---|---|
| **Find** | Is there something out there worth engaging? | ISR sensors, patrols, intel |
| **Fix** | Where exactly is it, precisely enough to act? | Sensor operator, analyst, fusion |
| **Track** | Can I keep knowing that while it moves? | Sensor / track manager |
| **Target** | Am I allowed to, and with what? | Commander, legal, fires |
| **Engage** | Deliver the effect. | Effector crew / mission system |
| **Assess** | Did it work, and do I go again? | ISR + analyst (BDA) |

Every phase is a *handoff* between organisations, systems and authorities. That
is why the interesting engineering is not in any single phase — it is in the
seams.

Doctrine distinguishes **deliberate** targeting (planned, hours to days, full
review) from **dynamic** targeting (targets of opportunity, minutes, compressed
authority). **Time-sensitive targeting** is the extreme case: a window measured
in minutes, a compressed decision process, and pre-delegated authority. A
"kill chain" in the popular sense almost always means the dynamic/TST case,
because that is where latency becomes decisive.

Reference: US joint doctrine JP 3-60, *Joint Targeting*. NATO has equivalents.
The F2T2EA acronym is the common shorthand; you will also see "find, fix,
finish" and the Army's "decide, detect, deliver, assess" (D3A).

## 2. Kill chain vs OODA — they are not the same thing

Boyd's **OODA loop** (Observe, Orient, Decide, Act) is a *cognitive* model of
how a decision-maker stays ahead of an adversary. The **kill chain** is a
*process decomposition* of one specific mission.

People conflate them because both are loops and both are about tempo. The useful
distinction:

- OODA explains why the side that adapts faster wins even with worse equipment.
- The kill chain tells you which of your handoffs to fix on Monday morning.

Where they meet: your chain is only as fast as its slowest *decision*, and your
adversary's whole job is to move that decision point later — by hiding, by
decoys, by jamming the link that carries the track, or by making you unsure
enough that a human has to look again.

## 3. The three constraints that actually bind

Sensor latency is the fun problem and rarely the binding one. In practice the
clock is eaten by three things:

### Positive identification (PID)

Before you can act you must be confident *what* the thing is. Confidence is a
number produced by a classifier or a human, and it is **calibrated badly** in
both directions: analysts over-trust a crisp video feed, and models are
over-confident out of distribution. The cost of a false positive is not
symmetric with a false negative, which is exactly why thresholds get set high
and why high thresholds produce re-attack cycles.

The re-cycle is the killer. If each attempt has a 30% chance of failing PID,
the chain does not take 30% longer — it takes a *geometric* number of attempts,
and the tail is what breaks the schedule.

### Authority

There are two different questions and systems routinely conflate them:

- **Can** the system do it? (technical, a tasking path)
- **May** the system do it? (legal, an authority)

Delegation is the mechanism that makes the second fast. A commander pre-authorises
a subordinate echelon to release under stated conditions. Above the delegation
limit, the proposal must go up — and going up costs minutes to hours, because it
enters another human's battle rhythm. Note what escalation buys: usually *nothing*
in decision quality, because the higher echelon applies the same numeric
threshold to the same evidence package. It buys accountability, which is a
legitimate thing to buy, but it should be priced honestly.

### Collateral and ROE

The legal filter runs **before** the confidence filter. A perfectly identified
target in a protected site, or with an unacceptably high collateral estimate, is
not a target. This is the check that a planning tool should be able to fail
*before* anyone spends an hour flying a sensor to look at it.

## 4. From kill chain to kill web

A single chain is fragile: lose one link and nothing closes. The modern answer is
a **kill web** — many sensors, many paths, composable effects, so that the
question becomes "what is the probability that *at least one* path closes inside
the window?" rather than "how long is the chain?"

The CSBA "Mosaic Warfare" work (Clark, Patt, Schramm, 2019–2020) is the
accessible statement of this idea: decompose capability into many small,
recombinable parts and let the network compose them per mission, rather than
building a few exquisite chains.

The engineering consequence is that **redundancy is worth more than speed** once
your p95 is dominated by failures rather than by work. A path that is 20% slower
but 95% available beats a fast path that fails a fifth of the time.

## 5. The latency maths (why the mean lies)

Kill-chain time is a sum of right-skewed random variables with feedback loops.
Three consequences:

1. **Sums of skewed variables are more skewed.** Even if each step is only
   mildly skewed, ten of them in series produce a long right tail. Reporting a
   mean hides the case that matters.
2. **Recycles make it geometric.** With attempt-success probability `p`, the
   expected number of attempts is `1/p` and the variance explodes as `p` falls.
   A 70%-per-attempt PID gives a mean of 1.43 attempts but a 1-in-20 chance of
   five or more.
3. **Local speedups are diluted.** Halving a step that is 10% of the cycle buys
   5%, not 50%. Amdahl's law, with worse manners.

So the metrics that matter are **p50, p95, p99, and success rate**, and the
analysis that matters is *which node owns the tail* — not which node is slowest
on average.

## 6. What the software actually is

Nothing in the above is about weapons. The software stack around a kill chain is
five unglamorous layers:

1. **Track management / common operational picture.** Ingest sensor reports,
   correlate them into tracks, maintain identity and provenance. This is 60% of
   the work and 90% of the bugs.
2. **Fusion and classification.** Combine sources; produce a confidence with a
   provenance trail you can defend.
3. **Authority and ROE workflow.** Encode who may decide what, route proposals,
   record the decision with its reasoning. This layer is usually a spreadsheet
   and a radio, and that is the problem.
4. **Tasking and assignment.** Turn a decision into an order on a specific
   effector, with constraints and deconfliction. (The optimisation version is
   weapon-target assignment; it is a well-studied assignment problem.)
5. **Assessment.** Close the loop; feed the re-attack decision.

And the glue: **time**. Every message needs a timestamp, every handoff a
latency, every decision an author and a reason.

### The standards that make it real

If you want your software to touch actual systems rather than a mock-up:

- **Cursor-on-Target (CoT)** — XML events over TCP/TLS or UDP, the lingua franca
  of the TAK ecosystem (ATAK, WinTAK, iTAK, TAK Server). This is the single most
  accessible real interface in the whole field. `kc cot` emits it.
- **Link 16 / J-series messages** — tactical data link; track and command
  messages between platforms.
- **STANAG 4676** — NATO ISR tracking standard.
- **MIP / JC3IEDM** — coalition information exchange models for orders and
  reports.
- **DIS / HLA** — distributed simulation protocols; what you use to plug a model
  into a synthetic training environment.

### What is genuinely hard

- **Calibration.** A confidence number that is not calibrated is a random number
  with a decimal point, and every downstream threshold is built on it.
- **Classification and releasability.** Data at different classifications cannot
  simply be joined; coalition sharing has its own rules. This constrains the
  architecture more than any algorithm.
- **Denied and degraded comms.** The plan that works on a good link is not the
  plan that works when the link is jammed. Scenario 03 exists to make this
  visible.
- **Trust and auditability.** A human will not delegate to a system they cannot
  interrogate afterwards. An unexplained decision is unusable even when correct.

## 7. Where a solo builder can be legitimately real

Genuinely buildable without clearances or authorities:

- A **CoT-native** COP or track manager that interoperates with ATAK/TAK Server.
- **Latency and capacity analysis** of a targeting process — this repository.
- **Wargaming and course-of-action** tools over published doctrine.
- **Decision-support with explicit, inspectable policy** and an audit trail.
- Data engineering: ingest, normalise, provenance, time-series.

Not buildable from the outside: actual targeting systems, weaponeering, and
anything that touches real tasking. Those require accreditation, authorities and
a customer. It is worth being clear-eyed about that line rather than LARPing
across it.

## 8. Reading list

- JP 3-60 *Joint Targeting* — the doctrine, and the source of F2T2EA.
- Christian Brose, *The Kill Chain* (2020) — the policy argument, readable.
- Clark, Patt & Schramm, *Mosaic Warfare* (CSBA, 2019/2020) — kill webs and
  decision-centric operations.
- Boyd, *Patterns of Conflict* / *A Discourse on Winning and Losing* — OODA from
  the source, not the summaries.
- Any good treatment of **weapon-target assignment** (assignment problem / MILP)
  if you want the optimisation layer.
- The **CoT** schema and TAK Server documentation if you want to interoperate.
