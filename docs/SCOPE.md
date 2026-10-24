# Scope, and the lines this project does not cross

## What this is

A **process simulator and analysis tool**:

- Models a targeting cycle as a stochastic activity network (a graph of timed
  activities and probabilistic branches, with feedback loops).
- Runs Monte Carlo trials to produce latency distributions, not point estimates.
- Encodes targeting authority, PID thresholds, ROE posture and collateral
  limits as **executable, inspectable policy**.
- Produces a **tamper-evident decision log** for every simulated engagement.
- Emits **Cursor-on-Target** XML so its synthetic tracks can be displayed on a
  real TAK common operational picture.

Every number in every scenario is invented. The target is `TRK-1042`, a label
with no location, no unit and no real-world referent.

## What this is not

- **Not a weapon model.** There is no blast, fragmentation, Pk, CEP, range or
  weaponeering calculation anywhere in the codebase. The "effector" is a single
  delay distribution named `Effect delivery window`, deliberately abstract.
- **Not a targeting system.** It does not accept real target data, does not
  ingest live feeds, and does not and cannot task anything.
- **Not accredited, certified or reviewed** by anyone. It has no security
  accreditation and is not authorised for any operational use.
- **Not a doctrine authority.** The policy logic is a plausible simplification
  of published practice, not a legal opinion. Real ROE and release authority are
  jurisdiction-, mission- and time-specific.
- **Not a prediction.** The distributions are illustrative shapes, not measured
  data. The point is the *structure* of the result (where the tail comes from),
  not the specific minutes.

## Why build it this way

Because the defensible, useful, buildable part of defence software is exactly
this: **decision latency, authority routing, and auditability**. It is also the
part that is hardest to fake. A toy that draws a missile on a map teaches
nothing; a model that shows a 4× latency improvement coming mostly from fewer
re-identification cycles, rather than from a faster sensor, teaches something
true.

There is also a restraint argument. Making the collateral check a **hard,
first-class gate** — one that fires before any identification logic and can shut
a plan down in the planning phase — is the correct architecture. A tool that
makes the "no" path as legible as the "yes" path is doing useful work.

## Data handling

- All scenarios are static JSON in `scenarios/`. No network calls are made by the
  simulator itself.
- The local console binds to `127.0.0.1` by default and has no authentication.
  Do not expose it to a network.
- Audit logs are written only where you ask them to be written.
- The CoT output uses a notional origin (0, 0) and synthetic coordinates.

## If you want to go further

Legitimate next steps from here:

1. **Interoperate for real.** Point `kc cot` at a local TAK Server or ATAK
   device and watch the synthetic track appear. That is a genuine, unclassified
   integration exercise.
2. **Replace invented distributions with measured ones.** Time your own
   tabletop exercise; use the observed task times. The model is only as good as
   its inputs, and the engine does not care where the numbers come from.
3. **Model the kill web properly.** Add parallel branches with per-branch
   availability and compute P(at least one path closes within T). The current
   `03-contested-comms` scenario only hints at this.
4. **Study the standards.** CoT, Link 16, STANAG 4676, MIP. Interoperability is
   where a small team can add real value.

Anything that touches actual targeting goes through a customer, a clearance and
an accreditation process. That is not a limitation to work around; it is the
control that makes the system trustworthy.
