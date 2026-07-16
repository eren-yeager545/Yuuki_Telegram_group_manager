# Secret Safety Audit

## Summary

This repository was checked for hardcoded secrets, exposed env patterns, and log leakage risk.

## Findings

- No live third-party API secret, password, Stripe key, Supabase key, database URI, OAuth secret, JWT secret, or cloud credential was found hardcoded in the current cleaned repository files.
- Runtime configuration values are loaded from environment variables in `config.py`: `BOT_TOKEN`, `OWNER_IDS`, `SUDO_USERS`, `LOG_CHANNEL_ID`, `QUIZ_INTERVAL_SECONDS`, and `SEEN_UPDATE_COOLDOWN_SECONDS`.
- `.env` is excluded in `.gitignore`.
- `.env.example` exists with placeholder values only.
- The current codebase does not contain frontend code, so there are no `NEXT_PUBLIC_` or `REACT_APP_` browser exposure risks in this repository.

## Notes on sensitive values

| Variable | Where used | Status |
|---|---|---|
| `BOT_TOKEN` | `config.py` | Loaded from env, not hardcoded |
| `OWNER_IDS` | `config.py` | Loaded from env, not hardcoded |
| `SUDO_USERS` | `config.py` | Loaded from env, not hardcoded |
| `LOG_CHANNEL_ID` | `config.py` | Loaded from env, not hardcoded |
| `QUIZ_INTERVAL_SECONDS` | `config.py` | Non-secret runtime env setting |
| `SEEN_UPDATE_COOLDOWN_SECONDS` | `config.py` | Non-secret runtime env setting |

## Logging review

- Logging statements were reviewed for accidental token or secret output.
- Current logs mostly emit operational events such as starts, quiz sends, and moderation actions; they do not intentionally print environment variable values.
- Keep production error traces private because third-party libraries can sometimes echo request details during failures.

## Git history warning

If a real bot token or any other secret was ever committed in an earlier version outside this cleaned repository snapshot, rotate it immediately before deployment.
