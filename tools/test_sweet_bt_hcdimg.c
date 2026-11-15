/* Test-only image slot for tools/test_sweet_bt_patchram.c: the backend declares these three as `extern const`; here they are plain objects so every test case can load its own image
 * (separate translation unit, no LTO, so the qualifier difference cannot be optimised across). Never part of a kernel build. */
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
#include <string.h>
uint8_t sweet_bt_hcd_data[700000];
size_t sweet_bt_hcd_len;
char sweet_bt_hcd_sha256[96] = "none";
void hcdimg_set(const uint8_t *p, size_t n, const char *sha)
{
	memcpy(sweet_bt_hcd_data, p, n);
	sweet_bt_hcd_len = n;
	snprintf(sweet_bt_hcd_sha256, sizeof(sweet_bt_hcd_sha256), "%s", sha);
}
