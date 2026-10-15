#!/system/bin/sh
# Run explicitly through KernelSU root; stdout only, no phone writes.
echo '=== identity and kernel ==='
id
uname -r
getprop ro.product.device
getprop ro.boot.slot_suffix
getprop sys.boot_completed
getenforce
echo '=== module loading state ==='
cat /proc/sys/kernel/modules_disabled
grep '^camera ' /proc/modules
ls -ld /sys/module/camera /sys/module/camera/holders
getprop vendor.all.modules.ready
getprop init.svc.vendor.modprobe
echo '=== original loader evidence ==='
sha256sum /proc/1/root/vendor_dlkm/lib/modules/camera.ko /vendor/bin/vendor_modprobe.sh /vendor/etc/init/hw/init.qti.kernel.rc
ls -ldZ /vendor/lib/modules /vendor_dlkm/lib/modules/modules.blocklist
cat /vendor_dlkm/lib/modules/modules.blocklist
echo '=== early-hook collision and recovery paths ==='
ls -ldZ /metadata /metadata/ksu /metadata/watchdog/ksu /data/adb/modules
for rc in /metadata/ksu/modules.rc /metadata/watchdog/ksu/modules.rc; do
    if [ -e "$rc" ] || [ -L "$rc" ]; then
        echo "EXISTING RC: $rc; do not overwrite"
        ls -lZ "$rc"
        sha256sum "$rc"
    fi
done
echo '=== existing kernel flags ==='
cat /proc/sys/kernel/tainted
if [ -e /sys/module/module/parameters/sig_enforce ]; then cat /sys/module/module/parameters/sig_enforce; fi
