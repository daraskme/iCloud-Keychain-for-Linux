# Linux版 iCloud Passwords の構成

2026-09-23 にインストール構成とソースを確認。実際のサイト一覧、ユーザー名、
パスワード、Apple認証情報はこの文書やリポジトリに含めない。

## アプリと保存の流れ

| 部分 | 実装 | 役割 |
| --- | --- | --- |
| デスクトップGUI | `icp/gui/app.py` | PySide6画面、検索、編集、生成、認証コード表示 |
| GUIサービス | `icp/gui/service.py` | 同期・保存の排他制御、認証更新、保存後の再同期 |
| CLI | `icp/cli/app.py` | 初回ログイン、端末参加、同期、パスワード管理 |
| Apple認証 | `icp/auth/`, `icp/escrow/`, `icp/octagon/` | 認証、鍵の復旧、Octagon参加・鍵取得 |
| Anisette | rootless Podmanの `icp-anisette` | ループバックの6969番ポートで認証用情報を提供 |
| 取得・復号 | `icp/transport/`, `icp/keychain/pipeline.py` | CloudKit/CKKS取得、TLK・クラス鍵・項目の復号 |
| 更新 | `icp/keychain/manager.py`, `write.py` | 対象照合、再暗号化、etag付き保存、再取得による照合 |
| ローカル保管 | `icp/vault/store.py`, `icp/auth/session.py` | libsodium SecretBoxによる暗号化保存 |
| メモ・TOTP | `icp/keychain/sidecar.py` | 同じサイト・ユーザー名に紐づく別レコード |
| メールを非公開 | `icp/hme/` | Webセッション経由のエイリアス管理、暗号化キャッシュ |

ランタイムデータは `$XDG_CONFIG_HOME/icp`（通常 `~/.config/icp`）にある。
セッション、パスワードキャッシュ、メールキャッシュはそれぞれ
`session.enc`、`vault.enc`、`aliases.enc`。マスター鍵はSecret Serviceの
ログインキーリング、利用できない場合は権限0600の `master.key` に保存される。
これらや端末識別情報の `device.json` はソースコードではない。

## Chrome連携

関連拡張はローカル配置の **Apple Passwords 0.2.2**。
配置先は `~/.local/share/icp/extension`。調査時の実装ファイル4つは
このリポジトリの `extension/` と一致していた。

- `content.js`: フォーム検出、候補表示、ユーザー名・パスワード・コードの入力。
- `background.js`: ページのオリジン確認、入力先ドキュメント指定、ネイティブ通信。
- `popup.html` / `popup.js`: 候補選択、コピー、自動入力設定、メールエイリアス選択。
- `org.icp.native`: Chrome Native Messagingホスト。
  登録ファイルは `~/.config/google-chrome/NativeMessagingHosts/org.icp.native.json`。
  許可された拡張IDから `icp.vault.host` を起動する。
- ホストは暗号化キャッシュを読み、サイトに一致する候補を返す。
  `sendNativeMessage` ごとの起動なのでGUI保存後の再同期結果を次の要求で読む。
  キャッシュが古い場合はバックグラウンド同期も起動する。

拡張の設定・一時的なアカウント選択はChrome storageで管理する。
サイトとユーザー名の変更はGUI側で行い、iCloudへの保存後にキャッシュを再構築する。
拡張ソースの変更は今回不要。

同じChrome環境にはuBlock Origin Lite、uAutoPagerize、Google オフライン
ドキュメント等も存在したが、今回のiCloud保存・編集経路には含まれない。

## 今回の原因と修正

既存項目の入力欄がread-onlyで、GUIサービスも変更を拒否していた。
入力欄を編集可能にし、元のサイト・ユーザー名で既存レコードを取得してから、
ログインとメタデータのサイト・ユーザー名を更新する保存処理を追加した。
既存ID、パスワード、メモ、TOTPと未知のフィールドは、明示的に編集した値以外は維持する。
衝突・古い編集内容・途中失敗の扱いは `WRITE_SUPPORT.md` を参照。

## 公開リポジトリへの追加ルール

- 公開先は `daraskme/iCloud-Keychain-for-Linux`。ソースと合成データのテストだけを追加する。
- `~/.config/icp`、Chromeプロファイル、キーリング、認証サーバーのデータをコピーしない。
- `.gitignore` は暗号化データ・鍵・端末情報・環境設定・ログ・バックアップを除外する。
  `git add -f` でこれらを追加しない。
- コミット前に `git diff --cached` と対象ファイル名を確認する。
  `.gitignore` は、ソースや文書に貼り付けた秘密情報まで検出するものではない。
- 本番の保存データを使ったテスト結果やスクリーンショットをGitに追加しない。

## 検証と反映

`nix build .#default` がGUI・保存処理のPythonテストと拡張バックグラウンドの
Nodeテストを実行する。テストは合成データを使い、実アカウントの登録内容は変更しない。
インストール後は `icp-register-chrome <現在の拡張ID>` でネイティブホストの参照を更新する。
既に起動しているGUIは閉じて開き直すと新しい実装に切り替わる。
