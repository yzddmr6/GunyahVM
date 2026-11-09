<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Linux 来宾

本文说明如何选择 Linux 镜像、使用图形界面与 SSH、共享文件，以及关闭虚拟机。

## 创建第一台虚拟机

使用活动本地桌面会话中的普通账户。点击应用标题栏的显示器图标打开**宿主信息**，再点**加载驱动**。此操作加载当前宿主内核已安装的虚拟机模块，不能补上缺失的内核支持。对应的命令行为 `gunyahvm load`。

点击 **+**，选择 **Ubuntu cloud image** 下载目录中的镜像，或选择 **Linux ARM64 disk image** 使用本地 raw/qcow2 磁盘。本地镜像须包含已安装的 ARM64 Linux 系统，不能是安装 ISO；应用的账户和来宾控制设置需要 cloud-init。图形窗口还需要来宾桌面。Ubuntu 云镜像方式在选择图形模式后会安装最小桌面，并启用来宾自动登录。

选择 CPU、内存和磁盘大小，设置图形模式，点击**创建**，再选中虚拟机并点击**启动**。命令行共用同一核心：

```sh
gunyahvm load
gunyahvm create ubuntu --mirror official --start
gunyahvm list
```

使用本地磁盘时添加 `--image /path/to/linux.qcow2`，应用会将磁盘复制到自己的虚拟机存储中。`--mirror tuna` 和 `--mirror ustc` 选择替代下载源，镜像校验值仍取自 Ubuntu 官方构建目录。`--release YYYYMMDD` 选择目录中的构建版本。默认从来宾磁盘提取启动文件；本地镜像可通过 `--boot-dir PATH` 指定匹配的内核与 initrd 目录。

## 连接来宾

```sh
gunyahvm console ubuntu
gunyahvm ssh ubuntu
```

cloud-init 配置来宾控制通道和锁定密码的 SSH 账户。未指定 `--ssh-key` 时，应用会生成 SSH 密钥。等 `gunyahvm list` 中出现 `guest_ready` 和 `ipv4` 后再连接。导入的镜像如果缺少所需来宾设置，就不会报告就绪，也不能使用这些 SSH 凭据登录。

Ctrl-C 退出控制台日志查看。使用 `gunyahvm console ubuntu --interactive` 时，Ctrl-] 断开输入连接。重新连接最多回放 256 KiB 控制台输出。

## 选择图形模式

在已停止虚拟机的**设置**中，选择 **Software** 使用软件显示，或选择 **OpenGL (virgl)** 使用 GL 加速。可选项由宿主和运行组件能力决定。virgl 和 virtio-GPU 2D 需要启用**使用配套来宾内核**，以使用匹配的内核与模块。例如：

```sh
gunyahvm create desktop --guest-payload --gpu virgl --start
```

本机窗口需要 Wayland 会话。导入的磁盘须提供适合所选显示模式的桌面，仅修改图形设置不会安装桌面。

如需用 VNC 代替本机窗口，在设置中启用 **VNC**，用客户端连接 `127.0.0.1` 和设置的端口（默认 `5900`）。**VNC 不验证连接者身份，同一宿主上的其他账户也能查看和控制来宾。** 如果不信任这些用户，请保持关闭。监听回环地址阻止的是远程直接连接，不是本机其他账户访问。

## 网络与共享文件夹

**Shared NAT** 让来宾通过宿主联网，**Disconnected** 关闭虚拟网络。虚拟机停止时，可在**本机端口转发**中添加 TCP/UDP 规则，将来宾服务映射到宿主回环端口。其他本机用户也能访问这些转发的服务，因此仍须保留来宾服务的身份验证。可选的 vhost-net 需要宿主内核支持和设备访问权限。

在**共享文件夹**中添加目录和标签，保存设置后启动虚拟机。在来宾内创建挂载点并挂载标签，例如标签为 `shared` 时：

```sh
sudo mkdir -p /mnt/shared
sudo mount -t 9p -o trans=virtio shared /mnt/shared
```

**共享目录可写。** 来宾能在宿主账户权限范围内修改或删除其中的文件，此后端不提供只读导出。请使用专门的交换目录，不要共享整个主目录。访问边界见[安全模型](security.zh-CN.md)。

## 关机、更新与数据

```sh
gunyahvm stop ubuntu
gunyahvm restart ubuntu
```

`stop` 请求来宾正常关机并等待退出。**`stop --force` 相当于断电，可能丢失数据。** 来宾关机失败时会报告失败。低电量、过热或宿主睡眠也会触发关机；睡眠等待超时后可能强制终止。关闭管理器只会断开管理界面，重新打开应用即可继续管理运行中的来宾。

升级或卸载应用前关闭全部虚拟机。版本通知只打开下载页面，不自动安装软件包。可在**应用设置**中关闭检查，或以 `GUNYAHVM_NO_UPDATE_CHECK=1` 启动。检查使用 24 小时缓存，不向 GitHub 发送设备信息。

虚拟机数据位于 `~/.local/share/gunyahvm/`，磁盘在 `vms/` 下，生成的密钥在 `keys/` 下。备份此目录前先关闭虚拟机。卸载软件包会保留这些数据。**`gunyahvm delete ubuntu` 会删除已停止的虚拟机及其磁盘**，请先复制需要保留的数据。

使用磁盘内启动文件时，下次启动或来宾请求重启会从来宾磁盘提取更新后的内核。启用**使用配套来宾内核**时，内核和模块则来自已安装的 GunyahVM 组件包。

温度保护读取运行内核 sysfs 中所有定义了 passive 温控点的热区。任一热区达到其 passive 温度时拒绝启动，并提示设备正在降频；运行中达到该热区的 hot 温度时请求正常关机，没有 hot 时使用 critical 温度减 5°C。超过既有关机期限仍未退出时才强制停止。提示和状态记录包含触发热区、温度及温控点；电量保护保持不变。
