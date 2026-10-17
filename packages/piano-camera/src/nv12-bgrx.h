/* SPDX-License-Identifier: MIT */
/* Local loopback publication conversion, independent of ISP/control code. */
#ifndef PIANO_NV12_BGRX_H
#define PIANO_NV12_BGRX_H

#include <stddef.h>
#include <stdint.h>
#ifdef __aarch64__
#include <arm_neon.h>
#endif

static inline uint8_t bgrx_clip(int v)
{
	return v < 0 ? 0 : v > 255 ? 255 : v;
}

static inline void bgrx_pair(const uint8_t *restrict luma, const uint8_t *restrict chroma,
			     uint8_t *restrict d)
{
	int u = (int)chroma[0] - 128;
	int v = (int)chroma[1] - 128;
	int b = 454 * u, g = -88 * u - 183 * v, r = 359 * v;

	int y0 = (int)luma[0] * 256 + 128;
	int y1 = (int)luma[1] * 256 + 128;
	d[0] = bgrx_clip((y0 + b) >> 8);
	d[1] = bgrx_clip((y0 + g) >> 8);
	d[2] = bgrx_clip((y0 + r) >> 8);
	d[3] = 0;
	d[4] = bgrx_clip((y1 + b) >> 8);
	d[5] = bgrx_clip((y1 + g) >> 8);
	d[6] = bgrx_clip((y1 + r) >> 8);
	d[7] = 0;
}

/* Full-range BT.601, Q8 coefficients; even dimensions, shared Y/UV stride.
 * Destination is tightly packed B,G,R,x bytes (V4L2 XBGR32 / XR24).
 * Input and destination must not overlap; x is deterministic zero padding.
 */
static inline void nv12_to_bgrx_ref(const uint8_t *restrict y,
			      const uint8_t *restrict uv, unsigned int stride,
			      uint8_t *restrict dst, unsigned int w, unsigned int h)
{
	for (unsigned int row = 0; row < h; row++) {
		const uint8_t *luma = y + (size_t)row * stride;
		const uint8_t *chroma = uv + (size_t)(row / 2) * stride;
		uint8_t *d = dst + (size_t)row * w * 4;

		for (size_t col = 0; col < w; col += 2)
			bgrx_pair(luma + col, chroma + col, d + 4 * col);
	}
}

#ifdef __aarch64__
/* Add Q8 luma/rounding before arithmetic shift and unsigned saturation.
 * All intermediates fit signed 32 bits; chroma products do not fit 16 bits.
 */
static inline uint8x8_t bgrx_neon_channel(int32x4_t lo, int32x4_t hi,
                                         int32x4_t ylo, int32x4_t yhi)
{
	return vqmovn_u16(vcombine_u16(vqshrun_n_s32(vaddq_s32(lo, ylo), 8),
	                              vqshrun_n_s32(vaddq_s32(hi, yhi), 8)));
}

static inline uint8x8x4_t bgrx_neon_eight(uint8x8_t y, int16x8_t u, int16x8_t v)
{
	uint16x8_t yw = vmovl_u8(y);
	int32x4_t ylo = vaddq_s32(vreinterpretq_s32_u32(vshll_n_u16(vget_low_u16(yw), 8)),
	                         vdupq_n_s32(128));
	int32x4_t yhi = vaddq_s32(vreinterpretq_s32_u32(vshll_n_u16(vget_high_u16(yw), 8)),
	                         vdupq_n_s32(128));
	int16x4_t ul = vget_low_s16(u), uh = vget_high_s16(u);
	int16x4_t vl = vget_low_s16(v), vh = vget_high_s16(v);
	uint8x8x4_t out;
	out.val[0] = bgrx_neon_channel(vmull_n_s16(ul, 454), vmull_n_s16(uh, 454), ylo, yhi);
	out.val[1] = bgrx_neon_channel(vmlal_n_s16(vmull_n_s16(ul, -88), vl, -183),
	                               vmlal_n_s16(vmull_n_s16(uh, -88), vh, -183), ylo, yhi);
	out.val[2] = bgrx_neon_channel(vmull_n_s16(vl, 359), vmull_n_s16(vh, 359), ylo, yhi);
	out.val[3] = vdup_n_u8(0);
	return out;
}
#endif

static inline void nv12_to_bgrx(const uint8_t *restrict y,
                              const uint8_t *restrict uv, unsigned int stride,
                              uint8_t *restrict dst, unsigned int w, unsigned int h)
{
#ifdef __aarch64__
	for (unsigned int row = 0; row < h; row++) {
		const uint8_t *luma = y + (size_t)row * stride;
		const uint8_t *chroma = uv + (size_t)(row / 2) * stride;
		uint8_t *d = dst + (size_t)row * w * 4;
		size_t col = 0;
		for (; col + 16 <= w; col += 16) {
			uint8x16_t yy = vld1q_u8(luma + col);
			uint8x8x2_t uv8 = vld2_u8(chroma + col);
			int16x8_t u = vsubq_s16(vreinterpretq_s16_u16(vmovl_u8(uv8.val[0])),
			                          vdupq_n_s16(128));
			int16x8_t v = vsubq_s16(vreinterpretq_s16_u16(vmovl_u8(uv8.val[1])),
			                          vdupq_n_s16(128));
			/* Each chroma sample belongs to two adjacent luma pixels. */
			uint8x8x4_t lo = bgrx_neon_eight(vget_low_u8(yy), vzip1q_s16(u, u), vzip1q_s16(v, v));
			uint8x8x4_t hi = bgrx_neon_eight(vget_high_u8(yy), vzip2q_s16(u, u), vzip2q_s16(v, v));
			uint8x16x4_t out;
			out.val[0] = vcombine_u8(lo.val[0], hi.val[0]);
			out.val[1] = vcombine_u8(lo.val[1], hi.val[1]);
			out.val[2] = vcombine_u8(lo.val[2], hi.val[2]);
			out.val[3] = vdupq_n_u8(0);
			vst4q_u8(d + 4 * col, out);
		}
		for (; col < w; col += 2)
			bgrx_pair(luma + col, chroma + col, d + 4 * col);
	}
#else
	nv12_to_bgrx_ref(y, uv, stride, dst, w, h);
#endif
}

#endif
