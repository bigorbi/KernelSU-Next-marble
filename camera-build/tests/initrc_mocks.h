/* Host-only adapters. The code under test is extracted from pinned GPL-2.0 KSU. */
#define _GNU_SOURCE
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <fcntl.h>
_Static_assert(sizeof(loff_t) == 8 && sizeof(size_t) == 8, "64-bit host required");
#define CONFIG_KSU_SUSFS 1
#define __user
#define __exit
#define GFP_KERNEL 0
#define KERNEL_SU_DOMAIN "ksu"
#define KSUD_PATH "/data/adb/ksud"
#define S64_MAX INT64_MAX
#define check_add_overflow(a, b, out) __builtin_add_overflow(a, b, out)
#define pr_info(...) ((void)0)
#define pr_warn(...) ((void)0)
#define pr_err(...) ((void)0)
#define IS_ERR(p) ((intptr_t)(p) < 0 && (intptr_t)(p) > -4096)
#define PTR_ERR(p) ((long)(p))
struct inode { mode_t i_mode; loff_t size; };
struct file { struct inode inode; bool init; };
struct cred { int unused; };
struct file_operations { int unused; };
struct kiocb { int unused; };
struct iov_iter { char *buf; size_t count; };
static struct file primary = {{S_IFREG, 58427}, true};
static struct file custom = {{S_IFREG, 233}, false};
static struct cred credentials;
static const struct cred *ksu_cred = &credentials;
static bool ksu_no_custom_rc, missing, watchdog_missing, allocation_fail, copy_fail;
static ssize_t read_result = 233;
static int gets, puts_, opens, successful_opens, closes, overrides, reverts, allocations, frees_, live;
static size_t physical_pos;
static bool physical_error;
static struct file *fget(int fd) { if (fd < 0) return NULL; gets++; return &primary; }
static void fput(struct file *f) { assert(f == &primary); puts_++; }
static bool is_init_rc(struct file *f) { return f->init; }
static struct file *filp_open(const char *path, int flags, int mode) {
    opens++;
    if (missing || (watchdog_missing && strstr(path, "watchdog")))
        return (struct file *)(intptr_t)-2;
    successful_opens++; return &custom;
}
static void filp_close(struct file *f, void *unused) { assert(f == &custom); closes++; }
static struct inode *file_inode(struct file *f) { return &f->inode; }
static loff_t i_size_read(struct inode *i) { return i->size; }
static const struct cred *override_creds(const struct cred *c) { overrides++; return c; }
static void revert_creds(const struct cred *c) { assert(c == ksu_cred); reverts++; }
static void *kvmalloc(size_t size, int flags) {
    if (allocation_fail) return NULL;
    void *p = malloc(size); assert(p); allocations++; live++; return p;
}
static void kvfree(void *p) { if (p) { frees_++; live--; assert(live >= 0); free(p); } }
static ssize_t kernel_read(struct file *f, void *buf, size_t size, loff_t *pos) {
    assert(f == &custom);
    if (read_result > 0) { assert((size_t)read_result <= size); memset(buf, 'C', read_result); }
    return read_result;
}
static size_t copy_to_user(char *out, const void *in, size_t size) {
    if (copy_fail) { copy_fail = false; return size; }
    memcpy(out, in, size); return 0;
}
static size_t copy_to_iter(const void *in, size_t size, struct iov_iter *out) {
    if (copy_fail) { copy_fail = false; return 0; }
    if (size > out->count) size = out->count;
    memcpy(out->buf, in, size); out->buf += size; out->count -= size; return size;
}
static ssize_t physical_read(struct file *f, char *buf, size_t count, loff_t *pos) {
    if (physical_error) return -5;
    size_t size = 58427 - physical_pos;
    if (size > count) size = count;
    /* Exercise multiple short underlying reads as well as virtual append reads. */
    if (size > 7919) size = 7919;
    memset(buf, 'P', size); physical_pos += size; *pos += size; return size;
}
static ssize_t physical_read_iter(struct kiocb *io, struct iov_iter *to) {
    loff_t pos = physical_pos;
    ssize_t r = physical_read(&primary, to->buf, to->count, &pos);
    if (r > 0) { to->buf += r; to->count -= r; } return r;
}
