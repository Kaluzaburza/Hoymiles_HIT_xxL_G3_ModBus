"""Contract tests for non-blocking, serialized Home Assistant optimizers."""

from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from pathlib import Path
import threading
from types import MethodType
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "hoymiles_hit_modbus"
SENSORS = {
    "rce_sensor.py": ("HoymilesRCEOptimizerSensor", "optimize_rce"),
    "tariff_sensor.py": (
        "HoymilesTariffOptimizerSensor",
        "optimize_tariff_charging",
    ),
    "rcm_sensor.py": ("HoymilesRCMOptimizerSensor", "optimize_rcm"),
}


def _class_node(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1, f"Expected exactly one {name} class"
    return matches[0]


def _method(
    class_node: ast.ClassDef,
    name: str,
) -> ast.FunctionDef | ast.AsyncFunctionDef:
    matches = [
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == name
    ]
    assert len(matches) == 1, f"Expected exactly one {class_node.name}.{name}"
    return matches[0]


def _is_self_attribute(node: ast.AST, name: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == name
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _is_hass_executor(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "async_add_executor_job"
        and isinstance(node.value, ast.Attribute)
        and node.value.attr == "hass"
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
    )


def _awaited_self_call(method: ast.AST, name: str) -> list[ast.Await]:
    return [
        node
        for node in ast.walk(method)
        if isinstance(node, ast.Await)
        and isinstance(node.value, ast.Call)
        and _is_self_attribute(node.value.func, name)
    ]


def _optimizer_executor_await(
    method: ast.AsyncFunctionDef,
    optimizer_name: str,
) -> ast.Await:
    matches: list[ast.Await] = []
    for node in ast.walk(method):
        if not isinstance(node, ast.Await) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if not _is_hass_executor(call.func):
            continue
        if (
            len(call.args) >= 2
            and isinstance(call.args[0], ast.Name)
            and call.args[0].id == optimizer_name
        ):
            matches.append(node)
    assert len(matches) == 1, (
        f"{optimizer_name} must be awaited through exactly one "
        "hass.async_add_executor_job call"
    )
    return matches[0]


def _compile_probe_method(method: ast.AsyncFunctionDef) -> type[Any]:
    """Compile an actual wrapper method without importing Home Assistant."""
    probe_class = ast.ClassDef(
        name="Probe",
        bases=[],
        keywords=[],
        decorator_list=[],
        body=[deepcopy(method)],
    )
    module = ast.fix_missing_locations(ast.Module(body=[probe_class], type_ignores=[]))
    namespace: dict[str, Any] = {}
    exec(compile(module, "<optimizer-lock-probe>", "exec"), namespace)
    return namespace["Probe"]


def _compile_executor_probe(executor_await: ast.Await) -> type[Any]:
    """Compile the executor call shape taken directly from a sensor method."""
    call = deepcopy(executor_await.value)
    assert isinstance(call, ast.Call)
    call.args = [
        ast.Name(id="optimizer_callable", ctx=ast.Load()),
        ast.Name(id="optimizer_input", ctx=ast.Load()),
    ]
    method = ast.AsyncFunctionDef(
        name="run_optimizer",
        args=ast.arguments(
            posonlyargs=[],
            args=[
                ast.arg(arg="self"),
                ast.arg(arg="optimizer_callable"),
                ast.arg(arg="optimizer_input"),
            ],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[ast.Return(value=ast.Await(value=call))],
        decorator_list=[],
    )
    return _compile_probe_method(method)


def _assert_static_contract(
    path: Path,
    class_name: str,
    optimizer_name: str,
) -> tuple[ast.AsyncFunctionDef, ast.Await]:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))
    class_node = _class_node(tree, class_name)

    init = _method(class_node, "__init__")
    lock_assignments = [
        node
        for node in ast.walk(init)
        if isinstance(node, ast.Assign)
        and any(_is_self_attribute(target, "_optimizer_lock") for target in node.targets)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and isinstance(node.value.func.value, ast.Name)
        and node.value.func.value.id == "asyncio"
        and node.value.func.attr == "Lock"
    ]
    assert len(lock_assignments) == 1, f"{path.name} lacks one asyncio.Lock"

    callback_names = [
        "_async_control_timer" if path.name == "rcm_sensor.py" else "_async_timer",
    ]
    if path.name == "rcm_sensor.py":
        callback_names.append("_async_input_changed")
    for callback_name in callback_names:
        assert isinstance(_method(class_node, callback_name), ast.AsyncFunctionDef), (
            f"{path.name}:{callback_name} must await the serialized recalculation"
        )

    recalculate = _method(class_node, "_recalculate")
    recalculate_and_write = _method(class_node, "_recalculate_and_write")
    locked = _method(class_node, "_recalculate_locked")
    assert isinstance(recalculate, ast.AsyncFunctionDef)
    assert isinstance(recalculate_and_write, ast.AsyncFunctionDef)
    assert isinstance(locked, ast.AsyncFunctionDef)

    for wrapper in (recalculate, recalculate_and_write):
        lock_contexts = [
            node
            for node in ast.walk(wrapper)
            if isinstance(node, ast.AsyncWith)
            and any(
                _is_self_attribute(item.context_expr, "_optimizer_lock")
                for item in node.items
            )
        ]
        assert len(lock_contexts) == 1, (
            f"{path.name}:{wrapper.name} must serialize with _optimizer_lock"
        )
        assert len(_awaited_self_call(wrapper, "_recalculate_locked")) == 1

    parents = {
        child: parent
        for parent in ast.walk(class_node)
        for child in ast.iter_child_nodes(parent)
    }
    for node in ast.walk(class_node):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and _is_self_attribute(node.func, node.func.attr)
            and node.func.attr
            in {"_recalculate", "_recalculate_and_write", "_recalculate_locked"}
        ):
            assert isinstance(parents.get(node), ast.Await), (
                f"{path.name}:{node.func.attr} coroutine is called without await"
            )

    direct_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == optimizer_name
    ]
    assert not direct_calls, f"{path.name} still calls {optimizer_name} on the HA loop"
    executor_await = _optimizer_executor_await(locked, optimizer_name)
    return recalculate, executor_await


async def _assert_fifo_single_flight(
    wrapper: ast.AsyncFunctionDef,
    label: str,
) -> None:
    probe_type = _compile_probe_method(wrapper)
    probe = probe_type()
    probe._optimizer_lock = asyncio.Lock()

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    order: list[tuple[str, str]] = []
    active = 0
    maximum_active = 0

    async def fake_locked(self: Any) -> None:
        nonlocal active, maximum_active
        task = asyncio.current_task()
        assert task is not None
        task_name = task.get_name()
        active += 1
        maximum_active = max(maximum_active, active)
        order.append(("start", task_name))
        if task_name == "first":
            first_started.set()
            await release_first.wait()
        await asyncio.sleep(0)
        order.append(("end", task_name))
        active -= 1

    probe._recalculate_locked = MethodType(fake_locked, probe)
    first = asyncio.create_task(probe._recalculate(), name="first")
    await asyncio.wait_for(first_started.wait(), timeout=5.0)
    second = asyncio.create_task(probe._recalculate(), name="second")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert order == [("start", "first")], f"{label} allowed overlapping runs"
    release_first.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5.0)
    assert maximum_active == 1, f"{label} violated single-flight"
    assert order == [
        ("start", "first"),
        ("end", "first"),
        ("start", "second"),
        ("end", "second"),
    ], f"{label} did not preserve FIFO ordering: {order}"


class _FakeHass:
    async def async_add_executor_job(
        self,
        target: Callable[[object], object],
        argument: object,
    ) -> object:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, target, argument)


async def _assert_loop_remains_responsive(
    executor_await: ast.Await,
    label: str,
) -> None:
    probe_type = _compile_executor_probe(executor_await)
    probe = probe_type()
    probe.hass = _FakeHass()
    worker_started: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    release_worker = threading.Event()
    loop = asyncio.get_running_loop()

    def slow_optimizer(value: object) -> object:
        loop.call_soon_threadsafe(worker_started.set_result, None)
        assert release_worker.wait(timeout=5.0)
        return value

    optimizer_task = asyncio.create_task(
        probe.run_optimizer(slow_optimizer, label),
    )
    await asyncio.wait_for(worker_started, timeout=5.0)
    ticks = 0
    for _ in range(8):
        await asyncio.sleep(0)
        ticks += 1
    assert ticks == 8 and not optimizer_task.done(), (
        f"{label} blocked the event loop while its optimizer was running"
    )
    release_worker.set()
    result = await asyncio.wait_for(optimizer_task, timeout=5.0)
    assert result == label


async def _async_main() -> None:
    contracts: list[tuple[str, ast.AsyncFunctionDef, ast.Await]] = []
    for filename, (class_name, optimizer_name) in SENSORS.items():
        wrapper, executor_await = _assert_static_contract(
            COMPONENT / filename,
            class_name,
            optimizer_name,
        )
        contracts.append((filename, wrapper, executor_await))

    for filename, wrapper, executor_await in contracts:
        await _assert_fifo_single_flight(wrapper, filename)
        await _assert_loop_remains_responsive(executor_await, filename)


def main() -> None:
    asyncio.run(_async_main())
    print("Optimizer executor: offload, FIFO and single-flight contracts passed")


if __name__ == "__main__":
    main()
