static unsigned long cases, successes, frames;
static struct cam_packet *packet;
static struct cam_cmd_buf_desc *desc;
static void setup(uint32_t offset, uint32_t size, uint32_t length, uint32_t kmd, size_t mapped) {
    memset(packet, 0, 128);
    packet->header.size = offsetof(struct cam_packet, payload) + sizeof(*desc);
    packet->num_cmd_buf = 1;
    packet->kmd_cmd_buf_offset = kmd;
    desc = (void *)&packet->payload;
    *desc = (struct cam_cmd_buf_desc){ .mem_handle = 1, .offset = offset, .size = size, .length = length };
    map_len = mapped;
    gets = put_calls = refs = get_error = io_error = 0;
    zero_addr = mutate = false;
    io_addr = 0x10000000;
}
/* Independent interval oracle using widened endpoints, not the driver's subtraction sequence. */
static bool oracle(uint32_t o, uint32_t s, uint32_t l, uint32_t k, size_t n) {
    uint64_t end = (uint64_t)o + s, start = (uint64_t)o + k;
    return (o % 4 == 0 && s % 4 == 0 && k % 4 == 0 && l <= k && k < s &&
            end <= UINT32_MAX && end <= n && start < end);
}
static void check(int expected, bool write_tail) {
    struct cam_kmd_buf_info out, sentinel;
    memset(&out, 0xa5, sizeof(out)); sentinel = out;
    uint32_t o = desc->offset, s = desc->size, k = packet->kmd_cmd_buf_offset;
    int rc = cam_packet_util_get_kmd_buffer(packet, &out);
    cases++;
    assert((rc == 0) == (expected == 0));
    if (!rc) {
        successes++;
        assert(refs == 1 && gets == 1 && put_calls == 0);
        assert(out.handle == 1 && out.offset == (uint64_t)o + k && out.size == s - k && !out.used_bytes);
        assert((uint8_t *)out.cpu_addr == mapping + o + k);
        assert((uint64_t)out.offset + out.size <= map_len);
        if (write_tail) {
            assert(map_len <= 600000);
            memset(mapping, 0x73, map_len + 16);
            for (size_t i = 0; i < out.size / 4; i++) out.cpu_addr[i] = 0xabababab;
            for (size_t i = 0; i < out.offset; i++) assert(mapping[i] == 0x73);
            for (size_t i = (size_t)out.offset + out.size; i < map_len + 16; i++) assert(mapping[i] == 0x73);
        }
        cam_mem_put_cpu_buf(out.handle);
    } else {
        assert(!memcmp(&out, &sentinel, sizeof(out)));
        assert(refs == 0);
        assert(put_calls == (gets && !get_error ? 1 : 0));
    }
}
static uint32_t seed = 0x593f5638;
static uint32_t random32(void) { seed ^= seed << 13; seed ^= seed >> 17; seed ^= seed << 5; return seed; }
int main(void) {
    mapping = aligned_alloc(16, 600032);
    packet = calloc(1, 128);
    assert(mapping && packet);
    _Static_assert(offsetof(struct cam_packet, payload) == 56, "UAPI layout");
    _Static_assert(sizeof(struct cam_cmd_buf_desc) == 24, "UAPI descriptor");
    /* The log supplies the absolute descriptor offset as well as remaining length. */
    setup(461440, 32948, 48, 3272, 495616);
    struct cam_kmd_buf_info old;
    assert(original_get_kmd_buffer(packet, &old) == -EINVAL && !refs && put_calls == 1);
    setup(461440, 32948, 48, 3272, 495616); check(0, true);
    setup(461440, 32948, 48, 2104, 495616); check(0, true);
    setup(0, 16, 0, 20, 128);
    assert(!original_get_kmd_buffer(packet, &old) && old.size == UINT32_MAX - 3);
    cam_mem_put_cpu_buf(old.handle);
    setup(0, 16, 0, 20, 128); check(-EINVAL, false);
    setup(0, 16, 8, 4, 128);
    assert(!original_get_kmd_buffer(packet, &old)); cam_mem_put_cpu_buf(old.handle);
    setup(0, 16, 8, 4, 128); check(-EINVAL, false);
    setup(0, 16, 4, 4, 16); check(0, true); /* exact fit */
    setup(4, 8, 4, 4, 12); check(0, true); /* one dword */
    setup(0, 16, 4, 4, 15); check(-EINVAL, false);
    setup(0, 16, 0, 0, 16); check(0, true); /* fully empty command */
    setup(0, 16, 4, 16, 128); check(-EINVAL, false);
    setup(0, 16, 17, 4, 128); check(-EINVAL, false);
    setup(1, 16, 4, 4, 128); check(-EINVAL, false);
    setup(0, 17, 4, 4, 128); check(-EINVAL, false);
    setup(0, 16, 4, 5, 128); check(-EINVAL, false);
    setup(UINT32_MAX-3, 8, 0, 4, (size_t)UINT32_MAX+64); check(-EINVAL, false);
    setup(0, 0, 0, 0, 128); check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); desc->mem_handle = -1; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); desc->mem_handle = 0; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->num_cmd_buf = 0; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->kmd_cmd_buf_index = UINT32_MAX; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->kmd_cmd_buf_index = 1; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->header.size = 55; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->header.size--; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->cmd_buf_offset = 1; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->cmd_buf_offset = UINT32_MAX-3; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); packet->num_cmd_buf = UINT32_MAX; check(-EINVAL, false);
    setup(0, 16, 4, 4, 128); get_error = -EPERM; check(-EPERM, false);
    setup(0, 16, 4, 4, 128); zero_addr = true; check(-ENOMEM, false);
    setup(0, 16, 4, 4, 0); check(-ENOMEM, false);
    setup(0, 16, 4, 4, 128); mutate = true; mutate_packet = packet; mutate_desc = desc; check(0, true);
    assert(cam_packet_util_get_kmd_buffer(NULL, &old) == -EINVAL);
    assert(cam_packet_util_get_kmd_buffer(packet, NULL) == -EINVAL);
    setup(0, 16, 4, 4, 128);
    packet->cmd_buf_offset = 4;
    packet->header.size += 4;
    struct cam_cmd_buf_desc saved = *desc;
    desc = (void *)((uint8_t *)&packet->payload + 4); *desc = saved;
    check(0, true);
    setup(0, 16, 4, 4, 128);
    packet->num_cmd_buf = 2; packet->kmd_cmd_buf_index = 1;
    packet->header.size += sizeof(*desc);
    saved = *desc; desc++; *desc = saved;
    check(0, true);
    setup(0, 16, 4, 4, 128);
    packet->header.size = UINT32_MAX;
    packet->num_cmd_buf = UINT32_MAX; check(-EINVAL, false);
    /* Exhaust small fields, including every byte alignment and exact endpoints. */
    for (uint32_t o=0; o<9; o++) for (uint32_t s=0; s<21; s++)
      for (uint32_t l=0; l<23; l++) for (uint32_t k=0; k<23; k++)
        for (size_t n=0; n<34; n++) {
            setup(o,s,l,k,n); check(oracle(o,s,l,k,n) ? 0 : -EINVAL, false);
        }
    /* Deterministic valid/invalid interval fuzz and adversarial full-width integers. */
    for (unsigned i=0; i<200000; i++) {
        uint32_t o=random32()%128, s=random32()%512, l=random32()%512, k=random32()%512;
        size_t n=random32()%1024;
        if (i%2 == 0) { o &= ~3U; s &= ~3U; k &= ~3U; l %= (k+1); }
        setup(o,s,l,k,n); check(oracle(o,s,l,k,n) ? 0 : -EINVAL, true);
        o=random32(); s=random32(); l=random32(); k=random32(); n=random32();
        if (oracle(o,s,l,k,n)) continue; /* Huge valid allocations are not mocked. */
        setup(o,s,l,k,n); check(oracle(o,s,l,k,n) ? 0 : -EINVAL, false);
    }
    /* Run the real frame reservation function for all four-byte alignments. */
    for (unsigned alignment=0; alignment<256; alignment+=4)
      for (unsigned capacity=4; capacity<=384; capacity+=4) {
        setup(0, capacity, 0, 0, 1024);
        struct cam_kmd_buf_info buf={1,(uint32_t *)mapping,0,capacity,0};
        struct cam_isp_prepare_hw_update_data output={0};
        io_addr = 0x10000000 + alignment;
        uint32_t pad = alignment ? 256-alignment : 0;
        int rc=cam_ife_mgr_util_insert_frame_header(&buf,&output); frames++;
        assert((rc==0) == (capacity>=pad+64));
        if (!rc) {
            assert(buf.used_bytes==pad+64 && buf.offset==pad+64 && buf.used_bytes<=buf.size);
            assert(output.frame_header_cpu_addr==(uint32_t *)(mapping+pad));
            memset(output.frame_header_cpu_addr,0xaa,64);
        } else assert(!buf.used_bytes && !buf.offset && !output.frame_header_cpu_addr);
      }
    struct cam_isp_prepare_hw_update_data output={0};
    struct cam_kmd_buf_info buf={1,(uint32_t *)mapping,0,384,0};
    map_len=383; assert(cam_ife_mgr_util_insert_frame_header(&buf,&output)==-EINVAL);
    map_len=1024; io_addr=UINT64_C(0x100000000); assert(cam_ife_mgr_util_insert_frame_header(&buf,&output)==-EINVAL);
    io_addr=UINT32_MAX-3; buf.offset=8; assert(cam_ife_mgr_util_insert_frame_header(&buf,&output)==-EINVAL);
    buf.offset=0; assert(cam_ife_mgr_util_insert_frame_header(&buf,&output)==-EINVAL);
    io_addr=0x10000000; buf.used_bytes=4; assert(cam_ife_mgr_util_insert_frame_header(&buf,&output)==-ENOMEM);
    io_error=-EIO; assert(cam_ife_mgr_util_insert_frame_header(&buf,&output)==-EIO);
    printf("PASS: %lu interval cases; %lu accepted; %lu frame capacity cases; mapping ownership, malformed packet tables, mutation snapshots, original regression and canaries passed\n",cases,successes,frames);
    free(mapping); free(packet);
    return 0;
}
