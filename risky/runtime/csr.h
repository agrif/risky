#ifndef __RISKY_CSR_H
#define __RISKY_CSR_H

#define read_csr(addr) ({                                      \
            uint32_t result;                                   \
            asm volatile ("csrr %0, " #addr : "=r"(result));   \
            result;                                            \
        })

#endif /* __RISKY_CSR_H */
