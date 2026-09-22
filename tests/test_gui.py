"""Offline Qt interaction tests; all credentials are synthetic."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import threading
import time
import unittest
from unittest.mock import Mock

from PySide6.QtCore import QEvent, QMimeData
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLineEdit

from icp.gui.app import AliasDialog, GeneratorDialog, MainWindow, PasswordDialog, SecretField
from icp.gui.service import Snapshot, setup_uri
from icp.hme.client import HmeAlias
from icp.vault.host import Credential

KEY = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
URI = setup_uri(KEY, "example.com", "alice")
CREDENTIAL = Credential("example.com", "alice", "synthetic-only!", notes="元のメモ", totp=URI)
ALIAS = HmeAlias("test-id", "demo@example.invalid", "買い物", "メモ", "user@example.invalid", True, "", 0)


class GuiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def tearDownClass(cls):
        cls.app.clipboard().clear()
        cls.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cls.app.processEvents()

    def setUp(self):
        self.service = Mock()
        self.window = MainWindow(self.service, autoload=False)
        self.window.apply_snapshot(Snapshot([CREDENTIAL], [ALIAS], "demo@example.invalid"))
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def test_search_selection_masks_password_and_updates_code(self):
        self.assertEqual(self.window.list.count(), 1)
        secret = self.window.detail.findChild(SecretField)
        self.assertEqual(secret.edit.echoMode(), QLineEdit.EchoMode.Password)
        self.assertEqual(len(self.window.code_label.text().replace(" ", "")), 6)
        self.window.search.setText("missing")
        self.assertEqual(self.window.list.count(), 0)
        self.assertIsNone(self.window.current)
        self.window.search.clear()
        self.assertEqual(self.window.current.username, "alice")
        self.window.navigate("aliases")
        self.assertEqual(self.window.current.anonymous_id, "test-id")

    def test_editor_validates_code_before_submitting_and_preserves_multiline_notes(self):
        dialog = PasswordDialog(self.window, CREDENTIAL)
        drafts = []
        dialog.submitted.connect(drafts.append)
        dialog.notes.setPlainText("一行目\n二行目")
        dialog.totp.edit.setText("123456")
        dialog.submit()
        self.assertFalse(drafts)
        self.assertTrue(dialog.error.text())
        dialog.totp.edit.setText(KEY)
        dialog.submit()
        self.assertEqual(drafts[0].notes, "一行目\n二行目")
        self.assertEqual(drafts[0].password, CREDENTIAL.password)
        self.assertTrue(dialog.site.isReadOnly())
        dialog.totp.edit.clear()
        dialog.submit()
        self.assertEqual(drafts[1].totp, "")
        dialog.close()

    def test_generator_and_alias_dialog(self):
        generator = GeneratorDialog(self.window, use_value=True)
        generator.length.setValue(32)
        self.assertEqual(len(generator.password.text()), 32)
        previous = generator.password.text()
        generator.regenerate()
        self.assertNotEqual(previous, generator.password.text())
        generator.close()
        dialog = AliasDialog(self.window)
        submitted = []
        dialog.submitted.connect(lambda *args: submitted.append(args))
        dialog.submit()
        self.assertFalse(submitted)
        dialog.label.setText("テスト")
        dialog.note.setPlainText("メモ\n二行目")
        dialog.submit()
        self.assertEqual(submitted, [("テスト", "メモ\n二行目")])
        dialog.close()

    def test_clipboard_expiry_preserves_a_later_copy(self):
        self.window.copy_secret("test-secret")
        self.window.clear_clipboard()
        self.assertEqual(self.app.clipboard().text(), "")
        self.window.copy_secret("test-secret")
        other = QMimeData()
        other.setText("copied elsewhere")
        self.app.clipboard().setMimeData(other)
        self.window.clear_clipboard()
        self.assertEqual(self.app.clipboard().text(), "copied elsewhere")

    def test_background_save_keeps_draft_and_prevents_close_until_finished(self):
        dialog = PasswordDialog(self.window, CREDENTIAL)
        dialog.show()
        release = threading.Event()

        def slow_failure():
            release.wait(2)
            raise ValueError("synthetic conflict")

        self.window.start_job(slow_failure, "saving", dialog)
        self.window.close()
        dialog.reject()
        self.assertTrue(self.window.isVisible())
        self.assertTrue(dialog.isVisible())
        self.assertTrue(dialog.busy)
        release.set()
        deadline = time.monotonic() + 3
        while self.window.job is not None and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertIsNone(self.window.job)
        self.assertFalse(dialog.busy)
        self.assertIn("synthetic conflict", dialog.error.text())
        self.assertEqual(dialog.password.text(), CREDENTIAL.password)
        dialog.close()
