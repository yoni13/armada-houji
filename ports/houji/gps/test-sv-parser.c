// SPDX-License-Identifier: GPL-2.0-or-later
/* Host test for legacy/expanded packed QMI reports and malformed lengths. */
#define main loc_probe_main
#include "houji-loc-test.c"
#undef main
#include <assert.h>

static void put32(guint8 *p, guint32 value)
{
	value = GUINT32_TO_LE(value);
	memcpy(p, &value, 4);
}

static void report(guint8 tlv, const guint8 *data, guint16 size)
{
	QmiMessage *m = qmi_message_new(QMI_SERVICE_LOC, 1, 1, 0x25);
	assert(qmi_message_add_raw_tlv(m, tlv, data, size, NULL));
	satellite_message(m);
	qmi_message_unref(m);
}

int main(void)
{
	guint8 data[1 + 2 * 29] = {2};
	for (guint i = 0; i < 2; i++) {
		guint8 *e = data + 1 + 29 * i;
		put32(e, 0x88);              /* Valid status and signal strength. */
		put32(e + 11, i ? 2 : 3);    /* Tracking, then searching. */
		put32(e + 24, 0x41ac0000);   /* 21.5 dB-Hz, packed float. */
	}
	report(0x11, data, sizeof(data));
	assert(sv_reports == 1 && max_satellites == 2);
	report(0x11, data, sizeof(data) - 1);
	assert(sv_reports == 1);          /* Reject truncated records. */
	data[0] = 255;
	report(0x11, data, sizeof(data));
	assert(sv_reports == 1);          /* Reject excessive count. */
	max_satellites = 0;
	data[0] = 1;
	put32(data + 1, 8);              /* SNR value exists but is not valid. */
	report(0x11, data, 30);
	assert(sv_reports == 2 && max_satellites == 0);
	put32(data + 1, 0x88);
	put32(data + 25, 0x7fc00000);     /* NaN is not a measured signal. */
	report(0x11, data, 30);
	assert(sv_reports == 3 && max_satellites == 0);
	put32(data + 25, 0x41ac0000);
	report(0x10, data, 29);           /* Same first 28 bytes in old format. */
	assert(sv_reports == 4 && max_satellites == 1);
	g_print("satellite parser tests passed\n");
	return 0;
}
