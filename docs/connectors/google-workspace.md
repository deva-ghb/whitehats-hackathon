# Google Workspace

1. In Google Cloud, create a service account and a JSON key.
2. In the Google Admin console: *Security → Access and data control → API controls → Domain-wide delegation → Add new*, with the service account's client ID and the scope `https://www.googleapis.com/auth/admin.directory.user.readonly`.
3. In WhiteHats: *Connectors → Google Workspace → Connect* with your Workspace domain, the email of an administrator, and the JSON key.
   The key reads the directory as that administrator, read-only: Google only answers directory questions on behalf of an administrator.

*Save and check* exchanges the key for a token. A success means the key was accepted.

Every hour WhiteHats reads your active people, who is an administrator, who has 2-Step Verification, and when each person last signed in.
It raises a finding for an administrator or a person without 2-Step Verification, for an account nobody has signed in to for 90 days or more, and when more than 5 people are administrators.
Each finding closes on its own once Google no longer shows it.
