/* Houji front ambient light service.
 *
 * Tells the sensor DSP the panel backlight level (0 while the screen is off),
 * listens to the TCS3720 raw channels and sends the computed lux back, so the
 * standard ambient_light sensor reports real values to iio-sensor-proxy.
 * The raw channels only stream while a client keeps ambient_light enabled
 * and the screen is on, so the service is idle otherwise.
 */
#include <errno.h>
#include <fcntl.h>
#include <glib.h>
#include <glib-unix.h>
#include <libssc.h>
#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "als-math.h"

#define REPORT_MEASUREMENT 1025
#define STATE_POLL_SECONDS 2

static struct {
	GMainLoop *loop;
	SSCSensor *raw;
	GObject *client;
	guint64 uid_high, uid_low;
	struct als_coef coef;
	struct als_filter filter;
	char *backlight;
	char *dpms;
	int backlight_fd;
	gint64 sent_display;
	gboolean verbose;
	int status;
} st = { .backlight_fd = -1, .sent_display = -1 };

static void
send_done (GObject *source, GAsyncResult *result, gpointer user_data)
{
	g_autoptr (GError) error = NULL;

	if (!ssc_sensor_send_finish (SSC_SENSOR (source), result, &error))
		g_warning ("Sending %s to the sensor DSP failed: %s", (const char *) user_data, error->message);
}

static void
send_payload (const uint8_t *data, size_t len, const char *what)
{
	g_autoptr (GBytes) payload = g_bytes_new (data, len);

	ssc_sensor_send (st.raw, ALS_OEM_CONFIG_MSG_ID, payload, NULL, send_done, (gpointer) what);
}

static gint64
read_number (const char *path)
{
	g_autofree char *text = NULL;

	if (!g_file_get_contents (path, &text, NULL, NULL))
		return -1;
	return g_ascii_strtoll (text, NULL, 10);
}

static gboolean
screen_on (void)
{
	g_autofree char *text = NULL;

	if (!st.dpms || !g_file_get_contents (st.dpms, &text, NULL, NULL))
		return TRUE;
	return g_str_has_prefix (text, "On");
}

static void
update_display (void)
{
	g_autofree char *path = g_build_filename (st.backlight, "actual_brightness", NULL);
	gint64 level = read_number (path);
	uint8_t buf[16];

	if (!st.raw || level < 0)
		return;
	if (!screen_on ())
		level = 0;
	if (level == st.sent_display)
		return;
	if (st.verbose || st.sent_display < 0 || (level == 0) != (st.sent_display == 0))
		g_message ("Display %s, backlight %" G_GINT64_FORMAT, level ? "on" : "off", level);
	st.sent_display = level;
	send_payload (buf, als_encode_display (buf, (uint32_t) MIN (level, G_MAXUINT32)), "the display state");
}

static gboolean
backlight_changed (gint fd, GIOCondition condition, gpointer user_data)
{
	char discard[32];

	if (lseek (fd, 0, SEEK_SET) == 0)
		while (read (fd, discard, sizeof discard) > 0)
			;
	update_display ();
	return G_SOURCE_CONTINUE;
}

static gboolean
poll_state (gpointer user_data)
{
	update_display ();
	return G_SOURCE_CONTINUE;
}

static void
report (GObject *client, guint msg_id, guint64 uid_high, guint64 uid_low, GArray *buf, gpointer user_data)
{
	float values[16], lux;
	size_t n;
	uint8_t out[12];

	if (msg_id != REPORT_MEASUREMENT || uid_high != st.uid_high || uid_low != st.uid_low)
		return;
	n = als_decode_floats ((const uint8_t *) buf->data, buf->len, values, G_N_ELEMENTS (values));
	if (!als_filter_push (&st.filter, als_lux (&st.coef, values, n), g_get_monotonic_time (), &lux))
		return;
	if (st.verbose)
		g_message ("%.1f lux (C %.0f R %.0f G %.0f B %.0f)", lux, values[0], values[1], values[2], values[3]);
	send_payload (out, als_encode_lux (out, lux, 0.0f), "the lux value");
}

static void
open_done (GObject *source, GAsyncResult *result, gpointer user_data)
{
	g_autoptr (GError) error = NULL;

	if (!ssc_sensor_open_finish (SSC_SENSOR (source), result, &error)) {
		g_printerr ("Cannot enable the raw ambient light channels: %s\n", error->message);
		st.status = 1;
		g_main_loop_quit (st.loop);
	}
}

static void
sensor_ready (GObject *source, GAsyncResult *result, gpointer user_data)
{
	g_autoptr (GError) error = NULL;
	g_autofree char *name = NULL;

	st.raw = ssc_sensor_new_finish (result, &error);
	if (!st.raw) {
		g_printerr ("No raw ambient light sensor: %s\n", error->message);
		st.status = 1;
		g_main_loop_quit (st.loop);
		return;
	}
	g_object_get (st.raw, SSC_SENSOR_UID_HIGH, &st.uid_high, SSC_SENSOR_UID_LOW, &st.uid_low,
		      SSC_SENSOR_NAME, &name, SSC_SENSOR_CLIENT, &st.client, NULL);
	g_message ("Using %s (%" G_GUINT64_FORMAT " %" G_GUINT64_FORMAT ")", name ? name : "ambient_light_raw",
		   st.uid_high, st.uid_low);
	/* Reports for every sensor arrive on the client. The open request only
	 * finishes with the first report, which waits for a light client. */
	g_signal_connect (st.client, "report", G_CALLBACK (report), NULL);
	ssc_sensor_open (st.raw, NULL, open_done, NULL);
	update_display ();
}

static char *
find_backlight (void)
{
	g_autoptr (GDir) dir = g_dir_open ("/sys/class/backlight", 0, NULL);
	const char *name = dir ? g_dir_read_name (dir) : NULL;

	return name ? g_build_filename ("/sys/class/backlight", name, NULL) : NULL;
}

static gboolean
quit (gpointer user_data)
{
	g_main_loop_quit (st.loop);
	return G_SOURCE_REMOVE;
}

int
main (int argc, char **argv)
{
	g_autofree char *config = g_strdup ("/run/houji/qcom/sensors/config/lightSensorConfig.json");
	g_autofree char *json = NULL;
	g_autofree char *actual = NULL;
	g_autoptr (GError) error = NULL;
	g_autoptr (GOptionContext) context = g_option_context_new ("- Houji front ambient light service");
	GOptionEntry entries[] = {
		{ "config", 'c', 0, G_OPTION_ARG_FILENAME, &config, "Xiaomi light sensor configuration", "FILE" },
		{ "backlight", 'b', 0, G_OPTION_ARG_FILENAME, &st.backlight, "Backlight device directory", "DIR" },
		{ "dpms", 'd', 0, G_OPTION_ARG_FILENAME, &st.dpms, "DRM connector DPMS file", "FILE" },
		{ "verbose", 'v', 0, G_OPTION_ARG_NONE, &st.verbose, "Log every published value", NULL },
		{ NULL }
	};

	g_option_context_add_main_entries (context, entries, NULL);
	if (!g_option_context_parse (context, &argc, &argv, &error)) {
		g_printerr ("%s\n", error->message);
		return 2;
	}
	if (!g_file_get_contents (config, &json, NULL, &error) || !als_parse_coef (json, &st.coef)) {
		g_printerr ("No channel coefficients in %s%s%s\n", config, error ? ": " : "", error ? error->message : "");
		return 1;
	}
	if (!st.backlight && !(st.backlight = find_backlight ())) {
		g_printerr ("No backlight device\n");
		return 1;
	}
	if (!st.dpms)
		st.dpms = g_strdup ("/sys/class/drm/card0-DSI-1/dpms");

	st.loop = g_main_loop_new (NULL, FALSE);
	actual = g_build_filename (st.backlight, "actual_brightness", NULL);
	st.backlight_fd = open (actual, O_RDONLY | O_CLOEXEC);
	if (st.backlight_fd >= 0) {
		backlight_changed (st.backlight_fd, G_IO_PRI, NULL);
		g_unix_fd_add (st.backlight_fd, G_IO_PRI | G_IO_ERR, backlight_changed, NULL);
	} else {
		g_warning ("Cannot watch %s: %s", actual, g_strerror (errno));
	}
	g_timeout_add_seconds (STATE_POLL_SECONDS, poll_state, NULL);
	g_unix_signal_add (SIGTERM, quit, NULL);
	g_unix_signal_add (SIGINT, quit, NULL);
	ssc_sensor_new ((gchar *) "ambient_light_raw", NULL, sensor_ready, NULL);
	g_main_loop_run (st.loop);

	if (st.client)
		g_signal_handlers_disconnect_by_func (st.client, report, NULL);
	g_clear_object (&st.client);
	g_clear_object (&st.raw);
	g_main_loop_unref (st.loop);
	if (st.backlight_fd >= 0)
		close (st.backlight_fd);
	g_free (st.backlight);
	g_free (st.dpms);
	return st.status;
}
