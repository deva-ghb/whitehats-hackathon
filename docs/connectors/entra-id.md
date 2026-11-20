# Microsoft Entra ID

1. *Entra ID → App registrations → New registration*, name it `whitehats-identity`.
2. *API permissions → Microsoft Graph → Application permissions*: `AuditLog.Read.All`, `Directory.Read.All` and `UserAuthenticationMethod.Read.All`. Press *Grant admin consent*.
3. *Certificates & secrets → New client secret*; copy the value.
4. In WhiteHats: *Connectors → Microsoft Entra ID → Connect* with the tenant ID, client ID and secret.

*Save and check* requests a Microsoft Graph token. A success means the app registration and secret were accepted.

Every hour WhiteHats reads your enabled people, who is an administrator, who has registered multi-factor, and when each person last signed in.
It raises a finding for an administrator or a person without multi-factor, for an account nobody has signed in to for 90 days or more, and when more than 5 people are administrators.
Each finding closes on its own once Entra ID no longer shows it.

Multi-factor registration and last sign-in dates need an Entra ID P1 or P2 plan.
Without one, WhiteHats still reads your people but treats those two as unknown, and never raises a finding on what it cannot see.
