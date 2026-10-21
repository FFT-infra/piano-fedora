#!/bin/sh
# 只读采集启动证据；不加载模块、不配置 gadget、不改分区或服务状态。
export PATH=/usr/sbin:/usr/bin:/bin:/sbin
section() { printf '\n===== %s =====\n' "$1"; }
run() {
    printf '$'; printf ' %s' "$@"; printf '\n'
    "$@" 2>&1 || printf 'command_exit=%s\n' "$?"
}
section identity
run uname -a
run cat /proc/cmdline
run cat /proc/mounts
run cat /proc/partitions
for path in /etc/os-release /sysroot/etc/os-release /etc/piano/kernel-release /etc/piano/boot-chain; do
    [ ! -r "$path" ] || run cat "$path"
done
section devices
for node in /sys/class/udc/* /sys/class/drm/card* /sys/class/graphics/fb*; do
    [ -e "$node" ] || continue
    printf 'node=%s\n' "$node"
    for attribute in status enabled modes state current_speed name; do
        [ ! -r "$node/$attribute" ] || run cat "$node/$attribute"
    done
done
for device in 15000000.iommu 1d84000.ufshc f204008.clock-controller ae01000.display-controller 3d00000.gpu; do
    path=/sys/bus/platform/devices/$device
    [ ! -e "$path" ] || run readlink -f "$path/driver"
done
section kernel
dmesg 2>&1 | tail -n 160
if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
    section systemd
    run systemctl is-system-running
    run systemctl --failed --no-pager
    run systemctl list-jobs --no-pager
    # 显示管理器按镜像里实际启用的那个收集。镜像现在有五档桌面
    # （GNOME/KDE/niri/Hyprland/server），写死 gdm.service 在其余档位上
    # 只会得到 "could not be found"，真正的会话问题反而看不到。
    units='graphical.target display-manager.service piano-display.service piano-touch.service piano-camera.service piano-camerad.service'
    for unit in gdm.service sddm.service plasmalogin.service greetd.service; do
        [ ! -e "/usr/lib/systemd/system/$unit" ] || units="$units $unit"
    done
    for unit in $units; do
        run systemctl show "$unit" -p ActiveState -p SubState -p Result -p ExecMainStatus -p FragmentPath -p DropInPaths
    done
    if command -v journalctl >/dev/null 2>&1; then
        section journal
        journal_args=
        for unit in gdm.service sddm.service plasmalogin.service greetd.service; do
            [ ! -e "/usr/lib/systemd/system/$unit" ] || journal_args="$journal_args -u $unit"
        done
        run journalctl -b --no-pager -n 160 $journal_args -u piano-display.service -u piano-camerad.service
    fi
fi
section evidence_boundary
printf 'Read-only report. Backlight, cursor, Fastboot OKAY and active services alone do not prove GPU acceleration or a working desktop session.\n'
