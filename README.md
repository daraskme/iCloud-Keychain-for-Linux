# iCloud Keychain for Linux

**Unofficial** client for iCloud Keychain. Sign in with your Apple ID and
`icp` registers this computer as one of your Apple devices, then downloads your passwords into a
private, encrypted store on your machine. A browser extension fills them in for you, just like the
Passwords app on a Mac or iPhone. Your Hide My Email addresses show up too. This project is a 
reverse engineering attempt and **is not affiliated with Apple in any way**.

> **Note:** Most of this project was written with AI assistance (and reviewed by a human).
> It works with my own Apple account, but that's no guarantee it will work with yours.
> `icp password add`, `edit`, and `delete` update the user's iCloud Keychain.
> Notes and verification codes can also be added to existing web logins.
> `icp hme create` and `icp hme edit` change Hide My Email addresses in your Apple account.

> This is also **untested** with Advanced Data Protection enabled. Any help with this will be appreciated -
> let me know if you have any issues with ADP enabled (also lmk if it works). Please don't spam escrow attempts!

## What you'll need

An **anisette server** running on your machine. Apple's sign-in needs a small piece of data that
only Apple's own software can produce; the anisette server provides it locally. Start one with
Docker (or Podman) - it keeps running in the background:

```
docker run -d --restart=always -p 6969:6969 dadoum/anisette-v3-server:latest
```

You only have to do this once. (If you run it somewhere other than the default
`http://localhost:6969`, set `ICP_ANISETTE_URL` to its address.)

You will also need this repo. Clone it / download it as you wish.

## Install

From the project folder:

```
python3 -m venv .venv && .venv/bin/pip install -e .
source .venv/bin/activate
```

## Sign in

```
icp login
```

This will walk you through the login: enter your Apple ID, password, and the 2FA code Apple
sends to your other devices. `icp` then asks for a **device passcode or iCloud Security Code** so
it can join your keychain.

- You're only asked for your password and 2FA code **once**. `icp` should stay signed in for a year after this.
- The passcode step is **important and can't be undone**: entering the wrong passcode too many
  times (about 10) will permanently lock your keychain recovery. One correct entry is
  perfectly safe.
- Your 2FA code and passcode are used immediately and **never saved**. Your password is kept
  **encrypted on your computer** (in your login keyring) so `icp` can stay signed in without
  asking you again - it never leaves your machine. Your access tokens and passwords are stored
  the same way.

## Desktop password manager

Run `icp-gui` or open **iCloud Passwords** from the application launcher. The
Nix package includes a desktop entry for Plasma and other Linux desktops.

The Japanese GUI provides:

- Searchable password and Hide My Email lists.
- Add/delete web logins; edit passwords, multiline notes, and TOTP setup keys.
- Strong password generation with a selectable length (12–128 characters).
- Live verification codes, a countdown, and copy buttons. Paste the website's
  setup key or `otpauth://totp/…` URI in the editor, not a six-digit code.
- Hide My Email issuance and editing of labels and multiline notes.
- Explicit iCloud sync and background saves that keep the window responsive.

Passwords and setup keys are masked initially. Copies are marked as secret for
KDE's clipboard manager and cleared after 30 seconds if the clipboard still
contains that copy. Editing an existing login keeps its site and username fixed.
Clearing its notes or TOTP field and saving removes that metadata from iCloud.
The GUI uses the same encrypted local store and saved login as the CLI. Initial
login or renewed Apple two-factor approval still uses `icp login` in a terminal.

Saved values are fetched again from iCloud. Conflicting password/metadata edits
are rejected when the server differs from the value opened in the editor.
Password and metadata writes are separate server operations; a partial save is
reported explicitly. Hide My Email metadata has a pre-save conflict check, but
Apple's endpoint does not provide conditional writes.

## Browse your passwords in a terminal

```
icp show
```

Opens a full-screen list - start typing to filter, **Enter** to reveal a password,
**Esc** to quit. Add a word to search directly, or `--plain` for plain text:

```
icp show github
```

Generate a strong random password locally with `icp generate-password`. The default
is 24 characters; use `--length 32` for a different length. Generation does not
save the password to iCloud Keychain.

To edit an existing web login, use its exact saved site and username:

```
icp password edit example.com alice@example.com --password
icp password edit example.com alice@example.com --notes
icp password edit example.com alice@example.com --totp
```

To create or delete a login:

```
icp password add example.com alice@example.com --notes --totp
icp password add example.com alice@example.com --generate --length 32
icp password delete example.com alice@example.com
```

`add` prompts for the password and, with the shown options, initial notes and
an `otpauth://totp/...` URI. It creates a login and a metadata record, so the
new login can be edited later. `--generate` creates a strong password and
displays it once after the save succeeds. `delete` asks you to type the site before it
removes the login and its metadata. Writes use conditional CloudKit record
operations and are fetched again to verify the result. The local vault is
refreshed afterwards. An empty answer at the edit prompt clears notes or the
verification code; `add` treats empty answers as unset values.

Creating a login requires at least one existing web login and one existing
Password Manager Metadata record in the keychain. These provide the account's
zone and class-key format. If either is unavailable, `icp` stops before making
any write. Editing notes or codes on a login with no metadata record creates one
using an existing metadata template; newly created logins always receive one.

## Hide My Email

After `icp login`, use `icp hme list` to see your addresses. `icp hme create`
prompts for a label and optional note, generates an address, and reserves it in
your Apple account. `icp hme edit` lets you select an address and update its
label or note. A blank answer keeps the existing value; enter `-` to clear a note.

These commands use the saved iCloud web session and may request a separate 2FA
code the first time. The alias is available on other devices after Apple syncs it.

## Verification codes

If a login has an authenticator code set up in the Passwords app, `icp` picks it up too. `icp show`
lists the current 6-digit code next to the login with the seconds left before it changes, counting
down live.

## Set up the browser extension

The `extension/` folder works in Chromium based browsers and Firefox.

### Chrome, Chromium, Helium, or Brave

1. Open your browser's extensions page (e.g. `chrome://extensions`, `helium://extensions`) and turn on **Developer
   mode**.
2. Click **Load unpacked** and choose the `extension/` folder. Copy the **Extension ID** it shows.
3. Connect the extension to `icp` by running:

   ```
   host/install.sh <EXTENSION_ID>
   ```

4. Reload the extension. Click its toolbar icon - you'll see the logins for the current site.

### Firefox

1. Open `about:debugging#/runtime/this-firefox`, click **Load Temporary Add-on...**, and choose
   `extension/manifest.json`. (Firefox removes temporary add-ons when it restarts, so you'll need
   to re-load it after restarting.)
2. Connect it to `icp`:

   ```
   host/install.sh
   ```

3. Click the toolbar icon to see logins for the current site.

If you ever move this project to a different folder, just run `host/install.sh` again.

## Filling in passwords

- Automatic filling is enabled by default. A single matching login fills visible,
  empty username/email and current-password fields on HTTPS pages. Username-first
  flows, dynamically rendered inputs, and open shadow roots are supported.
- If there are several matching accounts, choose one in the field dropdown or
  toolbar popup. The choice is kept for 15 minutes for that tab and origin, including
  a subsequent password or verification-code page. No password is kept in extension storage.
- Stored **TOTP verification codes** fill a single code field or a labelled group of
  separate digit boxes. Expired codes are refreshed before use; a failed refresh
  leaves the field empty. Codes are never truncated to fit a shorter group.
- The popup lists active **Hide My Email** addresses. Choose one to fill the email
  field and remember that address for this origin. Unassigned addresses are not
  automatically chosen. Use the GUI to issue a new address first.
- The popup's **自動入力を有効にする** switch turns automatic filling on or off.
  Manual filling remains available. Existing input, hidden fields, new-password
  fields, unrelated forms, and cross-origin frames are not automatically filled.
  The extension does not press a submit button; some sites submit their code form
  themselves when the last digit is entered.

On NixOS, keep the unpacked extension at a stable path such as
`~/.local/share/icp/extension` and copy the package's `share/icp/extension/` there
after upgrading. Reload it at `chrome://extensions`, then reload the website.

Browser regression tests use an isolated profile and synthetic credentials:
`CHROME_BIN=google-chrome node tests/browser_autofill.mjs`. The Nix package also
runs the native-host tests and the background-worker authorization tests.

## Keeping your passwords up to date

Your passwords are a snapshot, so new or changed ones need a refresh. This happens
**automatically** in the background whenever the snapshot is more than 6 hours old - you don't have
to do anything. To refresh right now:

```
icp sync
```

## All commands

```
login    sign in, join your iCloud Keychain, and download your passwords
show     browse and search your passwords and Hide My Email addresses
sync     refresh your passwords now
logout   sign out (use --wipe-device to also forget this device)
generate-password  generate a random password locally
password add/edit/delete  manage iCloud web logins and their metadata
hme list/create/edit  manage Hide My Email aliases
```

## Credits

The GrandSlam sign-in flow is adapted from [JJTech's reference](https://gist.github.com/JJTech0130/049716196f5f1751b8944d93e73d3452).
The keychain decryption, Octagon trust join, and CloudKit transport follow
[OpenBubbles/rustpush](https://github.com/OpenBubbles/rustpush), cross-checked against
[Apple's open-source Security code](https://github.com/apple-oss-distributions/Security). Hide My
Email is ported from [dedoussis's browser extension](https://github.com/dedoussis/icloud-hide-my-email-browser-extension).
Machine data is provided by the SideStore ecosystem's `anisette-v3-server`. See `RESEARCH.md` for
how it all works.
