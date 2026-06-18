import logging
from argparse import Namespace

from csegraph._cli.main import _log_level_for_args


def _args(command: str, *, verbose: int = 0, quiet: int = 0) -> Namespace:
    return Namespace(command=command, log_verbose=verbose, log_quiet=quiet)


def test_default_cli_logging_is_warning():
    assert _log_level_for_args(_args("index")) == logging.WARNING


def test_watch_and_serve_default_to_info_logging():
    assert _log_level_for_args(_args("watch")) == logging.INFO
    assert _log_level_for_args(_args("serve")) == logging.INFO


def test_verbose_and_quiet_adjust_logging_levels():
    assert _log_level_for_args(_args("index", verbose=1)) == logging.INFO
    assert _log_level_for_args(_args("index", verbose=2)) == logging.DEBUG
    assert _log_level_for_args(_args("watch", quiet=1)) == logging.WARNING
    assert _log_level_for_args(_args("watch", quiet=2)) == logging.ERROR
