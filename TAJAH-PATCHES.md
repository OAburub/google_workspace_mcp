# Tajah fork: what we carry on top of upstream

This repository is `OAburub/google_workspace_mcp`, a fork of `taylorwilsdon/google_workspace_mcp`.
It is deployed on Fly as **tajah-gws-mcp** (`https://gws.mcp.tajahlabs.com`) and is the
"Google Workspace" connector in claude.ai for the eduArabia / Tajah team.

`main` is an upstream release plus the small set of patches below. Everything else is upstream.
Sync with `git fetch upstream` then rebase our commits onto `upstream/main`.

## Patches we carry

| # | Patch | Where | Why we need it |
|---|---|---|---|
| 1 | **Chat status tools** `set_chat_status`, `clear_chat_status` (scope `chat.users.availability`) | `gchat/chat_tools.py`, `auth/scopes.py`, `auth/permissions.py`, `auth/service_decorator.py` (`chat_availability` alias), `core/tool_tiers.yaml`, `tests/gchat/test_chat_status.py` | The `team-status` plugin (eduArabia/team-status) calls both. Upstream has no equivalent. |
| 2 | **Directory fallback for DM names** (scope `directory.readonly`) | `gchat/chat_helpers.py` (`_get_domain_directory_names`, used by `_lookup_people_names`), `auth/scopes.py`, `tests/gchat/test_chat_directory_fallback.py` | Upstream names DM members from the caller's contacts only. Coworkers who are not in the caller's contacts come back from People `getBatchGet` without a name and show as raw `users/<id>`. The directory lookup names them. It never raises: without the scope the label just stays the raw id. |
| 3 | **`list_spaces` keeps `resolve_dm_names` and `max_dm_spaces`** (both ignored; DMs are always named) | `gchat/chat_tools.py` | Existing callers pass them; dropping them would make those calls fail validation. |
| 4 | **Regression tests** | `tests/gchat/test_chat_space_names_real_client.py`, `tests/gchat/test_chat_client_library.py` | See the rules below. |
| 5 | **Fly deploy config** | `fly.toml` | It used to live only in a working tree (untracked), which made the deploy impossible to reproduce from a clone. |

Upstream's own DM-naming work (#1141, #1256) replaced our earlier DM patch (PR #737, closed upstream 2026-10-20).

## Rules learned the hard way

- **Never run Chat or People lookups concurrently through one `google-api-python-client` service.**
  A service wraps one `httplib2.Http`, which is not thread-safe: concurrent responses get crossed
  (a DM gets another person's name) or lost. Our old DM-name code looked memberships up five at a
  time and mislabeled most DMs, which caused a Chat message to be sent to the wrong person on
  2026-11-04 (board tasks RB1-T121, RB1-T120). Upstream's `_name_spaces` is sequential on purpose.
  `tests/gchat/test_chat_space_names_real_client.py` drives the real Google client against a local
  fake Chat server and fails if the lookups become concurrent or any label is wrong.
  A mocked service cannot see this class of bug.
- `space_type="room"` / `"dm"` needs the quoted filter (`spaceType = "SPACE"`). The unquoted form we
  carried returned HTTP 400. Upstream has the quoted form.
- Windows: run the tests with `PYTHONUTF8=1` (one upstream test reads a file with the default
  codepage). `valkey-glide` (the `--extra valkey` dependency) has no Windows build; the Docker image
  is Linux and is unaffected.

## Deploy

```
fly auth login
fly deploy -a tajah-gws-mcp
```

Secrets live on Fly (`fly secrets list` shows names only). Do not commit `.env*` files; they are
gitignored. After a deploy, check `https://gws.mcp.tajahlabs.com/health` and that a connected user
can still call a tool without re-authenticating.
