#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>

extern void busy_loop(void);

extern volatile struct {
    struct {
        bool tx_ready : 1;
    } control;
    uint32_t input;
    uint32_t output;
} UART;

void uart_send_c(char c) {
    while (!UART.control.tx_ready);
    UART.output = c;
}

void uart_send(const char* s) {
    for (size_t i = 0; s[i]; i++) {
        uart_send_c(s[i]);
    }
}

void main(void) {
    while (true) {
        uart_send("Hello, risky!\r\n");
        busy_loop();
    }
}
