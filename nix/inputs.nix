# nix/inputs.nix — Stable-Nix access to the non-nixpkgs build inputs.
#
# The flake pins uv2nix / pyproject-nix / build-system-pkgs in flake.lock.
# This file reads that same lock and fetches each input by its locked rev +
# narHash, then imports the input's classic (non-flake) entrypoint. Flake and
# non-flake consumers therefore share ONE source of truth — the lock — with no
# duplicated revisions to keep in sync.
#
# Each value is shaped to match the corresponding flake input, so downstream
# files (python.nix etc.) consume them identically whether wired from the flake
# or from here:
#   uv2nix.lib.workspace ...                 (flake `lib` output)
#   pyproject-nix.build.{hacks,packages} ... (flake `build` output)
#   pyproject-build-systems.overlays.default (flake `overlays.default` output)
#
# Stable-Nix usage:
#   let inputs = import ./nix/inputs.nix { inherit (pkgs) lib; }; in ...
{
  lib ? (import <nixpkgs> { }).lib,
}:
let
  lock = builtins.fromJSON (builtins.readFile ../flake.lock);

  # Fetch a locked github node by rev, verified against its narHash. Using the
  # lock's narHash keeps the fetch pure and identical to what the flake fetched.
  fetchLocked =
    name:
    let
      node = lock.nodes.${name}.locked;
    in
    assert node.type == "github";
    builtins.fetchTarball {
      url = "https://github.com/${node.owner}/${node.repo}/archive/${node.rev}.tar.gz";
      sha256 = node.narHash;
    };

  # pyproject.nix default.nix: { lib } -> { lib, build, packages }
  pyproject-nix = import (fetchLocked "pyproject-nix") { inherit lib; };

  # uv2nix default.nix: { lib, pyproject-nix } -> { lib }
  uv2nix = import (fetchLocked "uv2nix") { inherit lib pyproject-nix; };

  # build-system-pkgs default.nix: { lib, uv2nix, pyproject-nix }
  #   -> { sdist, wheel, default }. The flake exposes this overlay as
  #   `overlays.default`, so reshape to match for drop-in consumption.
  pyproject-build-systems = {
    overlays.default =
      (import (fetchLocked "pyproject-build-systems") {
        inherit lib uv2nix pyproject-nix;
      }).default;
  };
in
{
  inherit pyproject-nix uv2nix pyproject-build-systems;
}
