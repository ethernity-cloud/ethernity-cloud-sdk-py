#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
// Define sgx_status_t as uint32_t (from public SGX docs)
typedef uint32_t sgx_status_t;
#define SGX_SUCCESS 0x0000
#define SGX_ERROR_UNEXPECTED 0x0001 // Add other error codes as needed
// Function to generate report using inline assembly (invokes ENCLU[EREPORT])
sgx_status_t generate_sgx_report(const void* target_info, const void* report_data, void* report) {
    sgx_status_t status;
    __asm__ volatile (
        "movl $0, %%eax;\n\t"  // Set EAX = 0 (EREPORT leaf)
        "enclu;\n\t"
        : "=a" (status) // Output: status from RAX (EAX for 32-bit)
        : "b" (target_info), // RBX = address of target_info
          "c" (report_data), // RCX = address of report_data
          "d" (report) // RDX = address of report (output)
        : "memory", "cc" // Clobbers: memory and condition codes (if affected)
    );
    return status;
}
// Function to convert byte array to hex string (uses static buffer to avoid malloc)
const char* bytes_to_hex(const uint8_t* buf, size_t len) {
    static char hex_buffer[65]; // 32 bytes -> 64 chars + null
    if (len != 32) return NULL;
    for (size_t i = 0; i < len; ++i) {
        sprintf(hex_buffer + 2 * i, "%02x", buf[i]);
    }
    hex_buffer[64] = '\0';
    return hex_buffer;
}
const char* get_mr_enclave() {
    // Stack allocation with required alignments
    uint8_t target_info[512] __attribute__((aligned(512)));
    uint8_t report_data[64] __attribute__((aligned(128)));
    uint8_t report[432] __attribute__((aligned(512)));
    // Zero-initialize for a simple self-report (target_info all zeros means report for current enclave)
    memset(target_info, 0, sizeof(target_info));
    memset(report_data, 0, sizeof(report_data));
    memset(report, 0, sizeof(report));
    // Generate the report
    sgx_status_t status = generate_sgx_report(target_info, report_data, report);
    if (status == SGX_SUCCESS) {
        // Extract mr_enclave from correct offset (bytes 64-96 in sgx_report_t)
        return bytes_to_hex(report + 64, 32);
    }
    return NULL;
}
// Helper function for left rotation of uint8_t
static inline uint8_t rotl(uint8_t x, uint8_t n) {
    return (x << n) | (x >> (8 - n));
}
// Obfuscated function to generate a deterministic 32-byte string based on MR_ENCLAVE
void generate_obfuscated_string(const uint8_t* mr_enclave, uint8_t* output) {
    uint8_t temp[32];
    memcpy(temp, mr_enclave, 32);
    // Initial bogus mix to alter even indices (different constant for variety)
    for (int j = 0; j < 32; j += 2) {
        temp[j] ^= 0xA7;  // Arbitrary XOR, changed from original
    }
    // PRNG state for deterministic pseudo-random mixing (LCG parameters from PCG for good period)
    uint64_t prng_state = 0x853C49E6748FBA28ULL;  // Arbitrary fixed seed
    const uint64_t multiplier = 6364136223846793005ULL;
    const uint64_t increment = 1442695040888963407ULL;
    // High iteration count for debugger tedium (increased from original)
    const int max_iter = 1024;
    uint8_t mix_var = 0;
    for (int iter = 0; iter < max_iter; iter++) {
        // Opaque predicate (always false for integer iter >=0, as discriminant isn't perfect square)
        // Checked more frequently than original for added conditional branching tedium
        if (iter % 3 == 0) {
            if ((5 * iter * iter + 2) == (iter * 4)) {
                // Dead path: never executed, but confuses control flow analysis
                memset(temp, 0xFF, 32);
                iter = max_iter;  // Bogus exit
            }
        }
        // Advance PRNG and get mix type (0-3)
        prng_state = prng_state * multiplier + increment;
        uint8_t mix_type = (prng_state >> 59) % 4;  // Use higher bits for better distribution
        // Advance and get three indices (0-31)
        prng_state = prng_state * multiplier + increment;
        uint8_t idx1 = (prng_state >> 59) % 32;
        prng_state = prng_state * multiplier + increment;
        uint8_t idx2 = (prng_state >> 59) % 32;
        prng_state = prng_state * multiplier + increment;
        uint8_t idx3 = (prng_state >> 59) % 32;
        // Variable mixing operations based on type (different from original mixes)
        switch (mix_type) {
            case 0:  // Addition with wrap, then XOR (uses three indices)
                mix_var = (temp[idx1] + temp[idx2] + temp[idx3]) & 0xFF;
                temp[idx1] ^= mix_var;
                break;
            case 1: {  // Dynamic rotation left, then subtract
                uint8_t shift = (temp[idx3] % 8) + 1;  // Avoid zero shift
                temp[idx1] = rotl(temp[idx1], shift);
                temp[idx1] = (temp[idx1] - temp[idx2]) & 0xFF;
                break;
            }
            case 2:  // XOR chain across indices
                temp[idx1] ^= temp[idx2];
                temp[idx2] ^= temp[idx3];
                temp[idx3] ^= temp[idx1];
                break;
            case 3: {  // Multiply-like (shift-add) with wrap, then rotate right
                uint8_t rshift = temp[idx2] % 8;
                mix_var = ((temp[idx1] << 1) + temp[idx2]) & 0xFF;
                temp[idx1] = mix_var ^ temp[idx3];
                temp[idx1] = (temp[idx1] >> rshift) | (temp[idx1] << (8 - rshift));
                break;
            }
        }
        // Additional bogus conditional (always true, nested for extra stepping)
        if ((iter * iter % 2) != 1) {  // Always true for even/odd pattern, but simple
            // No-op path, but adds branch
        } else {
            // Dead, never hit
            temp[0] = 0;
        }
    }
    memcpy(output, temp, 32);
}
const char* get_mr_signer() {
    // Stack allocation with required alignments
    uint8_t target_info[512] __attribute__((aligned(512)));
    uint8_t report_data[64] __attribute__((aligned(128)));
    uint8_t report[432] __attribute__((aligned(512)));
    // Zero-initialize for a simple self-report (target_info all zeros means report for current enclave)
    memset(target_info, 0, sizeof(target_info));
    memset(report_data, 0, sizeof(report_data));
    memset(report, 0, sizeof(report));
    // Generate the report
    sgx_status_t status = generate_sgx_report(target_info, report_data, report);
    if (status == SGX_SUCCESS) {
        // Extract mr_enclave from correct offset (bytes 64-96 in sgx_report_t) for obfuscation
        uint8_t output[32];
        generate_obfuscated_string(report + 64, output);
        return bytes_to_hex(output, 32);
    }
    return NULL;
}