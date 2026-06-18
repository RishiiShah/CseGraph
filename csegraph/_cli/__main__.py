"""Allow `python -m csegraph._cli` to invoke the CLI."""

from csegraph._cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
