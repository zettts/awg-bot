import asyncio
import re

IHOR_HOST = "95.81.76.197"
IHOR_USER = "root"
IHOR_SSH_KEY = "/home/ubuntu/.ssh/ihor_tunnel"

COMBINED_CMD = "top -bn1 | grep 'Cpu(s)'; free -m; df -h /"
NETWORK_CMD = (
    "cat /sys/class/net/ens3/statistics/rx_bytes; "
    "cat /sys/class/net/ens3/statistics/tx_bytes"
)


async def _run_local(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
    return stdout.decode(errors="ignore")


async def _run_remote(cmd: str) -> str:
    ssh_cmd = (
        f"ssh -i {IHOR_SSH_KEY} -o ConnectTimeout=8 -o StrictHostKeyChecking=accept-new "
        f"{IHOR_USER}@{IHOR_HOST} \"{cmd}\""
    )
    return await _run_local(ssh_cmd)


def _parse_stats(raw: str) -> dict:
    result = {"cpu": "н/д", "ram": "н/д", "swap": "н/д", "disk": "н/д"}

    cpu_match = re.search(r"Cpu\(s\):\s*([\d.]+)\s*us,\s*([\d.]+)\s*sy", raw)
    if cpu_match:
        used = float(cpu_match.group(1)) + float(cpu_match.group(2))
        result["cpu"] = f"{used:.1f}%"

    mem_match = re.search(r"Mem:\s+(\d+)\s+(\d+)", raw)
    if mem_match:
        total, used = int(mem_match.group(1)), int(mem_match.group(2))
        pct = (used / total * 100) if total else 0
        result["ram"] = f"{used} / {total} МБ ({pct:.0f}%)"

    swap_match = re.search(r"Swap:\s+(\d+)\s+(\d+)", raw)
    if swap_match:
        total, used = int(swap_match.group(1)), int(swap_match.group(2))
        if total:
            pct = used / total * 100
            result["swap"] = f"{used} / {total} МБ ({pct:.0f}%)"
        else:
            result["swap"] = "нет"

    disk_match = re.search(r"(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+/", raw)
    if disk_match:
        size, used, avail, pct = disk_match.groups()
        result["disk"] = f"{used} / {size} ({pct}%), свободно {avail}"

    return result


async def get_oracle_stats() -> dict:
    try:
        raw = await _run_local(COMBINED_CMD)
        return _parse_stats(raw)
    except Exception:
        return {"cpu": "ошибка", "ram": "ошибка", "swap": "ошибка", "disk": "ошибка"}


async def get_ihor_stats() -> dict:
    try:
        raw = await _run_remote(COMBINED_CMD)
        return _parse_stats(raw)
    except Exception:
        return {"cpu": "ошибка", "ram": "ошибка", "swap": "ошибка", "disk": "ошибка"}


def _parse_network_counters(raw: str) -> tuple[int, int]:
    values = [int(value) for value in raw.split() if value.isdigit()]
    if len(values) < 2:
        raise ValueError("network counters are unavailable")
    return values[0], values[1]


async def _network_counters(remote: bool) -> tuple[int, int]:
    raw = await (_run_remote(NETWORK_CMD) if remote else _run_local(NETWORK_CMD))
    return _parse_network_counters(raw)


async def get_network_stats() -> dict:
    """Current and boot-total traffic for Oracle and Ihor public interfaces."""
    try:
        first_oracle, first_ihor = await asyncio.gather(
            _network_counters(False),
            _network_counters(True),
        )
        started = asyncio.get_running_loop().time()
        await asyncio.sleep(1)
        second_oracle, second_ihor = await asyncio.gather(
            _network_counters(False),
            _network_counters(True),
        )
        elapsed = max(asyncio.get_running_loop().time() - started, 0.001)

        def node(first: tuple[int, int], second: tuple[int, int]) -> dict:
            return {
                "rx_total": second[0],
                "tx_total": second[1],
                "rx_bps": max(0, second[0] - first[0]) * 8 / elapsed,
                "tx_bps": max(0, second[1] - first[1]) * 8 / elapsed,
            }

        oracle = node(first_oracle, second_oracle)
        ihor = node(first_ihor, second_ihor)
        return {
            "oracle": oracle,
            "ihor": ihor,
            "total": {
                "rx_total": oracle["rx_total"] + ihor["rx_total"],
                "tx_total": oracle["tx_total"] + ihor["tx_total"],
                "rx_bps": oracle["rx_bps"] + ihor["rx_bps"],
                "tx_bps": oracle["tx_bps"] + ihor["tx_bps"],
            },
        }
    except Exception:
        empty = {"rx_total": 0, "tx_total": 0, "rx_bps": 0, "tx_bps": 0}
        return {"oracle": empty.copy(), "ihor": empty.copy(), "total": empty.copy(), "error": True}
