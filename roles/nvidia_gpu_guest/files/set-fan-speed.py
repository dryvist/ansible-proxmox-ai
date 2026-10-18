#!/usr/bin/env python3
"""Set every NVIDIA GPU fan to 100 percent with NVML."""

import sys

import pynvml


def main() -> int:
    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError as error:
        print(f"ERROR NVML init {type(error).__name__}: {error}", file=sys.stderr)
        return 1

    failed = False
    try:
        device_count = pynvml.nvmlDeviceGetCount()
        if device_count == 0:
            print("ERROR NVML reported zero NVIDIA devices", file=sys.stderr)
            return 1

        for device_index in range(device_count):
            try:
                handle = pynvml.nvmlDeviceGetHandleByIndex(device_index)
                name = pynvml.nvmlDeviceGetName(handle)
                if isinstance(name, bytes):
                    name = name.decode("utf-8", errors="replace")
                fan_count = pynvml.nvmlDeviceGetNumFans(handle)
            except pynvml.NVMLError as error:
                print(
                    f"ERROR GPU={device_index} {type(error).__name__}: {error}",
                    file=sys.stderr,
                )
                failed = True
                continue

            if fan_count == 0:
                print(
                    f"ERROR GPU={device_index} name={name} has no NVML fan controllers",
                    file=sys.stderr,
                )
                failed = True
                continue

            for fan_index in range(fan_count):
                try:
                    pynvml.nvmlDeviceSetFanSpeed_v2(handle, fan_index, 100)
                except pynvml.NVMLError as error:
                    print(
                        f"ERROR GPU={device_index} fan={fan_index} "
                        f"{type(error).__name__}: {error}",
                        file=sys.stderr,
                    )
                    failed = True
                    continue
                print(f"SET GPU={device_index} name={name} fan={fan_index} speed=100%")
    finally:
        pynvml.nvmlShutdown()

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
