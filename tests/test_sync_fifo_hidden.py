from __future__ import annotations
import os
import random
from collections import deque
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge, Timer, First
from cocotb_tools.runner import get_runner

DEPTH = 16
# Per-edge: fail if clk does not rise (simulation time), avoids infinite wait on stuck clock
CLK_EDGE_TIMEOUT_NS = 500_000.0
# Whole-test simulation budget (cocotb aborts the coroutine if exceeded)
TEST_TIMEOUT_MS = 200.0


def cocotb_test(**kwargs):
    """cocotb.test with a default wall/sim timeout so tasks cannot hang forever."""
    opts = {"timeout_time": TEST_TIMEOUT_MS, "timeout_unit": "ms"}
    opts.update(kwargs)
    return cocotb.test(**opts)


async def rising_edge_guarded(dut, timeout_ns: float = CLK_EDGE_TIMEOUT_NS) -> None:
    """RisingEdge with a simulation-time limit; fails fast if clk never toggles."""
    t = Timer(timeout_ns, unit="ns")
    first = await First(RisingEdge(dut.clk), t)
    if first is t:
        raise AssertionError(
            f"No rising clock edge within {timeout_ns} ns "
            "(clk stuck, no time advance, or DUT not driving clk)"
        )


async def falling_edge_guarded(dut, timeout_ns: float = CLK_EDGE_TIMEOUT_NS) -> None:
    """FallingEdge with a simulation-time limit (paired with rising checks)."""
    t = Timer(timeout_ns, unit="ns")
    first = await First(FallingEdge(dut.clk), t)
    if first is t:
        raise AssertionError(
            f"No falling clock edge within {timeout_ns} ns (clk stuck or not toggling)"
        )


async def fifo_reset(dut, cycles: int = 3) -> Clock:
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value = 0
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    dut.wr_data.value = 0
    for _ in range(cycles):
        await rising_edge_guarded(dut)
    dut.rst_n.value = 1
    await rising_edge_guarded(dut)
    return clock


@cocotb_test()
async def test_basic_write_read(dut):
    """Write then read back a sequence of values."""
    await fifo_reset(dut)
    data_in = [random.randint(0, 255) for _ in range(8)]
    for val in data_in:
        assert not dut.full.value, "FIFO unexpectedly full"
        dut.wr_data.value = val
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)

    assert int(dut.wr_count.value) == 8, (
        f"Expected wr_count=8, got {int(dut.wr_count.value)}"
    )

    for expected in data_in:
        assert not dut.empty.value, "FIFO unexpectedly empty"
        dut.rd_en.value = 1
        await rising_edge_guarded(dut)
        dut.rd_en.value = 0
        await falling_edge_guarded(dut)
        assert int(dut.rd_data.value) == expected, (
            f"Expected {expected}, got {int(dut.rd_data.value)}"
        )
    await rising_edge_guarded(dut)
    assert dut.empty.value, "FIFO should be empty after draining"


@cocotb_test()
async def test_full_and_overflow_guard(dut):
    """Fill to capacity; verify full flag; writes while full must be dropped."""
    await fifo_reset(dut)
    for i in range(16):
        dut.wr_data.value = i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert dut.full.value, "FIFO should be full after 16 writes"
    assert not dut.empty.value

    dut.wr_data.value = 0xFF
    dut.wr_en.value = 1
    await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)

    dut.rd_en.value = 1
    await rising_edge_guarded(dut)
    dut.rd_en.value = 0
    await falling_edge_guarded(dut)
    assert int(dut.rd_data.value) == 0, (
        f"Overflow write corrupted FIFO head, got {int(dut.rd_data.value)}"
    )


@cocotb_test()
async def test_almost_full_flag(dut):
    """almost_full must assert when wr_count >= DEPTH-2 (i.e. 14 or more)."""
    await fifo_reset(dut)
    for i in range(13):
        dut.wr_data.value = i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert not dut.almost_full.value, "almost_full should NOT be set at count=13"

    dut.wr_data.value = 99
    dut.wr_en.value = 1
    await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert dut.almost_full.value, "almost_full should be set at count=14 (DEPTH-2)"


@cocotb_test()
async def test_almost_full_sweep_to_full(dut):
    """Step wr_count 12→16 and check almost_full/full flags at each boundary."""
    await fifo_reset(dut)
    for n in range(12):
        dut.wr_data.value = n
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert int(dut.wr_count.value) == 12
    assert not dut.almost_full.value
    assert not dut.full.value

    for count_after, expect_af, expect_f in [
        (13, False, False),
        (14, True, False),
        (15, True, False),
        (16, True, True),
    ]:
        dut.wr_data.value = count_after
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
        dut.wr_en.value = 0
        await rising_edge_guarded(dut)
        assert int(dut.wr_count.value) == count_after
        assert bool(dut.almost_full.value) == expect_af, (
            f"almost_full wrong at count={count_after}"
        )
        assert bool(dut.full.value) == expect_f, f"full wrong at count={count_after}"


@cocotb_test()
async def test_simultaneous_read_write(dut):
    """Simultaneous rd_en + wr_en when not full/empty: count must stay constant."""
    await fifo_reset(dut)
    for i in range(8):
        dut.wr_data.value = i + 10
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)

    count_before = int(dut.wr_count.value)
    dut.wr_data.value = 0xAB
    dut.wr_en.value = 1
    dut.rd_en.value = 1
    await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    await rising_edge_guarded(dut)
    count_after = int(dut.wr_count.value)
    assert count_after == count_before, (
        f"Simultaneous RW should keep count stable: was {count_before}, now {count_after}"
    )


@cocotb_test()
async def test_simultaneous_rw_when_full_drops_write(dut):
    """When full, concurrent rd+wr: read must succeed; overflow write ignored."""
    await fifo_reset(dut)
    for i in range(16):
        dut.wr_data.value = i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert dut.full.value

    dut.wr_data.value = 0xEE
    dut.wr_en.value = 1
    dut.rd_en.value = 1
    await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    await rising_edge_guarded(dut)

    assert int(dut.wr_count.value) == 15, "one pop, overflow write dropped"
    dut.rd_en.value = 1
    await rising_edge_guarded(dut)
    dut.rd_en.value = 0
    await falling_edge_guarded(dut)
    assert int(dut.rd_data.value) == 1, "head should still be 1 after ignored 0xEE"


@cocotb_test()
async def test_read_while_empty_noop(dut):
    """rd_en while empty must not change wr_count or corrupt state."""
    await fifo_reset(dut)
    for _ in range(5):
        dut.rd_en.value = 1
        await rising_edge_guarded(dut)
        dut.rd_en.value = 0
        await rising_edge_guarded(dut)
        assert int(dut.wr_count.value) == 0
        assert dut.empty.value

    dut.wr_data.value = 42
    dut.wr_en.value = 1
    await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert int(dut.wr_count.value) == 1
    dut.rd_en.value = 1
    await rising_edge_guarded(dut)
    dut.rd_en.value = 0
    await falling_edge_guarded(dut)
    assert int(dut.rd_data.value) == 42


@cocotb_test()
async def test_wraparound_ordering(dut):
    """Fill, partial drain, refill — verify ordering across address wrap."""
    await fifo_reset(dut)
    for i in range(16):
        dut.wr_data.value = i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)

    for expected in range(8):
        dut.rd_en.value = 1
        await rising_edge_guarded(dut)
        dut.rd_en.value = 0
        await falling_edge_guarded(dut)
        assert int(dut.rd_data.value) == expected

    for i in range(8):
        dut.wr_data.value = 100 + i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert dut.full.value

    expected_tail = list(range(8, 16)) + list(range(100, 108))
    for expected in expected_tail:
        dut.rd_en.value = 1
        await rising_edge_guarded(dut)
        dut.rd_en.value = 0
        await falling_edge_guarded(dut)
        assert int(dut.rd_data.value) == expected
    assert dut.empty.value


@cocotb_test()
async def test_reset_mid_stream(dut):
    """Assert reset clears FIFO after partial traffic."""
    await fifo_reset(dut)
    for i in range(10):
        dut.wr_data.value = i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    assert int(dut.wr_count.value) == 10

    dut.rst_n.value = 0
    await rising_edge_guarded(dut)
    await rising_edge_guarded(dut)
    dut.rst_n.value = 1
    await rising_edge_guarded(dut)

    assert int(dut.wr_count.value) == 0
    assert dut.empty.value
    assert not dut.full.value
    dut.wr_data.value = 7
    dut.wr_en.value = 1
    await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)
    dut.rd_en.value = 1
    await rising_edge_guarded(dut)
    dut.rd_en.value = 0
    await falling_edge_guarded(dut)
    assert int(dut.rd_data.value) == 7


@cocotb_test()
async def test_queue_model_stress(dut):
    """Reference queue vs DUT for many interleaved ops (seeded)."""
    await fifo_reset(dut)
    random.seed(12345)
    q: deque[int] = deque()

    for step in range(300):
        can_wr = len(q) < DEPTH
        can_rd = len(q) > 0
        wr = random.random() < 0.48 and can_wr
        rd = random.random() < 0.48 and can_rd
        if wr and not can_wr:
            wr = False
        elif rd and not can_rd:
            rd = False

        val = random.randint(0, 255)
        dut.wr_data.value = val
        dut.wr_en.value = int(wr)
        dut.rd_en.value = int(rd)
        await rising_edge_guarded(dut)
        dut.wr_en.value = 0
        dut.rd_en.value = 0

        if wr and rd:
            q.popleft()
            q.append(val)
        elif wr:
            q.append(val)
        elif rd:
            q.popleft()

        await rising_edge_guarded(dut)
        assert int(dut.wr_count.value) == len(q), (
            f"step {step}: model len={len(q)}, wr_count={int(dut.wr_count.value)}"
        )


@cocotb_test()
async def test_wr_count_accuracy(dut):
    """wr_count must track the exact number of stored entries at all times."""
    await fifo_reset(dut)
    random.seed(999)
    model_count = 0
    for cycle in range(80):
        do_wr = random.randint(0, 1) and model_count < 16
        do_rd = random.randint(0, 1) and model_count > 0
        dut.wr_en.value = int(do_wr)
        dut.rd_en.value = int(do_rd)
        dut.wr_data.value = random.randint(0, 255)
        await rising_edge_guarded(dut)
        dut.wr_en.value = 0
        dut.rd_en.value = 0
        if do_wr:
            model_count += 1
        if do_rd:
            model_count -= 1
        await rising_edge_guarded(dut)
        assert int(dut.wr_count.value) == model_count, (
            f"Cycle {cycle}: expected count={model_count}, got {int(dut.wr_count.value)}"
        )


@cocotb_test()
async def test_back_to_back_pipeline(dut):
    """Sustained simultaneous RW at half full for many cycles."""
    await fifo_reset(dut)
    for i in range(8):
        dut.wr_data.value = i
        dut.wr_en.value = 1
        await rising_edge_guarded(dut)
    dut.wr_en.value = 0
    await rising_edge_guarded(dut)

    for k in range(64):
        dut.wr_data.value = (k + 100) & 0xFF
        dut.wr_en.value = 1
        dut.rd_en.value = 1
        await rising_edge_guarded(dut)
        dut.wr_en.value = 0
        dut.rd_en.value = 0
        await rising_edge_guarded(dut)
        assert int(dut.wr_count.value) == 8

    for _ in range(8):
        dut.rd_en.value = 1
        await rising_edge_guarded(dut)
        dut.rd_en.value = 0
        await rising_edge_guarded(dut)
    assert dut.empty.value


def test_sync_fifo_hidden_runner():
    sim = os.getenv("SIM", "icarus")
    proj_path = Path(__file__).resolve().parent.parent
    sources = [proj_path / "sources/sync_fifo.sv"]
    runner = get_runner(sim)
    runner.build(sources=sources, hdl_toplevel="sync_fifo", always=True)
    runner.test(hdl_toplevel="sync_fifo", test_module="test_sync_fifo_hidden")
