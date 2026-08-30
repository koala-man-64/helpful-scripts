# Yahoo Fantasy browser sign-in

This procedure documents the Yahoo Fantasy Sports sign-in path without storing usernames, passwords, verification codes, cookies, or recovery details.

## Normal desktop flow

1. Open [Yahoo Fantasy Sports](https://sports.yahoo.com/fantasy/).
2. Select **Sign in** in the Yahoo Sports header.
3. Confirm the browser is on `login.yahoo.com` and the page says **Sign in to Yahoo Sports**.
   - Yahoo includes `src=sports` to identify the calling product.
   - Yahoo includes a `done=` URL so successful authentication returns to the Fantasy page that initiated sign-in.
4. Enter the Yahoo username, full email address, or phone number associated with the account.
5. Decide whether to leave **Stay signed in** selected.
   - Selected: Yahoo uses browser cookies to preserve the session. Yahoo documents a default period of two weeks, subject to security checks and cookie availability.
   - Cleared: expect to authenticate again in a later browser session.
6. Select **Next**.
7. Complete the account-specific primary authentication method Yahoo presents:
   - Password: enter the Yahoo password and select **Sign in**.
   - Account Key: approve the Yahoo notification on the enrolled mobile device; Yahoo may also request a displayed verification code.
   - Passkey: approve with the enrolled device's face, fingerprint, or device unlock. A desktop flow may display a QR code for a phone-held passkey.
   - Google: Yahoo starts an OAuth/OpenID Connect flow at `accounts.google.com`; Google authenticates the account and returns an authorization result to Yahoo's login callback. Yahoo then resumes its own session and redirects to Fantasy.
8. Complete any additional verification Yahoo requires. Depending on account configuration and sign-in risk, this can be:
   - approval in a Yahoo mobile app;
   - a code sent by SMS or phone call;
   - an authenticator-app code;
   - a physical security key;
   - a code sent to a recovery email;
   - a CAPTCHA or other risk check.
9. After Yahoo redirects through the original `done=` URL, confirm the browser has returned to Yahoo Fantasy Sports.
10. Verify success using visible product state: the **Sign in** link is replaced by the account/profile control and the expected fantasy teams or league entry points are available.

The Google branch was validated on 2026-08-30: Google returned control to Yahoo, Yahoo returned to the original Fantasy landing page, and the signed-in profile control replaced **Sign in**. Continue with the [football navigation procedure](yahoo-football-navigation.md) to reach a team homepage.

## Alternate entry and recovery branches

- **Sign in with Google** is available on the first Yahoo sign-in screen. The observed flow requested Google's basic OpenID identity scopes and used a Yahoo callback before returning to Fantasy. It is only repeatable when the intended Yahoo identity is linked to that Google sign-in.
- **Forgot username** opens Yahoo's recovery flow. Use a recovery phone number or email address already associated with the account.
- If Account Key approval does not arrive, use **Resend**, **Use text or email to sign in**, or **Try another way to sign in** when Yahoo offers those controls.
- If recovery information is unavailable or stale, stop and use Yahoo's Sign-in Helper. Do not create a second account as an implicit recovery step.

## Why the flow can change between runs

Yahoo can add secondary verification for a new browser or device, an unusual location, VPN or proxy use, private browsing, cleared cookies, repeated failed attempts, or other activity it considers unusual. These checks are account- and risk-dependent, so the exact screens after **Next** are not a fixed sequence.

The **Stay signed in** mechanism also depends on Yahoo cookies remaining available. Signing out, clearing or corrupting cookies, browser privacy settings, security software, switching accounts, or opening sensitive account-security pages can require authentication again.

## Session persistence: use the browser-managed cookie jar

Use the same persistent browser profile for later draft sessions and leave **Stay signed in** selected during the successful login. Yahoo writes its authenticated session into that profile's protected cookie jar. On the next run, reopen Yahoo Fantasy in the same profile; the browser loads the cookie jar automatically and Yahoo either restores the session or asks for fresh verification if the session expired or its risk checks require it.

Do not export, hand-edit, or load a standalone cookie JSON/Netscape file. An authenticated Yahoo cookie can act like a bearer credential, bypass the intended login ceremony until it expires, and leak through source control, logs, backups, or file sharing. A portable cookie file also omits other browser-bound security state and is less reliable than reusing the profile Yahoo originally authenticated.

For this Codex workflow:

1. Reuse the Codex in-app browser rather than creating a disposable browser profile for each draft session.
2. Leave **Stay signed in** selected before completing Yahoo or Google authentication.
3. Finish the login in that browser and return to Yahoo Fantasy.
4. On later runs, open Yahoo Fantasy in the same browser. Do not import cookies; allow the browser to present its existing session normally.
5. If Yahoo shows **Sign in** or asks for verification, treat the session as unavailable and repeat the interactive login. Do not try to repair it by copying cookies.

Repository protections intentionally exclude common cookie exports, browser profiles, and automation storage-state files. If a future standalone tool needs persistence, it should use a dedicated OS-protected browser profile outside this repository and expose only the profile location through configuration; it should never serialize Yahoo or Google authentication material into the project tree.

## Safe assisted-login boundary

- The account owner enters usernames, passwords, passkeys, one-time codes, and recovery information directly in the browser.
- Do not paste credentials or codes into chat, notes, scripts, screenshots, or repository files.
- If a CAPTCHA appears, the account owner completes it in the browser.
- Automation may navigate to the sign-in page and verify the post-login Yahoo Fantasy state. The browser may persist its own profile, but automation must not extract, copy, inspect, or serialize authentication cookies or browser storage.

## Repeatability check

Record only non-sensitive observations after a test run:

- Date and browser surface
- Entry URL and successful return URL
- Primary mechanism category: password, Account Key, passkey, or Google
- Secondary mechanism category, if any; never record the code or recovery destination
- Whether **Stay signed in** was selected
- Whether the expected Yahoo Fantasy profile and leagues appeared
