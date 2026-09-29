// SPDX-License-Identifier: GPL-2.0-or-later
/* Bounded QMI LOC test. Keep one QRTR client alive from start through stop.
 * Log fix validity and reception metrics, never coordinates or NMEA content.
 */
#include <glib-unix.h>
#include <libqmi-glib.h>
#include <libqrtr-glib.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>

static GMainLoop *loop;
static QrtrBus *bus;
static QmiDevice *device;
static QmiClientLoc *client;
static guint node_id, duration = 180;
static guint reports, fixes, sv_reports, max_satellites;
static gboolean started, stopping;
static gboolean query_only;
static gboolean diagnostics;
static gboolean full_power;
static gboolean nmea_test;
static guint nmea_reports;
static int position_fd = -1;
static guint debug_reports, measurement_reports;
static int exit_status;
static void released(GObject *source, GAsyncResult *res, gpointer unused);
static void setup_next(void);
static void start_session(void);
static gboolean stop_session(gpointer unused);
static const guint16 setup_ids[] = {0x3b, 0xbf, 0x36, 0x38};
static guint setup_index;
static gchar *xtra_data;
static gsize xtra_size;
static guint16 xtra_part = 1, xtra_parts;
static gboolean xtra_reply_pending, xtra_indication_pending;
static guint xtra_timeout;
static void xtra_send(void);
static void satellite_message(QmiMessage *message);

static void config_tlv(guint8 type, const guint8 *value, gsize length, gpointer unused)
{
	g_print(" config tlv=0x%02x bytes=%zu value=", type, length);
	for (gsize i = 0; i < MIN(length, 128); i++) g_print("%02x", value[i]);
	g_print("\n");
}

/* Qualcomm location_service_v02.c and HyperOS reportEngDebugDataInfo
 * (0x3e3d8) agree on these fields. Explicitly exclude position TLVs.
 */
static void receiver_tlv(guint8 type, const guint8 *value, gsize length, gpointer context)
{
	guint16 id = GPOINTER_TO_UINT(context);
	if (id == 0xe6 && ((type >= 0x10 && type <= 0x1a) ||
	    (type >= 0x29 && type <= 0x34) || (type >= 0x59 && type <= 0x5b)))
		config_tlv(type, value, length, NULL);
	if (id == 0x86 && (type <= 3 || type == 0x10 || type == 0x1c ||
	    type == 0x1e || type == 0x1f || type == 0x3f || type == 0x43))
		config_tlv(type, value, length, NULL);
	if (id == 0x86 && (type == 0x1b || type == 0x2e) && length)
		g_print(" measurement list tlv=0x%02x entries=%u bytes=%zu\n", type, value[0], length);
}

static void indication(QmiDevice *unused, QmiMessage *message)
{
	/* Discover newer firmware events without logging their private payloads.
	 * Report each message ID once; typed callbacks still count every report.
	 */
	static gboolean seen[65536];
	guint16 id = qmi_message_get_message_id(message);
	if (id == 0x25 && qmi_message_get_service(message) == QMI_SERVICE_LOC)
		satellite_message(message);
	if (diagnostics && qmi_message_get_service(message) == QMI_SERVICE_LOC &&
	    (id == 0xe6 || id == 0x86)) {
		guint count = id == 0xe6 ? ++debug_reports : ++measurement_reports;
		/* At most the first cycle and one sample per 30 reports. */
		if (count <= 6 || !(count % 30)) {
			g_print("receiver indication=0x%04x count=%u\n", id, count);
			qmi_message_foreach_raw_tlv(message, receiver_tlv, GUINT_TO_POINTER(id));
		}
	}
	if (qmi_message_get_service(message) == QMI_SERVICE_LOC && !seen[id]) {
		seen[id] = TRUE;
		g_print("indication id=0x%04x\n", id);
		if (id == 0x25) qmi_message_foreach_raw_tlv(message, config_tlv, NULL);
	}
	/* Only configuration and assistance-status replies, never location data. */
	if (qmi_message_get_service(message) == QMI_SERVICE_LOC &&
	    (id == 0x3b || id == 0xbf || id == 0x38 || id == 0x29 || id == 0x88)) {
		g_print("configuration indication=0x%04x\n", id);
		qmi_message_foreach_raw_tlv(message, config_tlv, NULL);
	}
}

static void orbit_source(QmiClientLoc *unused, QmiIndicationLocGetPredictedOrbitsDataSourceOutput *out)
{
	GArray *servers = NULL;
	guint32 file_size = 0, part_size = 0;
	QmiLocIndicationStatus status;
	if (qmi_indication_loc_get_predicted_orbits_data_source_output_get_indication_status(out, &status, NULL))
		g_print("orbit source status=%s\n", qmi_loc_indication_status_get_string(status));
	qmi_indication_loc_get_predicted_orbits_data_source_output_get_allowed_sizes(out, &file_size, &part_size, NULL);
	g_print("orbit limits file=%u part=%u\n", file_size, part_size);
	qmi_indication_loc_get_predicted_orbits_data_source_output_get_server_list(out, &servers, NULL);
	for (guint i = 0; servers && i < servers->len; i++)
		g_print("orbit server=%s\n", g_array_index(servers, gchar *, i));
}

static void fail(const char *stage, GError *error)
{
	g_printerr("error stage=%s message=%s\n", stage, error ? error->message : "unknown");
	g_clear_error(&error);
	exit_status = 1;
	if (client && !stopping) {
		stopping = TRUE;
		qmi_device_release_client(device, QMI_CLIENT(client), QMI_DEVICE_RELEASE_CLIENT_FLAGS_RELEASE_CID,
			10, NULL, released, NULL);
	} else {
		g_main_loop_quit(loop);
	}
}

static gboolean xtra_timed_out(gpointer unused)
{
	xtra_timeout = 0;
	fail("XTRA part acknowledgement timed out", NULL);
	return G_SOURCE_REMOVE;
}

static void xtra_advance(void)
{
	if (stopping || xtra_reply_pending || xtra_indication_pending) return;
	if (xtra_timeout) { g_source_remove(xtra_timeout); xtra_timeout = 0; }
	if (xtra_part == 1 || !(xtra_part % 16) || xtra_part == xtra_parts)
		g_print("XTRA accepted part=%u/%u\n", xtra_part, xtra_parts);
	if (xtra_part == xtra_parts) {
		g_print("XTRA upload accepted bytes=%zu\n", xtra_size);
		stop_session(NULL);
		return;
	}
	xtra_part++;
	xtra_send();
}

static void xtra_indication(QmiClientLoc *unused, QmiIndicationLocInjectXtraDataOutput *out)
{
	QmiLocIndicationStatus status;
	guint16 part;
	if (!xtra_data || stopping) return;
	if (!qmi_indication_loc_inject_xtra_data_output_get_indication_status(out, &status, NULL) ||
	    !qmi_indication_loc_inject_xtra_data_output_get_part_number(out, &part, NULL) ||
	    status != QMI_LOC_INDICATION_STATUS_SUCCESS || part != xtra_part) {
		fail("XTRA indication rejected or unexpected part", NULL);
		return;
	}
	xtra_indication_pending = FALSE;
	xtra_advance();
}

static void xtra_ready(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessageLocInjectXtraDataOutput *out = qmi_client_loc_inject_xtra_data_finish(client, res, &error);
	if (!out || !qmi_message_loc_inject_xtra_data_output_get_result(out, &error)) {
		if (out) qmi_message_loc_inject_xtra_data_output_unref(out);
		fail("XTRA response rejected", error); return;
	}
	qmi_message_loc_inject_xtra_data_output_unref(out);
	xtra_reply_pending = FALSE;
	xtra_advance();
}

static void xtra_send(void)
{
	QmiMessageLocInjectXtraDataInput *input = qmi_message_loc_inject_xtra_data_input_new();
	gsize offset = (xtra_part - 1) * 1024, size = MIN(1024, xtra_size - offset);
	GArray *part = g_array_sized_new(FALSE, FALSE, sizeof(guint8), size);
	g_array_append_vals(part, xtra_data + offset, size);
	qmi_message_loc_inject_xtra_data_input_set_total_size(input, xtra_size, NULL);
	qmi_message_loc_inject_xtra_data_input_set_total_parts(input, xtra_parts, NULL);
	qmi_message_loc_inject_xtra_data_input_set_part_number(input, xtra_part, NULL);
	qmi_message_loc_inject_xtra_data_input_set_part_data(input, part, NULL);
	g_array_unref(part);
	xtra_reply_pending = xtra_indication_pending = TRUE;
	xtra_timeout = g_timeout_add_seconds(15, xtra_timed_out, NULL);
	qmi_client_loc_inject_xtra_data(client, input, 10, NULL, xtra_ready, NULL);
	qmi_message_loc_inject_xtra_data_input_unref(input);
}

static void engine(QmiClientLoc *unused, QmiIndicationLocEngineStateOutput *out)
{
	QmiLocEngineState state;
	if (qmi_indication_loc_engine_state_output_get_engine_state(out, &state, NULL))
		g_print("engine state=%s\n", qmi_loc_engine_state_get_string(state));
}

static void nmea(QmiClientLoc *unused, QmiIndicationLocNmeaOutput *out)
{
	const gchar *sentence;
	if (!qmi_indication_loc_nmea_output_get_nmea_string(out, &sentence, NULL)) return;
	/* Do not print the sentence: it can contain the phone's coordinates. */
	nmea_reports++;
	if (nmea_reports <= 4 || !(nmea_reports % 60))
		g_print("NMEA reports=%u bytes=%zu\n", nmea_reports, strlen(sentence));
}

static gboolean result_ok(QmiMessage *message, const char *stage)
{
	guint16 length;
	const guint8 *result = qmi_message_get_raw_tlv(message, 2, &length);
	if (result && length == 4 && !result[0] && !result[1]) return TRUE;
	g_printerr("%s failed: QMI error=%u\n", stage,
		result && length == 4 ? result[2] | (result[3] << 8) : 65535);
	return FALSE;
}

typedef struct {
	gboolean fix;
	guint64 utc_ms;
	gdouble latitude, longitude;
	/* NAN means the optional TLV was absent. Preserve the modem values;
	 * do not infer a radius or confidence from DOP or a previous report.
	 */
	gfloat circular, semi_major, semi_minor;
	gint confidence;
} PositionFrame;

static void append_uncertainty(GString *frame, const char *name, gdouble value)
{
	gchar number[G_ASCII_DTOSTR_BUF_SIZE];
	g_string_append_printf(frame, ",\"%s\":%s", name,
		isfinite(value) && value >= 0 ? g_ascii_dtostr(number, sizeof(number), value) : "null");
}

static gchar *position_frame(const PositionFrame *sample)
{
	gchar latitude[G_ASCII_DTOSTR_BUF_SIZE], longitude[G_ASCII_DTOSTR_BUF_SIZE];
	GString *frame;
	if (!sample->fix || !isfinite(sample->latitude) || !isfinite(sample->longitude) ||
	    fabs(sample->latitude) > 90 || fabs(sample->longitude) > 180 ||
	    sample->utc_ms > G_GUINT64_CONSTANT(4102444799999))
		return g_strdup("{\"fix\":false}\n");
	g_ascii_dtostr(latitude, sizeof(latitude), sample->latitude);
	g_ascii_dtostr(longitude, sizeof(longitude), sample->longitude);
	frame = g_string_new(NULL);
	g_string_append_printf(frame, "{\"fix\":true,\"utc_ms\":%" G_GUINT64_FORMAT ",\"lat\":%s,\"lon\":%s",
		sample->utc_ms, latitude, longitude);
	append_uncertainty(frame, "horizontal_uncertainty_m", sample->circular);
	append_uncertainty(frame, "horizontal_semi_major_m", sample->semi_major);
	append_uncertainty(frame, "horizontal_semi_minor_m", sample->semi_minor);
	/* LOC defines this confidence for the ellipse when both ellipse and
	 * circular uncertainty are present. It must not be silently applied to
	 * the circle or labelled as a standard deviation / 95% accuracy.
	 */
	if (sample->confidence >= 0 && sample->confidence <= 99)
		g_string_append_printf(frame, ",\"horizontal_confidence_percent\":%d", sample->confidence);
	else
		g_string_append(frame, ",\"horizontal_confidence_percent\":null");
	g_string_append(frame, "}\n");
	return g_string_free(frame, FALSE);
}

static void position(QmiClientLoc *unused, QmiIndicationLocPositionReportOutput *out)
{
	QmiLocSessionStatus status;
	QmiLocTechnologyUsed tech = 0;
	gdouble lat = NAN, lon = NAN;
	gfloat uncertainty = -1;
	gboolean coordinates;
	if (!qmi_indication_loc_position_report_output_get_session_status(out, &status, NULL))
		return;
	coordinates = qmi_indication_loc_position_report_output_get_latitude(out, &lat, NULL) &&
		qmi_indication_loc_position_report_output_get_longitude(out, &lon, NULL);
	coordinates = coordinates && isfinite(lat) && isfinite(lon) && fabs(lat) <= 90 && fabs(lon) <= 180;
	qmi_indication_loc_position_report_output_get_horizontal_uncertainty_circular(out, &uncertainty, NULL);
	qmi_indication_loc_position_report_output_get_technology_used(out, &tech, NULL);
	reports++;
	if (status == QMI_LOC_SESSION_STATUS_SUCCESS && coordinates && (tech & QMI_LOC_TECHNOLOGY_USED_SATELLITE))
		fixes++;
	if (position_fd >= 0) {
		guint8 confidence;
		PositionFrame sample = {.latitude = lat, .longitude = lon, .circular = NAN,
			.semi_major = NAN, .semi_minor = NAN, .confidence = -1};
		gchar *frame;
		sample.fix = status == QMI_LOC_SESSION_STATUS_SUCCESS && coordinates &&
		    (tech & QMI_LOC_TECHNOLOGY_USED_SATELLITE) &&
		    qmi_indication_loc_position_report_output_get_utc_timestamp(out, &sample.utc_ms, NULL);
		qmi_indication_loc_position_report_output_get_horizontal_uncertainty_circular(out, &sample.circular, NULL);
		qmi_indication_loc_position_report_output_get_horizontal_uncertainty_elliptical_major(out, &sample.semi_major, NULL);
		qmi_indication_loc_position_report_output_get_horizontal_uncertainty_elliptical_minor(out, &sample.semi_minor, NULL);
		if (qmi_indication_loc_position_report_output_get_horizontal_confidence(out, &confidence, NULL))
			sample.confidence = confidence;
		frame = position_frame(&sample);
		/* A separate inherited pipe, never stdout/journal. Small writes are
		 * atomic; a slow consumer may drop an update without stalling QMI.
		 */
		if (write(position_fd, frame, strlen(frame)) < 0 && errno == EPIPE)
			stop_session(NULL);
		g_free(frame);
	}
	g_print("position status=%s coordinates_present=%d uncertainty_m=%.1f technology=0x%x\n",
		qmi_loc_session_status_get_string(status), coordinates, uncertainty, tech);
}

/* The newer modem uses expanded list TLV 0x11 instead of libqmi's
 * legacy 0x10. Each packed record is 28 bytes plus one GLONASS channel byte.
 * A search candidate is not a received satellite: count signal separately.
 */
static guint32 get_le32(const guint8 *p)
{
	guint32 value;
	memcpy(&value, p, 4);
	return GUINT32_FROM_LE(value);
}

static void satellite_message(QmiMessage *message)
{
	guint16 length = 0;
	guint stride = 29, with_signal = 0, tracking = 0, searching = 0;
	gfloat peak = 0;
	const guint8 *list = qmi_message_get_raw_tlv(message, 0x11, &length);
	if (!list) { stride = 28; list = qmi_message_get_raw_tlv(message, 0x10, &length); }
	if (list && (!length || length != 1 + list[0] * stride)) {
		g_printerr("malformed satellite list bytes=%u stride=%u\n", length, stride);
		return;
	}
	guint count = list ? list[0] : 0;
	for (guint i = 0; i < count; i++) {
		const guint8 *e = list + 1 + i * stride;
		guint32 valid = get_le32(e), status = get_le32(e + 11), bits = get_le32(e + 24);
		gfloat snr;
		memcpy(&snr, &bits, 4);
		if (valid & 8) { searching += status == 2; tracking += status == 3; }
		if ((valid & 128) && isfinite(snr) && snr > 0) {
			with_signal++; peak = MAX(peak, snr);
			if (!(sv_reports % 30) && (valid & 3) == 3)
				g_print("signal system=%u sv=%u status=%u cn0=%.1f\n",
					get_le32(e + 4), e[8] | (e[9] << 8), (valid & 8) ? status : 0, snr);
		}
	}
	sv_reports++;
	max_satellites = MAX(max_satellites, with_signal);
	g_print("satellites candidates=%u searching=%u tracking=%u with_signal=%u peak_cn0=%.1f list=%s\n",
		count, searching, tracking, with_signal, peak, list ? (stride == 29 ? "expanded" : "legacy") : "absent");
}

static void released(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	if (!qmi_device_release_client_finish(device, res, &error)) {
		fail("release", error);
		return;
	}
	g_print("summary position_reports=%u valid_fixes=%u satellite_reports=%u max_satellites_with_signal=%u\n",
		reports, fixes, sv_reports, max_satellites);
	if (nmea_test) g_print("NMEA summary reports=%u\n", nmea_reports);
	g_main_loop_quit(loop);
}

static void stopped(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessageLocStopOutput *out = qmi_client_loc_stop_finish(client, res, &error);
	if (!out || !qmi_message_loc_stop_output_get_result(out, &error)) {
		g_printerr("stop failed: %s\n", error ? error->message : "unknown");
		g_clear_error(&error);
		exit_status = 1;
	} else {
		g_print("session stopped\n");
	}
	if (out) qmi_message_loc_stop_output_unref(out);
	qmi_device_release_client(device, QMI_CLIENT(client), QMI_DEVICE_RELEASE_CLIENT_FLAGS_RELEASE_CID,
		10, NULL, released, NULL);
}

static gboolean stop_session(gpointer unused)
{
	QmiMessageLocStopInput *input;
	if (stopping) return G_SOURCE_REMOVE;
	stopping = TRUE;
	if (!started) {
		if (client)
			qmi_device_release_client(device, QMI_CLIENT(client), QMI_DEVICE_RELEASE_CLIENT_FLAGS_RELEASE_CID,
				10, NULL, released, NULL);
		else
			g_main_loop_quit(loop);
		return G_SOURCE_REMOVE;
	}
	input = qmi_message_loc_stop_input_new();
	qmi_message_loc_stop_input_set_session_id(input, 1, NULL);
	qmi_client_loc_stop(client, input, 10, NULL, stopped, NULL);
	qmi_message_loc_stop_input_unref(input);
	return G_SOURCE_REMOVE;
}

static void start_ready(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessage *out = qmi_device_command_full_finish(device, res, &error);
	if (!out || !result_ok(out, "start")) {
		if (out) qmi_message_unref(out);
		fail("start", error);
		return;
	}
	qmi_message_unref(out);
	started = TRUE;
	g_print("session started duration_seconds=%u\n", duration);
	g_timeout_add_seconds(duration, stop_session, NULL);
}

static void start_session(void)
{
	GError *error = NULL;
	QmiMessage *input;
	const guint8 session_id = 1;
	guint32 periodic = GUINT32_TO_LE(1), accuracy = GUINT32_TO_LE(3);
	guint32 intermediate = GUINT32_TO_LE(1), interval = GUINT32_TO_LE(1000);
	guint32 altitude_assumed = GUINT32_TO_LE(2);
	guint32 power_mode[] = {GUINT32_TO_LE(1), 0};
	/* HyperOS startTimeBasedTracking lambda (0x52354) explicitly requests
	 * accuracy level 3. IDL 0x1b21b maps it to uint32 TLV 0x11; the current
	 * libqmi Start API does not expose this field.
	 */
	input = qmi_message_new(QMI_SERVICE_LOC, qmi_client_get_cid(QMI_CLIENT(client)),
		qmi_client_get_next_transaction_id(QMI_CLIENT(client)), 0x22);
	if (!qmi_message_add_raw_tlv(input, 1, &session_id, 1, &error) ||
	    !qmi_message_add_raw_tlv(input, 0x10, (guint8 *)&periodic, 4, &error) ||
	    !qmi_message_add_raw_tlv(input, 0x11, (guint8 *)&accuracy, 4, &error) ||
	    !qmi_message_add_raw_tlv(input, 0x12, (guint8 *)&intermediate, 4, &error) ||
	    !qmi_message_add_raw_tlv(input, 0x13, (guint8 *)&interval, 4, &error) ||
	    !qmi_message_add_raw_tlv(input, 0x15, (guint8 *)&altitude_assumed, 4, &error) ||
	    !qmi_message_add_raw_tlv(input, 0x16, (guint8 *)&interval, 4, &error)) {
		qmi_message_unref(input);
		fail("encode start", error);
		return;
	}
	/* Qualcomm's non-duty-cycling receiver mode, for a bounded weak-signal
	 * test. This is a per-session request and does not change RF/NV data.
	 */
	if (full_power && !qmi_message_add_raw_tlv(input, 0x1a, (guint8 *)power_mode, 8, &error)) {
		qmi_message_unref(input); fail("encode power mode", error); return;
	}
	qmi_device_command_full(device, input, NULL, 10, NULL, start_ready, NULL);
	qmi_message_unref(input);
}

static void receiver_configured(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessage *out = qmi_device_command_full_finish(device, res, &error);
	if (!out || !result_ok(out, "receiver report configuration")) {
		if (out) qmi_message_unref(out);
		fail("receiver report configuration", error); return;
	}
	qmi_message_unref(out);
	start_session();
}

static void configure_receiver_reports(void)
{
	GError *error = NULL;
	guint64 constellations = GUINT64_TO_LE(63);
	guint32 nmea_types = GUINT32_TO_LE(QMI_LOC_NMEA_TYPE_GGA | QMI_LOC_NMEA_TYPE_RMC);
	QmiMessage *input = qmi_message_new(QMI_SERVICE_LOC, qmi_client_get_cid(QMI_CLIENT(client)),
		qmi_client_get_next_transaction_id(QMI_CLIENT(client)), nmea_test ? 0x3e : 0x88);
	/* This enables report delivery, not constellations or RF calibration.
	 * Same six-system measurement mask as HyperOS at 0x327bc.
	 */
	if (!(nmea_test ? qmi_message_add_raw_tlv(input, 1, (guint8 *)&nmea_types, 4, &error) :
	      qmi_message_add_raw_tlv(input, 0x10, (guint8 *)&constellations, 8, &error))) {
		qmi_message_unref(input); fail("encode receiver configuration", error); return;
	}
	qmi_device_command_full(device, input, NULL, 10, NULL, receiver_configured, NULL);
	qmi_message_unref(input);
}

static void setup_ready(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessage *out = qmi_device_command_full_finish(device, res, &error);
	g_print("setup request=0x%04x accepted=%d\n", setup_ids[setup_index], out && result_ok(out, "setup"));
	if (error) { g_printerr("setup: %s\n", error->message); g_clear_error(&error); }
	if (out) qmi_message_unref(out);
	setup_index++;
	setup_next();
}

static void setup_next(void)
{
	GError *error = NULL;
	QmiMessage *input;
	guint32 lock_type = GUINT32_TO_LE(3), uncertainty = GUINT32_TO_LE(1000);
	guint64 utc_ms = GUINT64_TO_LE(g_get_real_time() / 1000);
	if (stopping) return;
	if (setup_index == G_N_ELEMENTS(setup_ids)) {
		if (diagnostics || nmea_test) configure_receiver_reports();
		else start_session();
		return;
	}
	if (query_only && setup_ids[setup_index] == 0x38) {
		g_timeout_add_seconds(5, stop_session, NULL);
		return;
	}
	input = qmi_message_new(QMI_SERVICE_LOC, qmi_client_get_cid(QMI_CLIENT(client)),
		qmi_client_get_next_transaction_id(QMI_CLIENT(client)), setup_ids[setup_index]);
	/* HyperOS getEngineLockStateSync asks for lock type 3 (IDL 0x1b6bf).
	 * setTime sends UTC milliseconds and uncertainty in ms (IDL 0x1b634).
	 * The caller must verify the phone clock is synchronized before testing.
	 */
	if ((setup_ids[setup_index] == 0x3b &&
	     !qmi_message_add_raw_tlv(input, 0x10, (guint8 *)&lock_type, 4, &error)) ||
	    (setup_ids[setup_index] == 0x38 &&
	     (!qmi_message_add_raw_tlv(input, 1, (guint8 *)&utc_ms, 8, &error) ||
	      !qmi_message_add_raw_tlv(input, 2, (guint8 *)&uncertainty, 4, &error)))) {
		qmi_message_unref(input); fail("encode setup", error); return;
	}
	qmi_device_command_full(device, input, NULL, 10, NULL, setup_ready, NULL);
	qmi_message_unref(input);
}

static void registered(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessage *out = qmi_device_command_full_finish(device, res, &error);
	if (!out || !result_ok(out, "register")) {
		if (out) qmi_message_unref(out);
		fail("register", error); return;
	}
	qmi_message_unref(out);
	g_print("stock client registration accepted\n");
	if (xtra_data) xtra_send();
	else setup_next();
}

static void allocated(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QmiMessage *input;
	guint64 mask = QMI_LOC_EVENT_REGISTRATION_FLAG_POSITION_REPORT |
		QMI_LOC_EVENT_REGISTRATION_FLAG_GNSS_SATELLITE_INFO | QMI_LOC_EVENT_REGISTRATION_FLAG_ENGINE_STATE |
		QMI_LOC_EVENT_REGISTRATION_FLAG_FIX_SESSION_STATE | QMI_LOC_EVENT_REGISTRATION_FLAG_INJECT_TIME_REQUEST |
		QMI_LOC_EVENT_REGISTRATION_FLAG_INJECT_PREDICTED_ORBITS_REQUEST;
	if (diagnostics) mask |= G_GUINT64_CONSTANT(0x0002000001000000);
	if (nmea_test) mask |= QMI_LOC_EVENT_REGISTRATION_FLAG_NMEA;
	mask = GUINT64_TO_LE(mask);
	const guint8 name[] = {'H', 'A', 'L'};
	const guint8 client_type[] = {1, 0, 0, 0};
	const guint8 stock_flag = 0;
	client = QMI_CLIENT_LOC(qmi_device_allocate_client_finish(device, res, &error));
	if (!client) { fail("allocate", error); return; }
	g_signal_connect(client, "position-report", G_CALLBACK(position), NULL);
	g_signal_connect(client, "engine-state", G_CALLBACK(engine), NULL);
	g_signal_connect(client, "nmea", G_CALLBACK(nmea), NULL);
	g_signal_connect(client, "get-predicted-orbits-data-source", G_CALLBACK(orbit_source), NULL);
	g_signal_connect(client, "inject-xtra-data", G_CALLBACK(xtra_indication), NULL);
	/* HyperOS libloc_api_v02 locClientRegisterEventMask.cfi (0x612e4)
	 * supplies these three optional fields. Its IDL descriptor at 0x1b20b
	 * defines TLV 0x10 as a <=4-byte string, 0x11 uint32, 0x12 uint8.
	 * libqmi 1.36 exposes only the event mask and gets InvalidArgument.
	 */
	input = qmi_message_new(QMI_SERVICE_LOC, qmi_client_get_cid(QMI_CLIENT(client)),
		qmi_client_get_next_transaction_id(QMI_CLIENT(client)), 0x21);
	if (!qmi_message_add_raw_tlv(input, 1, (const guint8 *)&mask, sizeof(mask), &error) ||
	    !qmi_message_add_raw_tlv(input, 0x10, name, sizeof(name), &error) ||
	    !qmi_message_add_raw_tlv(input, 0x11, client_type, sizeof(client_type), &error) ||
	    !qmi_message_add_raw_tlv(input, 0x12, &stock_flag, sizeof(stock_flag), &error)) {
		qmi_message_unref(input);
		fail("encode", error);
		return;
	}
	qmi_device_command_full(device, input, NULL, 10, NULL, registered, NULL);
	qmi_message_unref(input);
}

static void opened(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	if (!qmi_device_open_finish(device, res, &error)) { fail("open", error); return; }
	qmi_device_allocate_client(device, QMI_SERVICE_LOC, QMI_CID_NONE, 10, NULL, allocated, NULL);
}

static void device_ready(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	device = qmi_device_new_from_node_finish(res, &error);
	if (!device) { fail("device", error); return; }
	g_signal_connect(device, QMI_DEVICE_SIGNAL_INDICATION, G_CALLBACK(indication), NULL);
	qmi_device_open(device, QMI_DEVICE_OPEN_FLAGS_NONE, 10, NULL, opened, NULL);
}

static void bus_ready(GObject *source, GAsyncResult *res, gpointer unused)
{
	GError *error = NULL;
	QrtrNode *node;
	bus = qrtr_bus_new_finish(res, &error);
	if (!bus) { fail("bus", error); return; }
	node = qrtr_bus_peek_node(bus, node_id);
	if (!node) { fail("QRTR node absent", NULL); return; }
	qmi_device_new_from_node(node, NULL, device_ready, NULL);
}

int main(int argc, char **argv)
{
	guint64 value;
	if ((argc < 3 || argc > 5) || !g_ascii_string_to_unsigned(argv[1], 10, 0, G_MAXUINT32, &value, NULL))
		return 2;
	if (argc == 4) {
		if (!g_strcmp0(argv[3], "query")) query_only = TRUE;
		else if (!g_strcmp0(argv[3], "diagnostics")) diagnostics = TRUE;
		else if (!g_strcmp0(argv[3], "full-power")) diagnostics = full_power = TRUE;
		else if (!g_strcmp0(argv[3], "nmea")) nmea_test = TRUE;
		else return 2;
	}
	if (argc == 5) {
		if (!g_strcmp0(argv[3], "json-fd")) {
			guint64 fd;
			if (!g_ascii_string_to_unsigned(argv[4], 10, 3, 65535, &fd, NULL)) return 2;
			int flags = fcntl(fd, F_GETFL);
			if (flags < 0 || (flags & O_ACCMODE) == O_RDONLY || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) return 2;
			position_fd = fd;
			signal(SIGPIPE, SIG_IGN);
			full_power = TRUE;
		} else {
			if (g_strcmp0(argv[3], "xtra") || !g_file_get_contents(argv[4], &xtra_data, &xtra_size, NULL) ||
			    !xtra_size || xtra_size > 133120) return 2;
			xtra_parts = (xtra_size + 1023) / 1024;
		}
	}
	node_id = value;
	if (!g_ascii_string_to_unsigned(argv[2], 10, 10, 900, &value, NULL)) return 2;
	duration = value;
	setvbuf(stdout, NULL, _IOLBF, 0);
	loop = g_main_loop_new(NULL, FALSE);
	g_unix_signal_add(SIGINT, stop_session, NULL);
	g_unix_signal_add(SIGTERM, stop_session, NULL);
	qrtr_bus_new(2000, NULL, bus_ready, NULL);
	g_main_loop_run(loop);
	g_clear_object(&client);
	g_clear_object(&device);
	g_clear_object(&bus);
	g_main_loop_unref(loop);
	g_free(xtra_data);
	return exit_status;
}
