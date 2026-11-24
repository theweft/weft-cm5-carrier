# Run `just` to list recipes. CI runs `just ci` inside `nix develop`.

default:
    @just --list

# Spelling.
typos:
    typos

# Licence and copyright: REUSE compliance.
licenses:
    reuse lint

# Everything CI checks.
ci: licenses typos
