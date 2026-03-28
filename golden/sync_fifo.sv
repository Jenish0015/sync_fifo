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
    localparam signed [COEFF_WIDTH-1:0] COEFFS [0:NUM_TAPS-1] = '{
        16'd12, 16'd32, 16'd64, 16'd128,
        16'd128, 16'd64, 16'd32, 16'd12
    };

    logic signed [DATA_WIDTH-1:0]  shift_reg [0:NUM_TAPS-1];
    logic signed [DATA_WIDTH+COEFF_WIDTH:0] acc;
    logic [2:0] valid_pipe;
    integer i;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            for (i = 0; i < NUM_TAPS; i++) shift_reg[i] <= '0;
            valid_pipe <= '0;
        end else begin
            valid_pipe <= {valid_pipe[1:0], valid_in};
            if (valid_in) begin
                shift_reg[0] <= data_in;
                for (i = 1; i < NUM_TAPS; i++)
                    shift_reg[i] <= shift_reg[i-1];
            end
        end
    end

    always_comb begin
        acc = '0;
        for (i = 0; i < NUM_TAPS; i++)
            acc += shift_reg[i] * COEFFS[i];
    end

    assign valid_out = valid_pipe[2];
    assign data_out  = acc[DATA_WIDTH+COEFF_WIDTH-2 -: DATA_WIDTH];

endmodule
