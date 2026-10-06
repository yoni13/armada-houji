/* SPDX-License-Identifier: BSD-3-Clause
 * Small ABI for the QTEELS relay. All license decisions remain in stock QTEE.
 */
#include <pthread.h>
#include <stdint.h>
#include <string.h>
#include <sys/ioctl.h>
#include "qcomtee_object.h"
#include "qcomtee_object_types.h"

static struct qcomtee_object *manager;

static void *callbacks(void *root)
{
	while (!qcomtee_object_process_one(root)) {}
	return NULL;
}

/* Process lifetime owns the namespace and its callback thread. */
int houji_qtee_init(void)
{
	struct qcomtee_object *root, *credentials, *env;
	struct qcomtee_param args[2] = {0};
	qcomtee_result_t result = -1;
	pthread_t thread;
	uint32_t uid = 119;
	int rc;

	if (manager)
		return 0;
	root = qcomtee_object_root_init("/dev/tee0", ioctl, NULL, NULL);
	if (!root || pthread_create(&thread, NULL, callbacks, root))
		return -1;
	pthread_detach(thread);
	if (qcomtee_object_credentials_init(root, &credentials))
		return -1;
	args[0].attr = QCOMTEE_OBJREF_INPUT;
	args[0].object = credentials;
	args[1].attr = QCOMTEE_OBJREF_OUTPUT;
	rc = qcomtee_object_invoke(root, 2, args, 2, &result);
	if (rc || result)
		return rc ? rc : (int32_t)result;
	env = args[1].object;
	args[0].attr = QCOMTEE_UBUF_INPUT;
	args[0].ubuf = (struct qcomtee_ubuf){&uid, sizeof(uid)};
	args[1].attr = QCOMTEE_OBJREF_OUTPUT;
	args[1].object = NULL;
	rc = qcomtee_object_invoke(env, 0, args, 2, &result);
	if (!rc && !result)
		manager = args[1].object;
	return rc ? rc : (int32_t)result;
}

int houji_qtee_check(unsigned secure, void *input, size_t input_size,
		     void *output, size_t *output_size)
{
	struct qcomtee_param args[2] = {
		{.attr = QCOMTEE_UBUF_INPUT, .ubuf = {input, input_size}},
		{.attr = QCOMTEE_UBUF_OUTPUT, .ubuf = {output, *output_size}},
	};
	qcomtee_result_t result = -1;
	int rc;
	if (!manager || secure > 1 || input_size > 1024 || *output_size > 1024)
		return -1;
	rc = qcomtee_object_invoke(manager, secure ? 4 : 7, args, 2, &result);
	*output_size = rc || result ? 0 : args[1].ubuf.size;
	return rc ? rc : (int32_t)result;
}

int houji_qtee_ready(void)
{
	unsigned char buffer[4096];
	struct qcomtee_param arg = {
		.attr = QCOMTEE_UBUF_OUTPUT, .ubuf = {buffer, sizeof(buffer)},
	};
	qcomtee_result_t result = -1;
	int rc;
	if (!manager)
		return -1;
	/* Read installed license serials without disclosing them to the caller. */
	rc = qcomtee_object_invoke(manager, 6, &arg, 1, &result);
	/* No identifiers escape this function, including on a failed read. */
	explicit_bzero(buffer, sizeof(buffer));
	return rc ? rc : (int32_t)result;
}
