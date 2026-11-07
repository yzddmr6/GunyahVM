<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 安全模型

## 授权

`gunyah` 系统组由管理员管理。udev 将 `/dev/gunyah` 建为 `root:gunyah 0660`，在 systemd 座席规则之前添加 `uaccess`，logind 为活动本地座席用户添加 ACL。不设置全员可写设备，也不自动永久加组。polkit 只向本地活动且有座席的主体或 `gunyah` 组成员授予三个精确的 GunyahVM 动作。其他调用者直接拒绝，不回退到管理员密码认证。SSH 用户需要组成员身份。

ACL 和资源额度属于 UID/进程，不属于终端连接：与已授权桌面用户使用同一 UID 的 SSH 进程共享该用户的自主访问权限。Linux 的 uaccess 无法把这些进程变成不同安全主体。不信任的远程登录应使用独立 Unix 账户。切换座席会撤销后续打开设备的 ACL，但不会收回已打开的设备 FD 或停止运行中的虚拟机。组成员一直被信任。

## 有限 memlock 与非特权 VMM

普通桌面用户无需重新登录或改 PAM。启动时，polkit 执行固定的 `gunyahvm-run` 助手。它只读取内核状态和 root 所有的设备上限，计算不超过 6 GiB、且不超过 MemTotal 减去 25%/1 GiB 余量的额度，设置 memlock 软硬限额，随后初始化组并丢弃全部 saved/effective/real UID 和 GID，最后才执行固定安装位置的 Python CLI。root 阶段不用用户提供的可执行文件、Python 导入目录或虚拟机文件。监督进程和 crosvm 继承有限额度，没有文件 capabilities、setuid、CAP_IPC_LOCK 或 CAP_NET_ADMIN。组 PAM 规则提供有限的会话默认值，但已有用户管理器也能通过启动器取得正确额度。

这是每进程上限，不是多用户全局内存配额。受信用户可以独立启动进程，也能在 GunyahVM 之外消耗内存；多名互不信任用户共用宿主时应由管理员另设宿主级资源策略。

## 特权助手

模块加载器不接受参数，验证实时 Gunyah 固件和宿主挂载命名空间，拒绝 initrd 及不受支持的平台插件，只在干净环境加载固定模块并核对设备归属。不依赖机型专用挂载标记，也不卸载驱动。

网络助手只接受 up/down、UUID、RFC1918 IPv4 /24 和可选固定 vhost-net 开关。调用者身份来自 polkit，不来自指定用户名。它使用宿主网络命名空间、固定命令路径和归调用者所有的 NetworkManager 配置，拒绝宿主路由重叠或不匹配的已有接口。不执行 shell，不加载任意模块。失去活动座席前先清理虚拟机网络，或返回该座席；组成员身份仍是远程管理入口。

助手使用 Python 隔离模式。VMM 使用 minijail，并显式指定 `/usr/share/gunyahvm/crosvm/empty` 为 pivot root。AppArmor 配置仅授予 minijail 所需的用户命名空间操作；unconfined 标志不构成独立文件系统沙箱。失败时不会自动使用 `--disable-sandbox`。

## 集成与来宾

Dracut 将虚拟机管理模块排除在 initrd 外，按需显式加载。tmpfiles 将 shmem THP 设为 `advise`，只影响主动请求大页的映射。chroot 内安装时跳过 live udev/sysfs/AppArmor 操作。卸载包不杀虚拟机、不卸载模块，保留用户数据和组。

来宾、ISO、磁盘及来宾控制输出均不可信；分享日志前审阅。Windows 运行测试签名驱动，没有 vTPM 和来宾 Secure Boot，本机 VNC 允许其他本地用户连接，不要转发到不受信网络。这些限制独立于 Gunyah protected 内存模式。漏洞报告见 [SECURITY](../SECURITY.zh-CN.md)。

## 参考

[Polkit 授权模型](https://www.freedesktop.org/software/polkit/docs/latest/polkit.8.html)、[Linux 资源额度](https://man7.org/linux/man-pages/man2/getrlimit.2.html) 和已安装的 systemd `73-seat-late.rules` 定义了集成边界。
