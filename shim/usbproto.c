/*
 * usbproto.c - pure USB protocol logic for the stage-2 CDC-ACM log
 * gadget; see usbproto.h for the contract. Descriptors are emitted as
 * const byte arrays laid out exactly as a host walks them (bLength
 * chain), so the host test can diff every byte and step the config
 * descriptor end to end. No MMIO, no DMA, no libc.
 */
#include <stdint.h>
#include "usbproto.h"

/* bmRequestType fields: bit7 direction (1 = device-to-host),
 * bits 6:5 type (0 standard, 1 class), bits 4:0 recipient
 * (0 device, 1 interface, 2 endpoint). Requests are accepted only
 * with these exact bmRequestType values; anything else stalls. */
#define RT_IN		0x80u
#define RT_STANDARD	0x00u
#define RT_CLASS	0x20u
#define RT_RCPT_DEVICE	0x00u
#define RT_RCPT_INTERFACE 0x01u

/* Standard bRequest codes handled here. */
#define STD_CLEAR_FEATURE	0x01u
#define STD_SET_FEATURE		0x03u
#define STD_GET_STATUS		0x00u
#define STD_SET_ADDRESS		0x05u
#define STD_GET_DESCRIPTOR	0x06u
#define STD_GET_CONFIGURATION	0x08u
#define STD_SET_CONFIGURATION	0x09u
#define STD_GET_INTERFACE	0x0Au
#define STD_SET_INTERFACE	0x0Bu
#define STD_SET_SEL		0x30u	/* 48: USB 3 ch9.h, as patch 0173 */
#define STD_SET_ISOCH_DELAY	0x31u	/* 49 */
#define DT_BOS			0x0Fu

/* ---- descriptors ---- */

/* Device: USB 2.00, class 0x02 (communications device so hosts load
 * the cdc_acm driver pair), 64-byte ep0, VID 0x1209 PID 0x0001, strings 1/2/3, one configuration.
 * bcdDevice 0x0100 is this shim's version 1.0 (chosen here). */
const uint8_t usbproto_device_desc[USBPROTO_DEVICE_DESC_LEN] = {
	0x12,			/* bLength */
	0x01,			/* bDescriptorType: DEVICE */
	0x00, 0x02,		/* bcdUSB 2.00 */
	0x02,			/* bDeviceClass: CDC */
	0x00,			/* bDeviceSubClass */
	0x00,			/* bDeviceProtocol */
	0x40,			/* bMaxPacketSize0: 64 */
	0x09, 0x12,		/* idVendor 0x1209 */
	0x01, 0x00,		/* idProduct 0x0001 */
	0x00, 0x01,		/* bcdDevice 1.00 */
	0x01,			/* iManufacturer: string 1 */
	0x02,			/* iProduct: string 2 */
	0x03,			/* iSerialNumber: string 3 */
	0x01,			/* bNumConfigurations */
};

/* Device qualifier: same class facts at the other speed's ep0, one
 * configuration, reserved byte 0. The other-speed configuration
 * itself is not provided (high speed only, STALL). */
const uint8_t usbproto_qualifier_desc[USBPROTO_QUALIFIER_DESC_LEN] = {
	0x0A,			/* bLength */
	0x06,			/* bDescriptorType: DEVICE_QUALIFIER */
	0x00, 0x02,		/* bcdUSB 2.00 */
	0x02,			/* bDeviceClass: CDC */
	0x00,			/* bDeviceSubClass */
	0x00,			/* bDeviceProtocol */
	0x40,			/* bMaxPacketSize0: 64 */
	0x01,			/* bNumConfigurations */
	0x00,			/* bReserved */
};

/* CDC-ACM configuration: interface 0 = communications (ACM subclass
 * 2, protocol 1, CDC functional descriptors, interrupt IN ep 0x83),
 * interface 1 = data (bulk OUT 0x01 / bulk IN 0x81, 512-byte HS
 * packets). wTotalLength 67 must match USBPROTO_CONFIG_DESC_LEN. */
const uint8_t usbproto_config_desc[USBPROTO_CONFIG_DESC_LEN] = {
	/* Configuration descriptor */
	0x09,			/* bLength */
	0x02,			/* bDescriptorType: CONFIGURATION */
	0x43, 0x00,		/* wTotalLength 67 */
	0x02,			/* bNumInterfaces */
	0x01,			/* bConfigurationValue */
	0x00,			/* iConfiguration */
	0x80,			/* bmAttributes: bus powered */
	0x32,			/* bMaxPower: 100 mA */
	/* Interface 0: communications */
	0x09,			/* bLength */
	0x04,			/* bDescriptorType: INTERFACE */
	0x00,			/* bInterfaceNumber */
	0x00,			/* bAlternateSetting */
	0x01,			/* bNumEndpoints */
	0x02,			/* bInterfaceClass: CDC */
	0x02,			/* bInterfaceSubClass: ACM */
	0x01,			/* bInterfaceProtocol: AT (v.25ter) */
	0x00,			/* iInterface */
	/* CDC header functional descriptor */
	0x05,			/* bLength */
	0x24,			/* bDescriptorType: CS_INTERFACE */
	0x00,			/* bDescriptorSubtype: HEADER */
	0x10, 0x01,		/* bcdCDC 1.10 */
	/* CDC call management functional descriptor */
	0x05,			/* bLength */
	0x24,			/* bDescriptorType: CS_INTERFACE */
	0x01,			/* bDescriptorSubtype: CALL_MANAGEMENT */
	0x00,			/* bmCapabilities: none */
	0x01,			/* bDataInterface: 1 */
	/* CDC ACM functional descriptor */
	0x04,			/* bLength */
	0x24,			/* bDescriptorType: CS_INTERFACE */
	0x02,			/* bDescriptorSubtype: ACM */
	0x02,			/* bmCapabilities: SET_LINE_CODING etc. */
	/* CDC union functional descriptor */
	0x05,			/* bLength */
	0x24,			/* bDescriptorType: CS_INTERFACE */
	0x06,			/* bDescriptorSubtype: UNION */
	0x00,			/* bMasterInterface: 0 */
	0x01,			/* bSlaveInterface: 1 */
	/* Interrupt IN endpoint 0x83 */
	0x07,			/* bLength */
	0x05,			/* bDescriptorType: ENDPOINT */
	0x83,			/* bEndpointAddress: IN 3 */
	0x03,			/* bmAttributes: interrupt */
	0x08, 0x00,		/* wMaxPacketSize 8 */
	0x10,			/* bInterval 16 */
	/* Interface 1: data */
	0x09,			/* bLength */
	0x04,			/* bDescriptorType: INTERFACE */
	0x01,			/* bInterfaceNumber */
	0x00,			/* bAlternateSetting */
	0x02,			/* bNumEndpoints */
	0x0A,			/* bInterfaceClass: CDC data */
	0x00,			/* bInterfaceSubClass */
	0x00,			/* bInterfaceProtocol */
	0x00,			/* iInterface */
	/* Bulk OUT endpoint 0x01 */
	0x07,			/* bLength */
	0x05,			/* bDescriptorType: ENDPOINT */
	0x01,			/* bEndpointAddress: OUT 1 */
	0x02,			/* bmAttributes: bulk */
	0x00, 0x02,		/* wMaxPacketSize 512 (high speed) */
	0x00,			/* bInterval: N/A for bulk */
	/* Bulk IN endpoint 0x81 */
	0x07,			/* bLength */
	0x05,			/* bDescriptorType: ENDPOINT */
	0x81,			/* bEndpointAddress: IN 1 */
	0x02,			/* bmAttributes: bulk */
	0x00, 0x02,		/* wMaxPacketSize 512 (high speed) */
	0x00,			/* bInterval: N/A for bulk */
};

/* String 0: LANGID 0x0409 (English, United States). */
const uint8_t usbproto_string0_desc[USBPROTO_STRING0_DESC_LEN] = {
	0x04, 0x03, 0x09, 0x04,
};

/* String 1: "PixelIOS". */
const uint8_t usbproto_string1_desc[USBPROTO_STRING1_DESC_LEN] = {
	0x12, 0x03,
	'P', 0x00, 'i', 0x00, 'x', 0x00, 'e', 0x00,
	'l', 0x00, 'I', 0x00, 'O', 0x00, 'S', 0x00,
};

/* String 2: "Pixel 6a shim log". */
const uint8_t usbproto_string2_desc[USBPROTO_STRING2_DESC_LEN] = {
	0x24, 0x03,
	'P', 0x00, 'i', 0x00, 'x', 0x00, 'e', 0x00, 'l', 0x00,
	' ', 0x00, '6', 0x00, 'a', 0x00, ' ', 0x00,
	's', 0x00, 'h', 0x00, 'i', 0x00, 'm', 0x00, ' ', 0x00,
	'l', 0x00, 'o', 0x00, 'g', 0x00,
};

/* String 3: "0001". */
const uint8_t usbproto_string3_desc[USBPROTO_STRING3_DESC_LEN] = {
	0x0A, 0x03,
	'0', 0x00, '0', 0x00, '0', 0x00, '1', 0x00,
};

_Static_assert(USBPROTO_CONFIG_DESC_LEN == 67u, "wTotalLength math");
_Static_assert((uint8_t)(USBPROTO_DEVICE_DESC_LEN - 2) ==
	       8u + 2u + 2u + 3u + 1u, "device descriptor body layout");
_Static_assert((uint8_t)(USBPROTO_STRING1_DESC_LEN - 2) / 2u ==
	       8u, "string 1 length");
_Static_assert((uint8_t)(USBPROTO_STRING2_DESC_LEN - 2) / 2u ==
	       17u, "string 2 length");
_Static_assert((uint8_t)(USBPROTO_STRING3_DESC_LEN - 2) / 2u ==
	       4u, "string 3 length");

#ifdef USB_GADGET_SS
/* ---- SuperSpeed descriptor set (build flag USB_GADGET_SS) ----
 * Same layout and citations as xnu/patches/0173-sweet-usb3-ss.patch
 * (ud_ss_*_desc, its comment block: Linux v6.12 ch9.h:253-265, :693-702,
 * :850-858, :899-916, :922-939; composite.c:540-547, :771-802, :1833-1836;
 * f_ecm.c:273-338). bcdUSB 0x0320 + bMaxPacketSize0 9 (2^9 = 512); bulk
 * 1024 with bMaxBurst 0; bMaxPower in 8 mA units, 500 mA -> 62;
 * interrupt companion wBytesPerInterval = the interrupt wMaxPacketSize (8).
 * Deviations from composite.c, on purpose (as 0173): U1/U2 exit latency 0
 * and USB 2.0 extension bmAttributes 0 (no LPM claim).
 * UNCITED: the interrupt ep bInterval stays 16 (the HS value). */
const uint8_t usbproto_ss_device_desc[USBPROTO_DEVICE_DESC_LEN] = {
	0x12, 0x01, 0x20, 0x03,	/* bLength, DEVICE, bcdUSB 3.20 */
	0x02, 0x00, 0x00,	/* CDC, subclass, protocol */
	0x09,			/* bMaxPacketSize0: 2^9 = 512 */
	0x09, 0x12, 0x01, 0x00,	/* VID 0x1209 PID 0x0001 */
	0x00, 0x01,		/* bcdDevice 1.00 */
	0x01, 0x02, 0x03,	/* strings */
	0x01,			/* bNumConfigurations */
};

const uint8_t usbproto_ss_config_desc[USBPROTO_SS_CONFIG_DESC_LEN] = {
	0x09, 0x02, 0x55, 0x00, 0x02, 0x01, 0x00, 0x80,
	0x3E,			/* bMaxPower 62 x 8 mA = 496 mA */
	/* Interface 0: communications, identical to the HS set */
	0x09, 0x04, 0x00, 0x00, 0x01, 0x02, 0x02, 0x01, 0x00,
	0x05, 0x24, 0x00, 0x10, 0x01,
	0x05, 0x24, 0x01, 0x00, 0x01,
	0x04, 0x24, 0x02, 0x02,
	0x05, 0x24, 0x06, 0x00, 0x01,
	/* Interrupt IN 0x83, 8 bytes, bInterval 16 + companion */
	0x07, 0x05, 0x83, 0x03, 0x08, 0x00, 0x10,
	0x06, 0x30, 0x00, 0x00, 0x08, 0x00,	/* burst 0, wBytesPerInterval 8 */
	/* Interface 1: data */
	0x09, 0x04, 0x01, 0x00, 0x02, 0x0A, 0x00, 0x00, 0x00,
	0x07, 0x05, 0x01, 0x02, 0x00, 0x04, 0x00,	/* bulk OUT 0x01, 1024 */
	0x06, 0x30, 0x00, 0x00, 0x00, 0x00,		/* burst 0, no streams */
	0x07, 0x05, 0x81, 0x02, 0x00, 0x04, 0x00,	/* bulk IN 0x81, 1024 */
	0x06, 0x30, 0x00, 0x00, 0x00, 0x00,
};

const uint8_t usbproto_ss_bos_desc[USBPROTO_SS_BOS_DESC_LEN] = {
	0x05, 0x0F, 0x16, 0x00, 0x02,			/* BOS, 22 bytes, 2 caps */
	0x07, 0x10, 0x02, 0x00, 0x00, 0x00, 0x00,	/* USB 2.0 ext, no LPM */
	0x0A, 0x10, 0x03, 0x00, 0x0F, 0x00, 0x01,	/* SS: speeds 0x000f, func 1 */
	0x00, 0x00, 0x00,				/* U1/U2 exit latency 0 */
};

_Static_assert(USBPROTO_SS_CONFIG_DESC_LEN == 9u + 9u + 19u + 7u + 6u + 9u + 13u + 13u,
	       "SS wTotalLength math");
#endif

/* ---- ep0 request handling ---- */

/* One-byte IN stages need storage the *data pointer can outlive the
 * call; single-core polled ep0 has no concurrent writers. */
static uint8_t in_byte;
static const uint8_t two_zeros[2] = { 0x00, 0x00 };

void usbproto_init(struct usbproto *st)
{
	st->address = 0;
	st->configured = 0;
	st->alt[0] = 0;
	st->alt[1] = 0;
	st->line_coding[0] = 0x00;	/* dwDTERate 115200 = 0x0001C200 LE */
	st->line_coding[1] = 0xC2;
	st->line_coding[2] = 0x01;
	st->line_coding[3] = 0x00;
	st->line_coding[4] = 0x00;	/* bCharFormat: 1 stop bit */
	st->line_coding[5] = 0x00;	/* bParityType: none */
	st->line_coding[6] = 0x08;	/* bDataBits: 8 */
	st->dtr = 0;
	st->rts = 0;
}

static uint16_t rd16le(const uint8_t *p)
{
	return (uint16_t)(p[0] | (uint16_t)p[1] << 8);
}

static uint16_t min_len(uint16_t desc_len, uint16_t wLength)
{
	return desc_len < wLength ? desc_len : wLength;
}

static int get_descriptor(uint8_t type, uint8_t index, uint16_t wLength,
			  const uint8_t **data, uint16_t *len, uint8_t ss)
{
#ifndef USB_GADGET_SS
	(void)ss;	/* HS-only build: the HS set is always served */
#else
	if (ss) {
		/* SuperSpeed (only after a SS ConnectDone). Qualifier and
		 * other-speed: STALL (Linux composite.c, as 0173); BOS and
		 * the SS device/config set are served; strings unchanged. */
		switch (type) {
		case USBPROTO_DT_DEVICE:
			if (index != 0)
				return USBPROTO_STALL;
			*data = usbproto_ss_device_desc;
			*len = min_len(USBPROTO_DEVICE_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		case USBPROTO_DT_CONFIGURATION:
			if (index != 0)
				return USBPROTO_STALL;
			*data = usbproto_ss_config_desc;
			*len = min_len(USBPROTO_SS_CONFIG_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		case DT_BOS:
			if (index != 0)
				return USBPROTO_STALL;
			*data = usbproto_ss_bos_desc;
			*len = min_len(USBPROTO_SS_BOS_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		case USBPROTO_DT_DEVICE_QUALIFIER:
		case USBPROTO_DT_OTHER_SPEED_CONFIG:
			return USBPROTO_STALL;
		default:
			break;	/* strings and the rest: as at HS */
		}
	}
#endif
	switch (type) {
	case USBPROTO_DT_DEVICE:
		*data = usbproto_device_desc;
		*len = min_len(USBPROTO_DEVICE_DESC_LEN, wLength);
		return USBPROTO_OK_DATA_IN;
	case USBPROTO_DT_CONFIGURATION:
		if (index != 0)
			return USBPROTO_STALL;
		*data = usbproto_config_desc;
		*len = min_len(USBPROTO_CONFIG_DESC_LEN, wLength);
		return USBPROTO_OK_DATA_IN;
	case USBPROTO_DT_DEVICE_QUALIFIER:
		*data = usbproto_qualifier_desc;
		*len = min_len(USBPROTO_QUALIFIER_DESC_LEN, wLength);
		return USBPROTO_OK_DATA_IN;
	case USBPROTO_DT_OTHER_SPEED_CONFIG:
		/* High speed only: no other-speed configuration. */
		return USBPROTO_STALL;
	case USBPROTO_DT_STRING:
		switch (index) {
		case 0:
			*data = usbproto_string0_desc;
			*len = min_len(USBPROTO_STRING0_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		case 1:
			*data = usbproto_string1_desc;
			*len = min_len(USBPROTO_STRING1_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		case 2:
			*data = usbproto_string2_desc;
			*len = min_len(USBPROTO_STRING2_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		case 3:
			*data = usbproto_string3_desc;
			*len = min_len(USBPROTO_STRING3_DESC_LEN, wLength);
			return USBPROTO_OK_DATA_IN;
		default:
			return USBPROTO_STALL;
		}
	default:
		return USBPROTO_STALL;
	}
}

int usbproto_setup(const uint8_t setup[8], struct usbproto *st,
		   const uint8_t **data, uint16_t *len)
{
	const uint8_t bmRequestType = setup[0];
	const uint8_t bRequest = setup[1];
	const uint16_t wValue = rd16le(setup + 2);
	const uint16_t wIndex = rd16le(setup + 4);
	const uint16_t wLength = rd16le(setup + 6);
	const uint8_t value_lo = (uint8_t)(wValue & 0xffu);

	switch (bmRequestType) {
	case RT_IN | RT_STANDARD | RT_RCPT_DEVICE:	/* 0x80 */
		if (bRequest == STD_GET_DESCRIPTOR)
			return get_descriptor((uint8_t)(wValue >> 8), value_lo,
					      wLength, data, len, st->ss);
		if (bRequest == STD_GET_STATUS) {
			*data = two_zeros;
			*len = min_len(2, wLength);
			return USBPROTO_OK_DATA_IN;
		}
		if (bRequest == STD_GET_CONFIGURATION) {
			in_byte = st->configured;
			*data = &in_byte;
			*len = min_len(1, wLength);
			return USBPROTO_OK_DATA_IN;
		}
		return USBPROTO_STALL;

	case RT_STANDARD | RT_RCPT_DEVICE:	/* 0x00, OUT */
#ifdef USB_GADGET_SS
		if (st->ss) {
			/* USB 3 standard requests (as 0173): SET_SEL takes 6
			 * data bytes that are discarded; SET_ISOCH_DELAY is
			 * acked; U1/U2_ENABLE (feature 48/49) stall. */
			if (bRequest == STD_SET_SEL && wValue == 0u &&
			    wIndex == 0u && wLength == 6u)
				return USBPROTO_OK_SET_SEL;
			if (bRequest == STD_SET_ISOCH_DELAY && wIndex == 0u &&
			    wLength == 0u)
				return USBPROTO_OK_ISOCH_DELAY;
			if ((bRequest == STD_SET_FEATURE ||
			     bRequest == STD_CLEAR_FEATURE) &&
			    (wValue == 48u || wValue == 49u))
				return USBPROTO_STALL;
		}
#endif
		if (bRequest == STD_SET_ADDRESS) {
			if (wValue > 127u || wIndex != 0u || wLength != 0u)
				return USBPROTO_STALL;
			st->address = value_lo;
			return USBPROTO_OK_SET_ADDRESS;
		}
		if (bRequest == STD_SET_CONFIGURATION) {
			if (wValue > 1u || wIndex != 0u || wLength != 0u)
				return USBPROTO_STALL;
			st->configured = value_lo;
			return USBPROTO_OK_NO_DATA;
		}
		return USBPROTO_STALL;

	case RT_IN | RT_STANDARD | RT_RCPT_INTERFACE:	/* 0x81 */
		if (bRequest == STD_GET_INTERFACE) {
			if (wValue != 0u || wIndex > 1u)
				return USBPROTO_STALL;
			in_byte = st->alt[wIndex];
			*data = &in_byte;
			*len = min_len(1, wLength);
			return USBPROTO_OK_DATA_IN;
		}
		return USBPROTO_STALL;

	case RT_STANDARD | RT_RCPT_INTERFACE:	/* 0x01, OUT */
		if (bRequest == STD_SET_INTERFACE) {
			/* One altsetting (0) on both interfaces. */
			if (wValue != 0u || wIndex > 1u || wLength != 0u)
				return USBPROTO_STALL;
			st->alt[wIndex] = 0;
			return USBPROTO_OK_NO_DATA;
		}
		return USBPROTO_STALL;

	case RT_IN | RT_CLASS | RT_RCPT_INTERFACE:	/* 0xA1, device-to-host */
		if (wIndex != 0u || bRequest != USBPROTO_CDC_GET_LINE_CODING ||
		    wLength != USBPROTO_LINE_CODING_LEN)
			return USBPROTO_STALL;
		*data = st->line_coding;
		*len = USBPROTO_LINE_CODING_LEN;
		return USBPROTO_OK_DATA_IN;

	case RT_CLASS | RT_RCPT_INTERFACE:	/* 0x21, host-to-device */
		if (wIndex != 0u)
			return USBPROTO_STALL;	/* control interface is 0 */
		switch (bRequest) {
		case USBPROTO_CDC_SET_LINE_CODING:
			if (wLength != USBPROTO_LINE_CODING_LEN)
				return USBPROTO_STALL;
			return USBPROTO_OK_DATA_OUT;
		case USBPROTO_CDC_SET_CONTROL_LINE_STATE:
			if (wLength != 0u)
				return USBPROTO_STALL;
			st->dtr = wValue & 1u;
			st->rts = (wValue >> 1) & 1u;
			return USBPROTO_OK_NO_DATA;
		default:
			return USBPROTO_STALL;
		}

	default:
		return USBPROTO_STALL;
	}
}
