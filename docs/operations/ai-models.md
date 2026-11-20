# AI models

Every AI feature goes through one gateway (`backend/app/services/llm.py`).
It picks a model by purpose, tries models in priority order, keeps every prompt inside the model's budget, enforces the company's AI policy, records every call, and falls back to the built-in rules engine so no screen ever breaks.

## Purposes

| Purpose | Used by |
| --- | --- |
| assistant | Ask Big Hat |
| summarizer | Log digests |
| executive | The CEO briefing and the board report |
| triage, review | Reserved for Fix Factory stages |

## Connecting Azure OpenAI

Each company starts with `whitehats · gpt-6-luna` configured as its primary model.

For local development and the proving ground, put `WH_ENV=development`, `AZURE_OPENAI_API_KEY` and `AZURE_OPENAI_ENDPOINT` in the repo `.env`. The endpoint may include `/openai/v1`; startup stores the resource root and seals the key into each company's seeded primary model when that row has no key yet. The key is never logged or sent back to the browser. If `WH_ENV` is unset or `production`, enter keys on this screen only.

1. In the Azure portal, open the Azure OpenAI resource and copy the endpoint (`https://<resource>.openai.azure.com`) and one of the keys.
2. Either set the two variables above, or in WhiteHats open *AI models → whitehats · gpt-6-luna → Add key*, paste the key and save.
3. Press *Test*. A green *ready* means the model answered. If something is missing, the message names only that setting.

The API version `v1` uses Azure's current OpenAI-compatible endpoint.
For a dated API, enter it instead, for example `2025-04-01-preview`.

`whitehats · gpt-5.6-terra` is pre-configured and disabled.
Open it, add the key, enable it and test when you want that deployment.
Lower priority numbers are tried first.

## Self-hosted and other models

Choose *OpenAI-compatible endpoint* for vLLM, Ollama, LM Studio or OpenAI itself.
Enter the base URL ending in `/v1` and the model name.
Tick *zero data retention* only if the endpoint truly keeps nothing; the AI policy relies on it.

## Budgets

- Each deployment declares its context window and maximum output.
- Ask Big Hat packs its briefing into a quarter of the context window, capped at 6,000 tokens.
- Each collector has its own token budget for log digests, and the digest has a total budget.
- Sections that do not fit are trimmed or dropped, and the answer says which.

## Policy enforcement

A company's AI policy lists the providers it allows and whether zero retention is required.
The gateway skips any model that breaks the policy, then falls back to the rules engine.
Change a policy only with a signed change to the data processing agreement.

## When a model fails

The call is recorded with its error, the model is marked *error* or *needs key*, and the next model or the rules engine answers.
*AI models → Recent AI calls* shows every attempt.
