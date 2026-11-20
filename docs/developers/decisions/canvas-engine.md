# Canvas engine

**Status:** accepted
**Date:** 2026-10-19

The canvas is React Flow (xyflow) with ELK for auto-layout. v1 has two views of the same graph: Logical and Network. This is B2a and B3a in the build spec, section 1.

JointJS / Rappid was the commercial canvas. A custom canvas on Cytoscape or Sigma was the other. React Flow is the one that already fits the React app and is mature enough to carry the Studio. ELK does the auto-layout so the product does not invent its own.

Invalid edges are blocked with a reason. A Generic relation is the escape hatch (B4a). That rule is separate from the engine choice and lives with the edge schema.
