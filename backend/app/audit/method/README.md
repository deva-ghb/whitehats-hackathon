# The audit method (vendored)

These files are the open security audit method the audit engine follows, copied unchanged from
https://github.com/cloudflare/security-audit-skill (directory `skills/security-audit`) at commit
`c1c8a8c1471069fb0e188eeaff69b8e8db6564a8` (2026-09-14). They are MIT licensed; see `LICENSE`.

The engine in `app/audit/` reads the prompt blocks from these files and runs the two validators
(`validate-findings.cjs`, `validate-coverage-ledger.cjs`) on every run's output. Do not edit them here: to update,
copy a newer commit over them, record the commit above, and run `node --test *.test.cjs` in this folder.
