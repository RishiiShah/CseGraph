"""python -m csegraph._core is not a CLI entry point.

To use the command-line interface, install csegraph:

    pip install csegraph

If working from source:

    pip install -e .

Then use: csegraph index / csegraph context "..."
"""

import sys


def _main() -> None:
    print(
        "Notice: `python -m csegraph._core` is not the CLI.\n\n"
        "`csegraph` is the distribution name; `csegraph._core` is a private engine namespace.\n"
        "It contains storage, parser, retrieval, and graph internals.\n\n"
        "To install the public package, run:\n"
        "    pip install csegraph\n\n"
        "To install the CLI from source, run:\n"
        "    pip install -e .\n\n"
        "Then you can run the CLI via:\n"
        "    csegraph <command>\n"
        "    python -m csegraph._cli <command>",
        file=sys.stderr,
    )
    sys.exit(1)


if __name__ == "__main__":
    _main()
