# Decisions API

**Status:** accepted
**Date:** 2026-10-20

Every WhiteHat scan is two steps. A cheap Decisions API screens each chunk of raw data. Only a chunk it flags goes to a model, which confirms or rejects the problem. This is the scan shape in the build spec, section 1, and the shape in section 5.1.

The Decisions API is deterministic. It never calls a model. It uses rules, baselines and small statistical checks.

- Input: the chunk, its sense point, its mode, and the node and edge context from the clone.
- Output: `clean`, or `suspect` with the rule ids that fired, a score, and the evidence spans (line numbers, config paths, file locations).
- Every decision is stored, so a later review can see why a chunk was or was not escalated.

The model call, through the existing model gateway, receives the flagged spans and a small packed context. It does not receive the whole raw stream. Its answer is confirmed or rejected, plus category, severity, confidence and a plain explanation. A rejection is recorded as noise and can tune that rule. If no model is connected, the WhiteHat still reports the Decisions API result and marks it unconfirmed.

Logs and code are untrusted data. The model can confirm, add context and explain. It can never downgrade a serious finding the deterministic check already raised. Only a person can.

The other shapes were to send every chunk to the model, or to publish the rule result as the finding with no confirmation. The first grows cost with the volume of data. The second publishes every false positive. The two-step shape keeps the model on the flagged share and still leaves a finding when the model is absent.
