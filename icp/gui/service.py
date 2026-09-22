"""GUI operations without terminal prompts or plaintext subprocess IPC."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
from urllib.parse import quote, urlencode, urlsplit

from ..auth import session, webauth
from ..auth.anisette import Anisette
from ..auth.device import Device
from ..cli.app import _ensure_fresh_tokens, _ensure_web_session
from ..hme.client import HmeAlias, HmeClient
from ..hme.store import load_aliases, save_aliases
from ..keychain import manager
from ..keychain.sidecar import totp_from_uri
from ..octagon.client import OctagonClient, is_joined
from ..paths import sync_lock_file
from ..vault.host import Credential
from ..vault.store import load_vault


@dataclass
class Snapshot:
    credentials: list[Credential] = field(default_factory=list, repr=False)
    aliases: list[HmeAlias] = field(default_factory=list, repr=False)
    account: str = ""
    warning: str = ""


@dataclass(repr=False)
class PasswordDraft:
    site: str
    username: str
    password: str
    notes: str = ""
    totp: str = ""


def setup_uri(value: str, site: str, username: str) -> str:
    """Accept an otpauth URI or the setup key shown by a website."""
    value = value.strip()
    if not value:
        return ""
    if not value.startswith("otpauth://"):
        value = "otpauth://totp/" + quote(f"{site}:{username}", safe="") + "?" + urlencode({
            "secret": value.replace(" ", "").replace("-", "").upper(),
            "issuer": site, "algorithm": "SHA1", "digits": "6", "period": "30"})
    totp_from_uri(value)
    return value


def normalize_site(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = parsed.hostname or ""
    if "." not in host or any(c.isspace() for c in host):
        raise ValueError("サイトのドメインを入力してください（例: example.com）。")
    return host.lower()


@contextmanager
def operation_lock():
    with open(sync_lock_file(), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("ほかの同期・保存が実行中です。完了してから再実行してください。") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class Service:
    def load(self) -> Snapshot:
        record = session.load() or {}
        return Snapshot(load_vault().all(), load_aliases(), record.get("username", ""))

    def _client(self):
        record = session.load()
        if not record or not is_joined(record):
            raise RuntimeError("iCloud にログインしてください。端末で icp login を実行します。")
        device, anisette = Device.load_or_create(), Anisette()
        _ensure_fresh_tokens(record, device, anisette, interactive=False)
        session.save(record)
        client = OctagonClient(record, device, anisette)
        session.save(record)
        return client

    def _hme(self):
        record = session.load()
        if not record:
            raise RuntimeError("端末で icp login を実行してください。")
        web, data = _ensure_web_session(record, interactive=False)
        session.save(record)
        base = webauth.extract_webservices(data).get("premiummailsettings")
        if not base:
            raise RuntimeError("このアカウントでは「メールを非公開」を利用できません。")
        return HmeClient(base, web.http)

    def sync(self) -> Snapshot:
        with operation_lock():
            self._client().sync_and_decrypt()
            warning = ""
            try:
                save_aliases(self._hme().list())
            except Exception as exc:
                warning = f"パスワードは同期済みです。メールの同期に失敗しました: {exc}"
            result = self.load()
            result.warning = warning
            return result

    def _refresh_after_save(self, client) -> Snapshot:
        warning = ""
        try:
            client.sync_and_decrypt()
        except Exception as exc:
            warning = f"iCloud への保存は完了しました。一覧の再同期が必要です: {exc}"
        result = self.load()
        result.warning = warning
        return result

    def save_password(self, draft: PasswordDraft, original: Credential | None) -> Snapshot:
        if not draft.site or not draft.username or not draft.password:
            raise ValueError("サイト・ユーザー名・パスワードを入力してください。")
        if draft.totp:
            totp_from_uri(draft.totp)
        if original and (draft.site, draft.username) != (original.domain, original.username):
            raise ValueError("既存項目のサイト・ユーザー名は変更できません。")
        with operation_lock():
            client = self._client()
            password_saved = False
            try:
                if original is None:
                    manager.create_password(client, draft.site, draft.username, draft.password,
                                            notes=draft.notes, totp_uri=draft.totp)
                else:
                    if draft.password != original.password:
                        manager.edit_password(client, draft.site, draft.username, draft.password,
                                              expected_password=original.password)
                        password_saved = True
                    if (draft.notes, draft.totp) != (original.notes, original.totp):
                        manager.edit_metadata(client, draft.site, draft.username,
                                              notes=draft.notes if draft.notes != original.notes else None,
                                              totp_uri=draft.totp if draft.totp != original.totp else None,
                                              expected_metadata=(original.notes, original.totp))
            except Exception as exc:
                try:
                    client.sync_and_decrypt()
                except Exception:
                    pass
                if password_saved:
                    raise RuntimeError(f"パスワードは保存済みです。メモ・認証コードの保存に失敗しました。"
                                       f"一覧を同期してから編集をやり直してください。\n{exc}") from exc
                raise
            return self._refresh_after_save(client)

    def delete_password(self, original: Credential) -> Snapshot:
        with operation_lock():
            client = self._client()
            manager.delete_password(client, original.domain, original.username,
                                    expected_values=(original.password, original.notes, original.totp))
            return self._refresh_after_save(client)

    def save_alias(self, label: str, note: str, original: HmeAlias | None) -> Snapshot:
        label = label.strip()
        if not label:
            raise ValueError("ラベルを入力してください。")
        with operation_lock():
            client = self._hme()
            if original is None:
                address = client.generate()
                created = client.reserve(address, label, note)
                alias_id = created.anonymous_id
            else:
                current = [a for a in client.list() if a.anonymous_id == original.anonymous_id]
                if len(current) != 1 or (current[0].label, current[0].note) != (original.label, original.note):
                    raise ValueError("このアドレスは別の端末で変更されています。同期してから編集してください。")
                alias_id = original.anonymous_id
                client.update_metadata(alias_id, label, note)
            try:
                aliases = client.list()
                saved = [a for a in aliases if a.anonymous_id == alias_id]
                if len(saved) != 1 or (saved[0].label, saved[0].note) != (label, note):
                    raise RuntimeError("保存内容が一致しません。")
                if original is None and not saved[0].is_active:
                    raise RuntimeError("発行したアドレスが有効になっていません。")
                save_aliases(aliases)
            except Exception as exc:
                raise RuntimeError("iCloud は保存を受け付けましたが、結果の再取得に失敗しました。"
                                   f"再発行せず、同期で状態を確認してください。\n{exc}") from exc
            return self.load()
