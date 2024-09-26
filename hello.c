#include <stdint.h>
#include <stdlib.h>
#include <stdbool.h>

#include "risky.h"
#include "csr.h"

void uart_send_c(char c) {
    while (!(IO_UART_CONTROL & 0x01));
    IO_UART_TX = c;
}

void uart_send(const char* s) {
    for (size_t i = 0; s[i]; i++) {
        uart_send_c(s[i]);
    }
}

uint64_t get_cycle(void) {
    while (true) {
        uint64_t hi = read_csr(0xc80);
        uint64_t lo = read_csr(0xc00);
        if (read_csr(0xc80) == hi) {
            return (hi << 32) | lo;
        }
    }
}

void sleep_ms(uint16_t ms) {
    uint64_t now = get_cycle();
    uint64_t amount = (uint64_t)IO_CLK_FREQ_DATA * (uint64_t)ms / 1000;
    uint64_t later = now + amount;

    while (get_cycle() < later);
}

void main(void) {
    IO_LEDS_0 = 0;
    while (true) {
        uart_send("Hello, risky!\r\n");
        IO_LEDS_0 += 1;
        sleep_ms(1000);
    }
}
