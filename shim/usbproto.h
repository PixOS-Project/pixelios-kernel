#pragma once
#include <stdint.h>

/* SuperSpeed (port of xnu patch 0173) is the DEFAULT; build.sh --usb2
 * defines USB_GADGET_HS_ONLY, which keeps the HS-only gadget byte for
 * byte. USB_GADGET_SS is the internal switch every SS block tests. */
#if !defined(USB_GADGET_HS_ONLY) && !defined(USB_GADGET_SS)
#define USB_GADGET_SS
#endif

/*
 * usbproto.h - pure USB protocol logic for the stage-2 CDC-ACM log
 * gadget: the descriptor set as
 * const byte arrays and the ep0 control-transfer decision for one
 * SETUP packet.
 *
 * STRICTLY pure: no MMIO, no DMA, no malloc, no libc. The caller
 * (shim/usbgadget.c, a later step) owns every register and every
 * buffer movement; usbproto_setup() only classifies a SETUP packet
 * and hands back a pointer into const descriptor data (IN stage), a
 * receive directive (OUT stage into st->line_coding), a deferred
 * address, or a stall.
 *
 * Speed note: the gadget runs HIGH SPEED only, so bulk endpoints are 512 bytes and GET_DESCRIPTOR
 * for the other-speed configuration is answered STALL.
 *
 * Host-tested in shim/usbproto_host_test.c (built by the line next to
 * usbprobe_host_test in shim/build.sh).
 */

/* Descriptor types (wValue high byte of GET_DESCRIPTOR). */
#define USBPROTO_DT_DEVICE             0x01u
#define USBPROTO_DT_CONFIGURATION      0x02u
#define USBPROTO_DT_STRING             0x03u
#define USBPROTO_DT_DEVICE_QUALIFIER   0x06u
#define USBPROTO_DT_OTHER_SPEED_CONFIG 0x07u

/* CDC-ACM class requests (bRequest, sent with bmRequestType 0x21). */
#define USBPROTO_CDC_SET_LINE_CODING        0x20u
#define USBPROTO_CDC_GET_LINE_CODING        0x21u
#define USBPROTO_CDC_SET_CONTROL_LINE_STATE 0x22u

/* Descriptor lengths in bytes; the arrays below are exactly this
 * long, so host tests can sizeof them and compare with wTotalLength. */
#define USBPROTO_DEVICE_DESC_LEN    18u
#define USBPROTO_QUALIFIER_DESC_LEN 10u
#define USBPROTO_STRING0_DESC_LEN    4u	/* LANGID 0x0409 */
#define USBPROTO_STRING1_DESC_LEN   18u	/* "PixelIOS" */
#define USBPROTO_STRING2_DESC_LEN   36u	/* "Pixel 6a shim log" */
#define USBPROTO_STRING3_DESC_LEN   10u	/* "0001" */
/* 9 config + interface 0 (9 + CDC header 5 + call mgmt 5 + ACM 4 +
 * union 5 + interrupt IN ep 7) + interface 1 (9 + bulk OUT 7 + bulk
 * IN 7). Must equal wTotalLength in the array. */
#define USBPROTO_CONFIG_DESC_LEN    67u

extern const uint8_t usbproto_device_desc[USBPROTO_DEVICE_DESC_LEN];
extern const uint8_t usbproto_qualifier_desc[USBPROTO_QUALIFIER_DESC_LEN];
extern const uint8_t usbproto_config_desc[USBPROTO_CONFIG_DESC_LEN];
#ifdef USB_GADGET_SS
/* SuperSpeed set (build flag USB_GADGET_SS), served only while st->ss.
 * Config: 9 + if0 (9 + 5 + 5 + 4 + 5 + int ep 7 + companion 6) + if1
 * (9 + 2 x (bulk ep 7 + companion 6)) = 85. BOS: 5 + 7 + 10 = 22. */
#define USBPROTO_SS_CONFIG_DESC_LEN 85u
#define USBPROTO_SS_BOS_DESC_LEN    22u
extern const uint8_t usbproto_ss_device_desc[USBPROTO_DEVICE_DESC_LEN];
extern const uint8_t usbproto_ss_config_desc[USBPROTO_SS_CONFIG_DESC_LEN];
extern const uint8_t usbproto_ss_bos_desc[USBPROTO_SS_BOS_DESC_LEN];
#endif
extern const uint8_t usbproto_string0_desc[USBPROTO_STRING0_DESC_LEN];
extern const uint8_t usbproto_string1_desc[USBPROTO_STRING1_DESC_LEN];
extern const uint8_t usbproto_string2_desc[USBPROTO_STRING2_DESC_LEN];
extern const uint8_t usbproto_string3_desc[USBPROTO_STRING3_DESC_LEN];

/* Data-stage length of SET_LINE_CODING / GET_LINE_CODING. */
#define USBPROTO_LINE_CODING_LEN 7u

/* Result of usbproto_setup() for one SETUP packet. */
#define USBPROTO_STALL            (-1)	/* STALL ep0: unsupported request */
#define USBPROTO_OK_NO_DATA        0	/* zero-length status stage */
#define USBPROTO_OK_DATA_IN        1	/* send *data[0..*len) as IN stage */
#define USBPROTO_OK_DATA_OUT       2	/* receive wLength bytes into
					 * st->line_coding (SET_LINE_CODING) */
#define USBPROTO_OK_SET_ADDRESS    3	/* zero-length status stage, THEN
						 * apply st->address to DCFG */
#ifdef USB_GADGET_SS
#define USBPROTO_OK_SET_SEL        4	/* SuperSpeed SET_SEL: receive the 6
					 * data bytes and DISCARD them (not
					 * line_coding), then status */
#define USBPROTO_OK_ISOCH_DELAY    5	/* SuperSpeed SET_ISOCH_DELAY: status
					 * only; wValue = delay in ns */
#endif

/* ep0 device state the caller keeps across transfers. */
struct usbproto {
	uint8_t address;		/* SET_ADDRESS value, apply after status */
	uint8_t configured;		/* SET_CONFIGURATION value: 0 or 1 */
	uint8_t alt[2];			/* altsetting of interface 0 / 1 */
	uint8_t line_coding[USBPROTO_LINE_CODING_LEN];
	uint8_t dtr, rts;		/* SET_CONTROL_LINE_STATE wValue bits */
	uint8_t ss;			/* set by the gadget at a SuperSpeed
					 * ConnectDone (DSTS.CONNECTSPD >= 4);
					 * only read under USB_GADGET_SS, else
					 * the HS set is always served */
};

/* Post-reset state: address 0, unconfigured, altsetting 0, default
 * line coding 115200 8N1 (00 C2 01 00 00 00 08), DTR/RTS clear. */
void usbproto_init(struct usbproto *st);

/* Classify one ep0 SETUP packet (the raw 8 bytes, little-endian
 * fields). USBPROTO_OK_DATA_IN sets *data and *len to descriptor or
 * scratch data already truncated to wLength. USBPROTO_OK_DATA_OUT
 * means receive wLength (= 7, otherwise STALL was returned) bytes
 * into st->line_coding. USBPROTO_OK_SET_ADDRESS means: complete the
 * zero-length status stage, then write st->address to DCFG - the USB
 * 2.0 spec (section 9.4.5) forbids changing the address before the
 * status stage completes. Unknown or malformed requests return
 * USBPROTO_STALL. Nothing here touches hardware. */
int usbproto_setup(const uint8_t setup[8], struct usbproto *st,
		   const uint8_t **data, uint16_t *len);
