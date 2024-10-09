#include <ctype.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "risky.h"
#include "csr.h"

#if defined(ROM_BASE)
#define BOOT_ADDR ROM_BASE
#elif defined(RAM_BASE)
#define BOOT_ADDR RAM_BASE
#else
#error "no entry point"
#endif

#define STRINGIFY_EXPAND(s) STRINGIFY(s)
#define STRINGIFY(s) #s

#define VERSION 1
#define BANNER_TEXT "risky-b" STRINGIFY_EXPAND(VERSION)
#define BUFFER_SIZE 1024

static inline void uart_set_baud(uint32_t baud) {
    // default precalculated divisor, usually for 115200
    IO_UART_BAUD = IO_INFO_STD_BAUD;
}

static inline bool uart_can_send(void) {
    return IO_UART_TX_CONTROL & IO_UART_TX_CONTROL_READY_MASK;
}

static inline void uart_send_c(char c) {
    while (!uart_can_send());
    IO_UART_TX = c;
}

void uart_send(const char* s) {
    for (size_t i = 0; s[i]; i++) {
        uart_send_c(s[i]);
    }
}

#define uart_send_line(...) uart_send(__VA_ARGS__ "\r\n")

#define uart_send_error(s) uart_send_line("e: " s)

void uart_send_hex(uint32_t val, uint8_t width) {
    uint8_t digit = 7;
    bool started = false;
    while (digit < 8) {
        uint8_t part = val >> 28;
        val <<= 4;

        if (started || part || digit < width) {
            char c = (part < 10) ? ('0' + part) : ('a' + part - 10);
            uart_send_c(c);
            started = true;
        }

        digit--;
    }
}

void uart_send_status(char c, uint32_t val) {
    uart_send_c(c);
    uart_send_c(' ');
    uart_send_hex(val, 1);
    uart_send_line();
}

static inline bool uart_can_recv(void) {
    return IO_UART_RX_CONTROL & IO_UART_RX_CONTROL_READY_MASK;
}

static inline char uart_recv_c(void) {
    while (!uart_can_recv());
    return IO_UART_RX;
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

static inline void boot(uint32_t addr) {
    // boot
    void (*entry)(void) = (void*)addr;
    entry();
}

static inline bool is_space(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

bool echo = false;
size_t parse_next;
char command[BUFFER_SIZE];

bool read_command(void) {
    static size_t command_next = 0;

    char c = uart_recv_c();
    command[command_next] = c;

    if (c == '\n' || c == '\r') {
        if (command_next > 0) {
            if (echo) {
                uart_send_line("");
            }

            command[command_next] = 0;
            command_next = 0;

            return true;
        }
    }

    if (command_next == 0 && is_space(c)) {
        return false;
    }

    command_next++;

    if (echo) {
        uart_send_c(c);
    }

    if (command_next >= BUFFER_SIZE) {
        // overrun
        uart_send_error("overrun");
        command_next = 0;
    }

    return false;
}

static inline bool parse_end(void) {
    return command[parse_next] == 0;
}

void parse_space(void) {
    while (is_space(command[parse_next])) {
        parse_next++;
    }
}

bool parse_hex(uint32_t* out) {
    *out = 0;

    bool found = false;
    while (true) {
        char c = command[parse_next];
        char val;
        if (c >= '0' && c <= '9') {
            val = c - '0';
        } else if (c >= 'a' && c <= 'f') {
            val = 10 + c - 'a';
        } else if (c >= 'A' && c <= 'F') {
            val = 10 + c - 'A';
        } else {
            break;
        }

        *out = (*out << 4) | val;
        found = true;
        parse_next++;
    }

    parse_space();

    return found;
}

char command_c;
uint32_t command_arg1;
uint32_t command_arg2;
uint32_t command_arg3;

uint32_t parse_command(void) {
    command_c = command[0];
    if (command_c == 0) {
        return 0;
    }

    parse_next = 1;
    parse_space();

    return 1 + parse_hex(&command_arg1) + parse_hex(&command_arg2) + parse_hex(&command_arg3);
}

uint32_t read_memory(uint8_t* start, uint8_t* end) {
    uint8_t* cur = start;
    while (cur < end) {
        uart_send_hex((uint32_t)cur, 8);
        uart_send_c(':');

        uint8_t col = 0;
        while (cur < end && col < 16) {
            uart_send_c(' ');
            if ((col & 0x3) == 0) {
                uart_send_c(' ');
                if ((col & 0x7) == 0) {
                    uart_send_c(' ');
                }
            }

            uart_send_hex(*cur, 2);

            cur++;
            col++;
        }

        uart_send_line();
    }

    return end - start;
}

uint32_t copy_memory(uint8_t* start, uint8_t* end, uint8_t* dest) {
    uint8_t* cur = start;
    while (cur < end) {
        *dest = *cur;
        cur++;
        dest++;
    }

    return end - start;
}

uint32_t write_memory(uint8_t* start, uint32_t preparsed, uint8_t a, uint8_t b) {
    uint8_t* cur = start;
    if (preparsed) {
        *cur = a;
        cur++;
        preparsed--;
    }

    if (preparsed) {
        *cur = b;
        cur++;
    }

    uint32_t tmp;
    while (parse_hex(&tmp)) {
        *cur = tmp;
        cur++;
    }

    return cur - start;
}

bool run_command(void) {
    static uint32_t last_address = 0;

    uint32_t args = parse_command();
    bool end = parse_end();
    uint32_t status = 0;
    bool valid = false;

    if (command_c == 'i' && args == 1 && end) {
        uart_send_line(BANNER_TEXT);
        uart_send_status('k', BUFFER_SIZE);
        uart_send_status('b', BOOT_ADDR);

        status = VERSION;
        valid = true;
    }

    if (command_c == 'e' && args == 1 && end) {
        echo = ~echo;

        status = echo;
        valid = true;
    }

    if (command_c == 'b' && args >= 1 && args <= 2 && end) {
        uint32_t addr = args >= 2 ? command_arg1 : BOOT_ADDR;
        boot(addr);

        status = addr;
        valid = true;
    }

    if (command_c == 'm' && args >= 1 && args <= 3 && end) {
        uint32_t start = args >= 2 ? command_arg1 : last_address;
        uint32_t end = args >= 3 ? command_arg2 : start + 128;

        status = read_memory((uint8_t*)start, (uint8_t*)end);
        last_address = end;

        valid = true;
    }

    if (command_c == 'c' && args == 4) {
        status = copy_memory((uint8_t*)command_arg1, (uint8_t*)command_arg2, (uint8_t*)command_arg3);

        valid = true;
    }

    if (command_c == 'p' && args >= 2) {
        status = write_memory((uint8_t*)command_arg1, args - 2, command_arg2, command_arg3);

        valid = true;
    }

    if (valid) {
        uart_send_status(command_c, status);
    }

    return valid;
}

void main(void) {
    // roughly 250ms
    uint64_t timeout = get_cycle() + ((IO_INFO_CLK_FREQ) >> 2);

    // set up uart and send our info
    uart_set_baud(115200);
    uart_send_line(BANNER_TEXT);

    // wait for input with timeout
    bool timeout_active = true;
    while (!timeout_active || get_cycle() < timeout) {
        if (uart_can_recv()) {
            if (read_command() && run_command()) {
                // one successfull command means no timeout
                timeout_active = false;
            }
        }
    }

    // if we timeout, boot
    boot(BOOT_ADDR);
}
