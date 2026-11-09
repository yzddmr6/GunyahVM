<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Linux 来宾

## 创建与连接

打开 GunyahVM，选择 Ubuntu 镜像目录或本地 ARM64 raw/qcow2 磁盘。CLI 使用同一核心：

```sh
gunyahvm load
gunyahvm create ubuntu --mirror official --start
gunyahvm list
gunyahvm console ubuntu
gunyahvm ssh ubuntu
```

`--mirror tuna` 与 `--mirror ustc` 选择国内下载源，校验和仍来自 Ubuntu 官方不可变构建目录。`--release YYYYMMDD` 选择构建。本地磁盘使用 `--image PATH`，自备匹配的 kernel/initrd 目录使用 `--boot-dir PATH`。默认 Ubuntu 路径从来宾磁盘提取 kernel/initrd，不导入已有 VM 配置格式。

省略 `--ssh-key` 时生成私钥。cloud-init 配置来宾控制通道及密码锁定的 SSH 账户。等 VM 的 `guest_ready` 和 `ipv4` 字段就绪后再连接。Ctrl-C 退出日志查看；Ctrl-] 退出 `console --interactive`。重连最多回放 256 KiB。

## 图形与共享

设置页只提供宿主和已安装组件允许的图形模式。3D/2D virtio-GPU 与共享内存需要配套运行组件来宾内核。例如具备能力时：

```sh
gunyahvm create desktop --guest-payload --gpu virgl --start
```

应用创建的 Ubuntu 云镜像桌面会安装最小桌面，并为所建来宾用户启用自动登录。导入磁盘须自带桌面/cloud-init 集成。

VM 停止时，可在设置页添加可写宿主目录及本机端口转发。Linux 来宾可用 `sudo mount -t 9p -o trans=virtio TAG DIRECTORY` 挂载，权限仍受宿主账户约束。vhost-net 可选，须具备内核能力和设备权限。VNC 可替代本地 Wayland 窗口，只监听 localhost。

## 停止与维护

```sh
gunyahvm stop ubuntu
gunyahvm restart ubuntu
gunyahvm delete ubuntu
```

`stop` 等待干净关机，来宾 agent 无法确认时报告失败。`stop --force` 是显式崩溃式断电。低电量、过热或宿主睡眠会发起关机；睡眠路径有有界强停回退。关闭管理窗口只断开界面，不停止 VM。更新或卸载应用前先停止 VM。

使用磁盘内核时，下次从停止状态启动会提取来宾内核更新；来宾请求 reset 时也会刷新磁盘内核启动文件。使用运行组件内核时，内核及模块版本由组件包提供。卸载软件包保留用户数据。
