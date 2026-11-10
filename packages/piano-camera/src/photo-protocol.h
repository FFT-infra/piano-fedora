/* SPDX-License-Identifier: MIT */
/*
 * piano-camerad still-capture protocol: one request record and one reply
 * record per connection. The image itself travels as a memfd in SCM_RIGHTS,
 * packed NV12 with a tight stride.
 */
#ifndef PIANO_PHOTO_PROTOCOL_H
#define PIANO_PHOTO_PROTOCOL_H

#include <stdint.h>

#define PIANO_PHOTO_MAGIC 0x50484f54u /* "PHOT" */
#define PIANO_PHOTO_VERSION 1u
#define PIANO_PHOTO_SOCKET "/run/piano-camerad/control.sock"

struct piano_photo_reply {
	uint32_t magic, version, status;
	uint32_t width, height, stride;
	uint32_t colorspace, ycbcr_enc, quantization;
	uint32_t frames, flash_fault;
	uint64_t data_bytes;
};

#endif
