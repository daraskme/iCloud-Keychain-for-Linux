# iCloud Keychain write support

The password manager should create, update, and delete web logins in iCloud,
including notes and TOTP enrolments. A local-only vault edit is not enough: the
next `icp sync` would replace it with the server snapshot.

## Current boundary

`icp password edit` now re-fetches live CKKS records, keeps each record's raw
protobuf and change tag, prepares a freshly encrypted item, and sends a
RecordSave (operation 210) request. It then re-fetches the item to verify the
edit. It can edit a web login password and, when that login already has a
`com.apple.password-manager` sidecar, its notes and TOTP enrollment.

The save request needs **both** the record's own etag and the same etag in
`RecordSaveRequest` field 4, plus `saveSemantics = 1` (`failIfOutdated`). A
disposable-record test demonstrated that omitting field 4 accepted a stale
write even when the record's own etag was stale. With field 4 present, a stale
replay was rejected. This is an observed safety property, not an assumption.

Creating/deleting logins and creating a missing metadata sidecar are still
unimplemented. Apple's own Passwords app has not yet been used to confirm that
it displays the edited test item after syncing; the verification so far is a
fresh CKKS fetch and decrypt with this client.

## Required backend work

1. Retain the raw CKKS `item` record, zone, change tag, and decrypted plist
   while presenting a credential. Keep unknown record fields and unknown plist
   keys through an edit. Match a login to its separate
   `com.apple.password-manager` sidecar by `srvr` and `acct`.
2. Encode and send CloudKit RecordModify operations with conditional saves.
   Apple's CKKS implementation uses `CKRecordSaveIfServerRecordUnchanged` for
   outgoing items, so edits must reject a stale change tag and refresh before
   retrying. Deletes must identify the exact record and zone.
3. Implement the inverse CKKS encryption path: generate and wrap a fresh item
   key under the current class key, build the authenticated metadata in sorted
   order, encode the full binary plist, pad it, and encrypt with AES-SIV.
   Preserve or increment the generation and encryption version as Apple does.
4. For notes and TOTP, update or create the per-login sidecar record. Preserve
   unrelated sidecar fields, including password history and last-used data.
   TOTP stores raw secret bytes plus algorithm, digits, period, issuer, account
   name, and optionally the original `otpauth://` URL.
5. Refresh from iCloud after every mutation and verify that another client
   reads the intended value before claiming success. Add conflict, partial
   failure, and rollback handling for multi-record edits.

## Validation

Use synthetic fixture records for encryption and transport tests. Then test
create, edit, TOTP and notes, delete, and cross-device sync with disposable
credentials on a dedicated test account. Do not mutate the user's real
password records while the write protocol is unverified.

Sources: Apple's open-source `CKKSItemEncrypter.m`, `CKKSItem.m`, and
`CKKSOutgoingQueueOperation.m` in `apple-oss-distributions/Security`, plus this
project's `RESEARCH.md` and CKKS parser.
