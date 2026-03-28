`timescale 1ns/1ps
module sync_fifo #(
    parameter DATA_WIDTH = 8,
    parameter DEPTH      = 16
) (
    input  logic                  clk,
    input  logic                  rst_n,
    input  logic                  wr_en,
    input  logic                  rd_en,
    input  logic [DATA_WIDTH-1:0] wr_data,
    output logic [DATA_WIDTH-1:0] rd_data,
    output logic                  full,
    output logic                  empty,
    output logic                  almost_full,
    output logic [$clog2(DEPTH):0] wr_count
);
    localparam LOG2 = $clog2(DEPTH);

    logic [DATA_WIDTH-1:0] mem [0:DEPTH-1];
    logic [LOG2:0] wr_ptr, rd_ptr;

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) wr_ptr <= '0;
        else if (wr_en && !full) begin
            mem[wr_ptr[LOG2-1:0]] <= wr_data;
            wr_ptr <= wr_ptr + 1;
        end
    end

    always_ff @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            rd_ptr  <= '0;
            rd_data <= '0;
        end else if (rd_en && !empty) begin
            rd_data <= mem[rd_ptr[LOG2-1:0]];
            rd_ptr  <= rd_ptr + 1;
        end
    end

    assign wr_count    = wr_ptr - rd_ptr;
    assign full        = (wr_count == DEPTH[LOG2:0]);
    assign empty       = (wr_count == '0);
    assign almost_full = (wr_count >= (DEPTH - 2));

endmodule
