# Runner to Big Hat protocol

**Status:** accepted
**Date:** 2026-10-19

The Runner and Big Hat talk over four messages. The Runner sends a heartbeat (its version, the sense points that are on, and access-path health), a findings stream, and drift events for entities or flows that are not on the clone. Big Hat sends commands: start or stop a sense point, verify a fix, or collect more evidence. Commands are signed. The Runner checks the signature before it acts. This is the contract in the design, section 12.3, and it matches the build spec, section 1.

Raw evidence stays in the Runner. Big Hat receives the finding and a redacted excerpt, not the raw log, packet or file (F3a). Uploading every raw record to Big Hat was the other option, as was letting the customer choose per mode. Keeping the raw record on the customer side means a Big Hat compromise does not hand over the estate's logs.

The existing collector becomes one WhiteHat inside the Runner, in Telemetry mode (F4a). It uses this same contract. It does not grow a second protocol.
