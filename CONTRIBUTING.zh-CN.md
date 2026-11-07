<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 贡献

## 修改

较大改动先在 issue 讨论。Gunyah 修复应保持小范围，解释用户可见行为并加入有意义的离线回归测试。执行 `python3 tools/lint.py`、`python3 -m pytest -q tools/tests` 和离线打包器。同步更新中英文文档。不要提交设备身份信息或私人日志。设备支持声明须有设备证据，只有打包成功不够。

## 开发者来源证明

贡献需要遵守 [Developer Certificate of Origin 1.1](https://developercertificate.org/)，并添加本人 `Signed-off-by: Name <email>`。只有能亲自确认 DCO 时才用 `git commit -s`，不要代别人签署。保留上游作者、版权、许可证和已有签署。新项目文件使用 `SPDX-License-Identifier: GPL-2.0-only` 及真实版权持有人。

## 第三方改动

固定上游提交，将 crosvm 改动放在有序补丁队列。每个补丁说明用途、来源及上游状态。保留所改组件的特定许可证，不暗中更改导入代码许可。分发时保留对应源码、构建输入及声明。非公开漏洞报告见 [SECURITY](SECURITY.zh-CN.md)。
