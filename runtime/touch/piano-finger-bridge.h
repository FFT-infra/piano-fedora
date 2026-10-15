/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <stddef.h>
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
int piano_finger_open(const char *calibration);
int piano_finger_record(const uint8_t *payload, size_t size, uint64_t sequence,
                        uint64_t timestamp, uint16_t checksum, uint16_t flags,
                        uint32_t words, uint64_t now);
int piano_finger_tick(uint64_t now);
int piano_finger_reset(void);
void piano_finger_close(void);
#ifdef __cplusplus
}
#endif
