# fMOST Brain Viewer

[English](README.md) · [中文用户指南](docs/USER_GUIDE_zh-CN.md) · [报告问题](https://github.com/orionhu99/fMOST-Brain-Viewer/issues)

fMOST Brain Viewer 是一款 Windows 桌面软件，用于在同一个三维坐标系中查看
已经配准的 SWC 神经元、胞体位置和 Allen Mouse Brain Common Coordinate
Framework（CCFv3）脑图谱。软件支持多数据集联合显示、冠状面浏览、脑区表面、
截图、旋转 GIF 和 session 保存。

> 软件只显示已经使用相同 Allen CCF 坐标约定完成配准的数据，不执行图像配准。

## Windows 安装

普通用户不需要安装 Python、Git，也不需要使用命令行。

1. 从 [最新 Release](https://github.com/orionhu99/fMOST-Brain-Viewer/releases/latest)
   下载 `fMOST-Brain-Viewer-Setup-2.3.6-win64.exe`。
2. 双击安装程序，按照简短的向导完成安装。
3. 启动 **fMOST Brain Viewer**；首次启动时下载 Allen CCF 图谱，或者选择已有图谱目录。
4. 选择已经配准的数据集文件夹并开始查看。

首个正式版本未进行商业代码签名，Windows 可能显示 Microsoft SmartScreen 警告。
继续安装前，请用 Release 中的 `SHA256SUMS.txt` 核对安装包 SHA-256。Release 页面
同时提供内容相同的 portable ZIP。

## 图谱配置

安装包不包含 Allen 图谱体数据。首次运行向导只提供两个清晰入口：

- **Download Allen CCF atlas**：推荐，从 Allen Institute 下载所需图谱。
- **Use an existing atlas folder**：选择已有的图谱文件夹，其中必须包含
  `average_template_25.nrrd` 和 `annotation_10.nrrd`。

图谱和派生缓存与软件、实验数据分开保存。为了快速拖动 10 µm 冠状切面，准备
annotation 缓存需要数 GB 空间；经过校验的中断下载可以继续。存储、校验和常见错误
见[图谱配置说明](docs/ATLAS_SETUP_zh-CN.md)。

Allen Institute 内容不属于本项目 MIT License。使用图谱前请阅读
[Allen Institute Terms of Use](https://alleninstitute.org/terms-of-use/) 和
[Citation Policy](https://alleninstitute.org/citation-policy/)。

## 数据目录

数据集 ID 可以包含字母、数字、连字符和下划线，不需要是纯数字，也不需要遵循某个
实验室特定的命名方式。

```text
<project_folder>/
├── <dataset_id>_reg_800/
│   ├── <dataset_id>-<neuron_id>_reg.swc
│   └── ...
└── <dataset_id>/
    └── soma location/
        ├── <dataset_id>_root_reg.swc
        └── soma location_<dataset_id>.csv   # 可选的人工脑区校正
```

可以选择一个项目目录，也可以选择包含多个项目的上级目录。只有当 axon 文件名中能
唯一识别出一个 neuron ID，而且该 ID 与 soma SWC 的 node ID 匹配时，软件才建立
axon–soma 绑定。缺少匹配或文件名有歧义的 axon 会标记为 `Unmatched`，不会根据文件
顺序猜测。

详细规则见[数据格式说明](docs/DATA_FORMAT_zh-CN.md)。

## 主要功能

- 将一个或多个已配准数据集叠加在同一套 Allen CCF 坐标系中。
- 默认显示平滑全脑 Surface；需要时再从 Settings 加载 25 µm Volume。
- 沿 anterior–posterior 轴浏览 10 µm annotation 切面。
- 按单个神经元或 soma 脑区选择 axon。
- 输入 acronym、全名或 Allen structure ID 时实时显示候选脑区；可用键盘或鼠标添加。
- axon 只渲染真实 SWC 线段，不额外显示固定大小的中间节点。
- 放大 axon 时使用连续平滑圆管，同时保留纤细的 0x 中心线模式。
- soma 可全部显示，也可与当前可见 axon 严格绑定。
- 保存和恢复多数据集 `.fmost-session.json` session。
- 导出 TIFF、PNG、JPEG、BMP 截图以及循环旋转 GIF。
- 限制隐藏 axon actor 缓存，降低长时间浏览时的内存增长。
- 从 Help 菜单后台检查 GitHub 正式版本，并选择、校验和启动更新安装器。

## 隐私与数据处理

软件不包含遥测，不会上传用户数据。只有用户主动下载 Allen 图谱或 ontology，或者
选择 **Help > Check for updates** 时，软件才会访问网络。SWC、CSV 和 NRRD 输入始终只读；派生缓存、设置、日志、截图和
session 分开保存。分享 session、日志或截图前，请自行检查其中是否含有本机路径或
数据集标识。

## 帮助

- [完整中文用户指南](docs/USER_GUIDE_zh-CN.md)
- [常见问题排查](docs/TROUBLESHOOTING_zh-CN.md)
- 软件内：**Help > Check for updates**、**Open log folder** 和 **About fMOST Brain Viewer**

提交 GitHub issue 时只提供脱敏后的 diagnostics；除非明确希望公开，否则不要上传
实验数据。

## 开发者

普通用户应使用安装器或 portable ZIP。源码环境和测试方法见
[CONTRIBUTING.md](CONTRIBUTING.md)。

## 引用和许可

本项目源代码使用 [MIT License](LICENSE)，Copyright © 2026 Orion HU and
Li Bo Lab, Westlake University。Allen 数据、Qt/PySide 和其他依赖保留各自条款，
详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)；引用信息见
[CITATION.cff](CITATION.cff)。

当前版本：**2.3.6 — 透明应用图标与可靠的提权更新**。
