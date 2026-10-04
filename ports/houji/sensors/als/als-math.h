/* Houji front ambient light: lux from the TCS3720 raw channels.
 *
 * The stock DSP driver of the under-display sensor always reports 0 lux.
 * Xiaomi's Android sensor service computes lux on the application processor
 * from the raw channels and sends the result back to the DSP, which then
 * reports it as the ordinary ambient_light sensor. The formula follows that
 * service (alsAlgo::process). Its screen-content leak correction is left out:
 * it needs a capture of the pixels above the sensor.
 *
 * The messages are Xiaomi's sns_physical_sensor_oem_config, sent with
 * message ID 2048 to the ambient_light_raw sensor:
 *   field 1 = 5  (display info), field 9  = backlight level, 0 = screen off
 *   field 1 = 15 (set lux),      field 11 = lux, field 12 = CCT
 */
#ifndef HOUJI_ALS_MATH_H
#define HOUJI_ALS_MATH_H

#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#define ALS_OEM_CONFIG_MSG_ID 2048
#define ALS_SATURATED_COUNT 16775000.0
#define ALS_SATURATED_LUX 200000.0f
#define ALS_WINDOW_US 200000
#define ALS_RESTART_GAP_US 1000000

struct als_coef {
	double ir_threshold;
	double lo[4];
	double hi[4];
};

/* Read channel_coef.coef_data from lightSensorConfig.json: the IR ratio
 * threshold followed by the C, R, G, B coefficients for low and high IR. */
static inline bool
als_parse_coef (const char *json, struct als_coef *coef)
{
	const char *p = json ? strstr (json, "\"channel_coef\"") : NULL;
	double v[9];
	int i;

	if (!p || !(p = strstr (p, "\"coef_data\"")))
		return false;
	p += strlen ("\"coef_data\"");
	while (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r' || *p == ':')
		p++;
	if (*p++ != '"')
		return false;
	for (i = 0; i < 9; i++) {
		char *end;
		v[i] = strtod (p, &end);
		if (end == p || !isfinite (v[i]))
			return false;
		p = end;
		while (*p == ' ')
			p++;
		if (i < 8 && *p++ != ',')
			return false;
	}
	if (*p == ',')
		p++;
	if (*p != '"')
		return false;
	coef->ir_threshold = v[0];
	memcpy (coef->lo, v + 1, sizeof coef->lo);
	memcpy (coef->hi, v + 5, sizeof coef->hi);
	return true;
}

static inline bool
als_varint (const uint8_t *p, size_t len, size_t *i, uint64_t *value)
{
	int shift = 0;

	*value = 0;
	while (*i < len && shift < 64) {
		uint8_t b = p[(*i)++];
		*value |= (uint64_t) (b & 0x7f) << shift;
		if (!(b & 0x80))
			return true;
		shift += 7;
	}
	return false;
}

/* Decode the repeated float field 1 of an SSC sensor report. */
static inline size_t
als_decode_floats (const uint8_t *p, size_t len, float *out, size_t max)
{
	size_t i = 0, n = 0;

	while (i < len) {
		uint64_t key, size;

		if (!als_varint (p, len, &i, &key))
			break;
		switch (key & 7) {
		case 0:
			if (!als_varint (p, len, &i, &size))
				return n;
			break;
		case 1:
			i += 8;
			break;
		case 2:
			if (!als_varint (p, len, &i, &size) || size > len - i)
				return n;
			if ((key >> 3) == 1)
				for (uint64_t j = 0; j + 4 <= size; j += 4)
					if (n < max)
						memcpy (&out[n++], p + i + j, 4);
			i += size;
			break;
		case 5:
			if (i + 4 > len)
				return n;
			if ((key >> 3) == 1 && n < max)
				memcpy (&out[n++], p + i, 4);
			i += 4;
			break;
		default:
			return n;
		}
	}
	return n;
}

/* Raw report: [0..3] C, R, G, B counts, [4..7] the unit's channel scales. */
static inline float
als_lux (const struct als_coef *coef, const float *v, size_t n)
{
	double ch[4], ir, lux;
	const double *c;
	int i;

	if (n < 4)
		return -1.0f;
	if (v[0] > ALS_SATURATED_COUNT || v[1] > ALS_SATURATED_COUNT || v[2] > ALS_SATURATED_COUNT)
		return ALS_SATURATED_LUX;
	for (i = 0; i < 4; i++) {
		double scale = (n >= 8 && v[4 + i] > 0.0f) ? v[4 + i] : 1.0;
		ch[i] = fmax (v[i], 0.0) * scale;
	}
	if (ch[0] <= 0.0)
		return 0.0f;
	ir = (ch[1] + ch[2] + ch[3] - ch[0]) / (ch[0] + ch[0]);
	c = ir <= coef->ir_threshold ? coef->lo : coef->hi;
	lux = ch[0] * c[0] + ch[1] * c[1] + ch[2] * c[2] + ch[3] * c[3] - 0.3;
	return lux > 0.0 ? (float) lux : 0.0f;
}

/* Average 200 ms windows, take the median of the last three and publish
 * changes above 10 % (0.5 lux floor). A gap restarts the filter, and the
 * first value after a restart is published at once. */
struct als_filter {
	double sum;
	unsigned count;
	int64_t window_start;
	int64_t last_sample;
	float windows[3];
	unsigned filled;
	float published;
	bool has_published;
	bool force;
};

static inline float
als_median3 (const float *w, unsigned n)
{
	float a = w[0], b = w[1], c = w[2];

	if (n == 1)
		return a;
	if (n == 2)
		return (a + b) / 2.0f;
	if ((a <= b && b <= c) || (c <= b && b <= a))
		return b;
	if ((b <= a && a <= c) || (c <= a && a <= b))
		return a;
	return c;
}

static inline bool
als_filter_push (struct als_filter *f, float lux, int64_t now, float *out)
{
	float value;

	if (lux < 0.0f)
		return false;
	if (!f->last_sample || now - f->last_sample > ALS_RESTART_GAP_US) {
		f->sum = 0.0;
		f->count = 0;
		f->filled = 0;
		f->window_start = now;
		f->force = true;
	}
	f->last_sample = now;
	if (lux >= ALS_SATURATED_LUX) {
		f->sum = 0.0;
		f->count = 0;
		f->window_start = now;
		value = lux;
		goto publish;
	}
	f->sum += lux;
	f->count++;
	if (!f->force && now - f->window_start < ALS_WINDOW_US)
		return false;
	memmove (f->windows + 1, f->windows, 2 * sizeof (float));
	f->windows[0] = (float) (f->sum / f->count);
	if (f->filled < 3)
		f->filled++;
	f->sum = 0.0;
	f->count = 0;
	f->window_start = now;
	value = als_median3 (f->windows, f->filled);
publish:
	if (!f->force && f->has_published &&
	    fabsf (value - f->published) <= fmaxf (f->published * 0.1f, 0.5f))
		return false;
	f->force = false;
	f->has_published = true;
	f->published = value;
	*out = value;
	return true;
}

static inline size_t
als_encode_display (uint8_t *buf, uint32_t backlight)
{
	size_t n = 0;

	buf[n++] = 0x08;
	buf[n++] = 5;
	buf[n++] = 0x48;
	do {
		uint8_t b = backlight & 0x7f;
		backlight >>= 7;
		buf[n++] = b | (backlight ? 0x80 : 0);
	} while (backlight);
	return n;
}

static inline size_t
als_encode_lux (uint8_t *buf, float lux, float cct)
{
	buf[0] = 0x08;
	buf[1] = 15;
	buf[2] = 0x5d;
	memcpy (buf + 3, &lux, 4);
	buf[7] = 0x65;
	memcpy (buf + 8, &cct, 4);
	return 12;
}

#endif
