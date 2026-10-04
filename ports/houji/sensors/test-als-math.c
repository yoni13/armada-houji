/* Lux formula, SSC report decoding, OEM messages and the publish filter. */
#include <assert.h>
#include <math.h>
#include <stdio.h>
#include <string.h>
#include "als/als-math.h"

static size_t
put_float (uint8_t *p, uint8_t key, float value)
{
	p[0] = key;
	memcpy (p + 1, &value, 4);
	return 5;
}

static void
test_coef (void)
{
	struct als_coef c;
	const char *json = "{\"Cct\": {\"channel_coef\": {\"owner\": \"sns_tcs3720_fb\",\n"
			   "  \"coef_data\": \"0.1,0.02,-0.01,0.04,-0.05,0.03,-0.02,0.05,-0.06\"}}}";

	assert (als_parse_coef (json, &c));
	assert (c.ir_threshold == 0.1 && c.lo[0] == 0.02 && c.lo[3] == -0.05);
	assert (c.hi[0] == 0.03 && c.hi[3] == -0.06);
	assert (!als_parse_coef ("{\"coef_data\": \"1,2\"}", &c));
	assert (!als_parse_coef ("{\"channel_coef\": {\"coef_data\": \"0.1,1,2\"}}", &c));
	assert (!als_parse_coef (NULL, &c));
}

static void
test_decode (void)
{
	uint8_t msg[64];
	float v[4];
	size_t n = 0;

	n += put_float (msg + n, 0x0d, 1.5f);
	msg[n++] = 0x10; /* accuracy, varint */
	msg[n++] = 3;
	n += put_float (msg + n, 0x0d, -2.0f);
	assert (als_decode_floats (msg, n, v, 4) == 2 && v[0] == 1.5f && v[1] == -2.0f);

	n = 0;
	msg[n++] = 0x0a; /* packed */
	msg[n++] = 12;
	for (int i = 0; i < 3; i++) {
		float f = 10.0f * (i + 1);
		memcpy (msg + n, &f, 4);
		n += 4;
	}
	assert (als_decode_floats (msg, n, v, 2) == 2 && v[0] == 10.0f && v[1] == 20.0f);
	assert (als_decode_floats (msg, n - 1, v, 4) == 0);
}

static void
test_lux (void)
{
	struct als_coef c = { 0.1, { 0.02, -0.01, 0.04, -0.05 }, { 0.01, 0.0, 0.0, 0.0 } };
	float low_ir[8] = { 1000, 500, 300, 200, 1, 1, 1, 1 };
	float high_ir[4] = { 1000, 800, 600, 400 };
	float scaled[8] = { 1000, 500, 300, 200, 2, 2, 2, 2 };
	float dark[4] = { 0, 0, 0, 0 };
	float bright[4] = { 16775001, 1, 1, 1 };

	/* (500 + 300 + 200 - 1000) / 2000 = 0 <= 0.1: low IR coefficients. */
	assert (fabsf (als_lux (&c, low_ir, 8) - (20 - 5 + 12 - 10 - 0.3f)) < 1e-3);
	/* ratio 0.4: high IR coefficients. */
	assert (fabsf (als_lux (&c, high_ir, 4) - (10 - 0.3f)) < 1e-3);
	assert (fabsf (als_lux (&c, scaled, 8) - (2 * 17 - 0.3f)) < 1e-3);
	assert (als_lux (&c, dark, 4) == 0.0f);
	assert (als_lux (&c, bright, 4) == ALS_SATURATED_LUX);
	assert (als_lux (&c, low_ir, 3) < 0.0f);
	c.lo[0] = -1.0;
	assert (als_lux (&c, low_ir, 4) == 0.0f);
}

static void
test_filter (void)
{
	struct als_filter f = { 0 };
	float out = -1;
	int64_t t = 1000000;

	assert (als_filter_push (&f, 100.0f, t, &out) && out == 100.0f);
	assert (!als_filter_push (&f, 300.0f, t + 50000, &out));
	/* Window mean 200, median of (200, 100) = 150: a 50 % change. */
	assert (als_filter_push (&f, 100.0f, t + 210000, &out) && fabsf (out - (200 + 100) / 2.0f) < 1e-3);
	/* Small changes stay quiet. */
	assert (!als_filter_push (&f, 150.0f, t + 420000, &out));
	assert (!als_filter_push (&f, 155.0f, t + 630000, &out));
	/* A one-window spike is removed by the median. */
	assert (!als_filter_push (&f, 1000.0f, t + 840000, &out));
	/* Saturation is published at once. */
	assert (als_filter_push (&f, ALS_SATURATED_LUX, t + 900000, &out) && out == ALS_SATURATED_LUX);
	/* A gap means the stream restarted: publish the first sample. */
	assert (als_filter_push (&f, 155.0f, t + 3000000, &out) && out == 155.0f);
	assert (!als_filter_push (&f, -1.0f, t + 3100000, &out));

	/* After a restart even a value inside the hysteresis band is sent: the
	 * DSP reported its own 0 when the client enabled the sensor again. */
	struct als_filter g = { 0 };
	assert (als_filter_push (&g, 100.0f, t, &out) && out == 100.0f);
	assert (!als_filter_push (&g, 105.0f, t + 210000, &out));
	assert (als_filter_push (&g, 105.0f, t + 2500000, &out) && out == 105.0f);
}

static void
test_encode (void)
{
	uint8_t b[16];
	float lux, cct;

	assert (als_encode_display (b, 61) == 4 && !memcmp (b, "\x08\x05\x48\x3d", 4));
	assert (als_encode_display (b, 2047) == 5 && !memcmp (b, "\x08\x05\x48\xff\x0f", 5));
	assert (als_encode_display (b, 0) == 4 && !memcmp (b, "\x08\x05\x48\x00", 4));
	assert (als_encode_lux (b, 123.5f, 4000.0f) == 12);
	assert (b[0] == 0x08 && b[1] == 15 && b[2] == 0x5d && b[7] == 0x65);
	memcpy (&lux, b + 3, 4);
	memcpy (&cct, b + 8, 4);
	assert (lux == 123.5f && cct == 4000.0f);
}

int
main (void)
{
	test_coef ();
	test_decode ();
	test_lux ();
	test_filter ();
	test_encode ();
	puts ("als math: ok");
	return 0;
}
