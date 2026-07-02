# nix/overlay.nix — Nixpkgs overlay exposing pkgs.hermes-agent.
#
# A plain overlay: `final: prev: { … }`. Use it directly, no call needed —
#
#   nixpkgs.overlays = [ (import ./nix/overlay.nix) ];
#
# Build inputs come from nix/inputs.nix (which reads flake.lock), so a stable
# (non-flake) consumer needs nothing but this repo. The flake re-exports this
# same overlay verbatim.
#
# rev and the node sub-packages (tui/web/desktop) are flake-only concerns:
#   - rev embeds the locked flake revision for the update check; a stable
#     consumer has no flake revision, so it is null here.
#   - npm-lockfile-fix has no classic (non-flake) entrypoint, so the node
#     sub-packages aren't built through the overlay; null is correct for the
#     core Python package this overlay exposes.
final: _prev:
let
  inherit (import ./inputs.nix { inherit (final) lib; })
    uv2nix
    pyproject-nix
    pyproject-build-systems
    ;
in
{
  hermes-agent = final.callPackage ./hermes-agent.nix {
    inherit uv2nix pyproject-nix pyproject-build-systems;
    npm-lockfile-fix = null;
    rev = null;
  };
}
