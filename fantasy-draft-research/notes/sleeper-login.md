# Sleeper browser sign-in and draftboard handoff

This procedure prepares a user-owned Sleeper session for a future Sleeper
league or mock draft without storing usernames, passwords, verification codes,
cookies, browser storage, invite links, league IDs, or draft URLs.

## Operating boundary

Sleeper's current terms prohibit giving a third party account credentials,
tokens, or session identifiers, and prohibit automated/systematic extraction
without written consent. For that reason, the account owner completes every
authentication step and any Sleeper draft selection directly in Chrome. This
runbook may guide the visible flow and verify non-sensitive post-login state;
it does not automate login, inspect browser storage, or enter picks.

Use this page only for a Sleeper-hosted league or a Sleeper Draftboard rehearsal.
For the Yahoo league, Sleeper remains a preparation surface; Yahoo retains
authority for the actual Yahoo draft clock, player pool, picks, and roster.

## Chrome sign-in procedure

1. Open [Sleeper](https://sleeper.com/) in the same persistent Chrome profile
   intended for draft day.
2. Confirm the visible landing page shows **Log In** and **Sign Up**. Select
   **Log In**.
3. The account owner completes the authentication form or verification method
   Sleeper presents directly in Chrome.
   - Do not send a username, password, passkey prompt, one-time code, recovery
     detail, or session material through chat, a note, a script, or the
     repository.
   - If Sleeper asks for a CAPTCHA, device approval, phone/email verification,
     or another risk check, the owner completes it. Do not bypass it.
   - The exact credential and verification choices shown after **Log In** are
     **unverified / needs confirmation**; Sleeper's public support material
     reviewed for this runbook does not document one stable desktop sequence.
4. Wait for the expected signed-in product state. Verify it visibly rather than
   by reading cookies or local storage: the **Log In** control is no longer the
   active account entry point, and the intended league list or **Draftboards
   (Mock Drafts)** entry is available.
5. If a mock is the goal, open **Draftboards (Mock Drafts)** from the web app's
   league area, choose football, and claim the desired draft slot. Do not copy
   the board or invite URL into this repository.
6. Before drafting, verify the visible board settings, draft slot, team count,
   scoring, roster positions, draft format, keeper tiles, timer mode, and CPU
   behavior. Use the [Sleeper preparation procedure](sleeper.md#preparation-procedure)
   for the Yahoo-league rehearsal configuration.

## Session reuse and recovery

1. Reuse the same Chrome profile for later Sleeper sessions. Let Chrome present
   its normal browser-managed session; do not export, import, or serialize
   cookies, profile data, or a Playwright storage-state file.
2. If Sleeper again shows **Log In** or requests verification, consider the
   session unavailable. Repeat the owner-completed sign-in flow; do not repair
   it by copying browser data or credentials.
3. If the signed-in page is correct but the expected league or Draftboard is
   absent, stop and have the owner confirm the Sleeper account and the league
   invitation outside this runbook. Do not open a second account or fabricate
   a board as recovery.
4. If the room is already on a live clock, the owner takes the clock. Do not
   enable CPU/autopick or operate commissioner controls as a recovery shortcut.

## Draft-day handoff checklist

- [ ] Chrome is signed in to the intended Sleeper account through owner input.
- [ ] The visible league or Draftboard is the intended one; no identifiers or
      invite URLs have been copied into notes or logs.
- [ ] Format, draft order/slot, roster positions, keepers, timer, and CPU mode
      have been visually checked.
- [ ] The owner, not automation, will make Sleeper pick and commissioner
      actions.
- [ ] If this is a Yahoo rehearsal, the Yahoo room remains the authoritative
      live-draft surface.

## Evidence and limits

- Sleeper's landing page visibly exposes **Log In** and **Sign Up** as of
  2026-08-30: [Sleeper](https://sleeper.com/).
- Sleeper's support guidance places web Draftboards below the league list and
  supports claiming a draft slot: [How to Create a Mock Draft](https://support.sleeper.com/en/articles/3982891-how-to-create-a-mock-draft).
- The no-automation and credential/session restrictions are summarized in the
  existing [Sleeper safety guidance](sleeper.md#access-reliability-and-safety),
  which links Sleeper's current terms.
