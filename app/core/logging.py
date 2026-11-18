import inspect
import logging
from functools import wraps
from types import FunctionType
from typing import Any, Callable, ParamSpec, TypeVar, cast


P = ParamSpec("P")
R = TypeVar("R")


def configure_logging(debug: bool = False) -> None:
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
    root_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logging.getLogger(__name__).info(
        "Logging configured at %s level.",
        logging.getLevelName(root_logger.level),
    )


def log_execution(function: Callable[P, R]) -> Callable[P, R]:
    if getattr(function, "__log_execution__", False):
        return function

    logger = logging.getLogger(function.__module__)
    function_name = function.__qualname__

    def log_failure(exc: BaseException) -> None:
        logger.error(
            "Function %s failed with %s.",
            function_name,
            type(exc).__name__,
        )

    if inspect.isasyncgenfunction(function):
        @wraps(function)
        async def async_generator_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            logger.debug("Executing function %s.", function_name)
            try:
                async for item in function(*args, **kwargs):  # type: ignore[misc]
                    yield item
            except Exception as exc:
                log_failure(exc)
                raise
            logger.debug("Completed function %s.", function_name)

        setattr(async_generator_wrapper, "__log_execution__", True)
        return cast(Callable[P, R], async_generator_wrapper)

    if inspect.isgeneratorfunction(function):
        @wraps(function)
        def generator_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            logger.debug("Executing function %s.", function_name)
            try:
                yield from function(*args, **kwargs)  # type: ignore[misc]
            except Exception as exc:
                log_failure(exc)
                raise
            logger.debug("Completed function %s.", function_name)

        setattr(generator_wrapper, "__log_execution__", True)
        return cast(Callable[P, R], generator_wrapper)

    if inspect.iscoroutinefunction(function):
        @wraps(function)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            logger.debug("Executing function %s.", function_name)
            try:
                result = await function(*args, **kwargs)  # type: ignore[misc]
            except Exception as exc:
                log_failure(exc)
                raise
            logger.debug("Completed function %s.", function_name)
            return result

        setattr(async_wrapper, "__log_execution__", True)
        return cast(Callable[P, R], async_wrapper)

    @wraps(function)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
        logger.debug("Executing function %s.", function_name)
        try:
            result = function(*args, **kwargs)
        except Exception as exc:
            log_failure(exc)
            raise
        logger.debug("Completed function %s.", function_name)
        return result

    setattr(wrapper, "__log_execution__", True)
    return wrapper


def instrument_module(namespace: dict[str, Any]) -> None:
    module_name = namespace.get("__name__")
    for name, value in tuple(namespace.items()):
        if (
            isinstance(value, FunctionType)
            and value.__module__ == module_name
            and not getattr(value, "__log_execution__", False)
        ):
            namespace[name] = log_execution(value)
        elif inspect.isclass(value) and value.__module__ == module_name:
            _instrument_class(value)


def _instrument_class(class_object: type) -> None:
    for name, member in tuple(vars(class_object).items()):
        if isinstance(member, staticmethod):
            function = member.__func__
            if function.__module__ == class_object.__module__:
                setattr(class_object, name, staticmethod(log_execution(function)))
        elif isinstance(member, classmethod):
            function = member.__func__
            if function.__module__ == class_object.__module__:
                setattr(class_object, name, classmethod(log_execution(function)))
        elif isinstance(member, FunctionType):
            if member.__module__ == class_object.__module__:
                setattr(class_object, name, log_execution(member))
