<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 安全模型

## 授权

udev 将 `/dev/gunyah` 和 `/dev/vhost-net` 设为 `root:gunyah 0660`，并添加 logind uaccess 标记。活动本地座席用户获得 ACL；管理员可以显式将远程账户加入组。设备不设为全员可写。ACL 以 UID 为边界，与授权桌面共用 UID 的 SSH 进程共享该用户的自主访问权限。切换座席撤销后续打开权限，不回收已打开的 FD。

唯一的隔离 Python polkit 助手提供 `load`、`net-up`、`net-down`、`run`。加载驱动、建网络和启动要求活动本地座席或 `gunyah` 组成员身份。锁屏后，已识别的普通调用者仍能清理网络，但只能删除由该调用者与 VM UUID 派生的配置。助手核对 mount 和 network namespace，不以 root 读取用户 VM 配置或磁盘。

## 普通用户运行

启动器按总内存设置有限 memlock，初始化附加组，丢弃 real/effective/saved GID 和 UID，随后进入固定安装的监督入口。VMM、renderer、监督进程均使用同一普通账户。不安装 setuid 应用、文件 capabilities、CAP_IPC_LOCK 或宿主 CAP_NET_ADMIN。每进程上限与每用户 VM 锁不是全系统多用户内存配额。

普通模式保留 crosvm minijail 设备沙箱。当前 GuestAccept/共享内存接口需要单进程，选择这些模式时显式关闭设备沙箱，但仍以普通用户运行。沙箱失败不自动切换模式。AppArmor 只为固定安装的 crosvm 授予 minijail 所需用户命名空间操作，unconfined profile 不构成独立文件系统沙箱。

## 网络、共享与来宾

TAP 名、MAC、NetworkManager UUID 同时从 VM UUID 和调用者 UID 派生。助手只接受规范 UUID、RFC1918 /24 网络和固定加速选项，拒绝冲突路由及不匹配的既有接口。命令使用参数数组与固定环境。用户端口转发仅绑定 loopback。

Linux 9p 共享按宿主用户权限提供访问。随附后端导出可写目录，不伪装实现 crosvm 没有的只读选项；请据此选择目录。来宾控制消息有大小上限，IP 必须属于 VM 子网。SSH 使用每个 VM 独立的 known_hosts，拒绝变化的主机密钥。

Windows 使用测试签名驱动和 Test Mode，没有 vTPM 或来宾 Secure Boot。专用控制账户接受应用密钥，关闭 SSH 密码认证，防火墙仅允许宿主网关访问。RDP 使用 Windows 凭据。其他本地账户可以访问 loopback VNC，不要将其转发到不可信网络。来宾镜像、ISO 和日志均作为不可信输入。

## 集成与回收

模块加载器只请求运行内核已安装且尚未加载的 VM 管理/功能模块及可选 vhost-net，依赖交给 modprobe，不猜测可选平台或 trusted-VM hook。tmpfiles 启用 shmem THP `advise`。睡眠使用 logind delay inhibitor，为有界来宾关机及清理留出时间；强停记录为失败。chroot 安装跳过 live udev/sysfs/AppArmor 改动。软件包变更会拒绝活动的已安装 VMM/监督进程，保留用户数据，不卸载宿主模块。

安全问题通过 [SECURITY](../SECURITY.zh-CN.md) 报告。
