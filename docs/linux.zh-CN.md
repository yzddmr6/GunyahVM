<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Linux 用户指南

## 创建

安装 `gunyahvm`，检查宿主并执行 `gunyahvm load`，再使用已有 SSH 公钥：

```sh
gunyahvm create ubuntu --ssh-key ~/.ssh/id_ed25519.pub --disk-gib 16 --start
gunyahvm list
gunyahvm console ubuntu
```

创建时下载所选 Ubuntu ARM64 云镜像并核对上游校验值；默认固定镜像版本见 `gunyahvm create --help`。来宾采用公钥登录，账户密码锁定，私钥不会复制到来宾。可用 `--cpus`、`--memory-mib`、`--disk-gib`、`--username` 调整资源和用户名。默认内存按宿主总内存与在线 CPU 推算，启动还检查可用内存。显式 `--image` 可配合包含匹配 `Image`、`initrd.img` 及可选 `cmdline` 的 `--boot-dir`。

## 使用

```sh
gunyahvm ssh ubuntu
gunyahvm ssh ubuntu -- 'sudo apt-get update'
gunyahvm stop ubuntu
gunyahvm start ubuntu
gunyahvm plan ubuntu
```

等 `list` 显示来宾就绪和 IP 后再 SSH。Ctrl-C 退出控制台查看器。`console --interactive` 传递终端输入，但不会解除来宾账户的密码锁定。`plan` 只显示启动参数。来宾更新内核后，下次从停止状态启动时从磁盘提取新内核；来宾内部重启不会刷新宿主上的启动文件。旧启动文件会保留。

`stop` 请求来宾关机、等待退出并清理所属 NAT 连接。来宾自行关机后也应执行。`stop --force` 可能丢失未落盘数据。监督进程中断会记录为 interrupted；残留的 crosvm 必须退出才能再启动。运行锁限制每用户一台虚拟机。NetworkManager 创建临时且归调用者所有的 TAP/shared 连接，拒绝与宿主路由重叠。

## 数据与卸载

数据在 `~/.local/share/gunyahvm/vms/NAME/`；`--store` 必须位于自己的主目录。配置、磁盘、启动文件、状态和运行日志归虚拟机所有者。应在来宾停止后备份目录；稀疏磁盘随写入增长。分享日志前检查内容，来宾输出可能含私人信息。

```sh
gunyahvm stop ubuntu
gunyahvm delete ubuntu
sudo apt purge gunyahvm
```

`delete` 永久删除指定来宾。卸载包保留主目录数据，并在 crosvm 仍运行时拒绝卸载。组留给管理员管理。重启会清除已加载模块、AppArmor 授权和包设置的运行时大页策略；不要手工卸载正在使用的 Gunyah 驱动。

## 排错

执行 `gunyahvm check`，每个失败项都有修复提示。`load` 只启用固定模块。SSH 用户必须加入 `gunyah`；活动本地座席用户不需要永久加组。当前 shell 的低 memlock 额度会由授权启动器针对新虚拟机修复。沙箱失败不会自动降级为关闭沙箱。运行期间保持宿主唤醒，宿主关机前停止来宾。
