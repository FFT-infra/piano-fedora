#!/usr/bin/env python3
"""Run the patched discovery callback on a real GLib context with late QRTR data."""
import argparse
from pathlib import Path
import os
import re
import shlex
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PRELUDE = r'''
#include <gio/gio.h>
#include <unistd.h>
typedef GObject SSCClient;
typedef int QrtrBus;
typedef int QrtrNode;
typedef struct { QrtrBus *bus; gint64 ssc_lookup_deadline; } SSCClientPrivate;
static SSCClientPrivate state;
static GList node_list;
static gboolean available, completed, success;
static GError *result_error;
#define QMI_SERVICE_SSC 123
#define SSC_CLIENT_ERROR_LOOKUP 1
static SSCClientPrivate *ssc_client_get_instance_private(SSCClient *s) { return &state; }
static GQuark ssc_client_error_quark(void) { return g_quark_from_static_string("ssc-test"); }
static QrtrBus *qrtr_bus_new_finish(GAsyncResult *r, GError **e) { return (QrtrBus *)&state; }
static GList *qrtr_bus_peek_nodes(QrtrBus *b) { return available ? &node_list : NULL; }
static int qrtr_node_lookup_port(QrtrNode *n, int service) { return 1; }
static void device_new_ready(GObject *s, GAsyncResult *r, gpointer p) {}
static void qmi_device_new_from_node(QrtrNode *n, GCancellable *c, GAsyncReadyCallback cb, gpointer p) {
    GTask *task = p;
    g_task_return_boolean(task, TRUE);
    g_object_unref(task);
}
'''
HARNESS = r'''
static void done(GObject *s, GAsyncResult *r, gpointer p) {
    success = g_task_propagate_boolean(G_TASK(r), &result_error);
    completed = TRUE;
}
static gboolean announce(gpointer p) { available = TRUE; return G_SOURCE_REMOVE; }
static gboolean expire(gpointer p) { state.ssc_lookup_deadline = 0; return G_SOURCE_REMOVE; }
static gboolean cancel(gpointer p) { g_cancellable_cancel(p); return G_SOURCE_REMOVE; }
static void run_case(int scenario) {
    GMainContext *context = g_main_context_new();
    GCancellable *cancellable = g_cancellable_new();
    GObject *client = g_object_new(G_TYPE_OBJECT, NULL);
    g_main_context_push_thread_default(context);
    available = scenario == 0;
    completed = success = FALSE;
    result_error = NULL;
    node_list.data = &state;
    GTask *task = g_task_new(client, cancellable, done, NULL);
    g_task_set_task_data(task, client, NULL);
    GSource *event = g_timeout_source_new(20);
    if (scenario == 1) g_source_set_callback(event, announce, NULL, NULL);
    else if (scenario == 2) g_source_set_callback(event, expire, NULL, NULL);
    else if (scenario == 3) g_source_set_callback(event, cancel, cancellable, NULL);
    if (scenario) g_source_attach(event, context);
    g_source_unref(event);
    gint64 start = g_get_monotonic_time();
    bus_new_ready(NULL, NULL, task);
    g_assert_cmpint(g_get_monotonic_time() - start, <, 500000);
    while (!completed && g_get_monotonic_time() - start < 2000000) {
        g_main_context_iteration(context, FALSE);
        g_usleep(1000);
    }
    g_assert_true(completed);
    if (scenario < 2) { g_assert_true(success); g_assert_no_error(result_error); }
    if (scenario == 2) g_assert_error(result_error, ssc_client_error_quark(), SSC_CLIENT_ERROR_LOOKUP);
    if (scenario == 3) g_assert_error(result_error, G_IO_ERROR, G_IO_ERROR_CANCELLED);
    g_clear_error(&result_error);
    g_main_context_pop_thread_default(context);
    g_object_unref(client);
    g_object_unref(cancellable);
    g_main_context_unref(context);
}
int main(void) { for (int i = 0; i < 4; i++) run_case(i); return 0; }
'''


def patched_callback(patch, original_source=None):
    # Reconstruct the original touched lines, then apply the real patch with
    # zero fuzz. This keeps the fixture tied to the patch's actual C code.
    original = []
    active = False
    for line in patch.read_text().splitlines(True):
        hunk = re.match(r'@@ -(\d+)', line)
        if hunk:
            active = True
            position = int(hunk[1]) - 1
            original.extend(['\n'] * (position - len(original)))
        elif active and line.startswith((' ', '-')) and not line.startswith('---'):
            original.append(line[1:])
    with tempfile.TemporaryDirectory(prefix='ssc-source-', dir=os.environ.get('TMPDIR')) as directory:
        work = Path(directory)
        source = work / 'src/libssc-client.c'
        source.parent.mkdir()
        source.write_text(original_source.read_text() if original_source else ''.join(original))
        subprocess.run(['patch', '--batch', '--fuzz=0', '-p1', '-i', str(patch)],
                       cwd=work, check=True, capture_output=True)
        body = source.read_text()
    match = re.search(r'static (?:gboolean\nfind_ssc_node|void\nbus_new_ready)', body)
    assert match, 'patched QRTR discovery callback missing'
    return body[match.start():body.index('\nstatic void\nssc_client_init', match.start())]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--patch', type=Path, default=ROOT / 'packages/libssc/wait_for_qmi_service.patch')
    parser.add_argument('--source', type=Path, help='optional pinned source for comparing an older patch')
    args = parser.parse_args()
    pkg = '/usr/bin/pkg-config' if Path('/usr/bin/pkg-config').exists() else 'pkg-config'
    flags = shlex.split(subprocess.check_output([pkg, '--cflags', '--libs', 'gio-2.0'], text=True))
    compiler = '/usr/bin/gcc' if Path('/usr/bin/gcc').exists() else shutil.which('cc')
    with tempfile.TemporaryDirectory(prefix='ssc-retry-', dir=os.environ.get('TMPDIR')) as directory:
        work = Path(directory)
        source = work / 'retry.c'
        source.write_text(PRELUDE + patched_callback(args.patch.resolve(), args.source) + HARNESS)
        subprocess.run([compiler, '-Werror=implicit-function-declaration', str(source),
                        '-o', str(work / 'retry'), *flags], check=True, capture_output=True)
        subprocess.run([work / 'retry'], check=True, timeout=12,
                       env={**os.environ, 'G_DEBUG': 'fatal-warnings'})
    print('OK: immediate discovery, delayed announcement, bounded timeout and cancellation')


if __name__ == '__main__':
    main()
