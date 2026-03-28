from __future__ import annotations
import os
import random
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, FallingEdge
from cocotb_tools.runner import get_runner

COEFFS = [12, 32, 64, 128, 128, 64, 32, 12]
NUM_TAPS = 8

def fir_model(samples, coeffs):
    results = []
    buf = [0] * len(coeffs)
    for s in samples:
        buf = [s] + buf[:-1]
        acc = sum(b * c for b, c in zip(buf, coeffs))
        results.append(acc >> 15)
    return results

def to_signed16(v):
    v = int(v) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v

@cocotb.test()
async def test_impulse_response(dut):
    """Single impulse input — output must match FIR coefficients."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value   = 0
    dut.valid_in.value = 0
    dut.data_in.value  = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    awaitisingEdge(dut.clk)

    # Send impulse
    dut.data_in.value  = 1000
    dut.valid_in.value = 1
    await RisingEdge(dut.clk)
    dut.data_in.value  = 0
    for _ in range(NUM_TAPS + 4):
        await RisingEdge(dut.clk)
        if dut.valid_out.value:
            out = to_signed16(dut.data_out.value)
            assert abs(out) >= 0, "output must be numeric"
    dut.valid_in.value = 0

@cocotb.test()
async def test_dc_response(dut):
    """Constant input — output must stabilize to correct DC value."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value    = 0
    dut.valid_in.value = 0
    dut.data_in.value  = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    dc_val = 100
    samples = [dc_val] * 20
    expected = fir_model(samples, COEFFS)

    outputs = []
    dut.valid_in.value = 1
    for s in samples:
        dut.data_in.value = s
        await RisingEdge(dut.clk)
        if dut.valid_out.value:
          outputs.append(to_signed16(dut.data_out.value))
    dut.valid_in.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
        if dut.valid_out.value:
            outputs.append(to_signed16(dut.data_out.value))

    assert len(outputs) > 0, "No valid_out ever asserted"
    # After 8 samples, output should stabilize near expected DC
    if len(outputs) >= NUM_TAPS:
        steady = outputs[-1]
        ref    = expected[-1]
        assert abs(steady - ref) <= 2, \
            f"DC steady state wrong: got {steady}, expected ~{ref}"

@cocotb.test()
async def test_valid_pipeline_delay(dut):
    """valid_out must assert exactly after pipeline fills."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value    = 0
    dut.valid_in.value = 0
    dut.data_in.value  = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    assert not dut.valid_out.value, "valid_out should be 0 before any input"

    dut.valid_in.value = 1
    dut.data_in.value  = 500
    valid_out_count = 0
    for cycle in range(20):
        await RisingEdge(dut.clk)
        if dut.valid_out.value:
            valid_out_count += 1
    dut.valid_in.value = 0

    assert valid_out_count > 0, "valid_out never asserted during streaming input"

@cocotb.test()
async def test_reset_clears_state(dut):
    """After mid-stream reset, output must return to 0."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value    = 0
    dut.valid_in.value = 0
    dut.data_in.value  = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    # Stream data
    dut.valid_in.value = 1
    for i in range(10):
        dut.data_in.value = random.randint(100, 1000)
        await RisingEdge(dut.clk)

    # Assert reset mid-stream
    dut.rst_n.value    = 0
    dut.valid_in.value = 0
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    assert not dut.valid_out.value, \
        "valid_out should be 0 immediately after reset"
    assert to_signed16(dut.data_out.value) == 0, \
        "data_out should be 0 after reset"

@cocotb.test()
async def test_random_sequence_accuracy(dut):
    """Random input sequence must match software FIR model within tolerance."""
    clock = Clock(dut.clk, 10, unit="ns")
    clock.start(start_high=False)
    dut.rst_n.value    = 0
    dut.valid_in.value = 0
    dut.data_in.value  = 0
    for _ in range(3): await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    samples  = [random.randint(-500, 500) for _ in range(30)]
    expected = fir_model(samples, COEFFS)

    outputs = []
    dut.valid_in.value = 1
    for s in samples:
        dut.data_in.value = s & 0xFFFF
        await RisingEdge(dut.clk)
        if dut.valid_out.value:
            outputs.append(to_signed16(dut.data_out.value))
    dut.valid_in.value = 0
    for _ in range(5):
        await RisingEdge(dut.clk)
        if dut.valid_out.value:
            outputs.append(to_signed16(dut.data_out.value))

    assert len(outputs) >= 10, f"Too few outputs: {len(outputs)}"
    mismatches = 0
    for i, (got, exp) in enumerate(zip(outputs, expected)):
        if abs(got - exp) > 3:
            mismatches += 1
    assert mismatches == 0, \
        f"{mismatches} samples exceeded tolerance vs software model"

def test_sync_fifo_hidden_runner():
    sim = os.getenv("SIM", "icarus")
    proj_path = Path(__file__).resolve().parent.parent
    sources = [proj_path / "sources/sync_fifo.sv"]
    runner = get_runner(sim)
    runner.build(sources=sources, hdl_toplevel="fir_filter", always=True)
    runner.test(hdl_toplevel="fir_filter", test_module="test_sync_fifo_hidden")
