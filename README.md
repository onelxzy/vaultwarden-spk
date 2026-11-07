# Vaultwarden for Synology DSM 7

把 [dani-garcia/vaultwarden](https://github.com/dani-garcia/vaultwarden) 的最新版本封装成群晖 DSM 7 套件（SPK），由 GitHub Actions 定时构建并发布。

与 Bitwarden 客户端兼容的轻量级密码服务器实现。

## 特性

- 不依赖 Docker，直接运行上游原生二进制
- 跟随上游 Release 自动构建，通常在上游发版后数小时内产出新包
- 同时提供 x86_64 与 ARM 两种架构
- 构建过程固定时间戳，同版本产物可重现

## 安装

### 手动安装

在 [Releases](../../releases) 下载对应架构的安装包，然后到套件中心 →「手动安装」：

| 架构 | 文件 |
| --- | --- |
| x86_64 机型 | `Vaultwarden_<version>_x86_64.spk` |
| ARM 机型 | `Vaultwarden_<version>_armv8.spk` |

若不确定机型对应的架构，可在 DSM「控制面板 → 信息中心」查看，或参考 [群晖机型列表](https://kb.synology.com/DSM/tutorial/What_kind_of_CPU_does_my_NAS_have)。

### 通过套件源安装

把本仓库的 Release 汇总到自建套件源后，即可在套件中心直接搜索安装。仓库本身不提供在线套件源。

## 配置

数据库与附件保存在套件数据目录，升级不会丢失；建议定期备份。

套件数据目录：`/var/packages/Vaultwarden/var`

## 构建

```bash
python build.py
```

产物输出到 `dist/`。构建流程：

1. 查询上游最新 Release
2. 按架构匹配对应资产并下载
3. 组装 `package.tgz` 与 `INFO`，打成 SPK
4. 生成 `dist/manifest.json` 与发布说明

仓库结构：

```
build.py                  构建脚本
spk_builder.py            SPK 封装实现
vaultwarden_artifacts.py  从官方 Docker 镜像中提取静态二进制与 web 前端
app.json                  套件元数据与上游资产匹配规则
template/                 INFO、conf、scripts、UI 等套件框架文件
.github/workflows/        自动构建与发布
```

最低 DSM 版本：7.0-40851

## 免责声明

本项目是社区维护的封装脚本，与上游项目及群晖科技均无隶属关系。软件本身版权归上游作者所有，使用前请阅读上游项目的许可证。
