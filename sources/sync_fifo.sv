`timescale 1ns/1ps
module fir_filter #(
    parameter DATA_WIDTH  = 16,
    parameter COEFF_WIDTH = 16,
    parameter NUM_TAPS    = 8
)(
    input  logic                          clk,
    input  logic                          rst_n,
    input  logic                          valid_in,
    input  logic signed [DATA_WIDTH-1:0]  data_in,
    output logic                          valid_out,
    output logic signed [DATA_WIDTH-1:0]  data_out
);
    // Fixed coefficients (low-pass, symmetric)
    // h = [12, 32, 64, 128, 128, 64, 32, 12]
    // Complete the implementation
endmodule
