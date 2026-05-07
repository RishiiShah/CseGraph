"""python -m csegraph is not available — csegraph is a library package.

To use the command-line interface, install csegraph-cli:

    pip install csegraph-cli

If working from source (monorepo):

    pip install -e . --config-settings="--pyproject=pyproject_cli.toml"

Then use: csegraph index . / csegraph context "..." / csegraph codegen "..."
"""

import sys


def _main() -> None:
    print(
        "Notice: `python -m csegraph` is no longer available.\n\n"
        "As of v1.1.2, `csegraph` is a pure SDK library.\n"
        "The command-line interface has been moved to its own package: `csegraph-cli`.\n\n"
        "To install the CLI from source in the monorepo, run:\n"
        "    pip install -e packages/csegraph-cli/\n\n"
        "Then you can run the CLI via:\n"
        "    csegraph <command>\n"
        "    python -m csegraph_cli <command>",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    _main()
