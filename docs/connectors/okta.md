# Okta

Lets the Identity WhiteHat read sign-in activity and user status.

1. Sign in to Okta as a **read-only administrator** (tokens inherit their creator's rights).
2. *Security → API → Tokens → Create token*, name it `whitehats`; copy it.
3. In WhiteHats: *Connectors → Okta → Connect*, enter your Okta domain (for example `acme.okta.com`) and the token.

*Save and check* calls `GET /api/v1/users?limit=1`. A success means the token was accepted.

Every hour WhiteHats reads your active people, who holds an administrator role, and each person's sign-in factors.
It raises a finding for an administrator or a person who can sign in with a password alone, for an account nobody has signed in to for 90 days or more, and when more than 5 people are administrators.
Each finding closes on its own once Okta no longer shows it.
It reads only: nothing in Okta is changed.
