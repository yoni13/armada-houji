// SPDX-License-Identifier: GPL-2.0-or-later
/* Synthetic records only: never use or print real phone coordinates. */
#define main loc_probe_main
#include "houji-loc-test.c"
#undef main

int main(void)
{
	PositionFrame sample = {.fix = TRUE, .latitude = 48, .longitude = 11,
		.utc_ms = 764426119000, .circular = 42.5, .semi_major = 50,
		.semi_minor = 20, .confidence = 68};
	gchar *text = position_frame(&sample);
	g_assert_cmpstr(text, ==, "{\"fix\":true,\"utc_ms\":764426119000,\"lat\":48,\"lon\":11,"
		"\"horizontal_uncertainty_m\":42.5,\"horizontal_semi_major_m\":50,"
		"\"horizontal_semi_minor_m\":20,\"horizontal_confidence_percent\":68}\n");
	g_free(text);
	/* A lost fix must never retain its last location or accuracy. */
	sample.fix = FALSE;
	text = position_frame(&sample);
	g_assert_cmpstr(text, ==, "{\"fix\":false}\n");
	g_free(text);
	sample.fix = TRUE;
	sample.circular = NAN;
	sample.semi_major = INFINITY;
	sample.semi_minor = -1;
	sample.confidence = -1;
	text = position_frame(&sample);
	g_assert_nonnull(strstr(text, "\"horizontal_uncertainty_m\":null"));
	g_assert_nonnull(strstr(text, "\"horizontal_semi_major_m\":null"));
	g_assert_nonnull(strstr(text, "\"horizontal_semi_minor_m\":null"));
	g_assert_nonnull(strstr(text, "\"horizontal_confidence_percent\":null"));
	g_free(text);
	/* Unknown confidence is distinct from the protocol's reported zero. */
	sample.confidence = 0;
	text = position_frame(&sample);
	g_assert_nonnull(strstr(text, "\"horizontal_confidence_percent\":0"));
	g_free(text);
	sample.confidence = 100;
	text = position_frame(&sample);
	g_assert_nonnull(strstr(text, "\"horizontal_confidence_percent\":null"));
	g_free(text);
	const double invalid_latitudes[] = {NAN, INFINITY, -91, 91};
	for (guint i = 0; i < G_N_ELEMENTS(invalid_latitudes); i++) {
		sample.latitude = invalid_latitudes[i];
		text = position_frame(&sample);
		g_assert_cmpstr(text, ==, "{\"fix\":false}\n");
		g_free(text);
	}
	sample.latitude = 48;
	sample.utc_ms = G_GUINT64_CONSTANT(4102444800000);
	text = position_frame(&sample);
	g_assert_cmpstr(text, ==, "{\"fix\":false}\n");
	g_free(text);
	g_print("position frame tests passed\n");
	return 0;
}
