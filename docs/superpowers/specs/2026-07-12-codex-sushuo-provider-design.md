# Codex Sushuo Provider Design

**Date:** 2026-07-12
**Status:** Approved design; awaiting written-spec review before implementation

## Goal

Add the user's OpenAI Chat Completions-compatible service as a separately
selectable Codex model provider named `sushuo`, without replacing the existing
Codex default or storing a credential in the QUANTOS repository.

The requested service contract is:

- base URL: `https://vip.sushuo.biz/v1`
- endpoint family: Chat Completions
- model: `gpt-5.6-sol`
- credential environment variable: `NEW_API_KEY`

## Scope and safety constraints

- Read the user-provided credential only from `key/key.txt` on this local
  machine. Never print, log, commit, or copy it into a repository file.
- Store it only in the Codex-private environment file
  `C:\\Users\\Administrator\\.codex\\.env` under `NEW_API_KEY`.
- Make additive changes to `C:\\Users\\Administrator\\.codex\\config.toml`.
  Preserve the current model, auth configuration, and all existing settings.
- Do not contact any trading, broker, governance, or live-operation endpoint.
  This is a local Codex-client configuration task only.
- Do not put the custom service on the default path. The existing provider
  remains the fallback.

## Design

### Native provider profile (chosen)

Add a provider definition called `sushuo` that points at the supplied base URL,
uses the Chat Completions wire protocol, and resolves the secret through
`NEW_API_KEY`. Add a matching `sushuo` profile whose model is `gpt-5.6-sol`.

The default configuration remains unchanged. Selecting the new profile is the
only action that routes a Codex model request to the external service. The
exact profile-selection entrypoint will be verified against the installed
Codex client before it is presented as usable.

### Credential handling

The installer will trim the local contents of `key/key.txt`, reject an empty
value, and write or replace only the `NEW_API_KEY` line in the private Codex
environment file. Existing unrelated `.env` entries are retained unchanged.
The file stays outside the repository and is never rendered in command output.

### Compatibility check

Before enabling the profile, issue one minimal Chat Completions request to the
provided API. Its prompt contains no repository content, credentials, or
trading data. The check must confirm a successful JSON response with a model
completion. A failed probe leaves the existing Codex configuration untouched
and reports only status/error category, not the key or full response body.

After the provider configuration is present, restart Codex and validate that a
new session can select the `sushuo` profile. If the installed desktop client
does not expose profile selection, retain the provider configuration but do not
claim desktop switching works; provide the documented client-specific switch
method instead.

## Alternatives considered

1. **Replace the default provider:** simpler but removes the proven fallback;
   rejected because the user chose a switchable setup.
2. **Expose the service through MCP:** useful for a callable tool, but it does
   not make the service Codex's reasoning model; rejected as out of scope.
3. **Add a local proxy:** can normalize non-standard APIs, but adds a process,
   an attack surface, and credential duplication; unnecessary unless the
   compatibility probe fails.

## Verification and rollback

1. Confirm `key/key.txt` is non-empty without displaying it.
2. Run one minimal compatibility probe over HTTPS.
3. Confirm `config.toml` includes the additive `sushuo` provider/profile
   entries without printing unrelated configuration values.
4. Confirm `.env` has exactly one non-empty `NEW_API_KEY` entry without
   displaying its value.
5. Restart Codex and perform a new-session profile selection check.

Rollback removes only the `sushuo` provider/profile entries and the
`NEW_API_KEY` entry. The pre-existing Codex configuration is preserved.

## Success criteria

- Existing Codex default behavior remains available.
- The Sushuo provider is opt-in and uses `gpt-5.6-sol` when selected.
- No secret appears in the repository, Git status, logs, or documentation.
- The external compatibility probe succeeds, or the configuration is left
  unchanged with a bounded failure report.
