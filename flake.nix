{
  description = "Development shell";

  inputs.nix-devenv.url = "github:dryvist/nix-devenv?ref=v0";

  outputs =
    { nix-devenv, ... }:
    let
      # Python test deps the contract suite needs on top of the ansible-apps
      # shell: CI runs `python -m pytest … -n auto` (pytest-xdist) and
      # ansible.cfg enables the community.general.opentelemetry callback, which
      # imports the OpenTelemetry trio. pytest itself is absent from the
      # ansible-apps Python env, so it is added here alongside its xdist plugin.
      testPythonPackages =
        ps: with ps; [
          pytest
          pytest-xdist
          opentelemetry-api
          opentelemetry-sdk
          opentelemetry-exporter-otlp
        ];

      # Extend the SAME interpreter that runs ansible, not a second env on PATH:
      # ansible loads implicit-localhost modules and callbacks with its own
      # sys.executable, so a separate python3.withPackages env is invisible to
      # them (see nix-devenv/shells/ansible/default.nix). The env is a buildEnv
      # whose symlink tree is driven by passthru.paths; appending the new
      # packages' requiredPythonModules closure there keeps every existing
      # package and also pulls the new packages' propagated deps (execnet, the
      # OTLP proto exporters, …) into the env.
      addTestDeps =
        drv:
        if drv ? python && drv ? paths then
          drv.overrideAttrs (old: {
            passthru = old.passthru // {
              paths = old.passthru.paths ++ drv.pkgs.requiredPythonModules (testPythonPackages drv.pkgs);
            };
          })
        else
          drv;
    in
    {
      devShells = builtins.mapAttrs (_: shells: {
        default = shells.ansible-apps.overrideAttrs (old: {
          buildInputs = map addTestDeps old.buildInputs;
        });
      }) nix-devenv.devShells;
    };
}
