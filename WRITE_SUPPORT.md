# iCloud Keychain write support

The password manager should create, update, and delete web logins in iCloud,
including notes and TOTP enrolments. A local-only vault edit is not enough: the
next `icp sync` would replace it with the server snapshot.

## Current boundary

The current CKKS implementation fetches records, decrypts them, and keeps only
flattened `Credential` objects in the local vault. It does not retain the
record zone, record change tag, encrypted sidecar, or unknown plist fields that
must be preserved to edit a server item safely. `CloudKitTransport` supports
RecordRetrieveChanges (operation 213) and Cuttlefish function calls, but has no
RecordModify transport.

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
