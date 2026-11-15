/* Host unit tests for the pure USB protocol layer (usbproto.h): every
 * descriptor byte-by-byte, the ep0 request handling for all supported
 * requests with exact expected bytes, wLength truncation, string
 * UTF-16LE content, DTR/RTS bits, deferred SET_ADDRESS, stall paths,
 * and a bLength walk of the config descriptor. Build (also run by
 * shim/build.sh):
 *   gcc -O1 -Wall -Wextra -I. -o out/usbproto_host_test \
 *       usbproto.c usbproto_host_test.c */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "usbproto.h"

static struct usbproto st;

/* Issue one SETUP packet (fields in host order, encoded here) and run
 * usbproto_setup against the shared device state. */
static int req(uint8_t bmRequestType, uint8_t bRequest,
	       uint16_t wValue, uint16_t wIndex, uint16_t wLength,
	       const uint8_t **data, uint16_t *len)
{
	uint8_t setup[8];

	setup[0] = bmRequestType;
	setup[1] = bRequest;
	setup[2] = (uint8_t)wValue;
	setup[3] = (uint8_t)(wValue >> 8);
	setup[4] = (uint8_t)wIndex;
	setup[5] = (uint8_t)(wIndex >> 8);
	setup[6] = (uint8_t)wLength;
	setup[7] = (uint8_t)(wLength >> 8);
	return usbproto_setup(setup, &st, data, len);
}

/* Decode a UTF-16LE string descriptor into a narrow buffer. */
static void decode_string(const uint8_t *d, char *out, unsigned cap)
{
	unsigned i;

	for (i = 0; 2u + 2u * i + 1 < d[0] && i + 1 < cap; i++)
		out[i] = (char)d[2 + 2 * i];
	out[i] = 0;
}

/* ---- descriptor tests ---- */

static void test_device_descriptor_bytes(void)
{
	static const uint8_t want[USBPROTO_DEVICE_DESC_LEN] = {
		0x12, 0x01, 0x00, 0x02, 0x02, 0x00, 0x00, 0x40,
		0x09, 0x12, 0x01, 0x00, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01,
	};

	assert(sizeof(usbproto_device_desc) == 18);
	assert(memcmp(usbproto_device_desc, want, sizeof(want)) == 0);
}

static void test_qualifier_descriptor_bytes(void)
{
	static const uint8_t want[USBPROTO_QUALIFIER_DESC_LEN] = {
		0x0A, 0x06, 0x00, 0x02, 0x02, 0x00, 0x00, 0x40, 0x01, 0x00,
	};

	assert(sizeof(usbproto_qualifier_desc) == 10);
	assert(memcmp(usbproto_qualifier_desc, want, sizeof(want)) == 0);
}

static void test_config_descriptor_walk(void)
{
	const uint8_t *d = usbproto_config_desc;
	unsigned off, n_iface, n_ep;

	assert(sizeof(usbproto_config_desc) == 67);
	assert(d[0] == 9 && d[1] == 0x02);
	assert(d[2] == 67 && d[3] == 0);	/* wTotalLength == sizeof */
	assert(d[5] == 1);			/* bConfigurationValue */
	assert(d[4] == 2);			/* bNumInterfaces */

	n_iface = n_ep = 0;
	for (off = 0; off < sizeof(usbproto_config_desc);) {
		uint8_t blen = d[off];

		assert(blen >= 2);
		assert(off + blen <= sizeof(usbproto_config_desc));
		if (d[off + 1] == 0x04) {
			n_iface++;
			assert(blen == 9);
		}
		if (d[off + 1] == 0x05) {
			n_ep++;
			assert(blen == 7);
		}
		if (d[off + 1] == 0x24)
			assert(blen >= 4);	/* functional descriptor */
		off += blen;
	}
	assert(off == sizeof(usbproto_config_desc));
	assert(n_iface == 2);
	assert(n_ep == 3);

	/* Interface 0 at offset 9: comm class 2, ACM subclass 2, proto 1,
	 * one endpoint. */
	assert(d[9] == 9 && d[10] == 0x04);
	assert(d[11] == 0 && d[12] == 0);	/* number 0, altsetting 0 */
	assert(d[13] == 1);			/* bNumEndpoints */
	assert(d[14] == 0x02 && d[15] == 0x02 && d[16] == 0x01);

	/* CDC functional descriptors, offsets 18..37. */
	assert(d[18] == 5 && d[19] == 0x24 && d[20] == 0x00);
	assert(d[21] == 0x10 && d[22] == 0x01);		/* bcdCDC 1.10 */
	assert(d[23] == 5 && d[24] == 0x24 && d[25] == 0x01);	/* call mgmt */
	assert(d[26] == 0x00);				/* bmCapabilities */
	assert(d[27] == 0x01);				/* bDataInterface */
	assert(d[28] == 4 && d[29] == 0x24 && d[30] == 0x02);	/* ACM */
	assert(d[31] == 0x02);				/* bmCapabilities */
	assert(d[32] == 5 && d[33] == 0x24 && d[34] == 0x06);	/* union */
	assert(d[35] == 0x00);				/* master interface 0 */
	assert(d[36] == 0x01);				/* slave interface 1 */

	/* Interrupt IN ep 0x83, size 8, interval 16. */
	assert(d[37] == 7 && d[38] == 0x05 && d[39] == 0x83);
	assert(d[40] == 0x03);
	assert(d[41] == 8 && d[42] == 0);
	assert(d[43] == 16);

	/* Interface 1 at offset 44: data class 0x0A, two endpoints. */
	assert(d[44] == 9 && d[45] == 0x04 && d[46] == 0x01);
	assert(d[48] == 2 && d[49] == 0x0A);

	/* Bulk OUT 0x01 / bulk IN 0x81, 512-byte packets. */
	assert(d[53] == 7 && d[54] == 0x05 && d[55] == 0x01);
	assert(d[56] == 0x02 && d[57] == 0x00 && d[58] == 0x02);
	assert(d[60] == 7 && d[61] == 0x05 && d[62] == 0x81);
	assert(d[63] == 0x02 && d[64] == 0x00 && d[65] == 0x02);
}

static void test_string_descriptors(void)
{
	char text[32];

	assert(sizeof(usbproto_string0_desc) == 4);
	assert(usbproto_string0_desc[0] == 4);
	assert(usbproto_string0_desc[1] == 0x03);
	assert(usbproto_string0_desc[2] == 0x09);	/* LANGID 0x0409 LE */
	assert(usbproto_string0_desc[3] == 0x04);

	assert(sizeof(usbproto_string1_desc) == 18);
	assert(usbproto_string1_desc[0] == 18 && usbproto_string1_desc[1] == 3);
	decode_string(usbproto_string1_desc, text, sizeof(text));
	assert(strcmp(text, "PixelIOS") == 0);

	assert(sizeof(usbproto_string2_desc) == 36);
	assert(usbproto_string2_desc[0] == 36 && usbproto_string2_desc[1] == 3);
	decode_string(usbproto_string2_desc, text, sizeof(text));
	assert(strcmp(text, "Pixel 6a shim log") == 0);
	/* Every high byte is 0 (ASCII in UTF-16LE). */
	for (unsigned i = 3; i < sizeof(usbproto_string2_desc); i += 2)
		assert(usbproto_string2_desc[i] == 0);

	assert(sizeof(usbproto_string3_desc) == 10);
	assert(usbproto_string3_desc[0] == 10 && usbproto_string3_desc[1] == 3);
	decode_string(usbproto_string3_desc, text, sizeof(text));
	assert(strcmp(text, "0001") == 0);
}

/* ---- ep0 request tests ---- */

static void test_get_descriptor_device(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x80, 0x06, 0x0100, 0x0000, 18, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 18);
	assert(memcmp(data, usbproto_device_desc, 18) == 0);

	/* First-packet read: exactly the first 8 bytes. */
	assert(req(0x80, 0x06, 0x0100, 0x0000, 8, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 8);
	assert(memcmp(data, usbproto_device_desc, 8) == 0);

	/* Over-long wLength truncates to the descriptor length. */
	assert(req(0x80, 0x06, 0x0100, 0x0000, 255, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 18);

	/* wLength 0: zero-length IN stage, not a stall. */
	assert(req(0x80, 0x06, 0x0100, 0x0000, 0, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 0);
}

static void test_get_descriptor_config(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x80, 0x06, 0x0200, 0x0000, 255, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 67);
	assert(memcmp(data, usbproto_config_desc, 67) == 0);

	/* Nine-byte header read, then the rest: standard host flow. */
	assert(req(0x80, 0x06, 0x0200, 0x0000, 9, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 9);
	assert(memcmp(data, usbproto_config_desc, 9) == 0);

	assert(req(0x80, 0x06, 0x0200, 0x0000, 66, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 66);

	/* Only configuration 0 exists. */
	assert(req(0x80, 0x06, 0x0201, 0x0000, 255, &data, &len) ==
	       USBPROTO_STALL);
}

static void test_get_descriptor_strings(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	for (uint8_t i = 0; i <= 3; i++) {
		assert(req(0x80, 0x06, (uint16_t)(0x0300u + i), 0x0000, 255,
			   &data, &len) == USBPROTO_OK_DATA_IN);
		switch (i) {
		case 0:
			assert(len == 4);
			assert(memcmp(data, usbproto_string0_desc, 4) == 0);
			break;
		case 1:
			assert(len == 18);
			assert(memcmp(data, usbproto_string1_desc, 18) == 0);
			break;
		case 2:
			assert(len == 36);
			assert(memcmp(data, usbproto_string2_desc, 36) == 0);
			break;
		case 3:
			assert(len == 10);
			assert(memcmp(data, usbproto_string3_desc, 10) == 0);
			break;
		}
	}
	/* Unused string index stalls. */
	assert(req(0x80, 0x06, 0x0304, 0x0000, 255, &data, &len) ==
	       USBPROTO_STALL);
	/* String truncation keeps the first bytes intact. */
	assert(req(0x80, 0x06, 0x0301, 0x0000, 4, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 4);
	assert(data[0] == 18 && data[1] == 3);
	assert(data[2] == 'P' && data[3] == 0);
}

static void test_get_descriptor_qualifier_and_other_speed(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x80, 0x06, 0x0600, 0x0000, 255, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 10);
	assert(memcmp(data, usbproto_qualifier_desc, 10) == 0);

	/* Other-speed configuration: unsupported, stall. */
	assert(req(0x80, 0x06, 0x0700, 0x0000, 255, &data, &len) ==
	       USBPROTO_STALL);
	/* Descriptor types this gadget does not have: stall. */
	assert(req(0x80, 0x06, 0x9900, 0x0000, 255, &data, &len) ==
	       USBPROTO_STALL);
}

static void test_set_address_deferred(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x00, 0x05, 42, 0x0000, 0, &data, &len) ==
	       USBPROTO_OK_SET_ADDRESS);
	assert(st.address == 42);

	/* Valid range 0..127 only, and no OUT data on this request. */
	assert(req(0x00, 0x05, 128, 0x0000, 0, &data, &len) ==
	       USBPROTO_STALL);
	assert(req(0x00, 0x05, 42, 0x0001, 0, &data, &len) ==
	       USBPROTO_STALL);
	assert(req(0x00, 0x05, 42, 0x0000, 1, &data, &len) ==
	       USBPROTO_STALL);
	assert(req(0x00, 0x05, 0, 0x0000, 0, &data, &len) ==
	       USBPROTO_OK_SET_ADDRESS);
	assert(st.address == 0);
}

static void test_set_get_configuration(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(st.configured == 0);

	assert(req(0x00, 0x09, 1, 0x0000, 0, &data, &len) ==
	       USBPROTO_OK_NO_DATA);
	assert(st.configured == 1);

	assert(req(0x80, 0x08, 0x0000, 0x0000, 1, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 1 && data[0] == 1);

	/* Unconfigure. */
	assert(req(0x00, 0x09, 0, 0x0000, 0, &data, &len) ==
	       USBPROTO_OK_NO_DATA);
	assert(st.configured == 0);
	assert(req(0x80, 0x08, 0x0000, 0x0000, 1, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 1 && data[0] == 0);

	/* Only configuration values 0 and 1 exist. */
	assert(req(0x00, 0x09, 2, 0x0000, 0, &data, &len) == USBPROTO_STALL);
	assert(st.configured == 0);
	assert(req(0x00, 0x09, 1, 0x0000, 1, &data, &len) == USBPROTO_STALL);
}

static void test_get_status_device(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x80, 0x00, 0x0000, 0x0000, 2, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 2 && data[0] == 0 && data[1] == 0);

	/* Short wLength truncates. */
	assert(req(0x80, 0x00, 0x0000, 0x0000, 1, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 1 && data[0] == 0);

	/* Only the device recipient is handled. */
	assert(req(0x81, 0x00, 0x0000, 0x0000, 2, &data, &len) ==
	       USBPROTO_STALL);
	assert(req(0x82, 0x00, 0x0000, 0x0000, 2, &data, &len) ==
	       USBPROTO_STALL);
}

static void test_set_get_interface(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x01, 0x0B, 0, 0, 0, &data, &len) == USBPROTO_OK_NO_DATA);
	assert(req(0x01, 0x0B, 0, 1, 0, &data, &len) == USBPROTO_OK_NO_DATA);

	assert(req(0x81, 0x0A, 0, 0, 1, &data, &len) == USBPROTO_OK_DATA_IN);
	assert(len == 1 && data[0] == 0);
	assert(req(0x81, 0x0A, 0, 1, 1, &data, &len) == USBPROTO_OK_DATA_IN);
	assert(len == 1 && data[0] == 0);

	/* Altsetting 1 does not exist; interface 2 does not exist. */
	assert(req(0x01, 0x0B, 1, 0, 0, &data, &len) == USBPROTO_STALL);
	assert(req(0x01, 0x0B, 0, 2, 0, &data, &len) == USBPROTO_STALL);
	assert(req(0x81, 0x0A, 0, 2, 1, &data, &len) == USBPROTO_STALL);
}

static void test_line_coding_default(void)
{
	static const uint8_t want[USBPROTO_LINE_CODING_LEN] = {
		0x00, 0xC2, 0x01, 0x00, 0x00, 0x00, 0x08,
	};
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(memcmp(st.line_coding, want, 7) == 0);

	assert(req(0xA1, 0x21, 0x0000, 0x0000, 7, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 7);
	assert(memcmp(data, want, 7) == 0);

	/* GET_LINE_CODING always carries exactly 7 bytes. */
	assert(req(0xA1, 0x21, 0x0000, 0x0000, 6, &data, &len) ==
	       USBPROTO_STALL);
}

static void test_set_line_coding(void)
{
	static const uint8_t want[USBPROTO_LINE_CODING_LEN] = {
		0x00, 0x80, 0x0B, 0x00, 0x00, 0x00, 0x08,
	};
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x21, 0x20, 0x0000, 0x0000, 7, &data, &len) ==
	       USBPROTO_OK_DATA_OUT);

	/* The caller performs the 7-byte OUT stage into st->line_coding;
	 * emulate it, then read back through GET_LINE_CODING. */
	memcpy(st.line_coding, want, 7);
	assert(req(0xA1, 0x21, 0x0000, 0x0000, 7, &data, &len) ==
	       USBPROTO_OK_DATA_IN);
	assert(len == 7);
	assert(memcmp(data, want, 7) == 0);

	/* Wrong length or wrong interface stalls. */
	assert(req(0x21, 0x20, 0x0000, 0x0000, 8, &data, &len) ==
	       USBPROTO_STALL);
	assert(req(0x21, 0x20, 0x0000, 1, 7, &data, &len) ==
	       USBPROTO_STALL);
	assert(req(0x21, 0x27, 0x0000, 0, 7, &data, &len) ==
	       USBPROTO_STALL);	/* unknown CDC request */
}

static void test_set_control_line_state(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	assert(req(0x21, 0x22, 0, 0, 0, &data, &len) == USBPROTO_OK_NO_DATA);
	assert(st.dtr == 0 && st.rts == 0);

	assert(req(0x21, 0x22, 1, 0, 0, &data, &len) == USBPROTO_OK_NO_DATA);
	assert(st.dtr == 1 && st.rts == 0);

	assert(req(0x21, 0x22, 2, 0, 0, &data, &len) == USBPROTO_OK_NO_DATA);
	assert(st.dtr == 0 && st.rts == 1);

	assert(req(0x21, 0x22, 3, 0, 0, &data, &len) == USBPROTO_OK_NO_DATA);
	assert(st.dtr == 1 && st.rts == 1);

	/* No data stage on this request. */
	assert(req(0x21, 0x22, 3, 0, 1, &data, &len) == USBPROTO_STALL);
}

static void test_unknown_requests_stall(void)
{
	const uint8_t *data;
	uint16_t len;

	usbproto_init(&st);
	/* Unknown standard request. */
	assert(req(0x80, 0xFE, 0, 0, 0, &data, &len) == USBPROTO_STALL);
	/* Vendor request. */
	assert(req(0x40, 0x00, 0, 0, 0, &data, &len) == USBPROTO_STALL);
	/* SET_DESCRIPTOR is refused with a stall. */
	assert(req(0x00, 0x07, 0, 0, 0, &data, &len) == USBPROTO_STALL);
	/* Class requests must target the control interface 0. */
	assert(req(0x21, 0x22, 3, 1, 0, &data, &len) == USBPROTO_STALL);
	/* Device-to-host class direction is not supported. */
	assert(req(0x21, 0x21, 0, 0, 7, &data, &len) == USBPROTO_STALL);
}

#ifdef USB_GADGET_SS
/* ---- SuperSpeed set (USB_GADGET_SS) ---- */
static void test_superspeed(void)
{
	static const uint8_t want_dev[18] = {
		0x12, 0x01, 0x20, 0x03, 0x02, 0x00, 0x00, 0x09,
		0x09, 0x12, 0x01, 0x00, 0x00, 0x01, 0x01, 0x02, 0x03, 0x01,
	};
	static const uint8_t want_bos[22] = {
		5, 0x0f, 22, 0, 2,
		7, 0x10, 2, 0, 0, 0, 0,
		10, 0x10, 3, 0, 0x0f, 0x00, 1, 0, 0, 0,
	};
	const uint8_t *d = 0;
	uint16_t n = 0, off = 0, bulk = 0, comp = 0;
	uint8_t hs_dev[18], hs_cfg[67];

	/* HS first: bytes served now must equal bytes served after SS reset */
	usbproto_init(&st);
	assert(st.ss == 0);
	assert(req(0x80, 6, 0x0100, 0, 64, &d, &n) == USBPROTO_OK_DATA_IN);
	memcpy(hs_dev, d, 18);
	assert(req(0x80, 6, 0x0200, 0, 255, &d, &n) == USBPROTO_OK_DATA_IN && n == 67);
	memcpy(hs_cfg, d, 67);
	assert(req(0x80, 6, 0x0f00, 0, 255, &d, &n) == USBPROTO_STALL);	/* BOS at HS */
	assert(req(0x80, 6, 0x0600, 0, 10, &d, &n) == USBPROTO_OK_DATA_IN);	/* qualifier at HS */
	assert(req(0x00, 0x30, 0, 0, 6, &d, &n) == USBPROTO_STALL);	/* SET_SEL at HS */
	assert(req(0x00, 0x31, 100, 0, 0, &d, &n) == USBPROTO_STALL);
	assert(req(0x00, 3, 48, 0, 0, &d, &n) == USBPROTO_STALL);

	st.ss = 1;
	assert(req(0x80, 6, 0x0100, 0, 64, &d, &n) == USBPROTO_OK_DATA_IN && n == 18);
	assert(memcmp(d, want_dev, 18) == 0);
	assert(req(0x80, 6, 0x0100, 0, 8, &d, &n) == USBPROTO_OK_DATA_IN && n == 8);
	assert(req(0x80, 6, 0x0f00, 0, 255, &d, &n) == USBPROTO_OK_DATA_IN && n == 22);
	assert(memcmp(d, want_bos, 22) == 0);
	assert(req(0x80, 6, 0x0f00, 0, 5, &d, &n) == USBPROTO_OK_DATA_IN && n == 5);
	assert(req(0x80, 6, 0x0600, 0, 10, &d, &n) == USBPROTO_STALL);	/* qualifier */
	assert(req(0x80, 6, 0x0700, 0, 255, &d, &n) == USBPROTO_STALL);	/* other speed */
	assert(req(0x80, 6, 0x0301, 0x0409, 255, &d, &n) == USBPROTO_OK_DATA_IN &&
	       d == usbproto_string1_desc);

	/* config: wTotalLength == real length == array size, bLength walk */
	assert(req(0x80, 6, 0x0200, 0, 255, &d, &n) == USBPROTO_OK_DATA_IN);
	assert(n == USBPROTO_SS_CONFIG_DESC_LEN && n == 85);
	assert(d[2] == 85 && d[3] == 0 && d[8] == 62);
	for (off = 0; off < n; off += d[off]) {
		assert(d[off] != 0 && off + d[off] <= n);
		if (d[off + 1] == 5 && (d[off + 2] == 0x01 || d[off + 2] == 0x81)) {
			assert(d[off + 4] == 0x00 && d[off + 5] == 0x04);	/* 1024 */
			assert(d[off + d[off] + 0] == 6 && d[off + d[off] + 1] == 0x30);
			assert(d[off + d[off] + 2] == 0);	/* bMaxBurst 0 */
			bulk++;
		}
		if (d[off + 1] == 5 && d[off + 2] == 0x83) {
			/* interrupt: wMaxPacketSize 8, companion wBytesPerInterval 8 */
			assert(d[off + 4] == 8 && d[off + 5] == 0);
			assert(d[off + 7] == 6 && d[off + 8] == 0x30);
			assert(d[off + 11] == 8 && d[off + 12] == 0);
			comp++;
		}
	}
	assert(off == n && bulk == 2 && comp == 1);
	/* interface 0 bytes up to the interrupt endpoint equal the HS set */
	assert(memcmp(d + 9, hs_cfg + 9, 28) == 0);

	/* USB 3 standard requests */
	assert(req(0x00, 0x30, 0, 0, 6, &d, &n) == USBPROTO_OK_SET_SEL);
	assert(req(0x00, 0x30, 0, 0, 5, &d, &n) == USBPROTO_STALL);
	assert(req(0x00, 0x31, 100, 0, 0, &d, &n) == USBPROTO_OK_ISOCH_DELAY);
	assert(req(0x00, 0x31, 100, 0, 2, &d, &n) == USBPROTO_STALL);
	assert(req(0x00, 3, 48, 0, 0, &d, &n) == USBPROTO_STALL);	/* SET U1_ENABLE */
	assert(req(0x00, 1, 49, 0, 0, &d, &n) == USBPROTO_STALL);	/* CLEAR U2_ENABLE */
	assert(memcmp(st.line_coding, "\x00\xC2\x01\x00\x00\x00\x08", 7) == 0);

	/* back to HS: bytes exactly as before */
	st.ss = 0;
	assert(req(0x80, 6, 0x0100, 0, 64, &d, &n) == USBPROTO_OK_DATA_IN);
	assert(memcmp(d, hs_dev, 18) == 0);
	assert(req(0x80, 6, 0x0200, 0, 255, &d, &n) == USBPROTO_OK_DATA_IN && n == 67);
	assert(memcmp(d, hs_cfg, 67) == 0);
	usbproto_init(&st);
	assert(st.ss == 0);
}
#endif

int main(void)
{
	test_device_descriptor_bytes();
	test_qualifier_descriptor_bytes();
	test_config_descriptor_walk();
	test_string_descriptors();
	test_get_descriptor_device();
	test_get_descriptor_config();
	test_get_descriptor_strings();
	test_get_descriptor_qualifier_and_other_speed();
	test_set_address_deferred();
	test_set_get_configuration();
	test_get_status_device();
	test_set_get_interface();
	test_line_coding_default();
	test_set_line_coding();
	test_set_control_line_state();
	test_unknown_requests_stall();
#ifdef USB_GADGET_SS
	test_superspeed();
#endif
	printf("PASS usbproto host test: descriptor bytes, config walk "
	       "(2 interfaces, 3 endpoints), string UTF-16, ep0 requests, "
	       "truncation, deferred address, stalls\n");
	return 0;
}
