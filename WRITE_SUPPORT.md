# iCloud Keychain write support

The password manager should create, update, and delete web logins in iCloud,
including notes and TOTP enrolments. A local-only vault edit is not enough: the
next `icp sync` would replace it with the server snapshot.

## Current boundary

`icp password add`, `edit`, and `delete` now re-fetch live CKKS records, keep each record's raw
protobuf and change tag, prepares a freshly encrypted item, and sends a
RecordSave (operation 210) request. It then re-fetches the item to verify the
edit. It can edit a web login password, its notes, and TOTP enrollment. If an
existing login has no metadata sidecar, the first notes/TOTP edit creates one. Creation
uses `saveSemantics = 2` (`failIfExists`) and creates both a login and metadata
sidecar. Deletion uses RecordDelete (operation 214) with the current etag in
field 2 and verifies that both records disappear.

The save request needs **both** the record's own etag and the same etag in
`RecordSaveRequest` field 4, plus `saveSemantics = 1` (`failIfOutdated`). A
disposable-record test demonstrated that omitting field 4 accepted a stale
write even when the record's own etag was stale. With field 4 present, a stale
replay was rejected. This is an observed safety property, not an assumption.

The iPhone/iPad Passwords app has displayed the edited note and TOTP, and a
newly created login with its note and TOTP. It also stopped displaying a
deleted disposable login. The `add` path creates a sidecar for new logins.
Multi-record create/delete operations are sequential rather than atomic, so a
partial failure may require cleanup. A failed sidecar creation attempts to
delete the login it just created.

## Implementation

1. Existing items are fetched immediately before a write. Their raw protobuf,
   zone, etag, and unknown fields are preserved. Logins and metadata sidecars
   are matched by exact `srvr` and `acct` values.
2. Edits generate a fresh 64-byte item key, wrap it under the current class
   key, encrypt a padded binary plist with AES-SIV, and replace only the CKKS
   `data` and `wrappedkey` fields. Other item plist keys, including password
   history, remain intact.
3. A save includes `RecordSaveRequest.etag` and fail-if-outdated semantics.
   Create uses fail-if-exists. Delete includes the record identifier and etag.
4. Notes are UTF-8 bytes in the metadata sidecar. TOTP stores raw secret bytes,
   algorithm, digits, period, issuer, account name, and the original URI.
5. Every mutation fetches and decrypts the result. Write-path fetches are
   strict: a failed zone request cannot be mistaken for an absent record.

## Remaining limits

- GUI identity edits update `srvr` and `acct` on the existing login and its
  metadata sidecar, retaining record IDs, unknown fields, and unchanged secrets.
  Destination collisions and stale password/metadata values are rejected before
  writes. Each update uses the current etag and verifies the decrypted read-back.
  If a write or verification fails, attempted records are fetched again and
  restored only if they still equal this operation's candidate values. Concurrent
  edits are never overwritten by rollback. A failed rollback is reported explicitly.
- Identity changes span sequential server writes, not a server transaction.
  Process termination or power loss between writes can leave a partial rename;
  there is no durable recovery journal. Sync and inspect both identities before
  retrying after an interrupted save. Destination checks are snapshot checks;
  another device creating a duplicate during the operation cannot be prevented.
- Identity editing has synthetic encrypted-server and Qt tests. Live iCloud
  rename and Apple-device display have not been validated for this change.

- Creation needs a usable web-login template and metadata template from this
  account. It fails before writing if either is absent.
- Login and metadata records are created or deleted sequentially. A failed
  create attempts to roll back both new UUIDs; a partial delete is reported.

## Validation

Synthetic fixture tests cover encryption and record encoding. Disposable
records were then created, edited, and deleted on a live account. The user's
iPhone/iPad Passwords app displayed the new login, note, TOTP, and changed
password, and stopped displaying the deleted test login. A stale save and a
stale delete were both rejected by CloudKit.

Sources: Apple's open-source `CKKSItemEncrypter.m`, `CKKSItem.m`, and
`CKKSOutgoingQueueOperation.m` in `apple-oss-distributions/Security`, plus this
project's `RESEARCH.md` and CKKS parser.
