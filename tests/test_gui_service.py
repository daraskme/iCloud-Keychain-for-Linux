import contextlib
import dataclasses
import plistlib
import unittest
from unittest.mock import Mock, patch

from icp.gui.service import PasswordDraft, Service, Snapshot, normalize_site, setup_uri
from icp.hme.client import HmeAlias
from icp.keychain import manager
from icp.vault.host import Credential


class ServiceTests(unittest.TestCase):
    def test_setup_key_and_site_validation(self):
        self.assertEqual(normalize_site("https://example.com/login"), "example.com")
        with self.assertRaises(ValueError):
            setup_uri("123456", "example.com", "user")
        uri = setup_uri("JBSW Y3DP EHPK 3PXP", "example.com", "user")
        self.assertIn("secret=JBSWY3DPEHPK3PXP", uri)
        self.assertEqual(setup_uri(uri, "example.com", "user"), uri)
        self.assertEqual(setup_uri("", "example.com", "user"), "")

    def test_stale_password_and_metadata_cannot_overwrite_newer_values(self):
        client = Mock()
        login = (Mock(), {"v_Data": b"changed elsewhere"})
        metadata = (Mock(), {"v_Data": plistlib.dumps({"notes": b"changed elsewhere"}, fmt=plistlib.FMT_BINARY)})
        with patch.object(manager, "_snapshot", return_value=({}, {})), patch.object(manager, "_target", return_value=(login, metadata)):
            with self.assertRaises(manager.PasswordEditError):
                manager.edit_password(client, "example.com", "user", "new", expected_password="old")
            with self.assertRaises(manager.PasswordEditError):
                manager.edit_metadata(client, "example.com", "user", notes="new", expected_metadata=("old", ""))
            with self.assertRaises(manager.PasswordEditError):
                manager.delete_password(client, "example.com", "user", expected_values=("old", "old", ""))
        client.transport.save_record.assert_not_called()
        client.transport.delete_record.assert_not_called()

    def test_invalid_totp_is_rejected_before_password_write(self):
        service = Service()
        service._client = Mock()
        with self.assertRaises(ValueError):
            service.save_password(PasswordDraft("example.com", "user", "new", totp="invalid"), None)
        service._client.assert_not_called()

    def test_partial_save_reports_password_saved_and_refreshes(self):
        service = Service()
        client = Mock()
        service._client = Mock(return_value=client)
        original = Credential("example.com", "user", "old", notes="old notes")
        with patch("icp.gui.service.operation_lock", contextlib.nullcontext), patch.object(manager, "edit_password"), patch.object(manager, "edit_metadata", side_effect=ValueError("conflict")):
            with self.assertRaisesRegex(RuntimeError, "パスワードは保存済み"):
                service.save_password(PasswordDraft("example.com", "user", "new", notes="new notes"), original)
        client.sync_and_decrypt.assert_called_once()

    def test_alias_conflict_does_not_write(self):
        original = HmeAlias("id", "alias@example.invalid", "old", "old note", "", True, "", 0)
        client = Mock()
        client.list.return_value = [dataclasses.replace(original, note="changed")]
        service = Service()
        service._hme = Mock(return_value=client)
        with patch("icp.gui.service.operation_lock", contextlib.nullcontext):
            with self.assertRaisesRegex(ValueError, "別の端末"):
                service.save_alias("new", "new note", original)
        client.update_metadata.assert_not_called()
        client.reserve.assert_not_called()

    def test_changed_identity_uses_original_for_lookup_and_refreshes_vault(self):
        service = Service()
        client = Mock()
        service._client = Mock(return_value=client)
        service.load = Mock(return_value=Snapshot())
        original = Credential("old.example", "alice", "old", notes="memo")
        with patch("icp.gui.service.operation_lock", contextlib.nullcontext), patch.object(manager, "rename_password") as rename:
            service.save_password(PasswordDraft("new.example", "bob", "new", notes="memo"), original)
        rename.assert_called_once_with(client, "old.example", "alice", "new.example", "bob",
                                       password="new", notes="memo", totp_uri="",
                                       expected_values=("old", "memo", ""))
        client.sync_and_decrypt.assert_called_once()

    def test_alias_create_requires_server_readback_and_active_state(self):
        alias = HmeAlias("id", "alias@example.invalid", "テスト", "メモ", "", True, "", 0)
        client = Mock()
        client.generate.return_value = alias.address
        client.reserve.return_value = alias
        client.list.return_value = [alias]
        service = Service()
        service._hme = Mock(return_value=client)
        service.load = Mock(return_value=Snapshot(aliases=[alias]))
        with patch("icp.gui.service.operation_lock", contextlib.nullcontext), patch("icp.gui.service.save_aliases") as cache:
            result = service.save_alias("テスト", "メモ", None)
            self.assertEqual(result.aliases, [alias])
            cache.assert_called_once_with([alias])
            client.list.return_value = [dataclasses.replace(alias, is_active=False)]
            with self.assertRaisesRegex(RuntimeError, "再発行せず"):
                service.save_alias("テスト", "メモ", None)
