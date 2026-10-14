#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#define U32_MAX UINT32_MAX
#define IS_ALIGNED(x, a) (((x) & ((a) - 1)) == 0)
#define CAM_ERR(...) do {} while (0)
#define CAM_DBG(...) do {} while (0)
#define CAM_FRAME_HEADER_BUFFER_SIZE 64
#define CAM_FRAME_HEADER_ADDR_ALIGNMENT 256
typedef uint64_t dma_addr_t;
struct cam_cmd_buf_desc { int32_t mem_handle; uint32_t offset, size, length, type, meta_data; };
struct cam_packet_header { uint32_t op_code, size; uint64_t request_id; uint32_t flags, padding; };
struct cam_packet {
    struct cam_packet_header header;
    uint32_t cmd_buf_offset, num_cmd_buf, io_configs_offset, num_io_configs;
    uint32_t patch_offset, num_patches, kmd_cmd_buf_index, kmd_cmd_buf_offset;
    uint64_t payload[1];
};
struct cam_kmd_buf_info { int handle; uint32_t *cpu_addr; uint32_t offset, size, used_bytes; };
struct cam_isp_frame_header_info_dummy { int unused; };
struct cam_isp_prepare_hw_update_data { uint32_t frame_header_iova; uint32_t *frame_header_cpu_addr; };
struct cam_ife_hw_mgr { struct { int img_iommu_hdl, img_iommu_hdl_secure; } mgr_common; };
static struct cam_ife_hw_mgr g_ife_hw_mgr;
static uint8_t *mapping;
static size_t map_len;
static int gets, put_calls, refs, get_error, io_error;
static bool zero_addr, mutate;
static struct cam_packet *mutate_packet;
static struct cam_cmd_buf_desc *mutate_desc;
static uint64_t io_addr = 0x10000000;
static int cam_mem_get_cpu_buf(int handle, uintptr_t *addr, size_t *len) {
    gets++;
    if (get_error) return get_error;
    refs++;
    *addr = zero_addr ? 0 : (uintptr_t)mapping;
    *len = map_len;
    if (mutate) {
        mutate_packet->kmd_cmd_buf_offset = UINT32_MAX;
        mutate_desc->offset = UINT32_MAX;
        mutate_desc->size = 0;
        mutate_desc->mem_handle = 99;
    }
    return 0;
}
static void cam_mem_put_cpu_buf(int handle) { assert(handle == 1); put_calls++; refs--; assert(refs >= 0); }
static bool cam_mem_is_secure_buf(int handle) { return false; }
static int cam_mem_get_io_buf(int handle, int mmu, dma_addr_t *addr, size_t *len, void *flags) {
    if (io_error) return io_error;
    *addr = io_addr; *len = map_len; return 0;
}
