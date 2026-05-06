"""Allow `python -m csegraph_cli` to invoke the CLI."""
from csegraph_cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
