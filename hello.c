#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>

#include "risky.h"

extern void busy_loop(void);

void uart_send_c(char c) {
    while (!(IO_UART_CONTROL & 0x01));
    IO_UART_TX = c;
}

void uart_send(const char* s) {
    for (size_t i = 0; s[i]; i++) {
        uart_send_c(s[i]);
    }
}

void main(void) {
    IO_LEDS_0 = 0;
    while (true) {
        uart_send("Hello, risky!\r\n");
        IO_LEDS_0 += 1;
        busy_loop();
    }
}
