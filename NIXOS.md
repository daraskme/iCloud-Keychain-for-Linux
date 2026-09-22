# NixOS setup

This fork provides a Nix package for the `icp` CLI, the Chrome native messaging
host, and the unpacked browser extension. It uses the same pinned nixpkgs revision
as the NoxOS configuration.

1. Install: `nix profile add .#default` from this repository.
2. Start a local anisette server with rootless Podman:

   ```sh
   podman run --detach --name icp-anisette --restart=always \
     -p 127.0.0.1:6969:6969 docker.io/dadoum/anisette-v3-server:latest
   systemctl --user enable --now podman-restart.service
   ```

   Use `ICP_ANISETTE_URL` if the server runs elsewhere. The server only needs
   to listen on loopback on a single-user desktop.
3. Run `icp login` in your own terminal. Enter Apple credentials and any
   recovery passcode there. Never place them in shell command arguments.
   Check the passcode carefully: repeated wrong recovery attempts can lock
   keychain recovery, as noted in the upstream README.
4. In Chrome, open `chrome://extensions`, enable Developer mode, and use
   **Load unpacked** on the directory printed by
   `realpath ~/.nix-profile/share/icp/extension`.
5. Copy the resulting extension ID and run `icp-register-chrome <ID>`.
   Reload the extension.

The native host manifest points to this package's Nix store path. Re-run
`icp-register-chrome` after updating the package. The CLI keeps its account
data under `~/.config/icp`; this directory is not part of the Nix store.
