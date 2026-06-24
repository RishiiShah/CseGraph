import logging
from argparse import Namespace

from csegraph._cli.main import (
    _configure_dependency_logging,
    _log_format_for_args,
    _log_level_for_args,
)


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


def test_watch_default_logging_format_keeps_info_without_logger_names():
    assert _log_format_for_args(_args("watch")) == "%(levelname)s: %(message)s"
    assert _log_format_for_args(_args("serve")) == "%(levelname)s: %(message)s"


def test_verbose_logging_format_keeps_logger_names():
    assert _log_format_for_args(_args("watch", verbose=1)) == "%(levelname)s %(name)s: %(message)s"
    assert _log_format_for_args(_args("index")) == "%(levelname)s %(name)s: %(message)s"


def test_dependency_watchfiles_info_logs_are_suppressed_by_default():
    _configure_dependency_logging(logging.INFO)
    assert logging.getLogger("watchfiles").level == logging.WARNING
    assert logging.getLogger("watchfiles.main").level == logging.WARNING

    _configure_dependency_logging(logging.DEBUG)
    assert logging.getLogger("watchfiles").level == logging.NOTSET
    assert logging.getLogger("watchfiles.main").level == logging.NOTSET
