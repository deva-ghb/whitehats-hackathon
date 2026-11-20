# Offboarding a company

Offboarding ends service, gives the customer their data, and deletes it on schedule with a certificate.

## Steps

1. Confirm in writing that the customer is leaving, and the retention window they want (default 30 days).
2. On the company's page, open *Danger zone → Offboard* and type the company name to confirm.
3. WhiteHats then:
   - exports the company's data to `backend/.data/exports/<slug>-<date>.json` and records its SHA-256 fingerprint;
   - deactivates every user and stops every collector;
   - sets the company to *offboarded* with a deletion date.
4. Send the export file to the customer through a secure channel, with its fingerprint so they can check it.
5. On or after the deletion date, press *Delete data* and type the name again.
   Deleting early needs the customer's written request and the *delete early* box.
6. Keep the deletion certificate the console shows. It lists rows deleted per table and the export fingerprint, and is stored on the company record and in the platform audit trail.

## What the export contains

Users (without passwords or two-step secrets), WhiteHats, incidents and their timelines, signals, fixes, posture history, collectors, log digests, network findings, AI model settings (without keys), connectors (without credentials), vault credentials (the reference and the scope, never the value) and the audit trail.

## What deletion removes

Every row belonging to the company, including raw log lines, shape counts, AI call records, chat history and vault secrets, then the company's encryption key.
A tombstone record remains with the deletion certificate so the event is provable later.
