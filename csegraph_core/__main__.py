"""python -m csegraph_core is not a CLI entry point.

To use the command-line interface, install csegraph-cli:

    pip install csegraph-cli

If working from source (monorepo):

    pip install -e . -e packages/csegraph-cli/

Then use: csegraph index . / csegraph context "..." / csegraph codegen "..."
"""

import sys


def _main() -> None:
    print(
        "Notice: `python -m csegraph_core` is not the CLI.\n\n"
        "`csegraph-core` is the distribution name; `csegraph_core` is the Python import namespace.\n"
        "It is the storage, parser, retrieval, and graph library.\n"
        "The command-line interface lives in the `csegraph-cli` package.\n\n"
        "To install the CLI from source in the monorepo, run:\n"
        "    pip install -e . -e packages/csegraph-cli/\n\n"
        "Then you can run the CLI via:\n"
        "    csegraph <command>\n"
        "    python -m csegraph_cli <command>",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    _main()
