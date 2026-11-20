# Runner isolation

**Status:** accepted
**Date:** 2026-10-19

Each WhiteHat runs in its own container. Active modes use gVisor or Firecracker. This is F2a in the build spec, section 1.

The other shape was one container for the whole Runner, with the WhiteHats as processes inside it. A process shares the kernel and the filesystem with its neighbours. A container per WhiteHat gives each one its own egress policy, CPU and memory cap, and rate limit. A sandbox runtime on the active modes keeps a test that reaches out from also reaching the host.

The customer-hosted Runner is still the default way into private systems, and it calls out only (E1a). Packaging is a container image plus a Helm chart, and a VM image where there is no Kubernetes (F1a). Those are separate decisions. This record is only about the wall between WhiteHats on the same Runner.
