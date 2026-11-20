import contextlib
import hashlib
import os
import subprocess
import sys
import tempfile
from typing import Any, cast

import yaml

try:
    cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    cast(Any, sys.stderr).reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OK = "[ OK ]"
FAIL = "[FAIL]"

ENV = dict(os.environ, PYTHONIOENCODING="utf-8")

STREAM_STEP = 1000
STREAM_START = 10


def encode_value(value: Any) -> str:
    if isinstance(value, str):
        if value == "EOF":
            return "EOF"
        if value in ("\\n", "\n"):
            return "\\n"
        if value in ("\\t", "\t"):
            return "\\t"
        if value == " ":
            return "\\s"
        if len(value) == 1:
            return value
        raise ValueError(f"ожидался один символ, получено {value!r}")
    if isinstance(value, bool):
        raise ValueError("bool не является значением ввода")
    if isinstance(value, int):
        if value < 0:
            return "EOF"
        return str(value)
    raise ValueError(f"неподдерживаемое значение ввода: {value!r}")


def is_schedule_pair(item: Any) -> bool:
    return (
        isinstance(item, (list, tuple))
        and len(item) == 2
        and isinstance(item[0], int)
        and not isinstance(item[0], bool)
    )


def text_to_schedule(text: str) -> list[tuple[int, int]]:
    events = [(STREAM_START + STREAM_STEP * k, ord(ch)) for k, ch in enumerate(text)]
    events.append((STREAM_START + STREAM_STEP * len(text), -1))
    return events


def make_schedule(test: dict[str, Any]) -> list[tuple[int, int]] | None:
    schedule = test.get("input_schedule")
    if schedule is None:
        schedule = test.get("input")

    if schedule is None or schedule in ([], ""):
        return None

    if isinstance(schedule, str):
        return text_to_schedule(schedule)

    if isinstance(schedule, list):
        if all(is_schedule_pair(item) for item in schedule):
            return [(item[0], item[1]) for item in schedule]
        return text_to_schedule("".join(str(item) for item in schedule))

    raise ValueError(f"неподдерживаемый input_schedule: {schedule!r}")


def write_schedule(path: str, events: list[tuple[int, int]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for tick, value in events:
            f.write(f"{tick} {encode_value(value)}\n")


def parse_stats(stderr: str) -> tuple[int | None, int | None, int | None]:
    ticks = None
    intr = None
    overrun = None
    for line in stderr.splitlines():
        if "Итого тактов" in line:
            with contextlib.suppress(IndexError, ValueError):
                ticks = int(line.split(":")[1].split("-")[0].strip())
        elif "Прерываний" in line:
            with contextlib.suppress(IndexError, ValueError):
                intr = int(line.split(":")[1].split(",")[0].strip())
                overrun = int(line.split(":")[2].split("-")[0].strip())
    return ticks, intr, overrun


def run_test(yaml_file: str, tmpdir: str) -> bool:
    with open(yaml_file, encoding="utf-8") as f:
        test: dict[str, Any] = yaml.safe_load(f)

    name = test["algorithm"]
    print(f"\n=== Тест: {name} ===")

    src_path = os.path.join(tmpdir, f"{name}.lisp")
    bin_path = os.path.join(tmpdir, f"{name}.bin")
    with open(src_path, "w", encoding="utf-8") as src:
        src.write(test["source"])

    print("Трансляция...")
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, "translator.py"), src_path, bin_path],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=HERE,
        env=ENV,
    )
    if result.returncode != 0:
        print(f"ОШИБКА ТРАНСЛЯЦИИ:\n{result.stdout}\n{result.stderr}")
        return False

    expected_sha = test.get("machine_code_sha256")
    if expected_sha:
        with open(bin_path, "rb") as bf:
            digest = hashlib.sha256(bf.read()).hexdigest()
        if digest != expected_sha:
            print(f"{FAIL} ТЕСТ ПРОВАЛЕН: машинный код изменился")
            return False

    listing_excerpt = test.get("listing_excerpt")
    if listing_excerpt:
        lst_path = os.path.splitext(bin_path)[0] + ".lst"
        with open(lst_path, encoding="utf-8") as lf:
            listing = lf.read()
        if not listing.rstrip("\n").endswith(listing_excerpt.rstrip("\n")):
            print(f"{FAIL} ТЕСТ ПРОВАЛЕН: листинг не совпадает с эталоном")
            return False

    schedule_path = None
    events = make_schedule(test)
    if events:
        schedule_path = os.path.join(tmpdir, f"{name}.sched")
        write_schedule(schedule_path, events)

    print("Симуляция...")
    log_path = os.path.join(tmpdir, f"{name}.log")
    cmd = [sys.executable, os.path.join(HERE, "machine.py"), bin_path]
    if schedule_path:
        cmd += ["--schedule", schedule_path]
    if test.get("log_excerpt"):
        cmd += ["--log", log_path]
    result = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=HERE,
        env=ENV,
    )
    if result.returncode != 0:
        print(f"ОШИБКА СИМУЛЯЦИИ:\n{result.stderr}")
        return False

    actual = result.stdout
    expected = test["expected_output"]
    print(f"Ожидалось: {expected!r}")
    print(f"Получено:  {actual!r}")

    ticks, intr, overrun = parse_stats(result.stderr)
    if ticks is not None:
        expected_ticks = test.get("expected_ticks")
        extra = f" (лимит {expected_ticks})" if expected_ticks else ""
        print(f"Тактов: {ticks}{extra}")
    if intr is not None:
        print(f"Прерываний: {intr}, переполнений входа: {overrun}")

    if actual != expected:
        print(f"{FAIL} ТЕСТ ПРОВАЛЕН: вывод не совпадает")
        return False

    log_excerpt = test.get("log_excerpt")
    if log_excerpt:
        with open(log_path, encoding="utf-8") as lf:
            log_text = lf.read()
        for line in log_excerpt.splitlines():
            if line.strip() and line not in log_text:
                print(f"{FAIL} ТЕСТ ПРОВАЛЕН: в журнале нет строки {line!r}")
                return False

    expected_ticks = test.get("expected_ticks")
    if expected_ticks and ticks is not None and ticks > expected_ticks:
        print(f"{FAIL} ТЕСТ ПРОВАЛЕН: превышен лимит тактов")
        return False

    expected_intr = test.get("expected_interrupts")
    if expected_intr is not None and intr != expected_intr:
        print(f"{FAIL} ТЕСТ ПРОВАЛЕН: прерываний {intr}, ожидалось {expected_intr}")
        return False

    expected_overrun = test.get("expected_overrun")
    if expected_overrun is not None and overrun != expected_overrun:
        print(
            f"{FAIL} ТЕСТ ПРОВАЛЕН: переполнений входа {overrun}, "
            f"ожидалось {expected_overrun}"
        )
        return False

    ticks_no_pipeline = test.get("ticks_no_pipeline")
    if ticks_no_pipeline is not None:
        slow_cmd = [
            sys.executable,
            os.path.join(HERE, "machine.py"),
            bin_path,
            "--no-pipeline",
        ]
        if schedule_path:
            slow_cmd += ["--schedule", schedule_path]
        slow = subprocess.run(
            slow_cmd,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=HERE,
            env=ENV,
        )
        if slow.returncode != 0:
            print(f"{FAIL} ТЕСТ ПРОВАЛЕН: ошибка многотактной модели:\n{slow.stderr}")
            return False
        slow_ticks, _, _ = parse_stats(slow.stderr)
        if slow.stdout != expected:
            print(f"{FAIL} ТЕСТ ПРОВАЛЕН: многотактная модель дала {slow.stdout!r}")
            return False
        print(f"Многотактная модель: {slow_ticks} тактов")
        if ticks is not None and slow_ticks is not None:
            if ticks >= slow_ticks:
                print(
                    f"{FAIL} ТЕСТ ПРОВАЛЕН: конвейер не быстрее "
                    f"({ticks} >= {slow_ticks})"
                )
                return False
            print(f"Ускорение: {slow_ticks / ticks:.2f}x")

    print(f"{OK} ТЕСТ ПРОЙДЕН")
    return True


def main() -> int:
    test_dir = os.path.join(HERE, "golden")
    passed = 0
    failed = 0

    with tempfile.TemporaryDirectory(prefix="lab4_") as tmpdir:
        for filename in sorted(os.listdir(test_dir)):
            if not filename.endswith(".yml"):
                continue
            yaml_file = os.path.join(test_dir, filename)
            if run_test(yaml_file, tmpdir):
                passed += 1
            else:
                failed += 1

    print(f"\nИтог: {passed} пройдено, {failed} провалено")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
