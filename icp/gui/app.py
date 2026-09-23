"""Native Qt desktop interface for iCloud Passwords."""
from __future__ import annotations

import math
import sys
import time
import uuid

from PySide6.QtCore import QMimeData, QSize, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter, QVBoxLayout, QWidget,
)

from ..passwords import generate as generate_password
from ..totp import generate as generate_totp
from .service import PasswordDraft, Service, Snapshot, normalize_site, setup_uri


STYLE = """
QMainWindow, QDialog { background: #f5f6fa; color: #18233b; }
QWidget { font-size: 14px; }
QLabel { color: #18233b; }
QLabel#muted { color: #68758c; }
QLabel#heading { font-size: 27px; font-weight: 700; }
QLabel#detailHeading { font-size: 22px; font-weight: 600; }
QLabel#code { font-size: 32px; font-weight: 600; color: #5951be; }
QLabel#error { color: #b33740; }
QFrame#sidebar { background: #19233b; border-radius: 14px; }
QFrame#sidebar QLabel { color: #dce3f5; }
QFrame#sidebar QPushButton { background: transparent; color: #dce3f5; border: 0; text-align: left; padding: 13px; }
QFrame#sidebar QPushButton:checked { background: #394463; color: white; }
QPushButton { background: white; color: #263451; border: 1px solid #dce1ed; border-radius: 8px; padding: 9px 13px; min-height: 20px; }
QPushButton:hover { background: #edf0fa; }
QPushButton:disabled { color: #939bae; background: #eff1f6; }
QPushButton#primary { background: #6259cf; color: white; border: 0; }
QPushButton#primary:hover { background: #534ab7; }
QPushButton#danger { color: #b33740; }
QLineEdit, QPlainTextEdit, QSpinBox { background: white; color: #18233b; border: 1px solid #dce1ed; border-radius: 7px; padding: 9px; selection-background-color: #6259cf; }
QLineEdit, QSpinBox { min-height: 20px; }
QLineEdit:read-only { background: #f1f3f8; }
QListWidget { background: white; color: #18233b; border: 1px solid #e0e5ef; border-radius: 12px; padding: 5px; outline: 0; }
QListWidget::item { padding: 15px 12px; border-radius: 7px; margin: 2px; }
QListWidget::item:selected { background: #eeebff; color: #342e79; }
QFrame#detail { background: white; border: 1px solid #e0e5ef; border-radius: 12px; }
QProgressBar { border: 0; border-radius: 3px; background: #e7e9f3; max-height: 5px; }
QProgressBar::chunk { background: #6259cf; border-radius: 3px; }
QSplitter::handle { background: transparent; width: 12px; }
"""


def label(text, name="", wrap=False):
    result = QLabel(text)
    result.setTextFormat(Qt.TextFormat.PlainText)
    result.setWordWrap(wrap)
    result.setObjectName(name)
    return result


def button(text, callback, name=""):
    result = QPushButton(text)
    result.setObjectName(name)
    result.clicked.connect(callback)
    return result


class SecretField(QWidget):
    def __init__(self, value="", readonly=False, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setReadOnly(readonly)
        self.toggle = button("表示", self.reveal)
        row.addWidget(self.edit, 1)
        row.addWidget(self.toggle)

    def reveal(self):
        hidden = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.toggle.setText("隠す" if hidden else "表示")

    def text(self):
        return self.edit.text()


class GeneratorDialog(QDialog):
    def __init__(self, parent, use_value=False):
        super().__init__(parent)
        self.setWindowTitle("安全なパスワードを生成")
        self.setMinimumWidth(510)
        layout = QVBoxLayout(self)
        layout.setSpacing(18)
        layout.addWidget(label("パスワード生成", "detailHeading"))
        layout.addWidget(label("英大文字・小文字・数字・記号を含めて生成します。", "muted", True))
        form = QFormLayout()
        self.length = QSpinBox()
        self.length.setRange(12, 128)
        self.length.setValue(24)
        form.addRow("文字数", self.length)
        layout.addLayout(form)
        self.password = SecretField()
        layout.addWidget(self.password)
        row = QHBoxLayout()
        row.addWidget(button("もう一度生成", self.regenerate))
        row.addWidget(button("コピー", lambda: parent.window().copy_secret(self.password.text())))
        layout.addLayout(row)
        layout.addWidget(label("生成しただけでは iCloud に保存されません。", "muted", True))
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("閉じる")
        box.rejected.connect(self.reject)
        if use_value:
            use = box.addButton("このパスワードを使う", QDialogButtonBox.ButtonRole.AcceptRole)
            use.setObjectName("primary")
            box.accepted.connect(self.accept)
        layout.addWidget(box)
        self.length.valueChanged.connect(self.regenerate)
        self.regenerate()

    def regenerate(self, *_):
        self.password.edit.setText(generate_password(self.length.value()))


class SaveDialog(QDialog):
    busy = False

    def reject(self):
        if not self.busy:
            super().reject()

    def closeEvent(self, event):
        if self.busy:
            event.ignore()
        else:
            super().closeEvent(event)


class PasswordDialog(SaveDialog):
    submitted = Signal(object)

    def __init__(self, parent, original=None):
        super().__init__(parent)
        self.original = original
        self.setWindowTitle("パスワードを編集" if original else "パスワードを追加")
        self.setMinimumWidth(610)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.addWidget(label(self.windowTitle(), "detailHeading"))
        form = QFormLayout()
        form.setSpacing(12)
        self.site = QLineEdit(original.domain if original else "")
        self.site.setPlaceholderText("example.com")
        self.username = QLineEdit(original.username if original else "")
        self.username.setPlaceholderText("ユーザー名またはメールアドレス")
        self.password = SecretField(original.password if original else "")
        self.totp = SecretField(original.totp if original else "")
        self.totp.edit.setPlaceholderText("設定キー または otpauth://totp/…")
        self.notes = QPlainTextEdit(original.notes if original else "")
        self.notes.setMinimumHeight(110)
        form.addRow("サイト", self.site)
        form.addRow("ユーザー名", self.username)
        form.addRow("パスワード", self.password)
        form.addRow("", button("安全なパスワードを生成…", self.generate))
        form.addRow("認証コードの設定", self.totp)
        hint = label("6桁のコードではなく、サイトから提供された設定キーを入力します。\n空欄で保存すると認証コードの設定を削除します。", "muted", True)
        form.addRow("", hint)
        form.addRow("メモ", self.notes)
        layout.addLayout(form)
        self.error = label("", "error", True)
        layout.addWidget(self.error)
        self.box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Save).setText("iCloud に保存")
        self.box.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        self.box.accepted.connect(self.submit)
        self.box.rejected.connect(self.reject)
        layout.addWidget(self.box)

    def generate(self):
        dialog = GeneratorDialog(self.parent(), use_value=True)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.password.edit.setText(dialog.password.text())

    def submit(self):
        try:
            site = (self.original.domain if self.original and self.site.text() == self.original.domain
                    else normalize_site(self.site.text()))
            username = (self.original.username if self.original and self.username.text() == self.original.username
                        else self.username.text().strip())
            if not username or not self.password.text():
                raise ValueError("ユーザー名とパスワードを入力してください。")
            draft = PasswordDraft(site, username, self.password.text(), self.notes.toPlainText(),
                                  setup_uri(self.totp.text(), site, username))
        except ValueError as exc:
            self.error.setText(str(exc))
            return
        self.error.clear()
        self.saved_identity = (site, username)
        self.submitted.emit(draft)


class AliasDialog(SaveDialog):
    submitted = Signal(str, str)

    def __init__(self, parent, original=None):
        super().__init__(parent)
        self.setWindowTitle("メールのラベル・メモを編集" if original else "メールを非公開を発行")
        self.setMinimumWidth(520)
        self.setModal(True)
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.addWidget(label(self.windowTitle(), "detailHeading"))
        layout.addWidget(label(original.address if original else "新しいアドレスを発行し、iCloud の転送先に届けます。", "muted", True))
        form = QFormLayout()
        self.label = QLineEdit(original.label if original else "")
        self.label.setPlaceholderText("例: ショッピング")
        self.note = QPlainTextEdit(original.note if original else "")
        self.note.setMinimumHeight(120)
        form.addRow("ラベル", self.label)
        form.addRow("メモ", self.note)
        layout.addLayout(form)
        self.error = label("", "error", True)
        layout.addWidget(self.error)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        box.button(QDialogButtonBox.StandardButton.Save).setText("保存" if original else "アドレスを発行")
        box.button(QDialogButtonBox.StandardButton.Save).setObjectName("primary")
        box.button(QDialogButtonBox.StandardButton.Cancel).setText("キャンセル")
        box.accepted.connect(self.submit)
        box.rejected.connect(self.reject)
        layout.addWidget(box)

    def submit(self):
        if not self.label.text().strip():
            self.error.setText("ラベルを入力してください。")
            return
        self.error.clear()
        self.submitted.emit(self.label.text().strip(), self.note.toPlainText())


class Job(QThread):
    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.operation()
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.operation = None


class MainWindow(QMainWindow):
    def __init__(self, service=None, autoload=True):
        super().__init__()
        self.service = service or Service()
        self.snapshot = Snapshot()
        self.section = "passwords"
        self.job = None
        self.dialog = None
        self.current = None
        self.next_selection = None
        self.copy_token = None
        self.setWindowTitle("iCloud パスワード")
        self.setWindowIcon(QIcon.fromTheme("dialog-password"))
        self.resize(1150, 850)
        self.setMinimumSize(930, 640)
        self.root = QWidget()
        self.setCentralWidget(self.root)
        outer = QHBoxLayout(self.root)
        outer.setContentsMargins(18, 18, 18, 18)
        outer.setSpacing(22)
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(205)
        nav = QVBoxLayout(sidebar)
        nav.setContentsMargins(15, 24, 15, 20)
        nav.setSpacing(12)
        brand = label("iCloud\nパスワード")
        brand.setStyleSheet("font-size: 23px; font-weight: 700; padding: 8px;")
        nav.addWidget(brand)
        nav.addSpacing(22)
        self.password_nav = button("パスワード", lambda: self.navigate("passwords"))
        self.alias_nav = button("メールを非公開", lambda: self.navigate("aliases"))
        for item in (self.password_nav, self.alias_nav):
            item.setCheckable(True)
            nav.addWidget(item)
        self.password_nav.setChecked(True)
        nav.addStretch()
        nav.addWidget(button("パスワード生成", self.generate))
        self.account = label("", wrap=True)
        self.account.setStyleSheet("font-size: 12px; padding: 8px;")
        nav.addWidget(self.account)
        outer.addWidget(sidebar)
        content = QVBoxLayout()
        content.setSpacing(16)
        heading = QHBoxLayout()
        titles = QVBoxLayout()
        self.heading = label("パスワード", "heading")
        self.subtitle = label("iCloud に保存したログイン情報", "muted")
        titles.addWidget(self.heading)
        titles.addWidget(self.subtitle)
        heading.addLayout(titles, 1)
        self.sync_button = button("同期", self.sync)
        self.add_button = button("＋ 追加", self.add, "primary")
        heading.addWidget(self.sync_button)
        heading.addWidget(self.add_button)
        content.addLayout(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("サイト・ユーザー名を検索")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_list)
        content.addWidget(self.search)
        self.splitter = QSplitter()
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.select)
        self.splitter.addWidget(self.list)
        self.detail = QFrame()
        self.detail.setObjectName("detail")
        self.detail_layout = QVBoxLayout(self.detail)
        self.detail_layout.setContentsMargins(24, 25, 24, 24)
        self.detail_layout.setSpacing(9)
        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.detail_scroll.setWidget(self.detail)
        self.splitter.addWidget(self.detail_scroll)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setSizes([330, 490])
        content.addWidget(self.splitter, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.hide()
        content.addWidget(self.progress)
        self.status = label("保存済みのデータを読み込みます。", "muted", True)
        content.addWidget(self.status)
        outer.addLayout(content, 1)
        self.clock = QTimer(self)
        self.clock.timeout.connect(self.update_code)
        self.clock.start(250)
        self.clip_timer = QTimer(self)
        self.clip_timer.setSingleShot(True)
        self.clip_timer.timeout.connect(self.clear_clipboard)
        self.show_empty()
        if autoload:
            QTimer.singleShot(0, lambda: self.start_job(self.service.load, "保存済みデータを読み込み中…"))

    def start_job(self, operation, message, dialog=None, success_message=""):
        if self.job is not None:
            return
        self.dialog = dialog
        self.success_message = success_message
        self.root.setEnabled(False)
        if dialog:
            dialog.busy = True
            dialog.setEnabled(False)
        self.progress.show()
        self.status.setText(message)
        self.job = Job(operation, self)
        self.job.finished.connect(self.job_finished)
        self.job.start()

    @Slot()
    def job_finished(self):
        job, dialog = self.job, self.dialog
        job.wait()
        self.job = None
        self.dialog = None
        self.root.setEnabled(True)
        self.progress.hide()
        if dialog:
            dialog.busy = False
            dialog.setEnabled(True)
        if job.error:
            self.status.setText("処理を完了できませんでした。")
            if dialog:
                dialog.error.setText(job.error)
            else:
                self.message("処理に失敗しました", job.error)
        else:
            if dialog:
                if isinstance(dialog, PasswordDialog):
                    self.next_selection = dialog.saved_identity
                elif isinstance(dialog, AliasDialog):
                    previous = {a.anonymous_id for a in self.snapshot.aliases}
                    new = [a for a in job.result.aliases if a.anonymous_id not in previous]
                    if len(new) == 1:
                        self.next_selection = new[0].anonymous_id
                dialog.accept()
            self.apply_snapshot(job.result)
            if not job.result.warning:
                if dialog:
                    self.status.setText("iCloud に保存し、内容を確認しました。")
                elif self.success_message:
                    self.status.setText(self.success_message)
            if job.result.warning:
                self.message("保存・同期の確認", job.result.warning)
        job.result = None
        job.deleteLater()

    def apply_snapshot(self, snapshot):
        self.snapshot = snapshot
        self.account.setText(snapshot.account or "ログインが必要です")
        self.password_nav.setText(f"パスワード  {len(snapshot.credentials)}")
        self.alias_nav.setText(f"メールを非公開  {len(snapshot.aliases)}")
        self.refresh_list()
        self.status.setText(snapshot.warning or "準備完了 · 最新の状態を取得するには「同期」を押してください。")

    def message(self, title, text):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(text)
        box.exec()

    def navigate(self, section):
        self.section = section
        passwords = section == "passwords"
        self.password_nav.setChecked(passwords)
        self.alias_nav.setChecked(not passwords)
        self.heading.setText("パスワード" if passwords else "メールを非公開")
        self.subtitle.setText("iCloud に保存したログイン情報" if passwords else "メールアドレスと転送先を管理")
        self.add_button.setText("＋ 追加" if passwords else "＋ アドレスを発行")
        self.search.setPlaceholderText("サイト・ユーザー名を検索" if passwords else "ラベル・アドレス・メモを検索")
        self.search.clear()
        self.refresh_list()

    def identity(self, value):
        if value is None:
            return None
        return (value.domain, value.username) if hasattr(value, "password") else value.anonymous_id

    def refresh_list(self, *_):
        selected = self.next_selection or self.identity(self.current)
        self.next_selection = None
        self.list.blockSignals(True)
        self.list.clear()
        query = self.search.text().casefold()
        passwords = self.section == "passwords"
        values = self.snapshot.credentials if passwords else self.snapshot.aliases
        values = sorted(values, key=lambda v: (v.domain if passwords else v.label).casefold())
        selected_row = 0
        for value in values:
            title = value.domain if passwords else (value.label or value.address)
            subtitle = value.username if passwords else value.address
            haystack = f"{title} {subtitle}" if passwords else f"{title} {subtitle} {value.note}"
            if query not in haystack.casefold():
                continue
            text = title + "\n" + subtitle
            if not passwords and not value.is_active:
                text += " · 無効"
            item = QListWidgetItem(text)
            item.setSizeHint(QSize(0, 76))
            item.setToolTip(text)
            item.setData(Qt.ItemDataRole.UserRole, value)
            if self.identity(value) == selected:
                selected_row = self.list.count()
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self.list.count():
            self.list.setCurrentRow(selected_row)
        else:
            self.current = None
            self.show_empty()

    def clear_detail(self):
        self.code_label = None
        self.code_progress = None
        self.code_remaining = None
        while self.detail_layout.count():
            item = self.detail_layout.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()

    def show_empty(self):
        self.clear_detail()
        self.detail_layout.addStretch()
        self.detail_layout.addWidget(label("項目を選択してください", "detailHeading", True))
        self.detail_layout.addWidget(label("「追加」から新しい項目を作成できます。", "muted", True))
        self.detail_layout.addStretch()

    def copy_row(self, caption, value, secret=False):
        self.detail_layout.addWidget(label(caption, "muted"))
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        field = SecretField(value, readonly=True) if secret else QLineEdit(value)
        if not secret:
            field.setReadOnly(True)
        row.addWidget(field, 1)
        row.addWidget(button("コピー", lambda: self.copy_secret(value)))
        self.detail_layout.addWidget(container)

    def select(self, item, *_):
        if item is None:
            return
        self.current = value = item.data(Qt.ItemDataRole.UserRole)
        self.clear_detail()
        passwords = self.section == "passwords"
        self.detail_layout.addWidget(label(value.domain if passwords else value.label, "detailHeading", True))
        if passwords:
            self.copy_row("ユーザー名", value.username)
            self.copy_row("パスワード", value.password, True)
            self.detail_layout.addWidget(label("認証コード", "muted"))
            self.code_label = label("", "code")
            self.detail_layout.addWidget(self.code_label)
            self.code_remaining = label("", "muted")
            self.detail_layout.addWidget(self.code_remaining)
            self.code_progress = QProgressBar()
            self.code_progress.setTextVisible(False)
            self.detail_layout.addWidget(self.code_progress)
            if value.totp:
                self.detail_layout.addWidget(button("認証コードをコピー", self.copy_code))
            self.update_code()
            notes = value.notes
        else:
            self.copy_row("メールアドレス", value.address)
            self.copy_row("転送先", value.forward_to)
            self.detail_layout.addWidget(label("有効" if value.is_active else "無効", "muted"))
            notes = value.note
        self.detail_layout.addWidget(label("メモ", "muted"))
        note = QPlainTextEdit(notes)
        note.setReadOnly(True)
        note.setMinimumHeight(70)
        self.detail_layout.addWidget(note, 1)
        self.detail_layout.addWidget(button("編集", self.edit, "primary"))
        if passwords:
            self.detail_layout.addWidget(button("このパスワードを削除…", self.delete, "danger"))

    def update_code(self):
        if self.code_label is None or self.current is None or self.section != "passwords":
            return
        result = generate_totp(self.current.totp)
        if not result:
            self.code_label.setText("未設定")
            self.code_remaining.setText("編集から設定キーを追加できます。")
            self.code_progress.hide()
            return
        code = result["code"]
        self.code_label.setText(code[:len(code)//2] + " " + code[len(code)//2:])
        remaining = max(0, math.ceil(result["expires"] - time.time()))
        self.code_remaining.setText(f"あと {remaining} 秒で更新")
        self.code_progress.setRange(0, result["period"])
        self.code_progress.setValue(remaining)
        self.code_progress.show()

    def copy_code(self):
        if self.current and self.section == "passwords":
            result = generate_totp(self.current.totp)
            if result:
                self.copy_secret(result["code"])

    def copy_secret(self, value):
        self.copy_token = uuid.uuid4().hex.encode()
        mime = QMimeData()
        mime.setText(value)
        mime.setData("x-kde-passwordManagerHint", b"secret")
        mime.setData("application/x-icp-clipboard", self.copy_token)
        QApplication.clipboard().setMimeData(mime)
        self.clip_timer.start(30000)
        self.status.setText("コピーしました。30秒後に、このコピー内容をクリップボードから消去します。")

    def clear_clipboard(self):
        clipboard = QApplication.clipboard()
        mime = clipboard.mimeData()
        if self.copy_token and mime and bytes(mime.data("application/x-icp-clipboard")) == self.copy_token:
            clipboard.clear()
        self.copy_token = None

    def sync(self):
        self.start_job(self.service.sync, "iCloud と同期中…", success_message="iCloud と同期しました。")

    def generate(self):
        GeneratorDialog(self).exec()

    def add(self):
        self.open_editor(None)

    def edit(self):
        if self.current:
            self.open_editor(self.current)

    def open_editor(self, original):
        if self.section == "passwords":
            dialog = PasswordDialog(self, original)
            dialog.submitted.connect(lambda draft: self.start_job(
                lambda: self.service.save_password(draft, original), "iCloud に保存・確認中…", dialog))
        else:
            dialog = AliasDialog(self, original)
            dialog.submitted.connect(lambda title, note: self.start_job(
                lambda: self.service.save_alias(title, note, original), "メールアドレスを保存・確認中…", dialog))
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.open()

    def delete(self):
        original = self.current
        if original is None or self.section != "passwords":
            return
        box = QMessageBox(self)
        box.setWindowTitle("パスワードを削除")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(f"{original.domain}\n{original.username}\n\niCloud からパスワード・メモ・認証コードを削除します。")
        cancel = box.addButton("キャンセル", QMessageBox.ButtonRole.RejectRole)
        remove = box.addButton("削除", QMessageBox.ButtonRole.DestructiveRole)
        box.setDefaultButton(cancel)
        box.exec()
        if box.clickedButton() == remove:
            self.start_job(lambda: self.service.delete_password(original), "iCloud から削除・確認中…",
                           success_message="iCloud から削除したことを確認しました。")

    def closeEvent(self, event):
        if self.job is not None:
            self.status.setText("処理が完了してから閉じてください。")
            event.ignore()
            return
        self.clear_clipboard()
        super().closeEvent(event)


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("iCloud Passwords")
    app.setDesktopFileName("org.icp.Passwords")
    app.setStyle("Fusion")
    app.setStyleSheet(STYLE)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
