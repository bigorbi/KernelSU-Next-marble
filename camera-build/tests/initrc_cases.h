static ssize_t proxy(bool iter, char *buf, size_t size) {
    loff_t pos = physical_pos;
    if (iter) { struct iov_iter to = {buf, size}; return read_iter_proxy(NULL, &to); }
    return read_proxy(&primary, buf, size, &pos);
}
static void cleanup(void) {
    ksu_ksud_exit(); ksu_ksud_exit();
    assert(live == 0 && allocations == frees_);
    assert(overrides == reverts && gets == puts_);
    assert(successful_opens == closes);
}
int main(int argc, char **argv) {
    assert(argc == 3);
    const char *name = argv[1];
    bool iter = !strcmp(argv[2], "iter");
    orig_read = physical_read; orig_read_iter = physical_read_iter;
    assert(ksu_rc_len == 355);
    if (!strcmp(name, "original_budget")) {
        loff_t size = 58427; ksu_handle_vfs_fstat(0, &size);
        assert(size == 58782 && opens == 0);
        load_module_rc_once();
        char buf[59016]; size_t total = 0;
        while (total < (size_t)size) {
            ssize_t r = proxy(iter, buf + total, size - total); assert(r > 0); total += r;
        }
        assert(total == 58782 && module_rc_pos == 0 && live == 1);
        cleanup(); return 0;
    }
    if (!strcmp(name, "empty")) custom.inode.size = 0;
    if (!strcmp(name, "disabled")) ksu_no_custom_rc = true;
    if (!strcmp(name, "missing")) missing = true;
    if (!strcmp(name, "fallback")) watchdog_missing = true;
    if (!strcmp(name, "nonregular")) custom.inode.i_mode = S_IFDIR;
    if (!strcmp(name, "allocfail")) allocation_fail = true;
    if (!strcmp(name, "readerror")) read_result = -5;
    if (!strcmp(name, "readzero")) read_result = 0;
    if (!strcmp(name, "shortread")) read_result = 17;
    if (!strcmp(name, "unrelated")) primary.init = false;
    loff_t before = 58427, size;
    if (!strcmp(name, "negative")) before = -1;
    if (!strcmp(name, "overflow_static")) before = INT64_MAX - 354;
    if (!strcmp(name, "overflow_custom")) before = INT64_MAX - 355;
    if (!strcmp(name, "limit_exact")) before = INT64_MAX - 588;
    size = before;
    ksu_handle_vfs_fstat(!strcmp(name, "invalid_fd") ? -1 : 0, &size);
    bool filtered = !strcmp(name, "invalid_fd") || !strcmp(name, "unrelated");
    bool overflow = !strcmp(name, "negative") || !strcmp(name, "overflow_static") ||
                    !strcmp(name, "overflow_custom");
    if (filtered || overflow) {
        assert(size == before);
        if (filtered) assert(opens == 0 && overrides == 0);
        cleanup(); return 0;
    }
    if (!strcmp(name, "oversized_custom")) {
        module_rc_len = (size_t)INT64_MAX + 1;
        size = 58427; ksu_handle_vfs_fstat(0, &size); assert(size == 58427);
        cleanup(); return 0;
    }
    size_t cached = module_rc_len;
    assert(size == before + 355 + (loff_t)cached);
    if (!strcmp(name, "limit_exact")) { assert(size == INT64_MAX); cleanup(); return 0; }
    int initial_opens = opens, initial_allocations = allocations;
    /* The callback receives an underlying stat on each invocation, not its prior result. */
    for (int i = 0; i < 3; i++) {
        loff_t repeated = 58427; ksu_handle_vfs_fstat(0, &repeated); assert(repeated == size);
    }
    load_module_rc_once(); load_module_rc_once();
    assert(opens == initial_opens && allocations == initial_allocations);
    if (!strcmp(name, "exit_cleanup")) { assert(live == 1); cleanup(); return 0; }
    if (!strcmp(name, "stat_then_change")) {
        custom.inode.size = 1; read_result = 1; missing = true;
        load_module_rc_once(); assert(module_rc_len == 233 && opens == initial_opens);
    }
    if (!strcmp(name, "read_error")) {
        physical_error = true; char b[16]; assert(proxy(iter, b, sizeof(b)) == -5);
        assert(ksu_rc_pos == 0 && module_rc_pos == 0); cleanup(); return 0;
    }
    if (!strcmp(name, "zero_count")) {
        char b[1]; assert(proxy(iter, b, 0) == 0);
        assert(physical_pos == 0 && ksu_rc_pos == 0 && module_rc_pos == 0);
        cleanup(); return 0;
    }
    if (!strcmp(name, "module_copy_retry")) {
        physical_pos = 58427; ksu_rc_pos = ksu_rc_len;
        copy_fail = true; char b[16];
        assert(proxy(iter, b, sizeof(b)) == 0 && module_rc_pos == 0 && live == 1);
        assert(proxy(iter, b, sizeof(b)) == 16 && module_rc_pos == 16 && live == 1);
        cleanup(); return 0;
    }
    if (!strcmp(name, "copy_retry")) {
        physical_pos = 58427; copy_fail = true; char b[16];
        assert(proxy(iter, b, sizeof(b)) == 0 && ksu_rc_pos == 0);
        assert(proxy(iter, b, sizeof(b)) == 16 && ksu_rc_pos == 16);
        cleanup(); return 0;
    }
    char buf[59017]; memset(buf, 0x7f, sizeof(buf));
    size_t total = 0, budget = 58427 + 355 + cached;
    while (total < budget) {
        size_t count = budget - total;
        if (count > 29) count = 29;
        ssize_t r = proxy(iter, buf + total, count);
        assert(r > 0 && (size_t)r <= count); total += r;
    }
    assert(!memcmp(buf + 58427, KERNEL_SU_RC, 355));
    for (size_t i = 0; i < cached; i++) assert(buf[58782 + i] == 'C');
    assert((unsigned char)buf[budget] == 0x7f);
    assert(module_rc_buf == NULL && module_rc_len == 0 && live == 0);
    assert(proxy(iter, buf + budget, 1) == 0);
    assert(proxy(iter, buf + budget, 1) == 0);
    load_module_rc_once(); assert(module_rc_buf == NULL && opens == initial_opens);
    cleanup(); return 0;
}
