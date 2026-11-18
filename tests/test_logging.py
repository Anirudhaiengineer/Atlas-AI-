import asyncio
import logging

import pytest

from app.core.logging import log_execution


def test_log_execution_records_function_without_logging_arguments(caplog):
    @log_execution
    def example(secret: str) -> str:
        return secret

    with caplog.at_level(logging.DEBUG):
        assert example("private-value") == "private-value"

    assert "Executing function test_log_execution_records_function_without_logging_arguments.<locals>.example." in caplog.text
    assert "Completed function test_log_execution_records_function_without_logging_arguments.<locals>.example." in caplog.text
    assert "private-value" not in caplog.text


def test_log_execution_records_async_functions(caplog):
    @log_execution
    async def example() -> str:
        return "done"

    with caplog.at_level(logging.DEBUG):
        assert asyncio.run(example()) == "done"

    assert "example" in caplog.text


def test_log_execution_records_failures_and_reraises(caplog):
    @log_execution
    def example() -> None:
        raise ValueError("private failure detail")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(ValueError, match="private failure detail"):
            example()

    assert "failed with ValueError" in caplog.text
    assert "private failure detail" not in caplog.text
