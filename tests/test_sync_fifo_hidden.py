from __future__ import annotations
import os
import random
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge
from cocotb_tools.runner import get_runner

@cocotb.test()
async def test_basic_write_read(dut):
    """Write then read back a sequence of values."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value = 0
    dut.wr_en.value  = 0
    dut.rd_en.value  = 0
    dut.wr_data.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    data_in = [random.randint(0, 255) for _ in range(8)]
    for val in data_in:
        assert not dut.full.value, "FIFO unexpectedly full"
        dut.wr_data.value = val
        dut.wr_en.value   = 1
        await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)

    assert int(dut.wr_count.value) == 8, \
        f"Expected wr_count=8, got {int(dut.wr_count.value)}"

    for expected in data_in:
        assert not dut.empty.value, "FIFO unexpectedly empty"
        dut.rd_en.value = 1
        await RisingEdge(dut.clk)
        dut.rd_en.value = 0
        await FallingEdge(dut.clk)
        assert int(dut.rd_data.value) == expected, \
            f"Expected {expected}, got {int(dut.rd_data.value)}"
    await RisingEdge(dut.clk)
    assert dut.empty.value, "FIFO should be empty after draining"


@cocotb.test()
async def test_full_and_overflow_guard(dut):
    """Fill to capacity; verify full flag; writes while full must be dropped."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value = 0
    dut.wr_en.value  = 0
    dut.rd_en.value  = 0
    dut.wr_data.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    for i in range(16):
        dut.wr_data.value = i
        dut.wr_en.value   = 1
        await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)
    assert dut.full.value,  "FIFO should be full after 16 writes"
    assert not dut.empty.value

    # attempt overflow write — should be silently dropped
    dut.wr_data.value = 0xFF
    dut.wr_en.value   = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)

    # read back — first value should be 0, not 0xFF
    dut.rd_en.value = 1
    await RisingEdge(dut.clk)
    dut.rd_en.value = 0
    await FallingEdge(dut.clk)
    assert int(dut.rd_data.value) == 0, \
        f"Overflow write corrupted FIFO head, got {int(dut.rd_data.value)}"


@cocotb.test()
async def test_almost_full_flag(dut):
    """almost_full must assert when wr_count >= DEPTH-2 (i.e. 14 or more)."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value = 0
    dut.wr_en.value  = 0
    dut.rd_en.value  = 0
    dut.wr_data.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    for i in range(13):
        dut.wr_data.value = i
        dut.wr_en.value   = 1
        await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)
    assert not dut.almost_full.value, \
        f"almost_full should NOT be set at count=13"

    dut.wr_data.value = 99
    dut.wr_en.value   = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)
    assert dut.almost_full.value, \
        f"almost_full should be set at count=14 (DEPTH-2)"


@cocotb.test()
async def test_simultaneous_read_write(dut):
    """Simultaneous rd_en + wr_en when not full/empty: count must stay constant."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value = 0
    dut.wr_en.value  = 0
    dut.rd_en.value  = 0
    dut.wr_data.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    # prime with 8 entries
    for i in range(8):
        dut.wr_data.value = i + 10
        dut.wr_en.value   = 1
        await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    await RisingEdge(dut.clk)

    count_before = int(dut.wr_count.value)
    dut.wr_data.value = 0xAB
    dut.wr_en.value   = 1
    dut.rd_en.value   = 1
    await RisingEdge(dut.clk)
    dut.wr_en.value = 0
    dut.rd_en.value = 0
    await RisingEdge(dut.clk)
    count_after = int(dut.wr_count.value)
    assert count_after == count_before, \
        f"Simultaneous RW should keep count stable: was {count_before}, now {count_after}"


@cocotb.test()
async def test_wr_count_accuracy(dut):
    """wr_count must track the exact number of stored entries at all times."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value = 0
    dut.wr_en.value  = 0
    dut.rd_en.value  = 0
    dut.wr_data.value = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    model_count = 0
    for cycle in range(40):
        do_wr = random.randint(0, 1) and model_count < 16
        do_rd = random.randint(0, 1) and model_count > 0
        dut.wr_en.value   = int(do_wr)
        dut.rd_en.value   = int(do_rd)
        dut.wr_data.value = random.randint(0, 255)
        await RisingEdge(dut.clk)
        dut.wr_en.value = 0
        dut.rd_en.value = 0
        if do_wr: model_count += 1
        if do_rd: model_count -= 1
        await RisingEdge(dut.clk)
        assert int(dut.wr_count.value) == model_count, \
            f"Cycle {cycle}: expected count={model_count}, got {int(dut.wr_count.value)}"


def test_sync_fifo_hidden_runner():
    sim = os.getenv("SIM", "icarus")
    proj_path = Path(__file__).resolve().parent.parent
    sources = [proj_path / "sources/sync_fifo.sv"]
    runner = get_runner(sim)
    runner.build(sources=sources, hdl_toplevel="sync_fifo", always=True)
    runner.test(hdl_toplevel="sync_fifo", test_module="test_sync_fifo_hidden")
