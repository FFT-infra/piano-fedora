// SPDX-License-Identifier: MIT
/*
 * piano-camera-ctl: ask piano-camerad for a still picture and write it as
 * JPEG. The daemon owns the camera hardware; this client only receives a
 * packed NV12 frame over SCM_RIGHTS and encodes it.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>
#include <setjmp.h>
#include <jpeglib.h>
#include <linux/videodev2.h>

#include "photo-protocol.h"

struct jpeg_error {
	struct jpeg_error_mgr base;
	jmp_buf jump;
};

static void encode_error(j_common_ptr jpeg)
{
	struct jpeg_error *error = (struct jpeg_error *)jpeg->err;

	longjmp(error->jump, 1);
}

static uint8_t channel(int value)
{
	return value < 0 ? 0 : value > 255 ? 255 : (uint8_t)value;
}

/* BT.601 NV12 to RGB, full range unless the frame was tagged limited range. */
static void rgb_row(JSAMPLE *rgb, const uint8_t *y, const uint8_t *uv,
		    uint32_t width, bool limited)
{
	for (uint32_t x = 0; x < width; x++) {
		int cb = uv[x & ~1U] - 128, cr = uv[(x & ~1U) + 1] - 128;
		int luma = y[x], r, g, b;

		if (limited) {
			luma = 298 * (luma - 16);
			r = (luma + 409 * cr + 128) >> 8;
			g = (luma - 100 * cb - 208 * cr + 128) >> 8;
			b = (luma + 516 * cb + 128) >> 8;
		} else {
			r = luma + ((359 * cr + 128) >> 8);
			g = luma - ((88 * cb + 183 * cr + 128) >> 8);
			b = luma + ((454 * cb + 128) >> 8);
		}
		rgb[3 * x] = channel(r);
		rgb[3 * x + 1] = channel(g);
		rgb[3 * x + 2] = channel(b);
	}
}

static int encode_jpeg(int fd, const struct piano_photo_reply *r, FILE *output)
{
	struct jpeg_compress_struct jpeg;
	struct jpeg_error error;
	const uint8_t *pixels;
	JSAMPLE *row = NULL;
	bool limited = r->quantization == V4L2_QUANTIZATION_LIM_RANGE;
	volatile int ret = 0;

	if (r->width > JPEG_MAX_DIMENSION || r->height > JPEG_MAX_DIMENSION)
		return -ERANGE;
	pixels = mmap(NULL, (size_t)r->data_bytes, PROT_READ, MAP_SHARED, fd, 0);
	if (pixels == MAP_FAILED)
		return -errno;

	jpeg.err = jpeg_std_error(&error.base);
	error.base.error_exit = encode_error;
	if (setjmp(error.jump)) {
		ret = -EIO;
		goto out;
	}
	jpeg_create_compress(&jpeg);
	jpeg_stdio_dest(&jpeg, output);
	jpeg.image_width = r->width;
	jpeg.image_height = r->height;
	jpeg.input_components = 3;
	jpeg.in_color_space = JCS_RGB;
	jpeg_set_defaults(&jpeg);
	jpeg_set_quality(&jpeg, 95, TRUE);
	row = malloc((size_t)r->width * 3);
	if (!row) {
		ret = -ENOMEM;
		goto out;
	}
	jpeg_start_compress(&jpeg, TRUE);
	while (jpeg.next_scanline < r->height) {
		uint32_t line = jpeg.next_scanline;
		const uint8_t *y = pixels + (size_t)line * r->stride;
		const uint8_t *uv = pixels + (size_t)r->stride * r->height +
				    (size_t)(line / 2) * r->stride;

		rgb_row(row, y, uv, r->width, limited);
		jpeg_write_scanlines(&jpeg, &row, 1);
	}
	jpeg_finish_compress(&jpeg);
	if (fflush(output) != 0 || ferror(output))
		ret = -EIO;
out:
	if (row)
		free(row);
	jpeg_destroy_compress(&jpeg);
	munmap((void *)pixels, (size_t)r->data_bytes);
	return ret;
}

static int receive_reply(int fd, struct piano_photo_reply *reply, int *image_fd)
{
	char control[CMSG_SPACE(sizeof(int))] = { 0 };
	struct iovec iov = { .iov_base = reply, .iov_len = sizeof(*reply) };
	struct msghdr msg = {
		.msg_iov = &iov,
		.msg_iovlen = 1,
		.msg_control = control,
		.msg_controllen = sizeof(control),
	};
	struct cmsghdr *cmsg;
	ssize_t got;

	do {
		got = recvmsg(fd, &msg, 0);
	} while (got < 0 && errno == EINTR);
	if (got < 0)
		return -errno;
	if ((size_t)got != sizeof(*reply))
		return -EIO;
	if (reply->magic != PIANO_PHOTO_MAGIC ||
	    reply->version != PIANO_PHOTO_VERSION)
		return -EPROTO;
	if (reply->status)
		return -(int)reply->status;

	cmsg = CMSG_FIRSTHDR(&msg);
	if (!cmsg || cmsg->cmsg_level != SOL_SOCKET ||
	    cmsg->cmsg_type != SCM_RIGHTS ||
	    cmsg->cmsg_len != CMSG_LEN(sizeof(int)))
		return -ENODATA;
	memcpy(image_fd, CMSG_DATA(cmsg), sizeof(*image_fd));
	return 0;
}

static int capture(const char *camera, bool flash, const char *path)
{
	struct sockaddr_un sa = { .sun_family = AF_UNIX };
	struct piano_photo_reply reply = { 0 };
	struct pollfd wait;
	char request[64];
	FILE *output = NULL;
	bool created = false;
	int fd = -1, image_fd = -1, ret = 0, ready, length;

	snprintf(sa.sun_path, sizeof(sa.sun_path), "%s", PIANO_PHOTO_SOCKET);
	fd = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0);
	if (fd < 0)
		return -errno;
	if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) < 0) {
		ret = -errno;
		goto out;
	}
	length = snprintf(request, sizeof(request), "capture %s%s", camera,
			  flash ? " flash" : "");
	if (send(fd, request, (size_t)length, MSG_NOSIGNAL) != length) {
		ret = -errno;
		goto out;
	}
	wait = (struct pollfd){ .fd = fd, .events = POLLIN };
	do {
		ready = poll(&wait, 1, 15000);
	} while (ready < 0 && errno == EINTR);
	if (ready <= 0) {
		ret = ready < 0 ? -errno : -ETIMEDOUT;
		goto out;
	}
	ret = receive_reply(fd, &reply, &image_fd);
	if (ret < 0)
		goto out;

	if (!strcmp(path, "-")) {
		output = stdout;
	} else {
		/* The unprivileged client owns the destination path; the daemon
		 * never receives a filesystem path from us. */
		int ofd = open(path, O_WRONLY | O_CREAT | O_EXCL | O_CLOEXEC, 0644);

		if (ofd < 0) {
			ret = -errno;
			goto out;
		}
		created = true;
		output = fdopen(ofd, "wb");
		if (!output) {
			close(ofd);
			ret = -errno;
			goto out;
		}
	}
	ret = encode_jpeg(image_fd, &reply, output);
	if (output != stdout) {
		if (fclose(output) != 0 && !ret)
			ret = -errno;
		output = NULL;
	}
	if (!ret)
		fprintf(stderr, "Captured %ux%u JPEG%s.\n", reply.width,
			reply.height, reply.flash_fault ? " (flash fault)" : "");
out:
	if (output && output != stdout)
		fclose(output);
	if (image_fd >= 0)
		close(image_fd);
	if (fd >= 0)
		close(fd);
	if (ret && created)
		unlink(path);
	if (ret)
		fprintf(stderr, "capture: %s\n", strerror(-ret));
	return ret;
}

static int usage(void)
{
	fprintf(stderr, "Usage: piano-camera-ctl capture [rear|front] [--flash] "
			"--output FILE|-\n");
	return 2;
}

int main(int argc, char **argv)
{
	const char *camera = "rear", *path = NULL;
	bool flash = false;

	if (argc < 2 || strcmp(argv[1], "capture"))
		return usage();
	for (int i = 2; i < argc; i++) {
		if (!strcmp(argv[i], "--flash"))
			flash = true;
		else if (!strcmp(argv[i], "--output") && i + 1 < argc)
			path = argv[++i];
		else if (!strcmp(argv[i], "rear") || !strcmp(argv[i], "front"))
			camera = argv[i];
		else
			return usage();
	}
	if (!path)
		return usage();
	if (flash && strcmp(camera, "rear")) {
		fprintf(stderr, "capture: --flash is only available on the rear camera\n");
		return 2;
	}
	return capture(camera, flash, path) ? 1 : 0;
}
